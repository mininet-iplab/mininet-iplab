"""KrillClient: the only code in the project that talks to Krill's HTTP API.

Krill serves its API over HTTPS and, when ``unix_socket`` is set, over a unix
socket as well. The socket is a file, so the root namespace reaches a Node's
Krill through the bind-mounted path with no TLS and no namespace hop, as
:class:`mniplab.resolver.control_client.UnboundControlClient` does for Unbound.
Root needs no token over the socket; a token is sent when one is given.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mniplab.errors import KrillError
from mniplab.rpki.models import ASPA, ROA

API_PREFIX = "/api/v1"
TIMEOUT_SECONDS = 10.0
PUBLISH_INTERVAL_SECONDS = 1.0


class _UnixConnection(http.client.HTTPConnection):
    """An HTTP connection whose transport is a unix domain socket."""

    def __init__(self, socket_path: str, timeout: float) -> None:
        """Initialize a connection that will dial ``socket_path``.

        Args:
            socket_path: Host-side path of Krill's unix socket.
            timeout: Seconds allowed for each socket operation.
        """
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        """Open the unix socket connection.

        Raises:
            OSError: If the socket is missing or refuses the connection.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def _error_from(status: int, text: str) -> KrillError:
    """Build the error for a non-2xx answer from Krill's error JSON.

    Args:
        status: The HTTP status Krill answered with.
        text: The response body.

    Returns:
        A KrillError carrying Krill's label and message when the body is JSON.
    """
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        return KrillError(status, str(body.get("label", "")), str(body.get("msg", "")), body)
    return KrillError(status, "", text.strip(), body)


def _roa_json(roa: ROA) -> Dict[str, Any]:
    """Return a ROA in Krill's delta grammar.

    Args:
        roa: The ROA to send.

    Returns:
        ``asn``, ``prefix`` and ``max_length``.
    """
    return {"asn": roa.origin, "prefix": roa.prefix, "max_length": roa.max_length}


def _aspa_json(aspa: ASPA) -> Dict[str, Any]:
    """Return an ASPA in Krill's grammar.

    Args:
        aspa: The ASPA to send.

    Returns:
        ``customer`` and ``providers``.
    """
    return {"customer": aspa.customer, "providers": list(aspa.providers)}


class _PublishGate:
    """The publish record for one Hosted CA on one Krill socket.

    Attributes:
        lock: Held for the whole of a publish, so publishes at this Hosted CA never overlap.
        returned_at: The clock reading when the last publish returned, or None before the first.
    """

    def __init__(self) -> None:
        """Start with no publish recorded."""
        self.lock = threading.Lock()
        self.returned_at: Optional[float] = None


_GATES_LOCK = threading.Lock()
_PUBLISH_GATES: Dict[Tuple[str, str], _PublishGate] = {}


