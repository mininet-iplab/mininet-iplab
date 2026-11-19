"""Lab key allocation, validation, and derivation for mnIPLab.

A Lab key is a short token safe in a filename and an interface name,
allocated when a Lab starts and freed when it stops. In CLI Mode
without a manager the key is derived from the script and process.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from typing import Optional, Set

#: Valid Lab key regex: 1 to 6 lowercase alphanumeric characters.
LAB_KEY_PATTERN = re.compile(r"^[a-z0-9]{1,6}$")


def is_valid_lab_key(key: str) -> bool:
    """Check whether a string is a valid Lab key.

    A valid key is 1 to 6 lowercase alphanumeric characters, making it safe
    in filenames, unix domain socket paths, and Linux network interface names
    (which have a strict 15-character limit).

    Args:
        key: The key candidate string.

    Returns:
        True if the key matches the valid token format, False otherwise.
    """
    if not isinstance(key, str):
        return False
    return bool(LAB_KEY_PATTERN.match(key))


def validate_lab_key(key: str) -> None:
    """Validate a Lab key or raise ValueError.

    Args:
        key: The key candidate string.

    Raises:
        ValueError: If key does not meet character or length constraints.
    """
    if not is_valid_lab_key(key):
        raise ValueError(
            f"Invalid Lab key '{key}'. A Lab key must be 1-6 lowercase "
            "alphanumeric characters (e.g., 'k01', 'a1b2')."
        )


def derive_lab_key(script: Optional[str] = None, pid: Optional[int] = None) -> str:
    """Derive a short deterministic Lab key from script name and process ID.

    Used in CLI Mode to generate a unique key for the process without
    requiring manual naming.

    Args:
        script: Script filename or path. If None, uses sys.argv[0].
        pid: Process ID. If None, uses os.getpid().

    Returns:
        A 4-character lowercase hexadecimal string.
    """
    if script is None:
        script = sys.argv[0] if sys.argv else "mniplab"
    script_base = os.path.basename(script)
    if pid is None:
        pid = os.getpid()

    seed = f"{script_base}:{pid}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:4]


class LabKeyAllocator:
    """Thread-safe allocator tracking active Lab keys in a process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_keys: Set[str] = set()

    def allocate(
        self,
        key: Optional[str] = None,
        script: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> str:
        """Allocate a Lab key.

        If an explicit key is provided, it is validated and registered.
        If no key is provided, one is derived from script and pid. If the
        derived key is already in use in this process, alternate keys are
        generated until an unused one is found.

        Args:
            key: Optional explicit key to request.
            script: Optional script name for derivation if key is omitted.
            pid: Optional pid for derivation if key is omitted.

        Returns:
            The allocated Lab key.

        Raises:
            ValueError: If an explicit key is invalid or already allocated.
        """
        with self._lock:
            if key is not None:
                validate_lab_key(key)
                if key in self._active_keys:
                    raise ValueError(f"Lab key '{key}' is already in use")
                self._active_keys.add(key)
                return key

            base_key = derive_lab_key(script=script, pid=pid)
            candidate = base_key
            counter = 0

            # A derived key stays four characters however many Labs this
            # process has alive: the key sits inside kernel interface names,
            # and a longer key would shrink the Node names a Lab may use.
            while candidate in self._active_keys:
                counter += 1
                seed = f"{base_key}:{counter}".encode("utf-8")
                candidate = hashlib.sha256(seed).hexdigest()[: len(base_key)]

            self._active_keys.add(candidate)
            return candidate

    def release(self, key: str) -> None:
        """Release an allocated Lab key.

        Args:
            key: The key to free.
        """
        with self._lock:
            self._active_keys.discard(key)


#: Global process-level allocator instance.
DEFAULT_ALLOCATOR = LabKeyAllocator()
