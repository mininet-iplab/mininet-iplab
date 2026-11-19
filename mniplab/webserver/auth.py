"""Authentication module for mnIPLab web server."""

import time
import secrets
from typing import Any, Awaitable, Callable, Optional, Dict
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Security, status, Cookie
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings
from mniplab.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimiter:
    """Simple rate limiter for authentication attempts."""
    attempts: Dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    window: int = 60  # seconds
    max_attempts: int = settings.login_rate_limit
    
    def allows(self, key: str) -> bool:
        """Return whether the key is under its limit, spending nothing.

        Split from :meth:`check` so that a caller which may refuse a request
        for its own reasons can ask first and charge only once the request has
        actually been served.  See :meth:`record`.

        Args:
            key: The identity being limited.

        Returns:
            True if the key has budget left in the current window.
        """
        now = time.time()
        self.attempts[key] = [t for t in self.attempts[key] if now - t < self.window]
        return len(self.attempts[key]) < self.max_attempts

    def record(self, key: str) -> None:
        """Charge the key for one request.

        Args:
            key: The identity being limited.
        """
        self.attempts[key].append(time.time())

    def check(self, key: str) -> bool:
        """Return True if within limit, False if exceeded, charging the attempt.

        The one-shot form, for callers whose only verdict is this one — a login
        either succeeds or fails here, so an attempt is always worth charging.

        Args:
            key: The identity being limited.

        Returns:
            True if within limit, False if exceeded.
        """
        if not self.allows(key):
            return False

        self.record(key)
        return True
    
    def reset(self, key: str):
        """Reset rate limit for a specific key."""
        if key in self.attempts:
            del self.attempts[key]


# Global rate limiters
login_limiter = RateLimiter(max_attempts=settings.login_rate_limit)
ws_limiter = RateLimiter(max_attempts=settings.ws_rate_limit)
# Counts Terminal connection attempts whatever their outcome, so that the
# refusals ws_limiter deliberately does not charge for still end somewhere.
ws_attempt_limiter = RateLimiter(max_attempts=settings.ws_attempt_limit)

# HTTP Basic Auth security
security = HTTPBasic()

# Session storage
active_sessions: Dict[str, dict] = {}  # session_id -> {username, created_at, last_seen}

# Time source for Session bookkeeping.  Every write to ``created_at`` and
# ``last_seen`` reads it, so a test can drive Session ageing without waiting on
# the wall clock.  Production never reassigns it.
session_clock: Callable[[], float] = time.time

# How a destroyed Session's Terminals are closed.  Terminals live in the
# registry, which this module deliberately does not know about: the web layer
# registers the closer at startup so the dependency runs inward, and Session
# bookkeeping stays testable without a Lab.
TerminalCloser = Callable[[str], Awaitable[Any]]
_terminal_closer: Optional[TerminalCloser] = None


def set_terminal_closer(closer: Optional[TerminalCloser]) -> None:
    """Register how a destroyed Session's Terminals are closed.

    Called once by the web layer at application startup, and by tests that
    stand a fake registry in its place.  Passing None leaves Session records
    the only thing :func:`destroy_session` touches.

    Args:
        closer: Awaitable called with the destroyed Session's token, or None
            to unregister.
    """
    global _terminal_closer
    _terminal_closer = closer


# How many Terminals a Session currently holds.  The Session Cap uses this to
# tell an in-use Session from an idle one; same injection shape as the
# Terminal closer, and the same reason auth must not import the registry.
TerminalCounter = Callable[[str], int]
_terminal_counter: Optional[TerminalCounter] = None


def set_terminal_counter(counter: Optional[TerminalCounter]) -> None:
    """Register how the Session Cap learns a Session's live Terminal count.

    Called once by the web layer at application startup, and by tests that
    stand a fake registry in its place.  Passing None makes every Session
    read as holding no Terminals, so the cap falls back to evicting freely
    the way it did before this seam existed.

    Args:
        counter: Called with a Session's token, returning how many Terminals
            it holds, or None to unregister.
    """
    global _terminal_counter
    _terminal_counter = counter