class KrillClient:
    """Adapter for one RPKI CA's Krill API, over its unix socket.

    Each call opens its own connection, so a client holds nothing between
    calls and constructing one touches no socket. The publish record that
    paces Hosted CA publishes is kept at module level, so every client on the
    same socket shares it.

    Attributes:
        socket_path: Host-side path of Krill's unix API socket.
        token: The admin token to send as a bearer token, if any.
        timeout: Seconds allowed for each socket operation.
    """

    def __init__(
        self,
        socket_path: str,
        token: Optional[str] = None,
        timeout: float = TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialize the client against one Krill socket.

        Args:
            socket_path: Host-side path of Krill's unix API socket.
            token: The admin token, sent as ``Authorization: Bearer``.
            timeout: Seconds allowed for each socket operation.
            clock: Source of the seconds that pace publishes at a Hosted CA.
            sleep: Blocks for the given seconds to pace publishes at a Hosted CA.
        """
        self.socket_path = socket_path
        self.token = token
        self.timeout = timeout
        self._clock = clock
        self._sleep = sleep

    def _publish(self, handle: str, path: str, body: Any) -> None:
        """POST a request that makes Krill publish at a Hosted CA, paced against its last publish.

        A Validator keeps its cached manifest unless a new one has a greater
        manifestNumber and a more recent thisUpdate (RFC 9286 section 4.2.1), and
        Krill stamps thisUpdate to the second. So each publish waits until a second
        has passed since the previous publish at the same Hosted CA on the same
        socket returned, and publishes at different Hosted CAs or sockets never wait.
        A refused publish is recorded too: Krill published nothing, but the extra
        wait is harmless and simpler than telling the two apart.

        Args:
            handle: The Hosted CA the request publishes at.
            path: The path below the API prefix.
            body: The JSON body to send.

        Raises:
            KrillError: If Krill refuses the request or cannot be reached.
        """
        with _GATES_LOCK:
            gate = _PUBLISH_GATES.setdefault((self.socket_path, handle), _PublishGate())
        with gate.lock:
            if gate.returned_at is not None:
                remaining = gate.returned_at + PUBLISH_INTERVAL_SECONDS - self._clock()
                if remaining > 0:
                    self._sleep(remaining)
            try:
                self._request("POST", path, body)
            finally:
                gate.returned_at = self._clock()

    def _exchange(
        self,
        method: str,
        path: str,
        body: Any = None,
        accept: str = "application/json",
    ) -> Tuple[int, str]:
        """Send one request to the socket and return the answer's status and text.

        Args:
            method: The HTTP method.
            path: The full request path.
            body: The JSON body to send, if any.
            accept: The ``Accept`` header to send.

        Returns:
            The HTTP status and the body text of a 2xx answer.

        Raises:
            KrillError: If Krill answers non-2xx or cannot be reached.
        """
        connection = _UnixConnection(self.socket_path, self.timeout)
        headers = {"Accept": accept}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            status = response.status
            text = response.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException) as exc:
            raise KrillError(
                None, "unreachable", f"no answer on {self.socket_path}: {exc}"
            ) from exc
        finally:
            connection.close()

        if not 200 <= status < 300:
            raise _error_from(status, text)
        return status, text

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        """Send one API request and return its parsed JSON answer.

        Args:
            method: The HTTP method.
            path: The path below ``/api/v1``.
            body: The JSON body to send, if any.

        Returns:
            The parsed answer, or None when Krill answered with an empty body.

        Raises:
            KrillError: If Krill answers non-2xx, cannot be reached, or sends
                an answer that is not JSON where JSON was expected.
        """
        status, text = self._exchange(method, API_PREFIX + path, body)
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise KrillError(status, "", f"{method} {path} answered non-JSON text") from exc

    def tal(self) -> str:
        """Return the trust anchor locator Krill serves for its testbed CA.

        The socket serves the locator at the root of Krill's tree, outside the
        API prefix, so the root namespace reads it without TLS.

        Returns:
            The locator's text: the certificate URI, then the public key.

        Raises:
            KrillError: If Krill cannot be reached or does not serve the locator.
        """
        _status, text = self._exchange("GET", "/ta/ta.tal", accept="*/*")
        return text

    def is_running(self) -> bool:
        """Report whether Krill answers on its socket.

        Returns:
            True if the Hosted CA list was answered, False otherwise.
        """
        try:
            self.list_cas()
        except KrillError:
            return False
        return True

    def list_cas(self) -> List[str]:
        """Return the handle of every CA Krill holds, the testbed CA included.

        Returns:
            The CA handles, in Krill's order.
        """
        body = self._request("GET", "/cas")
        return [str(entry["handle"]) for entry in body.get("cas", [])]

    def add_ca(self, handle: str) -> None:
        """Create a CA with this handle.

        Args:
            handle: The CA's handle, for example ``as65002``.
        """
        self._request("POST", "/cas", {"handle": handle})

    def show_ca(self, handle: str) -> Dict[str, Any]:
        """Return a CA's state, including the resources it holds.

        Args:
            handle: The CA's handle.

        Returns:
            Krill's CA description.
        """
        return self._request("GET", f"/cas/{handle}")

    def child_request(self, handle: str) -> Dict[str, Any]:
        """Return a CA's request to become a child of a parent CA.

        Args:
            handle: The Hosted CA's handle.

        Returns:
            The request, with its ``id_cert``.
        """
        return self._request("GET", f"/cas/{handle}/id/child_request.json")

    def add_child(
        self,
        parent: str,
        child: str,
        resources: Dict[str, str],
        id_cert: str,
    ) -> Dict[str, Any]:
        """Certify a Hosted CA under a parent, granting it resources.

        Args:
            parent: The parent CA's handle.
            child: The Hosted CA's handle.
            resources: ``asn``, ``ipv4`` and ``ipv6`` strings, comma-separated.
            id_cert: The child's ``id_cert`` from :meth:`child_request`.

        Returns:
            The parent's response, for the child to accept.
        """
        return self._request(
            "POST",
            f"/cas/{parent}/children",
            {"handle": child, "resources": resources, "id_cert": id_cert},
        )

    def add_parent(self, child: str, parent: str, response: Dict[str, Any]) -> None:
        """Make a parent's certification the child's parent relationship.

        Not paced like a publish: the Lab adds a parent before the Hosted CA
        has a repository, and Krill publishes nothing until it has one.

        Args:
            child: The Hosted CA's handle.
            parent: The parent CA's handle.
            response: The parent's response from :meth:`add_child`.
        """
        self._request(
            "POST", f"/cas/{child}/parents", {"handle": parent, "response": response}
        )

    def publisher_request(self, handle: str) -> Dict[str, Any]:
        """Return a CA's request to publish under the repository.

        Args:
            handle: The CA's handle.

        Returns:
            The request, to hand to :meth:`add_publisher` as it is.
        """
        return self._request("GET", f"/cas/{handle}/id/publisher_request.json")

    def add_publisher(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Register a publisher with the repository.

        Args:
            request: The publisher request from :meth:`publisher_request`.

        Returns:
            The repository's response, for the CA to accept.
        """
        return self._request("POST", "/pubd/publishers", request)

    def configure_repo(self, handle: str, repository_response: Dict[str, Any]) -> None:
        """Point a CA at the repository that accepted its publisher request.

        Args:
            handle: The CA's handle.
            repository_response: The response from :meth:`add_publisher`.
        """
        self._publish(
            handle, f"/cas/{handle}/repo", {"repository_response": repository_response}
        )

    def list_roas(self, handle: str) -> List[ROA]:
        """Return the ROAs a CA has signed, without their signed objects.

        Args:
            handle: The CA's handle.

        Returns:
            One ROA per authorisation.
        """
        entries = self._request("GET", f"/cas/{handle}/routes")
        return [
            ROA(prefix=entry["prefix"], max_length=entry["max_length"], origin=entry["asn"])
            for entry in entries
        ]

    def update_roas(
        self,
        handle: str,
        added: Sequence[ROA],
        removed: Sequence[ROA],
    ) -> None:
        """Apply a ROA delta to a CA.

        Args:
            handle: The CA's handle.
            added: ROAs to sign.
            removed: ROAs to withdraw, matched by prefix, maximum length and origin.

        Raises:
            KrillError: If Krill refuses the delta, for example with ``notheld``
                for a prefix the CA does not hold.
        """
        self._publish(
            handle,
            f"/cas/{handle}/routes",
            {"added": [_roa_json(roa) for roa in added],
             "removed": [_roa_json(roa) for roa in removed]},
        )

    def list_aspas(self, handle: str) -> List[ASPA]:
        """Return the ASPAs a CA has signed.

        Args:
            handle: The CA's handle.

        Returns:
            One ASPA per customer.
        """
        entries = self._request("GET", f"/cas/{handle}/aspas")
        return [
            ASPA(customer=entry["customer"], providers=list(entry["providers"]))
            for entry in entries
        ]

    def update_aspas(
        self,
        handle: str,
        add_or_replace: Sequence[ASPA],
        remove: Sequence[int],
    ) -> None:
        """Add, replace and remove a CA's ASPAs.

        Args:
            handle: The CA's handle.
            add_or_replace: ASPAs to sign, replacing any existing one for the customer.
            remove: Customer ASNs whose ASPAs are withdrawn.

        Raises:
            KrillError: If Krill refuses, for example for a customer the CA
                does not hold.
        """
        self._publish(
            handle,
            f"/cas/{handle}/aspas",
            {"add_or_replace": [_aspa_json(aspa) for aspa in add_or_replace],
             "remove": list(remove)},
        )
