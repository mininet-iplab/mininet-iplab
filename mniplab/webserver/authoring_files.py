"""Supporting lesson files shared by Canvas and Python definitions.

Source, Seeds, Guide and Layout retain their existing authoritative fields.
This optional collection stores only additional files, without rewriting older
definitions or decoding binary assets as source text.
"""

import base64
import binascii
import hashlib
import re
from typing import Any, Dict

from mniplab.frrouter.config import FRRConfigBuilder


FILE_LIMIT = 2_000_000
TOTAL_LIMIT = 20_000_000
COUNT_LIMIT = 200
_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. ()+-]{0,99}")
_PRESENTATION_SUFFIXES = {"md", "txt", "png", "jpg", "jpeg", "gif", "webp", "pdf"}


def canonical_path(path: Any) -> str:
    """Require an unambiguous portable relative path, without normalization.

    Args:
        path: The requested supporting file path.

    Returns:
        The unchanged canonical path.

    Raises:
        ValueError: If the path is ambiguous, reserved or outside the lesson.
    """
    if (not isinstance(path, str) or not path or len(path) > 240
            or any(not _COMPONENT.fullmatch(part) or part.endswith((" ", "."))
                   or part.casefold() == "__pycache__" for part in path.split("/"))):
        raise ValueError("Lesson file path must be a canonical relative path of at most 240 ASCII bytes")
    return path


def file_role(path: str, record: Dict[str, Any]) -> str:
    """Resolve a conservative input role, allowing only declared presentation.

    Args:
        path: The canonical file path.
        record: A file record with an optional role.

    Returns:
        Build by default, or explicit presentation in its dedicated namespace.

    Raises:
        ValueError: If a role tries to hide code or configuration from inputs.
    """
    role = record.get("role", "build")
    if role not in ("build", "presentation"):
        raise ValueError("Lesson file role must be build or presentation")
    if role == "presentation" and (
        not path.startswith("presentation/")
        or path.rsplit(".", 1)[-1].lower() not in _PRESENTATION_SUFFIXES
    ):
        raise ValueError("Presentation role requires documentation or media under presentation/; "
                         "code and configuration remain build inputs")
    return role


def claim_path(path: str, files: set[str], directories: Dict[str, str]) -> None:
    """Claim one portable file path, rejecting aliases and prefix collisions.

    Args:
        path: A relative file path to validate and claim.
        files: Case-folded file paths already claimed in this inventory.
        directories: Case-folded directory paths mapped to original spelling.

    Raises:
        ValueError: If the path is noncanonical or collides with the inventory.
    """
    canonical_path(path)
    parts = path.split("/")
    for index in range(1, len(parts)):
        directory = "/".join(parts[:index])
        folded = directory.casefold()
        if folded in files or directories.get(folded, directory) != directory:
            raise ValueError(f"Lesson file path {path!r} collides with another file or directory")
        directories[folded] = directory
    folded = path.casefold()
    if folded in files or folded in directories:
        raise ValueError(f"Lesson file path {path!r} collides with another file or directory")
    files.add(folded)


def decode_file(record: Dict[str, Any]) -> bytes:
    """Decode one bounded UTF-8/base64 file record without changing its bytes.

    Args:
        record: The supporting file's encoding, content and optional role.

    Returns:
        The exact file bytes.

    Raises:
        ValueError: If the record, encoding, content or size is invalid.
    """
    if (not isinstance(record, dict)
            or not {"encoding", "content"} <= set(record) <= {"encoding", "content", "role"}):
        raise ValueError("Lesson file fields must be encoding, content and optional role")
    content = record["content"]
    if not isinstance(content, str):
        raise ValueError("Lesson file content must be text")
    if record["encoding"] == "utf-8":
        if len(content) > FILE_LIMIT:
            raise ValueError("Lesson file exceeds 2,000,000 bytes")
        data = content.encode("utf-8")
    elif record["encoding"] == "base64":
        if len(content) > 4 * ((FILE_LIMIT + 2) // 3):
            raise ValueError("Lesson file exceeds 2,000,000 bytes")
        try:
            data = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Lesson file must contain canonical base64") from exc
        if base64.b64encode(data).decode("ascii") != content:
            raise ValueError("Lesson file must contain canonical base64")
    else:
        raise ValueError("Lesson file encoding must be utf-8 or base64")
    if len(data) > FILE_LIMIT:
        raise ValueError("Lesson file exceeds 2,000,000 bytes")
    return data


def validate_lesson_files(document: Dict[str, Any]) -> None:
    """Validate the optional supporting collection without normalizing it.

    Args:
        document: A Canvas or Python definition.

    Raises:
        ValueError: If the supporting collection is invalid or too large.
    """
    files = document.get("lesson_files", {})
    if not isinstance(files, dict) or len(files) > COUNT_LIMIT:
        raise ValueError("Lesson files must be an object of at most 200 files")
    if not files:
        return
    # Import lazily: Python generation also uses the definition repository.
    from mniplab.webserver.authoring_python import script_slug

    claimed: set[str] = set()
    directories = {path: path for path in (
        "frr-config", "examples", "examples/guides", "examples/layouts",
    )}
    for path in {"lab.py", f"{script_slug(document['catalog']['name'])}.py",
                 "definition.json", "guide.md", "layout.json", "README.md", "iplab-package.json"}:
        claim_path(path, claimed, directories)
    for path in document.get("files", {}):
        claim_path(path, claimed, directories)
    total = 0
    owned_frr = {name.casefold() for name in (
        FRRConfigBuilder.AUTHORED_FILE, *FRRConfigBuilder.OWNED_FILES,
    )}
    for path, record in files.items():
        canonical_path(path)
        parts = path.casefold().split("/")
        if (parts[:2] in (["examples", "guides"], ["examples", "layouts"])
                or (parts[0] == "frr-config" and any(part in owned_frr for part in parts[1:]))):
            raise ValueError(f"Lesson file path {path!r} is owned by the recipe, Guide, Layout or FRR")
        claim_path(path, claimed, directories)
        total += len(decode_file(record))
        file_role(path, record)
        if total > TOTAL_LIMIT:
            raise ValueError("Lesson files exceed 20,000,000 bytes in total")


def build_inputs(document: Dict[str, Any]) -> Dict[str, Any]:
    """Describe supporting build inputs by canonical path, bytes and role.

    Args:
        document: A validated Canvas or Python definition.

    Returns:
        Stable byte digests for effective build files; presentation is omitted.
    """
    return {path: {"role": "build", "sha256": hashlib.sha256(decode_file(record)).hexdigest()}
            for path, record in document.get("lesson_files", {}).items()
            if file_role(path, record) == "build"}
