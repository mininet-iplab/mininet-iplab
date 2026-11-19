"""The Lab handle across a process boundary: a small JSON RPC on a unix socket.

A Lab process serves its :class:`~mniplab.labhandle.LocalLabHandle` with
:class:`LabRPCServer`; the manager reaches it through :class:`RemoteLabHandle`,
which has the same interface.  Messages are length-prefixed JSON, one
request and one answer per connection, so concurrent calls never queue
behind one another and a dead Lab is found at ``connect`` rather than in a
half-read stream.  There is no framework.

An error raised in the Lab travels back by class name and is re-raised as
the same class here, so the web API's exception handlers keep answering
400, 404, 502 or 503 exactly as they do for an in-process Lab.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from mniplab.constants import LAB_SOCKET_TEMPLATE
from mniplab.errors import (
    DNSControlError,
    DNSValidationError,
    ExaBGPControlError,
    KrillError,
    LabUnavailableError,
    LinkError,
    ResolverControlError,
    RoutinatorError,
    RPKINotHeldError,
    RPKIObjectError,
    mnIPLabError,
)
from mniplab.exabgp.query import SpeakerNotFoundError
from mniplab.labhandle import OPERATIONS, LabHandle
from mniplab.logging import get_logger

logger = get_logger(__name__)

#: Four bytes of big-endian length before every JSON message.
_HEADER = struct.Struct("!I")

#: Refuse any message larger than this: a route table is kilobytes, not more.
MAX_MESSAGE_SIZE = 64 * 1024 * 1024

#: How long a client waits for the Lab to answer one call.  Long, because an
#: iperf3 run legitimately takes as long as it was asked to.
CALL_TIMEOUT = 600.0

#: Exception classes that cross the boundary as themselves, by name.  A class
#: not listed here arrives as the nearest listed base, so an unknown
#: ValueError subclass is still a 400 and an unknown mnIPLabError a 500.
_ERROR_CLASSES: Tuple[Type[BaseException], ...] = (
    DNSValidationError,
    DNSControlError,
    ResolverControlError,
    ExaBGPControlError,
    SpeakerNotFoundError,
    LinkError,
    RPKINotHeldError,
    RPKIObjectError,
    KrillError,
    RoutinatorError,
    mnIPLabError,
    KeyError,
    ValueError,
    TypeError,
    OSError,
    RuntimeError,
)
_ERRORS_BY_NAME: Dict[str, Type[BaseException]] = {cls.__name__: cls for cls in _ERROR_CLASSES}

#: Constructor keywords the structured errors carry, copied across when set.
_ERROR_ATTRS = (
    "command", "error_code", "zone", "data", "socket_path",
    "hosted_ca", "prefixes", "status", "label", "body",
)

#: Classes whose constructor is not ``(message, **fields)``, rebuilt from the
#: encoded fields by these: KrillError takes its status, label and body first.
_ERROR_REBUILDERS: Dict[str, Callable[[str, Dict[str, Any]], BaseException]] = {
    "KrillError": lambda message, attrs: KrillError(
        attrs.get("status"), attrs.get("label", ""), message, attrs.get("body"),
    ),
}


def socket_path_for(key: str) -> str:
    """Return the socket path a Lab with this key serves on.

    Args:
        key: The Lab key.
    """
    return LAB_SOCKET_TEMPLATE % {"key": key}


def send_message(sock: socket.socket, payload: Dict[str, Any]) -> None:
    """Write one length-prefixed JSON message.

    Args:
        sock: A connected stream socket.
        payload: The message.
    """
    body = json.dumps(payload).encode("utf-8")
    sock.sendall(_HEADER.pack(len(body)) + body)


def receive_message(sock: socket.socket) -> Optional[Dict[str, Any]]:
    """Read one length-prefixed JSON message, or None at a clean end of stream.

    Args:
        sock: A connected stream socket.

    Raises:
        ValueError: The peer sent a length beyond :data:`MAX_MESSAGE_SIZE`.
        ConnectionError: The stream ended inside a message.
    """
    header = _read_exactly(sock, _HEADER.size)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(f"RPC message of {length} bytes exceeds {MAX_MESSAGE_SIZE}")
    body = _read_exactly(sock, length)
    if body is None:
        raise ConnectionError("RPC stream ended inside a message")
    return json.loads(body.decode("utf-8"))


def _read_exactly(sock: socket.socket, size: int) -> Optional[bytes]:
    """Read ``size`` bytes, or None if the stream ends before the first byte."""
    chunks: List[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            if not chunks:
                return None
            raise ConnectionError("RPC stream ended inside a message")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_error(exc: BaseException) -> Dict[str, Any]:
    """Describe an exception so the other side can raise its class again.

    Args:
        exc: The exception raised while serving an operation.
    """
    attrs = {
        name: getattr(exc, name)
        for name in _ERROR_ATTRS
        if getattr(exc, name, None) is not None
    }
    message = getattr(exc, "message", None)
    if not isinstance(message, str):
        # KeyError's str() is the repr of its key; its args carry the text.
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    return {
        "types": [cls.__name__ for cls in type(exc).__mro__],
        "message": str(message),
        "attrs": attrs,
    }


def decode_error(error: Dict[str, Any]) -> BaseException:
    """Rebuild the exception :func:`encode_error` described.

    Args:
        error: The encoded error.

    Returns:
        An instance of the first class in the error's chain this side knows,
        or a plain ``RuntimeError`` for one it does not.
    """
    message = error.get("message", "")
    attrs = error.get("attrs") or {}
    for name in error.get("types", []):
        cls = _ERRORS_BY_NAME.get(name)
        if cls is None:
            continue
        if name in _ERROR_REBUILDERS:
            return _ERROR_REBUILDERS[name](message, attrs)
        try:
            return cls(message, **attrs) if attrs else cls(message)
        except TypeError:
            return cls(message)
    return RuntimeError(message)


class LabRPCServer:
    """Serves a handle's operations on a unix socket, one thread per call.

    Args:
        handle: The in-process handle to serve.
        socket_path: Where to listen; parents are created, a stale socket
            file is replaced.
        on_stopped: Called after a ``stop`` operation has completed, so the
            process serving the Lab knows it may exit.
    """

    def __init__(
        self,
        handle: LabHandle,
        socket_path: str,
        on_stopped: Optional[Callable[[], None]] = None,
    ) -> None:
        self.handle = handle
        self.socket_path = socket_path
        self.on_stopped = on_stopped
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._closing = threading.Event()

    def start(self) -> None:
        """Bind, listen and serve on a background thread."""
        os.makedirs(os.path.dirname(self.socket_path), exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        server.listen(16)
        self._server = server
        self._thread = threading.Thread(target=self._accept_loop, name="lab-rpc", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Stop accepting calls and remove the socket file."""
        self._closing.set()
        if self._server is not None:
            try:
                self._server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._server.close()
            self._server = None
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._closing.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                break
            threading.Thread(target=self._serve_connection, args=(conn,), daemon=True).start()

    def _serve_connection(self, conn: socket.socket) -> None:
        """Answer every request on one connection until the peer hangs up."""
        with conn:
            while True:
                try:
                    request = receive_message(conn)
                except (OSError, ValueError):
                    return
                if request is None:
                    return
                try:
                    send_message(conn, self._answer(request))
                except OSError:
                    return

    def _answer(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Perform one request and describe its outcome."""
        request_id = request.get("id")
        operation = request.get("operation")
        if not isinstance(operation, str) or operation not in OPERATIONS:
            return {"id": request_id, "error": encode_error(ValueError(f"unknown Lab operation: {operation}"))}
        try:
            # _invoke answers the operation's JSON-able data; the public
            # method would rebuild a dataclass this side has no use for.
            result = self.handle._invoke(
                operation, *request.get("args", []), **request.get("kwargs", {}),
            )
        except Exception as exc:  # every error crosses back as itself
            return {"id": request_id, "error": encode_error(exc)}
        if operation == "stop" and self.on_stopped is not None:
            self.on_stopped()
        return {"id": request_id, "result": result}


class RemoteLabHandle(LabHandle):
    """A Lab handle over the RPC socket of a Lab in another process.

    Args:
        key: The Lab's key.
        example: The Lab Example id it runs.
        socket_path: The socket its process serves on; defaults to the
            path derived from the key.
        timeout: Seconds to wait for one call's answer.
    """

    def __init__(
        self,
        key: str,
        example: str,
        socket_path: Optional[str] = None,
        timeout: float = CALL_TIMEOUT,
    ) -> None:
        self.key = key
        self.example = example
        self.socket_path = socket_path or socket_path_for(key)
        self.timeout = timeout
        self._next_id = 0
        self._id_lock = threading.Lock()

    def _describe(self) -> str:
        return f"Lab '{self.example}' ({self.key})"

    def _invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        with self._id_lock:
            self._next_id += 1
            request_id = self._next_id
        request = {"id": request_id, "operation": operation, "args": list(args), "kwargs": kwargs}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                send_message(sock, request)
                answer = receive_message(sock)
        except socket.timeout as exc:
            raise LabUnavailableError(
                f"{self._describe()} did not answer '{operation}' within {self.timeout:g} s"
            ) from exc
        except OSError as exc:
            raise LabUnavailableError(
                f"{self._describe()} is not running: its process cannot be reached ({exc})"
            ) from exc
        if answer is None:
            raise LabUnavailableError(
                f"{self._describe()} stopped while answering '{operation}'"
            )
        if "error" in answer:
            raise decode_error(answer["error"])
        return answer.get("result")
