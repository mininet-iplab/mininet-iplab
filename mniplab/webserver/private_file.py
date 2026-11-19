"""Write a server-owned JSON file whole, readable by its owner only.

The server keeps what the Instructor changes on the page in files of its
own beside ``.env``: the Settings Store, and the logins added there.  Each
is replaced in one step, so a crash or a full disk never leaves it
half-written, and it is created at mode 0600 like ``.env``, since the
logins file holds passwords.
"""

import json
import os
import tempfile
from typing import Any


def write_private_json(path: str, data: Any) -> None:
    """Replace ``path`` with ``data`` as JSON, atomically, at mode 0600.

    The JSON goes to a temporary file in the same directory first and is
    moved over ``path`` only once fully written, so a reader sees the old
    file or the new one and nothing in between.

    Args:
        path: The file to replace.
        data: What to write; anything ``json.dump`` accepts.

    Raises:
        OSError: The file could not be written; ``path`` is left as it was.
        TypeError: ``data`` is not JSON-serialisable; ``path`` is left as it was.
    """
    directory = os.path.dirname(os.path.abspath(path))
    # mkstemp creates the file at 0600, so it is never readable by others.
    fd, temporary = tempfile.mkstemp(
        dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
