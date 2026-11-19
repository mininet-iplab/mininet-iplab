"""Groups and their Assignments — who may start which Lab Example.

A Group is a login from the users setting; the Instructor's login is a
Group too, and the single-user fallback is one Group.  An Assignment is
the Lab Examples a Group may start.  The Instructor edits Assignments in
the UI and they are saved as a small JSON file beside the server's
settings, so a class's setup survives a restart.  Changing an Assignment
never touches a running Lab: this module knows nothing about Labs.
"""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, List, Optional

from mniplab.logging import get_logger
from .config import settings
from .repositories import load_labs_manifest

logger = get_logger(__name__)

# Saved beside the server's settings: `.env` is read from the working
# directory, and so is this.
ASSIGNMENTS_FILE = "assignments.json"


def catalog_ids() -> List[str]:
    """The Lab Example ids in the catalog, in catalog order."""
    return [entry["id"] for entry in load_labs_manifest()]


class Groups:
    """The Groups that can log in, and the Assignment of each.

    Attributes:
        path: The JSON file the Assignments are saved in.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        catalog: Callable[[], List[str]] = catalog_ids,
    ) -> None:
        """Load the Assignments saved at ``path``.

        Args:
            path: The Assignments file; ``assignments.json`` in the working
                directory when omitted.
            catalog: Returns the Lab Example ids an Assignment may name.
        """
        self.path = path or os.path.join(os.getcwd(), ASSIGNMENTS_FILE)
        self._catalog = catalog
        self._assignments: Dict[str, List[str]] = self._load()

    def _load(self) -> Dict[str, List[str]]:
        """Read the saved Assignments; a missing or unreadable file is none."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning(f"Assignments file {self.path} unreadable, starting with none: {exc}")
            return {}
        saved = data.get("assignments") if isinstance(data, dict) else None
        if not isinstance(saved, dict):
            return {}
        return {
            str(group): [str(e) for e in ids]
            for group, ids in saved.items() if isinstance(ids, list)
        }

    def _save(self) -> None:
        """Write the Assignments, so they are there after a restart."""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "assignments": self._assignments}, f, indent=2, sort_keys=True)
            f.write("\n")

    def list_groups(self) -> List[Dict[str, str]]:
        """Every Group that can log in, with its role.

        Returns:
            One ``{"name", "role"}`` per login in the users setting.
        """
        return [
            {"name": name, "role": account["role"]}
            for name, account in settings.get_user_registry().items()
        ]

    def role_of(self, group: str) -> Optional[str]:
        """The role of a Group, or None when no such login exists."""
        account = settings.get_user_registry().get(group)
        return account["role"] if account else None

    def assignment(self, group: str) -> List[str]:
        """The Lab Examples a Group may start.

        The Instructor's Group, the one ``.env`` login of the single-user
        fallback among them, is assigned the whole catalog; any other
        Group, one added in Server settings included, has what the
        Instructor gave it, in catalog order.

        Args:
            group: The Group's name.

        Returns:
            Lab Example ids.
        """
        catalog = self._catalog()
        if self.role_of(group) == "instructor":
            return list(catalog)
        assigned = set(self._assignments.get(group, ()))
        return [example_id for example_id in catalog if example_id in assigned]

    def assignments(self) -> Dict[str, List[str]]:
        """Every Group's Assignment, keyed by Group name."""
        return {entry["name"]: self.assignment(entry["name"]) for entry in self.list_groups()}

    def assign(self, group: str, example_ids: List[str]) -> List[str]:
        """Set a Group's Assignment and save it.

        Args:
            group: The Group's name.
            example_ids: The Lab Examples it may start.

        Returns:
            The Assignment as saved, in catalog order.

        Raises:
            KeyError: No such Group can log in.
            ValueError: An id names no Lab Example in the catalog.
        """
        if self.role_of(group) is None:
            raise KeyError(group)
        catalog = self._catalog()
        unknown = [example_id for example_id in example_ids if example_id not in catalog]
        if unknown:
            raise ValueError(f"Unknown Lab Example: {', '.join(unknown)}")
        wanted = set(example_ids)
        self._assignments[group] = [example_id for example_id in catalog if example_id in wanted]
        self._save()
        return self.assignment(group)

    def unassign(self, group: str) -> None:
        """Drop a Group's Assignment and save, as when its login is removed.

        Args:
            group: The Group's name; nothing happens when it has none.
        """
        if self._assignments.pop(group, None) is not None:
            self._save()

    def may_start(self, group: str, example_id: str) -> bool:
        """Whether a Group's Assignment includes a Lab Example."""
        return example_id in self.assignment(group)
