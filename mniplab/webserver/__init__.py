import asyncio
import os
import signal
import socket
import sys
import threading
import logging
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, Request, Response
from fastapi.responses import JSONResponse
from starlette import status as http_status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from mininet.log import info, error

from .config import settings
from . import auth
from .api import router as api_router
from .websocket import (
    handle_capture_websocket,
    handle_labs_websocket,
    handle_network_tool_websocket,
    handle_prompt_websocket,
    handle_terminal_websocket,
    handle_topology_websocket,
)
from .logins import LOGINS_FILE, LoginStore
from .server_settings import SETTINGS_FILE, SettingsStore
from .session import registry
from mniplab import __version__
from mniplab.cleanup import MNIPLAB_LOG_FILE as _WEB_LOG_FILE
from mniplab.errors import (
    DNSControlError,
    DNSValidationError,
    ExaBGPControlError,
    KrillError,
    LabUnavailableError,
    RoutinatorError,
    WebServerError,
)
from mniplab.exabgp.query import SpeakerNotFoundError

# Global server state for shutdown
_server_instance: Optional[uvicorn.Server] = None
_server_thread = None
_server_loop = None
_server_socket = None


class _ConfirmingServer(uvicorn.Server):
    """Ask before an interactive Ctrl+C shuts down the server and active Lab.

    The confirmation only makes sense when a human is there interactively to
    answer it. A background launch (a container's foregrounded process,
    ``nohup``, a supervisor) has no interactive stdin to read, and a second
    Ctrl+C sent before the first prompt is answered means "stop asking, just
    quit" -- both shut down immediately, exactly like a plain uvicorn server
    would. The prompt itself is read on a daemon thread and signaled back
    into the event loop, so a prompt nobody answers can never block request
    handling or hold up process exit.
    """

    def __init__(self, config: uvicorn.Config, *args: object, **kwargs: object) -> None:
        super().__init__(config, *args, **kwargs)
        self._awaiting_confirmation = False

    def handle_exit(self, sig: int, frame: Optional[FrameType]) -> None:
        """Confirm an interactive SIGINT before requesting server shutdown."""
        if sig != signal.SIGINT or self.should_exit:
            super().handle_exit(sig, frame)
            return

        if self._awaiting_confirmation or not sys.stdin.isatty():
            # A second Ctrl+C, or nobody interactive is attached to ask:
            # behave like a plain Ctrl+C and shut down now.
            super().handle_exit(sig, frame)
            return

        self._awaiting_confirmation = True
        asyncio.get_running_loop().create_task(self._confirm_shutdown(sig, frame))

    async def _confirm_shutdown(self, sig: int, frame: Optional[FrameType]) -> None:
        """Read the confirmation off a daemon thread, then act on the answer."""
        loop = asyncio.get_running_loop()
        answer_future: "asyncio.Future[str]" = loop.create_future()

        def read_answer() -> None:
            try:
                answer = input("\nStop web server and active lab? [y/N]: ")
            except EOFError:
                answer = ""

            def resolve() -> None:
                if not answer_future.done():
                    answer_future.set_result(answer)

            loop.call_soon_threadsafe(resolve)

        threading.Thread(target=read_answer, daemon=True).start()

        try:
            answer = await answer_future
        finally:
            self._awaiting_confirmation = False

        if answer.strip().lower() in {"y", "yes"}:
            super().handle_exit(sig, frame)
        elif not self.should_exit:
            print("*** Shutdown cancelled; web server is still running.")


