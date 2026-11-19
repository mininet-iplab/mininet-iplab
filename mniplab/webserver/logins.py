"""The logins an Instructor added in Server settings.

Under ``serve`` the Instructor adds Learner logins from the page.  They are
kept in ``accounts.json`` beside the Settings Store, apart from it so that
discarding the Limits never locks a class out.  Each is a name and a
generated password, in plain text at mode 0600 like ``.env``: the
Instructor has to read a password back to tell the Learners.  A login
added here is always a Learner, so the role is not stored.

A Lab script run with ``--enable-web`` never opens this file; ``.env``
accounts are never written here.
"""

import json
import re
import secrets
from typing import Dict, Iterable, Optional

from mniplab.logging import get_logger
from .private_file import write_private_json

logger = get_logger(__name__)

#: The login file's name, beside the Settings Store.
LOGINS_FILE = "accounts.json"

#: A login's name: it is also a Group's name and a Config Copy directory.
NAME_RULE = re.compile(r"[a-z][a-z0-9-]{1,31}")

#: Words for generated passwords: short, easy to say across a room, and
#: free of i, l and o, which read like 1 and 0.
WORDS = (
    "amber", "aspen", "badge", "baker", "beach", "berry", "brave", "bread",
    "brush", "cabbage", "camera", "candy", "cedar", "charm", "cherry", "crane",
    "cream", "crest", "dance", "dawn", "desert", "earth", "ember", "feather",
    "fern", "ferry", "garden", "grape", "guava", "harvest", "heart", "hedge",
    "jacket", "jasper", "kayak", "marsh", "merry", "mesa", "nectar", "number",
    "nutmeg", "panda", "paper", "peach", "pepper", "prawn", "quartz", "raven",
    "ruby", "rusty", "saber", "stream", "summer", "sunset", "thunder", "tundra",
    "umber", "water", "wave", "wheat", "zebra", "zephyr", "acre", "badger",
    "banana", "barn", "bench", "butter", "cactus", "canvas", "carpet", "cave",
    "crab", "cyan", "dune", "dust", "fawn", "fudge", "gravy", "hammer",
    "hawk", "magenta", "mars", "mustard", "pecan", "prune", "puma", "quest",
    "ranger", "rhubarb", "saturn", "sketch", "spruce", "starch", "sugar",
    "thatch", "trumpet", "tuba", "venus", "yeast", "zest", "maze", "nugget",
)

#: Digits that read as nothing else: no 0 or 1.
DIGITS = "23456789"


def generate_login_password() -> str:
    """A new password for a page-added login, shaped like ``crane-ruby-4827``.

    Returns:
        Two words and four digits joined by hyphens, drawn with
        cryptographic randomness.
    """
    words = [secrets.choice(WORDS) for _ in range(2)]
    digits = "".join(secrets.choice(DIGITS) for _ in range(4))
    return "-".join([*words, digits])


class LoginRefused(ValueError):
    """An added login refused, with a message the dialog shows."""


class LoginTaken(LoginRefused):
    """An added login refused because its name is already a login."""


class LoginStore:
    """The Learner logins added on the page, by name.

    Attributes:
        path: The ``accounts.json`` file the logins are kept in.
    """

    def __init__(self, path: str) -> None:
        """Open the login file at ``path``; a missing one holds no logins.

        Args:
            path: The login file.
        """
        self.path = path
        self._logins: Dict[str, str] = self._load()

    def passwords(self) -> Dict[str, str]:
        """Each page-added login's password, by name."""
        return dict(self._logins)

    def add(self, name: str, taken: Iterable[str], by: str) -> str:
        """Add a Learner login with a generated password, and keep it.

        Args:
            name: The new login's name.
            taken: Every name already a login, ``.env`` accounts included.
            by: The Instructor adding it, for the log.

        Returns:
            The new login's password.

        Raises:
            LoginTaken: The name is already a login.
            LoginRefused: The name breaks the rule.
            OSError: The login file could not be written; nothing is added.
        """
        if name in set(taken) or name in self._logins:
            raise LoginTaken(f"{name} is already a login.")
        if not NAME_RULE.fullmatch(name):
            raise LoginRefused(
                "A name is 2 to 32 lowercase letters, digits and hyphens, "
                "starting with a letter."
            )
        password = self._new_password(name)
        logger.info(f"Server settings: {by} added the login {name}")
        return password

    def show(self, name: str, by: str) -> Optional[str]:
        """A page-added login's password, read back for the Instructor.

        Args:
            name: The login.
            by: The Instructor reading it, for the log.

        Returns:
            Its password, or None when it was not added here.
        """
        password = self._logins.get(name)
        if password is not None:
            logger.info(f"Server settings: {by} showed the password of {name}")
        return password

    def reset(self, name: str, by: str) -> Optional[str]:
        """Replace a page-added login's password with a new generated one.

        The file is written before the in-memory copy changes, so a failed
        write leaves the old password working.

        Args:
            name: The login.
            by: The Instructor resetting it, for the log.

        Returns:
            The new password, or None when it was not added here.

        Raises:
            OSError: The login file could not be written; nothing changed.
        """
        if name not in self._logins:
            return None
        password = self._new_password(name)
        logger.info(f"Server settings: {by} reset the password of {name}")
        return password

    def remove(self, name: str, by: str) -> bool:
        """Delete a page-added login.

        The file is written before the in-memory copy changes, so a failed
        write leaves the login in place.

        Args:
            name: The login.
            by: The Instructor removing it, for the log.

        Returns:
            Whether there was such a login to remove.

        Raises:
            OSError: The login file could not be written; nothing changed.
        """
        if name not in self._logins:
            return False
        kept = {login: password for login, password in self._logins.items() if login != name}
        write_private_json(self.path, self._document(kept))
        del self._logins[name]
        logger.info(f"Server settings: {by} removed the login {name}")
        return True

    def _new_password(self, name: str) -> str:
        """Give a login a new generated password and keep it.

        The file is written before the in-memory copy changes, so a failed
        write leaves the logins as they were.

        Args:
            name: The login, new or page-added.

        Returns:
            The new password.

        Raises:
            OSError: The login file could not be written; nothing changed.
        """
        password = generate_login_password()
        write_private_json(self.path, self._document({**self._logins, name: password}))
        self._logins[name] = password
        return password

    @staticmethod
    def _document(logins: Dict[str, str]) -> Dict[str, object]:
        """The login file's contents for these logins."""
        return {"version": 1, "logins": logins}

    def _load(self) -> Dict[str, str]:
        """Read the login file; a missing or unreadable one holds none.

        An entry whose name breaks the rule, or whose password is no
        string, is skipped with a warning, so a hand edit cannot stop the
        server.
        """
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning(f"Login file {self.path} unreadable, using none of it: {exc}")
            return {}
        saved = data.get("logins") if isinstance(data, dict) else None
        if not isinstance(saved, dict):
            return {}
        logins: Dict[str, str] = {}
        for name, password in saved.items():
            if not NAME_RULE.fullmatch(str(name)) or not isinstance(password, str) or not password:
                logger.warning(f"Login file {self.path}: ignoring the login {name!r}")
                continue
            logins[str(name)] = password
        return logins
