"""Persistent Config under the manager: the seed and each Group's copy.

A Lab Example declares a Router's Persistent Config with ``frr_dir``; that
directory is the **seed**, the Instructor's versioned copy, and in CLI Mode
it is used directly — that is how the Instructor authors it.  Under the
manager every Group gets its own copy, keyed by Group and Lab Example: the
Group's first start of a Lab Example copies each Router's seed into it,
later starts resume the copy, and ``write memory`` lands in the copy only.
Two Groups never share a file.

The manager side is :class:`ConfigCopies`, which knows where every copy
lives without running a Lab Example, so it can say whether a Group's next
start resumes or seeds and can reset a copy while its Lab is stopped.  The
Lab side is :func:`seed_router_copy`, run by ``addRouter`` in the Lab
process, which learns its Group's copy directory from the environment
(``MNIPLAB_CONFIG_COPY``) exactly as it learns its key.
"""

from __future__ import annotations

import os
import json
import shutil
import tempfile
from typing import Any, Dict, List, Optional

#: The environment variable through which the manager hands a Lab process
#: the directory its Group's copies of Persistent Config live in.
CONFIG_COPY_ENV = 'MNIPLAB_CONFIG_COPY'

#: Where the copies live, beside ``frr-config/`` (the seeds) and
#: ``assignments.json`` in the working directory the server runs from.
GROUP_CONFIG_DIR = 'group-config'
STRUCTURE_FILE = '.structure.json'


def read_copy_structure(copy_dir: str) -> Optional[Dict[str, Any]]:
    """Read the structure recorded when this Config Copy first started.

    Args:
        copy_dir: Directory for one Group's copy of a Lab Example.

    Returns:
        The recorded structure, or None when the record is absent or invalid.
    """
    try:
        with open(os.path.join(copy_dir, STRUCTURE_FILE), encoding='utf-8') as source:
            structure: Dict[str, Any] = json.load(source)
        return structure if isinstance(structure, dict) else None
    except (OSError, ValueError):
        return None


def record_copy_structure(copy_dir: str, structure: Dict[str, Any]) -> None:
    """Atomically record the structure that seeded a Group's Config Copy.

    Args:
        copy_dir: Directory for one Group's copy of a Lab Example.
        structure: The published Router structure used to seed the copy.
    """
    os.makedirs(copy_dir, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.structure-', dir=copy_dir)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            json.dump(structure, target, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, os.path.join(copy_dir, STRUCTURE_FILE))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def seed_router_copy(copy_root: str, seed_dir: str, name: str) -> str:
    """The Group's copy of one Router's Persistent Config, seeded on first use.

    Args:
        copy_root: The Group's copy directory for this Lab Example.
        seed_dir: The Router's seed — its ``frr_dir`` with the name filled in.
        name: The Router's name.

    Returns:
        The Router's directory inside the copy.  It holds the seed's files
        the first time; whatever the Group has written since, after.  A seed
        that does not exist leaves the copy to be rendered from templates,
        as a missing ``frr_dir`` is in CLI Mode.
    """
    copy_dir = os.path.join(copy_root, name)
    if not os.path.isdir(copy_dir) and os.path.isdir(seed_dir):
        shutil.copytree(seed_dir, copy_dir)
    return copy_dir


def discard_copy(copy_dir: str) -> bool:
    """Remove a Group's copy of one Lab Example's Persistent Config.

    Args:
        copy_dir: The copy directory.

    Returns:
        Whether there was a copy to remove.
    """
    if not os.path.isdir(copy_dir):
        return False
    shutil.rmtree(copy_dir)
    return True


class ConfigCopies:
    """Every Group's copies of Persistent Config, keyed by Group and Lab Example.

    Attributes:
        root: The directory the copies live under.
    """

    def __init__(self, root: Optional[str] = None) -> None:
        """Keep the copies under ``root``.

        Args:
            root: The copies' directory; ``group-config`` in the working
                directory when omitted.
        """
        self.root = root or os.path.join(os.getcwd(), GROUP_CONFIG_DIR)

    @staticmethod
    def _segment(name: str, what: str) -> str:
        """A Group or Lab Example name as one path segment, or refuse it."""
        if not name or os.sep in name or name in ('.', '..'):
            raise ValueError(f"{what} '{name}' cannot name a directory")
        return name

    def copy_dir(self, group: str, example: str) -> str:
        """Where a Group's copy of a Lab Example's Persistent Config lives.

        Args:
            group: The Group's name.
            example: The Lab Example id.

        Returns:
            The directory, whether or not it exists yet.

        Raises:
            ValueError: A name that cannot be a directory name.
        """
        return os.path.join(
            self.root, self._segment(group, 'Group'), self._segment(example, 'Lab Example')
        )

    def exists(self, group: str, example: str) -> bool:
        """Whether a Group has a copy of a Lab Example's Persistent Config.

        A copy exists once a Router of that Lab Example has been seeded for
        the Group; an Ephemeral Config Lab Example never makes one.
        """
        return os.path.isdir(self.copy_dir(group, example))

    def compatible(self, group: str, example: str, structure: Dict[str, Any]) -> bool:
        """Whether the Group can resume its Config Copy under this structure.

        Args:
            group: The Group's name.
            example: The Lab Example id.
            structure: The published Router structure requested for a start.

        Returns:
            True for a missing copy or an exact recorded structure match.
        """
        directory = self.copy_dir(group, example)
        return not os.path.isdir(directory) or read_copy_structure(directory) == structure

    def examples_of(self, group: str) -> List[str]:
        """The Lab Examples a Group holds a copy of, sorted."""
        try:
            entries = os.listdir(os.path.join(self.root, self._segment(group, 'Group')))
        except (OSError, ValueError):
            return []
        return sorted(e for e in entries if self.exists(group, e))

    def groups_of(self, example: str) -> List[str]:
        """The Groups holding a copy of a Lab Example, sorted."""
        try:
            entries = os.listdir(self.root)
        except OSError:
            return []
        return sorted(g for g in entries if self.exists(g, example))

    def reset(self, group: str, example: str) -> bool:
        """Discard a Group's copy, so its next start seeds again.

        Args:
            group: The Group's name.
            example: The Lab Example id.

        Returns:
            Whether there was a copy to discard.
        """
        return discard_copy(self.copy_dir(group, example))
