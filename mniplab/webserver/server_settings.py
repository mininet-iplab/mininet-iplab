"""The Settings Store: the Limits an Instructor changed in Server settings.

Under ``serve`` the Instructor tunes six Limits from the page while the
class runs.  What they changed is kept in ``settings.json`` beside the
Assignments file and ``.env``, holding only the Limits overridden, so the
server comes back up with them.  Each Limit resolves in this order: a value
in the server's environment, then the Settings Store, then ``.env``, then
the default — whoever starts the server can still pin a Limit from outside.

A Lab script run with ``--enable-web`` never opens a store: it reads no
``settings.json`` and offers no Server settings.
"""

import json
import os
import warnings
from dataclasses import dataclass
from typing import Any, Dict, List, Set

from pydantic import ValidationError

from mniplab.logging import get_logger
from mniplab.runner import set_by_env_file
from .config import (
    PING_INTERVAL_REFUSAL,
    RECONNECT_BUDGET_REFUSAL,
    RELOADS_PER_MINUTE,
    Settings,
)
from .private_file import write_private_json

logger = get_logger(__name__)

#: The Settings Store's file name, beside the Assignments file.
SETTINGS_FILE = "settings.json"


@dataclass(frozen=True)
class Limit:
    """One number the Instructor may change in Server settings.

    Attributes:
        name: The setting it overrides.
        label: What the dialog calls it, used in refusals.
        minimum: The smallest value a save accepts.
        maximum: The largest value a save accepts.
        multi_only: Shown and accepted in Multi-Lab Mode only.
    """

    name: str
    label: str
    minimum: int
    maximum: int
    multi_only: bool = False


#: The largest server-wide Terminal cap a save accepts; the Terminal output
#: pool is sized for it, so the cap can be raised while the server runs.
MOST_TERMINALS_ON_THE_SERVER = 1000

#: The six Limits, in the dialog's order.  Timeouts are in seconds and at
#: least a minute, so a typo cannot end every Terminal or Session at once.
LIMITS = (
    Limit("terminal_per_session_cap", "Terminals per Session", 1, 100),
    Limit("terminal_global_cap", "Terminals on the whole server", 1, MOST_TERMINALS_ON_THE_SERVER),
    Limit("terminal_idle_timeout", "Idle Terminal timeout", 60, 86400),
    Limit("session_idle_timeout", "Idle Session timeout", 60, 604800),
    # 0 is the setting's own "no cap", its default, so it stays reachable.
    Limit("session_per_user_cap", "Sessions per login", 0, 100),
    Limit("lab_cap", "Labs alive at once", 1, 32, multi_only=True),
)


def _pinned_by_environment(name: str) -> bool:
    """Whether a setting's variable is in the server process's environment.

    Any spelling the setting is read under counts, older ones included.
    Upper-cased on both sides, as the settings model reads names.  A value
    ``serve`` copied in from ``.env`` is not a shell export and pins nothing.

    Args:
        name: The setting's name.

    Returns:
        True when a shell export sets it, so the store must not.
    """
    current = f"MNIPLAB_{name}".upper()
    names = {current, *Settings.renamed_env_names().get(current, ())}
    return any(key.upper() in names and not set_by_env_file(key) for key in os.environ)


class LimitRefused(ValueError):
    """A save refused, naming the field the dialog shows.

    Attributes:
        field: The Limit whose value was refused.
    """

    def __init__(self, field: str, message: str) -> None:
        """Refuse a save over one field.

        Args:
            field: The Limit whose value was refused.
            message: Why, in the dialog's words.
        """
        super().__init__(message)
        self.field = field