def _bind_server_socket(config: uvicorn.Config, host: str, port: int) -> socket.socket:
    """Bind the web server socket and convert uvicorn exits to domain errors."""
    try:
        return config.bind_socket()
    except (OSError, SystemExit) as exc:
        raise WebServerError(
            f"Could not bind web server to {host}:{port}. "
            "The port may already be in use or the address may be unavailable."
        ) from exc


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    
    # Composition root: teach the auth layer how to close a destroyed
    # Session's Terminals, and how to count a live Session's Terminals for
    # the Session Cap.  auth must not import the Terminal registry, so both
    # dependencies are registered here, from the web layer inward.
    auth.set_terminal_closer(registry.close_session_terminals)
    auth.set_terminal_counter(registry.terminal_count)

    app = FastAPI(
        title="Mininet-IPLab Web Server",
        description="Web-based interface for Mininet-IPLab network simulation",
        version="0.0.1"
    )
    
    # Security headers middleware
    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next) -> Response:
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self' ws: wss:; "
                "font-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self';"
            )
            # Only main.js is versioned; the modules it imports are not, so
            # without this a browser may reuse a stale one next to a fresh
            # index.html. no-cache still caches, but revalidates by ETag.
            if request.url.path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

    app.add_middleware(SecurityHeadersMiddleware)

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Global exception handler: ValueError from registry/services → 400 Bad Request
    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> Response:
        return JSONResponse(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)}
        )

    # Knot semantic check failures (CNAME collisions, bad syntax, ...) surface
    # as structured 400s so the Web UI can render them inline on the form.
    @app.exception_handler(DNSValidationError)
    async def dns_validation_error_handler(request: Request, exc: DNSValidationError) -> Response:
        return JSONResponse(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            content={
                "detail": exc.message,
                "command": exc.command,
                "error_code": exc.error_code,
                "zone": exc.zone,
                "data": exc.data,
            }
        )

    # Control channel failures (socket missing, knotd down) mean the DNS
    # Service is unavailable right now, not that the request was malformed.
    @app.exception_handler(DNSControlError)
    async def dns_control_error_handler(request: Request, exc: DNSControlError) -> Response:
        return JSONResponse(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": exc.message,
                "command": exc.command,
                "error_code": exc.error_code,
                "zone": exc.zone,
            }
        )

    # A Node named under /speakers that is not a Speaker is not found there,
    # not a bad request: the panel's poll must tell the two apart.
    @app.exception_handler(SpeakerNotFoundError)
    async def speaker_not_found_handler(request: Request, exc: SpeakerNotFoundError) -> Response:
        return JSONResponse(
            status_code=http_status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)}
        )

    # The Speaker's daemon rejected a command or did not answer: the thing
    # behind the API failed, and the command is the only reason ExaBGP gives.
    @app.exception_handler(ExaBGPControlError)
    async def exabgp_control_error_handler(request: Request, exc: ExaBGPControlError) -> Response:
        return JSONResponse(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            content={"detail": exc.message, "command": exc.command}
        )

    # Krill is the RPKI CA's own service. When it cannot be reached, or answers
    # a read with an error, the CA is unavailable right now, the answer the DNS
    # Service gives for a control socket that is down. A refused change is a
    # ValueError (400) by the time it gets here.
    @app.exception_handler(KrillError)
    async def krill_error_handler(request: Request, exc: KrillError) -> Response:
        return JSONResponse(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": exc.message}
        )

    # Routinator is the Validator's own service. When its HTTP endpoint does
    # not answer, the Validator is unavailable right now, the same answer an
    # unreachable Krill gets.
    @app.exception_handler(RoutinatorError)
    async def routinator_error_handler(request: Request, exc: RoutinatorError) -> Response:
        return JSONResponse(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)}
        )

    # The Lab's process is gone: the diagnostic names the Lab, and the
    # answer is that the Lab cannot serve right now, not that the request
    # was wrong.
    @app.exception_handler(LabUnavailableError)
    async def lab_unavailable_handler(request: Request, exc: LabUnavailableError) -> Response:
        return JSONResponse(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)}
        )

    # Stopping the manager stops every Lab it launched: a Lab is a child
    # process, and one left behind would keep its network alive with
    # nobody to reach it.
    app.router.on_shutdown.append(_stop_labs_on_shutdown)

    # Include API routes
    app.include_router(api_router)

    # Health and readiness endpoints — deliberately unauthenticated so that load
    # balancers and container probes can reach them.
    @app.get("/health", tags=["health"])
    async def health() -> dict:
        """Report service health and a summary of the Labs alive.

        Always returns 200 so a probe can distinguish "server down" from
        "server up, no Lab running".  The Lab fields describe the oldest
        Lab alive; ``labs`` counts them all.

        Returns:
            Service status, version, and a summary of the running Labs.
        """
        status = await registry.get_status()
        return {
            "status": "ok",
            "version": __version__,
            "lab_running": status.get("running", False),
            "labs": len(registry.lab_service.labs()),
            "lab_example": status.get("example"),
            "uptime": status.get("uptime", 0),
            "nodes": status.get("nodes", 0),
            "active_terminals": status.get("active_terminals", 0),
            "link_condition_drift": status.get("link_condition_drift", 0),
        }

    @app.get("/ready", tags=["health"])
    async def ready() -> dict:
        """Report whether a Lab is running and ready to serve.

        Returns:
            Readiness flag, plus the Lab Example name when one is running.
        """
        status = await registry.get_status()
        if status.get("running"):
            return {
                "ready": True,
                "lab": status.get("example"),
                "nodes": status.get("nodes", 0),
            }
        return {"ready": False, "reason": "no lab running"}

    @app.websocket("/ws/labs")
    async def websocket_labs(websocket: WebSocket):
        await handle_labs_websocket(websocket)

    # A Lab's feed: its Topology and Service updates
    @app.websocket("/ws/labs/{key}/topology")
    async def websocket_topology(websocket: WebSocket, key: str):
        await handle_topology_websocket(websocket, key)

    # WebSocket endpoint delivering a Terminal on a Lab's Node
    @app.websocket("/ws/labs/{key}/terminal")
    async def websocket_terminal(
        websocket: WebSocket, key: str, node: str, terminal: Optional[str] = None
    ):
        await handle_terminal_websocket(websocket, key, node, terminal)

    # WebSocket endpoint delivering a Packet Capture on a Node's Interface
    @app.websocket("/ws/labs/{key}/capture")
    async def websocket_capture(
        websocket: WebSocket, key: str, node: str, interface: str,
        capture_filter: str = Query("", alias="filter"),
    ) -> None:
        await handle_capture_websocket(websocket, key, node, interface, capture_filter)

    # WebSocket endpoint streaming one Network Tools ping or traceroute
    @app.websocket("/ws/labs/{key}/network-tool")
    async def websocket_network_tool(websocket: WebSocket, key: str) -> None:
        await handle_network_tool_websocket(websocket, key)

    # WebSocket endpoint delivering the Lab Prompt
    @app.websocket("/ws/labs/{key}/prompt")
    async def websocket_prompt(websocket: WebSocket, key: str):
        await handle_prompt_websocket(websocket, key)
    
    # Serve static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    # Root and terminal HTML pages
    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve topology visualization page."""
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse("<h1>Mininet-IPLab</h1><p>Web UI starting...</p>")
    
    @app.get("/terminal", response_class=HTMLResponse)
    async def terminal():
        """Serve terminal page."""
        terminal_file = static_dir / "terminal.html"
        if terminal_file.exists():
            return FileResponse(terminal_file)
        return HTMLResponse("<h1>Terminal</h1><p>Loading...</p>")

    @app.get("/guide", response_class=HTMLResponse)
    async def guide():
        """Serve the lab guide viewer page."""
        guide_file = static_dir / "guide.html"
        if guide_file.exists():
            return FileResponse(guide_file)
        return HTMLResponse("<h1>Guide</h1><p>Loading...</p>")

    @app.get("/authoring", response_class=HTMLResponse)
    async def authoring_page(request: Request) -> Response:
        """Serve sign-in for a missing Session, or the Instructor workspace.

        Args:
            request: The page request carrying the session cookie.

        Returns:
            The sign-in shell or authenticated authoring page.

        Raises:
            HTTPException: 403 when the authenticated user is a Learner.
        """
        try:
            user = await auth.get_current_user_with_role(request.cookies.get("session"))
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            # Keep the authoring URL and its query while the existing login
            # shell signs in. APIs still require an authenticated Instructor.
            return FileResponse(static_dir / "index.html")
        if user["role"] != "instructor":
            raise HTTPException(status_code=403, detail="Instructor role required")
        authoring_file = static_dir / "authoring.html"
        if authoring_file.exists():
            return FileResponse(authoring_file)
        return HTMLResponse("<h1>Lab authoring is unavailable</h1>", status_code=503)

    @app.get("/labs/{key}", response_class=HTMLResponse)
    async def lab_page(key: str):
        """Serve the same app shell as ``/``, addressed by a Lab key.

        The URL is the binding (see ``SessionRegistry``): a link an
        Instructor hands a Learner, or a reload of the page, lands here and
        the client resolves ``key`` against its own status once signed in —
        this route only has to exist so that landing does not 404.
        """
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse("<h1>Mininet-IPLab</h1><p>Web UI starting...</p>")

    return app


async def _stop_labs_on_shutdown() -> None:
    """Stop every Lab this server launched when the server shuts down.

    Only a Lab in a child process of this server is its to stop: a Lab
    attached in-process belongs to the script that built it, which stops
    it itself when its own prompt ends.  The server restarts with no Lab
    alive.
    """
    for result in await registry.stop_all():
        if "error" not in result:
            info(f"*** Lab '{result['example']}' ({result['key']}) stopped with the web server\n")


def start_server(
    net: Any = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    block: bool = True,
    prompt_fd: Optional[int] = None,
    local_fd: Optional[int] = None,
    prompt_interrupt: Optional[Callable[[], None]] = None,
) -> None:
    """Start the web server.

    Args:
        net: Mininet network instance. When ``None`` the server starts in
             standalone mode without a pre-loaded lab — users select a lab
             from the web UI via ``POST /api/labs``, each Lab in a child
             process of its own.
        host: Bind host (default: from settings)
        port: Bind port (default: from settings)
        block: If True, block until server stops; if False, run in background
        prompt_fd: Master pseudo-terminal file descriptor for the Lab Prompt.
        local_fd: File descriptor of local terminal output for --enable-web.
        prompt_interrupt: Callable that interrupts the Lab Prompt of ``net``
            when a viewer sends Ctrl+C, for --enable-web.
    """
    if not settings.enable_web:
        info("*** Web server disabled\n")
        return
    
    host = host or settings.host
    port = port or settings.port
    
    # Check and display configuration warnings
    from .config import check_config_warnings
    check_config_warnings()
    
    # Register the lab instance (only when a pre-built net is provided).  It
    # is registered as the app starts up, on the loop that serves, never on
    # a loop of its own: the Lab Prompt's reader is a task of whichever loop
    # attaches it, and a loop that stops after registration would leave the
    # prompt silent for the launching terminal and every viewer.
    registered = threading.Event()
    failure: list[Exception] = []

    async def register_lab() -> None:
        try:
            if net is not None:
                import sys
                example_name = Path(sys.argv[0]).stem if sys.argv else "unknown"
                await registry.start_lab(
                    net, example_name, prompt_fd=prompt_fd, local_fd=local_fd,
                    prompt_interrupt=prompt_interrupt,
                )
        except Exception as exc:
            failure.append(exc)
            raise
        finally:
            registered.set()

    # Configure logging to file for web server components only
    # (Redundant if started via run_lab, but useful for manual starts)
    log_file = _WEB_LOG_FILE
    from mniplab.logging import setup_logging, get_logger
    setup_logging(
        level="INFO",
        log_file=log_file,
        log_to_stdout=False,
        force=True,
        root_logger=False  # Web server logs only
    )
    
    web_logger = get_logger('mniplab.webserver')
    if net is None:
        # Under `serve` only: the Limits the Instructor changed are kept
        # beside the Assignments file.  A Lab script reads no store.
        # The logins added there are kept apart, so discarding the Limits
        # never locks a class out.
        server_files = os.path.dirname(registry.groups.path)
        registry.settings_store = SettingsStore(os.path.join(server_files, SETTINGS_FILE), settings)
        registry.settings_store.load()
        settings.use_login_store(LoginStore(os.path.join(server_files, LOGINS_FILE)))
    info(f"\n*** Starting web server on http://{host}:{port}\n")
    info(f"*** Web server logs: {log_file}\n")

    # Show login info reflecting single-user vs multi-user configuration
    user_registry = settings.get_user_registry()
    if settings.users.strip():
        # Multi-user mode: list each user, password, and role
        info("*** Login accounts (user / password / role):\n")
        for uname, uinfo in user_registry.items():
            if not uinfo.get("added"):
                info(f"***   {uname} / {uinfo['password']} / {uinfo['role']}\n")
    else:
        # Single-user mode (backward-compatible)
        info(f"*** Login username: {settings.username}  password: {settings.password}\n")
    # A page-added password stays where the Instructor chose to show it.
    added = sum(1 for uinfo in user_registry.values() if uinfo.get("added"))
    if added:
        info(f"***   and {added} login{'' if added == 1 else 's'} added in Server settings "
             "(passwords shown there)\n")
    shared_login = user_registry.get(settings.username, {})
    if settings.password_was_generated and shared_login.get("password") == settings.password:
        # Only while the shared login is accepted: MNIPLAB_USERS replaces it.
        # The console only: a log file outlives the run and gets shared.
        info("***   (password generated for this run; set MNIPLAB_PASSWORD to choose one)\n")
        web_logger.info("A login password was generated for this run; it is shown on the console only")

    # The mode is fixed for the life of the server: it is read here, once.
    if settings.lab_mode == 'multi':
        info(f"*** Lab mode: multi (a Lab per Group, at most {settings.lab_cap} alive)\n")
        info(f"*** Assignments: {registry.groups.path}\n")
    else:
        info("*** Lab mode: single\n")
    web_logger.info(f"Web server starting on {host}:{port} (lab mode: {settings.lab_mode})")
    
    app = create_app()
    app.router.on_startup.append(register_lab)

    global _server_instance, _server_thread, _server_loop, _server_socket

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        log_config=None,  # Disable default logging
        access_log=False  # Disable access logs to stdout
    )
    # Bind before anything else so a busy port fails fast and loudly instead
    # of dying quietly inside uvicorn's own startup.
    server_socket = _bind_server_socket(config, host, port)

    server: uvicorn.Server
    if block:
        # Run in foreground
        web_logger.info("Web server socket bound - accepting connections")
        server = _ConfirmingServer(config)
        _server_instance = server
        try:
            server.run(sockets=[server_socket])
        except (OSError, SystemExit) as exc:
            raise WebServerError(
                f"Web server failed during startup on {host}:{port}."
            ) from exc
        finally:
            server_socket.close()
            _clear_server_state()
        web_logger.info("Web server stopped")
        info("*** Web server stopped\n")
    else:
        # Run in background thread
        server = uvicorn.Server(config)
        
        # Store global references for shutdown
        _server_instance = server
        _server_socket = server_socket
        
        def run_server() -> None:
            global _server_loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _server_loop = loop
            try:
                loop.run_until_complete(server.serve(sockets=[server_socket]))
            finally:
                server_socket.close()
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        _server_thread = thread

        # Return once the Lab is registered, as before, or once the server
        # has died before getting there; a registration that failed ends the
        # server's startup, so raise it here as before too.
        while not registered.wait(timeout=0.2) and thread.is_alive():
            pass
        if failure:
            thread.join(timeout=5.0)
            _clear_server_state()
            raise failure[0]
        
        web_logger.info("Web server socket bound - running in background")
        info("*** Web server started in background\n")


def stop_server():
    """Stop the web server gracefully (synchronous wrapper for CLI).
    
    This schedules the async shutdown in the server's event loop and waits for completion.
    """
    global _server_instance, _server_thread, _server_loop
    
    if _server_instance is None or _server_loop is None:
        info("*** No web server running\n")
        return
    
    info("\n*** Stopping web server...\n")
    
    try:
        # Schedule shutdown in the server's event loop
        future = asyncio.run_coroutine_threadsafe(
            _shutdown_in_server_loop(),
            _server_loop
        )
        # Wait for shutdown to complete (with timeout)
        future.result(timeout=10.0)
    except (TimeoutError, RuntimeError, asyncio.CancelledError) as e:
        error(f"\n*** Error during web server shutdown: {e}\n")
    
    # Signal server to stop
    if _server_instance:
        _server_instance.should_exit = True
    
    # Wait for thread to complete (with timeout)
    if _server_thread and _server_thread.is_alive():
        _server_thread.join(timeout=5.0)
    
    _clear_server_state()
    
    info("\n*** Web server stopped\n")


def _clear_server_state() -> None:
    """Forget the server's instance, thread, loop and bound socket."""
    global _server_instance, _server_thread, _server_loop, _server_socket
    _server_instance = None
    _server_thread = None
    _server_loop = None
    _server_socket = None


async def _shutdown_in_server_loop():
    """Async shutdown routine that runs in the server's event loop."""
    await registry.stop_all()


__all__ = ["create_app", "start_server", "stop_server", "settings", "registry"]