def _terminal_count(session_id: str) -> int:
    """Return how many Terminals a Session currently holds.

    Args:
        session_id: The Session to check.

    Returns:
        The Session's live Terminal count, or 0 if no counter is registered.
    """
    if _terminal_counter is None:
        return 0
    return _terminal_counter(session_id)


def verify_credentials(credentials: HTTPBasicCredentials) -> str:
    """Verify HTTP Basic Auth credentials with constant-time comparison and a login limit.

    Only a wrong password counts against the login limit, per login name.

    Returns:
        Username if valid

    Raises:
        HTTPException: 429 if rate-limited, 401 if credentials are invalid
    """
    # The login limit counts wrong passwords per login name, and only those:
    # a Group's shared login signs in on many screens at once, and behind a
    # reverse proxy or a classroom NAT every browser has the same address.
    # Asked before the password is checked, so once a login is locked even
    # the right password is refused and a guesser learns nothing from it.
    if not login_limiter.allows(credentials.username):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )
    
    # Look up user in registry; always compare to resist timing attacks
    registry = settings.get_user_registry()
    user_entry = registry.get(credentials.username)
    expected_password = user_entry["password"] if user_entry else "dummy-constant-value-x9z"
    password_valid = secrets.compare_digest(
        str(credentials.password).encode("utf8"),
        str(expected_password).encode("utf8")
    )

    if not user_entry or not password_valid:
        login_limiter.record(credentials.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    return credentials.username


def check_ws_rate_limit(client_id: str) -> bool:
    """Return whether a Session may open another Terminal connection.

    Asks without charging: a connection the server goes on to refuse for its
    own reasons — the Terminal cap, most often — must not spend the budget the
    Learner needs to reconnect the Terminals they are allowed.  The successful
    connection is charged by :func:`record_ws_connection`.

    Args:
        client_id: The Session token opening the connection.

    Returns:
        True if the Session has reconnect budget left.
    """
    return ws_limiter.allows(client_id)


def check_ws_attempt_limit(client_id: str) -> bool:
    """Return whether a Session may make another Terminal connection attempt.

    Charges the attempt as it asks, because unlike :func:`check_ws_rate_limit`
    this limit does not care how the attempt ends.  It is the ceiling that
    bounds a client asking repeatedly for something it will never be granted —
    a node that does not exist, or a Terminal past the cap — which the
    reconnect budget cannot bound, since not charging for those refusals is
    the whole point of it.

    Args:
        client_id: The Session token attempting the connection.

    Returns:
        True if the Session is under its attempt ceiling.
    """
    return ws_attempt_limiter.check(client_id)


def record_ws_connection(client_id: str) -> None:
    """Charge a Session for a Terminal connection that was actually served.

    Args:
        client_id: The Session token that opened the Terminal.
    """
    ws_limiter.record(client_id)


def create_session(username: str, role: str) -> str:
    """Create a new session for authenticated user."""
    session_id = secrets.token_urlsafe(32)
    now = session_clock()
    active_sessions[session_id] = {
        "username": username,
        "role": role,
        "created_at": now,
        "last_seen": now
    }
    session_identity(session_id)
    return session_id


def touch_session(session_id: Optional[str]) -> bool:
    """Refresh a session's last-seen time without otherwise inspecting it.

    A Learner makes no HTTP request after the page loads, so Terminal and
    topology WebSocket traffic is the only sign of life they give.  Those paths
    call this to keep the owning Session current.

    Args:
        session_id: Session identifier, or None when no session cookie was sent.

    Returns:
        True if the session existed and was refreshed, False otherwise.
    """
    if not session_id:
        return False

    session = active_sessions.get(session_id)
    if session is None:
        return False

    session["last_seen"] = session_clock()
    return True


def _is_idle(session: dict, now: float) -> bool:
    """Report whether a Session has gone unseen past the idle timeout.

    The one home for the boundary, so the read path and the sweep can never
    disagree about when a Session dies.  Uses ``>=`` to match
    ``idle_timeout_check`` in the WebSocket handler, the package's other idle
    timeout.

    Args:
        session: The Session record to judge.
        now: The current time, in seconds.

    Returns:
        True if the Session has been unseen for at least the timeout.
    """
    return now - session["last_seen"] >= settings.session_idle_timeout


async def expire_idle_sessions() -> int:
    """Destroy every Session that has gone unseen past the idle timeout.

    A browser that is simply gone never asks about its own Session again, so
    expiring on read alone would leave its record — and its Terminals —
    standing indefinitely.  This is the scan that reaches them.

    Called at login and from the Instructor's Sessions list — the two events
    that care how many Sessions are live — rather than on a timer: a
    classroom's Session store holds tens of entries, and a lazy sweep has no
    background task to start, stop, or leak.

    Returns:
        How many Sessions were destroyed.
    """
    now = session_clock()
    # Snapshot first: destroying a Session mutates the store being scanned.
    expired = [
        session_id
        for session_id, session in active_sessions.items()
        if _is_idle(session, now)
    ]
    for session_id in expired:
        logger.info(f"Session {session_id} expired after {settings.session_idle_timeout}s idle")
        await destroy_session(session_id)
    return len(expired)


def list_sessions() -> list[dict]:
    """Describe every live Session, for an Instructor's view of who is signed in.

    Every public accessor refreshes the Session it reads, so building the list
    through them would keep a room full of abandoned tabs alive simply by
    looking at them.  This walks the store instead and disturbs nothing.  Call
    :func:`expire_idle_sessions` first, so the answer excludes Sessions that
    are already dead.

    Each field is named rather than the record copied wholesale: the result is
    serialized to a browser, so a record that grows a field later must not
    start publishing it by accident.  Nothing here is password material —
    ``active_sessions`` holds none — and this is the one place that decides so.

    ``session_id`` is the Session's cookie value, included because ending one
    Session by hand needs a handle on it.  That it reaches an Instructor's
    browser is deliberate: the caller is already role-checked, and
    no limit here is a security boundary — a Terminal is root on the lab VM, so
    credentials are not secret within a Lab in the first place.

    Returns:
        One description per live Session, oldest first, carrying its id,
        account, role, creation and last-seen times, and how many Terminals it
        holds.
    """
    return [
        {
            "session_id": session_id,
            "username": session["username"],
            "role": session["role"],
            "created_at": session["created_at"],
            "last_seen": session["last_seen"],
            "last_input_at": session.get("last_input_at"),
            "terminals": _terminal_count(session_id),
            **session_identity(session_id),
        }
        for session_id, session in active_sessions.items()
    ]


async def verify_session(session_id: Optional[str]) -> Optional[str]:
    """Return the Session's username, refreshing it, or None if it is dead.

    Awaitable because a Session found to be idle is destroyed on the spot, and
    destroying a Session closes the Terminals it owns.  Doing that from a
    detached task would swallow the close errors :func:`destroy_session` exists
    to log; leaving the Terminals open would make an expired Session the one
    way to keep a shell alive with nobody signed in.

    Args:
        session_id: Session identifier, or None when no session cookie was sent.

    Returns:
        The Session's username, or None when the id is unknown or the Session
        has been idle past ``session_idle_timeout``.
    """
    session = await get_session_data(session_id)
    if session is None:
        return None
    return session["username"]


async def destroy_session(session_id: str) -> bool:
    """Destroy a Session and close every Terminal it owns.

    Closing is awaited rather than scheduled, because a Terminal is torn down
    on the server's event loop and a detached task would swallow its errors.

    Destroying the Session is the contract; reclaiming its Terminals is
    best-effort.  The record is removed first and a failure to close is logged
    rather than raised, so that the caller — logout, eviction at the Session
    Cap, the idle sweep — still completes.  Raising instead would log a Learner
    out server-side while their browser kept a stale cookie and showed a 500,
    and would let one stuck Terminal fail an Instructor's force-logout.

    Args:
        session_id: The Session to destroy.

    Returns:
        True if the Session existed, False if it was already gone.
    """
    if session_id not in active_sessions:
        return False

    del active_sessions[session_id]
    if _terminal_closer is not None:
        try:
            await _terminal_closer(session_id)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error(f"Session {session_id} destroyed but its Terminals did not close: {e}")
    return True


def _session_owner(session_id: str) -> Optional[str]:
    """Return the account a Session belongs to, without disturbing it.

    Every public accessor refreshes ``last_seen``, and one destroys an idle
    Session outright.  Asking whose Session this is must do neither: a browser
    may present a colleague's cookie, and that Session is nobody else's
    business.

    Args:
        session_id: The Session to look up.

    Returns:
        The owning username, or None if no such Session exists.
    """
    session = active_sessions.get(session_id)
    return session["username"] if session is not None else None


async def destroy_previous_session(session_id: Optional[str], username: str) -> bool:
    """Destroy the Session a browser already holds for the account signing in.

    A login always issues a new token, which is the right instinct against
    session fixation — but it also means the cookie the browser sent is about
    to be overwritten.  Without this, a reload mints a second Session and
    orphans the first on the spot: nothing can ever present the old cookie
    again, yet its record and its Terminals stand until the idle sweep reaches
    them, which is the fastest way for a Learner to spend their own allowance
    without opening a second browser.

    A cookie naming *another* account's Session is left completely alone, not
    even refreshed: one machine may carry a colleague's cookie, and their
    Session is no business of this login.  Hence :func:`_session_owner` rather
    than :func:`get_session_data`, which would refresh it as a side effect of
    the lookup.

    Args:
        session_id: The Session cookie the browser presented, or None when it
            sent none.
        username: The account that has just authenticated.

    Returns:
        True if a Session was destroyed, False when the cookie was absent,
        unknown, already expired, or belonged to another account.
    """
    if not session_id:
        return False

    if _session_owner(session_id) != username:
        return False

    logger.info(f"Session {session_id} replaced by a new login for {username}")
    return await destroy_session(session_id)


def _sessions_held_by(username: str, exclude: Optional[str] = None) -> list[str]:
    """Return the ids of every live Session belonging to one account.

    A plain scan of the Session store, deliberately rather than a
    ``username -> sessions`` index: the Session Cap is read from the
    environment at startup and cannot change while the server runs, so an
    index would add bookkeeping to every login and logout to track a number
    that never moves.  A classroom's store holds tens of entries, and only a
    login ever asks.

    Args:
        username: The account to look for.
        exclude: A Session id to leave out even if it belongs to this
            account — the browser's own outgoing Session on a replacing
            login, which is about to be destroyed regardless of the cap.

    Returns:
        The account's Session ids, oldest first.
    """
    return [
        session_id
        for session_id, session in active_sessions.items()
        if session["username"] == username and session_id != exclude
    ]


async def destroy_user_sessions(username: str) -> None:
    """End every live Session of one account, closing the Terminals they own.

    The one step behind resetting or removing a login, so nobody keeps
    working on a password or an account the Instructor has taken away.

    Args:
        username: The account whose Sessions end.
    """
    for session_id in _sessions_held_by(username):
        await destroy_session(session_id)


async def make_room_for_session(username: str, exclude: Optional[str] = None) -> bool:
    """Free a slot under the Session Cap for an account about to sign in.

    ``session_per_user_cap`` of 0 — the default — disables the cap outright:
    no scan, no eviction, no refusal.  That is the decision, not a
    placeholder.  A runtime cannot tell a group account from a personal one,
    so any non-zero default locks out every class sharing one credential.

    "Genuinely active" means holding an open Terminal: that is the browser
    with a Learner actually at the keyboard, as opposed to one merely signed
    in.  At the cap, the least-recently-seen Session *with no open Terminal*
    is evicted, and its Terminals — there are none — closed regardless, in
    case one opened between the count and the destroy.  If every held Session
    has a Terminal open, nothing is evicted and the caller refuses the login.

    Call this before destroying the browser's own outgoing Session on a
    replacing login, passing that Session's id as ``exclude``:
    the arithmetic must already treat it as gone, but the destroy itself must
    wait until it is known the login will not be refused — otherwise a
    refused Learner would lose the Session their browser already held.  Call
    after :func:`expire_idle_sessions`, so a ghost tab never spends the
    account's allowance.

    Args:
        username: The account that has just authenticated.
        exclude: A Session id to treat as already gone — the caller's own
            outgoing Session on a replacing login, or None.

    Returns:
        True if the account may open another Session, False when every held
        Session (after ``exclude``) has an open Terminal and none can be
        evicted — the caller then refuses the login.
    """
    cap = settings.session_per_user_cap
    if cap <= 0:
        return True

    # ``held`` counts the Sessions that will survive this login, so the
    # account may keep at most cap - 1 of them.  Evicting at ``>= cap`` leaves
    # room for exactly one more.
    held = _sessions_held_by(username, exclude=exclude)
    while len(held) >= cap:
        idle = [session_id for session_id in held if _terminal_count(session_id) == 0]
        if not idle:
            # Every Session this account holds is in active use; there is
            # nothing left to evict.
            return False

        # Among Sessions with no open Terminal, ties go to the earliest
        # created: active_sessions preserves insertion order, and min() keeps
        # the first of equal keys.
        victim = min(idle, key=lambda session_id: active_sessions[session_id]["last_seen"])
        logger.info(f"Session {victim} evicted: {username} is at the Session Cap of {cap}")
        if not await destroy_session(victim):
            # The record was already gone by the time it was destroyed;
            # evicting it again would only spin.
            return False
        held = _sessions_held_by(username, exclude=exclude)
    return True


async def get_session_data(session_id: Optional[str]) -> Optional[dict]:
    """Return the full Session record, refreshing it, or None if it is dead.

    "Expired" means unseen for longer than ``session_idle_timeout``.  Such a
    Session is destroyed here rather than reported, so the caller and the store
    agree; see :func:`verify_session` for why this is awaitable.

    Args:
        session_id: Session identifier, or None when no session cookie was sent.

    Returns:
        The Session's record, or None when the id is unknown or the Session has
        been idle past ``session_idle_timeout``.
    """
    if not session_id:
        return None

    session = active_sessions.get(session_id)
    if session is None:
        return None

    now = session_clock()
    if _is_idle(session, now):
        await destroy_session(session_id)
        return None

    session["last_seen"] = now
    return session


async def get_current_user_with_role(session: Optional[str] = Cookie(None)) -> dict:
    """Identify the Session behind a request, or refuse it.

    Reading the Session is also what refreshes it, so every authenticated HTTP
    request keeps its Session alive.

    Args:
        session: The session cookie the browser presented, if any.

    Returns:
        The Session's username and role.

    Raises:
        HTTPException: 401 when no live Session backs the cookie.
    """
    data = await get_session_data(session)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return {"username": data["username"], "role": data["role"]}


def require_role(*allowed_roles: str):
    """Factory returning a FastAPI dependency that enforces role-based access."""
    def checker(user: dict = Depends(get_current_user_with_role)) -> dict:
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user
    return checker


def session_identity(session_id: Optional[str]) -> dict[str, str]:
    """Return the readable identity of a live Session, assigning it once.

    The public code is independent of the cookie and unique among active
    Sessions. Legacy records receive an identity on their first inspection.

    Args:
        session_id: The authenticated Session's private handle.

    Returns:
        Its editable nickname and stable public connection code.

    Raises:
        HTTPException: If the Session no longer exists.
    """
    record = active_sessions.get(session_id or "")
    if record is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if "connection_code" not in record:
        used = {item.get("connection_code") for item in active_sessions.values()}
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        while code in used:
            code = "".join(secrets.choice(alphabet) for _ in range(6))
        record["connection_code"] = code
        adjective = secrets.choice(
            ("Blue", "Amber", "Silver", "Green", "Swift", "Bright")
        )
        animal = secrets.choice(("Fox", "Otter", "Owl", "Finch", "Panda", "Lynx"))
        record["nickname"] = f"{adjective} {animal}"
    return {key: record[key] for key in ("nickname", "connection_code")}


def record_terminal_input(session_id: Optional[str]) -> None:
    """Record accepted Terminal input without retaining its contents.

    Output, keepalives and resize events do not call this function. The
    timestamp belongs to the live Session and survives closing its Terminals.

    Args:
        session_id: The Session whose Terminal accepted non-empty input.
    """
    session = active_sessions.get(session_id or "")
    if session is not None:
        session["last_input_at"] = session_clock()