class SettingsStore:
    """The Limits changed from the page, and the live settings they apply to.

    Attributes:
        path: The ``settings.json`` file the store is kept in.
        live: The server's settings object, changed in place on a save.
    """

    def __init__(self, path: str, live: Settings) -> None:
        """Open the store at ``path`` over the server's live settings.

        Args:
            path: The Settings Store file.
            live: The settings object every consumer reads.
        """
        self.path = path
        self.live = live
        # What each Limit reads without the store: environment, .env, default.
        self.started_with = {limit.name: getattr(live, limit.name) for limit in LIMITS}

    def limits(self) -> List[Limit]:
        """The Limits offered in the server's Lab mode."""
        multi = self.live.lab_mode == "multi"
        return [limit for limit in LIMITS if multi or not limit.multi_only]

    def describe(self) -> Dict[str, Any]:
        """Each offered Limit with its current value and bounds, for the dialog."""
        return {
            "limits": [
                {
                    "name": limit.name,
                    "value": getattr(self.live, limit.name),
                    "started_with": self.started_with[limit.name],
                    "pinned": _pinned_by_environment(limit.name),
                    "min": limit.minimum,
                    "max": limit.maximum,
                }
                for limit in self.limits()
            ]
        }

    def load(self) -> None:
        """Apply the stored Limits to the live settings, as ``serve`` starts.

        A Limit set in the server's environment keeps that value: the
        environment wins over the store.  A stored value out of its bounds
        is ignored, and a set the validators reject is ignored whole, each
        with a warning, so a hand-edited file cannot stop the server.
        """
        try:
            stored = self._read()
        except (OSError, ValueError) as exc:
            logger.warning(f"Settings Store {self.path} unreadable, using none of it: {exc}")
            return
        wanted: Dict[str, int] = {}
        for limit in LIMITS:
            value = stored.get(limit.name)
            if value is None or _pinned_by_environment(limit.name):
                continue
            if isinstance(value, bool) or not isinstance(value, int) \
                    or not limit.minimum <= value <= limit.maximum:
                logger.warning(f"Settings Store {self.path}: ignoring {limit.name}={value!r}")
                continue
            wanted[limit.name] = value
        try:
            self._validate(wanted)
        except LimitRefused as exc:
            logger.warning(f"Settings Store {self.path} not applied: {exc}")
            return
        for name, value in wanted.items():
            setattr(self.live, name, value)

    def save(self, changes: Dict[str, Any], by: str) -> None:
        """Change some Limits, all or none, and apply them at once.

        A value equal to the Limit's current one changes nothing and is
        not stored, so the store holds only what the Instructor overrode.
        ``None`` clears a Limit: its key leaves the store and it reads as
        the value the server was started with.  A Limit the server's
        environment sets cannot be changed or cleared.

        Args:
            changes: The new value of each Limit to change, by name.
            by: The Instructor saving, for the log.

        Raises:
            LimitRefused: A value is refused; nothing is stored or applied.
            OSError: The store could not be written; nothing is applied.
        """
        offered = {limit.name: limit for limit in self.limits()}
        stored = self._read()
        changed: Dict[str, int] = {}
        cleared: Set[str] = set()
        for name, value in changes.items():
            limit = offered.get(name)
            if limit is None:
                raise LimitRefused(name, f"{name} cannot be changed here.")
            if _pinned_by_environment(name) and value != getattr(self.live, name):
                raise LimitRefused(name, f"{limit.label} is set where the server was started.")
            if value is None:
                if name in stored or getattr(self.live, name) != self.started_with[name]:
                    changed[name] = self.started_with[name]
                    cleared.add(name)
                continue
            # bool is an int to Python, but true is no number of Terminals.
            if not isinstance(value, int) or isinstance(value, bool):
                raise LimitRefused(name, f"{limit.label} must be a whole number.")
            if not limit.minimum <= value <= limit.maximum:
                raise LimitRefused(
                    name,
                    f"{limit.label} must be between {limit.minimum} and {limit.maximum}.",
                )
            if value != getattr(self.live, name):
                changed[name] = value
        if not changed:
            return
        self._validate(changed)
        stored = {k: v for k, v in {**stored, **changed}.items() if k not in cleared}
        write_private_json(self.path, stored)
        for name, value in changed.items():
            old = getattr(self.live, name)
            setattr(self.live, name, value)
            logger.info(f"Server settings: {by} changed {name} from {old} to {value}")

    def _validate(self, changed: Dict[str, int]) -> None:
        """Run the settings' own cross-field validators on the would-be settings.

        Each change is added to the candidate in the dialog's order and the
        candidate validated after each, so the refusal names the first
        field whose value the validators reject.

        Args:
            changed: The Limits to change, by name.

        Raises:
            LimitRefused: The validators reject the settings with a change.
        """
        candidate = self.live.model_dump()
        for limit in LIMITS:
            if limit.name not in changed:
                continue
            candidate[limit.name] = changed[limit.name]
            try:
                with warnings.catch_warnings():
                    # The deprecated-name warning fired once, at start.
                    warnings.simplefilter("ignore", DeprecationWarning)
                    Settings.model_validate(candidate)
            except ValidationError as exc:
                error = exc.errors()[0]
                reason = str(error.get("ctx", {}).get("error", error["msg"]))
                raise LimitRefused(
                    limit.name, self._plain_reason(limit, reason, candidate)
                ) from exc

    @staticmethod
    def _plain_reason(limit: Limit, reason: str, candidate: Dict[str, Any]) -> str:
        """Put a validator's refusal in terms of the field the dialog shows.

        Args:
            limit: The Limit whose value the validators rejected.
            reason: The validator's own text, which may name hidden settings.
            candidate: The settings the validators were run on.

        Returns:
            A sentence naming only the Limit's label.
        """
        if reason.startswith(PING_INTERVAL_REFUSAL):
            return (
                f"{limit.label} must be at least {candidate['terminal_ping_interval'] + 1} "
                "seconds, so Terminals stay connected."
            )
        if reason.startswith(RECONNECT_BUDGET_REFUSAL):
            most = candidate["ws_rate_limit"] // RELOADS_PER_MINUTE
            return (
                f"{limit.label} must be at most {most}, so reloading the page "
                "does not lock a Learner out."
            )
        if any(name in reason for name in ("ws_", "terminal_ping", "activity_idle", "lab_builder")):
            return f"{limit.label} does not fit the server's other settings."
        return f"{limit.label}: {reason}"

    def _read(self) -> Dict[str, Any]:
        """The Limits the store file holds; a missing file holds none."""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        return data if isinstance(data, dict) else {}
