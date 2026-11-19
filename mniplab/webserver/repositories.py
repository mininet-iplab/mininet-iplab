"""Filesystem repositories for layouts and guides.

Isolates path sanitisation, atomic I/O, and fallback logic from the
FastAPI HTTP layer so that the API module is pure request/response routing.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from .authoring import AuthoringRepository, DraftNotFoundError, authored_labs_root

# Canonical Authored Layout schema.
LAYOUT_VERSION = 1
# The fixed, resolution-independent virtual canvas positions are stored in. A
# square canvas keeps ``normalize`` idempotent: the limiting span always maps to
# the full drawable width, so re-normalizing scales by exactly 1.
VIRTUAL_SIZE = 1000.0
# Inset kept clear on every side, so the arrangement never touches the edge.
LAYOUT_PADDING = 40.0


def _coord(position: Any, key: str) -> float:
    """Return a required numeric coordinate, refusing anything else loudly.

    Args:
        position: The per-node object expected to carry ``key``.
        key: Coordinate name (``'x'`` or ``'y'``).

    Returns:
        The coordinate as a float.

    Raises:
        ValueError: If *position* is not an object or *key* is missing/non-numeric.
    """
    value = position.get(key) if isinstance(position, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"position missing numeric '{key}'")
    return float(value)


def normalize(positions: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """Project raw node positions into the fixed virtual canvas.

    Fits the input bounding box into the padded virtual canvas with a single
    uniform scale, so relative arrangement (left-of / above relations) and aspect
    ratio are preserved and the result is idempotent when fed back in. Only ``x``
    and ``y`` are read and written; the retired ``fx``/``fy`` keys are dropped.

    Args:
        positions: Mapping of node id -> object carrying numeric ``x`` and ``y``.

    Returns:
        Mapping of node id -> ``{'x', 'y'}`` in virtual-canvas coordinates. An
        empty input yields an empty result.

    Raises:
        ValueError: If any node lacks a numeric ``x`` or ``y``.
    """
    points = [(nid, _coord(pos, 'x'), _coord(pos, 'y'))
              for nid, pos in positions.items()]
    if not points:
        return {}

    xs = [x for _, x, _ in points]
    ys = [y for _, _, y in points]
    min_x, span_x = min(xs), max(xs) - min(xs)
    min_y, span_y = min(ys), max(ys) - min(ys)

    drawable = VIRTUAL_SIZE - 2 * LAYOUT_PADDING
    if span_x > 0 and span_y > 0:
        scale = min(drawable / span_x, drawable / span_y)
    elif span_x > 0:
        scale = drawable / span_x
    elif span_y > 0:
        scale = drawable / span_y
    else:
        scale = 0.0  # A single node or coincident nodes have no span.

    # Centre the scaled bounding box within the drawable area on each axis.
    offset_x = LAYOUT_PADDING + (drawable - span_x * scale) / 2
    offset_y = LAYOUT_PADDING + (drawable - span_y * scale) / 2

    return {
        nid: {
            'x': round(offset_x + (x - min_x) * scale, 2),
            'y': round(offset_y + (y - min_y) * scale, 2),
        }
        for nid, x, y in points
    }


class LayoutRepository:
    """Persistent storage for the canonical Authored Layout JSON."""

    @staticmethod
    def _sanitize_name(example: Optional[str]) -> str:
        safe = (example or '').strip()
        return re.sub(r'[^A-Za-z0-9._-]', '_', safe) or 'unnamed'

    @staticmethod
    def validate_canonical(data: Any) -> None:
        """Refuse anything that is not a canonical layout document.

        Public so other modules that read or write the canonical schema
        (e.g. :mod:`mniplab.layout_converter`) can validate against the same
        rule rather than re-deriving it.

        Args:
            data: Parsed file contents.

        Raises:
            ValueError: If *data* is not ``{version, nodes:{id:{x,y}}}`` with the
                recognised ``LAYOUT_VERSION``.
        """
        if not isinstance(data, dict):
            raise ValueError("layout must be an object")
        if data.get('version') != LAYOUT_VERSION:
            raise ValueError(f"unrecognised layout version: {data.get('version')!r}")
        nodes = data.get('nodes')
        if not isinstance(nodes, dict):
            raise ValueError("layout 'nodes' must be an object")
        for nid, pos in nodes.items():
            _coord(pos, 'x')
            _coord(pos, 'y')

    @staticmethod
    def write_json(path: str, document: Dict[str, Any]) -> None:
        """Serialize *document* as indented UTF-8 JSON to *path*.

        Public so other modules that write the canonical schema (e.g.
        :mod:`mniplab.layout_converter`) share this single writer.

        Args:
            path: Destination file path.
            document: JSON-serializable content to write.
        """
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(document, f, ensure_ascii=False, indent=2)

    @staticmethod
    def save(example: Optional[str], nodes: Dict[str, Dict[str, float]]) -> dict:
        """Atomically write a canonical layout to ``examples/layouts/<name>.json``.

        Wraps *nodes* in the versioned ``{version, nodes}`` envelope. Falls back
        to ``/tmp/mniplab-<name>-layout.json`` on ``OSError``.

        Args:
            example: Lab identifier (sanitised before use).
            nodes: Canonical mapping of node id -> ``{'x', 'y'}``.

        Returns:
            Dict with ``saved``, ``file``, and ``fallback`` keys.
        """
        safe = LayoutRepository._sanitize_name(example)
        document = {'version': LAYOUT_VERSION, 'nodes': nodes}
        layouts_path = os.path.abspath(os.path.join(os.getcwd(), 'examples', 'layouts'))
        filename = os.path.join(layouts_path, f"{safe}.json")

        tmp = filename + '.tmp'
        try:
            os.makedirs(layouts_path, exist_ok=True)
            LayoutRepository.write_json(tmp, document)
            os.replace(tmp, filename)
            return {'saved': True, 'file': f"layouts/{safe}.json", 'fallback': False}
        except OSError:
            fallback_dir = '/tmp'
            os.makedirs(fallback_dir, exist_ok=True)
            filename2 = os.path.join(fallback_dir, f"mniplab-{safe}-layout.json")
            LayoutRepository.write_json(filename2, document)
            return {'saved': True, 'file': f"layouts/{safe}.json", 'fallback': True}

    @staticmethod
    def load(example: Optional[str]) -> Optional[Dict[str, Any]]:
        """Load a lab's canonical Authored Layout.

        Args:
            example: Lab identifier (sanitised before use).

        Returns:
            The canonical ``{version, nodes}`` document, or ``None`` when the lab
            has no Authored Layout file.

        Raises:
            ValueError: If a file exists but its shape or ``version`` is not the
                recognised canonical schema (a format mistake fails loud rather
                than being silently misread).
        """
        safe = LayoutRepository._sanitize_name(example)
        layouts_path = os.path.abspath(os.path.join(os.getcwd(), 'examples', 'layouts'))
        filename = os.path.join(layouts_path, f"{safe}.json")
        if not os.path.exists(filename):
            fallback = os.path.join('/tmp', f"mniplab-{safe}-layout.json")
            if not os.path.exists(fallback):
                return None
            filename = fallback

        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)

        LayoutRepository.validate_canonical(data)
        return data


class GuideRepository:
    """Persistent storage for lab guide markdown."""

    @staticmethod
    def exists(example: Optional[str], examples_dir: str) -> bool:
        """Whether a Lab Example ships a guide.

        Args:
            example: Lab identifier (sanitised before use).
            examples_dir: Absolute path to the examples directory.

        Returns:
            Whether the guide file is there and readable as a file. A name
            that escapes *examples_dir* is not, rather than raising: a
            caller asking "is there one?" wants an answer, not an error.
        """
        try:
            safe = LayoutRepository._sanitize_name(example)
        except ValueError:
            return False
        guide_path = os.path.realpath(
            os.path.join(examples_dir, 'guides', f"{safe}.md")
        )
        examples_root = os.path.realpath(examples_dir)
        if not guide_path.startswith(examples_root + os.sep):
            return False
        return os.path.isfile(guide_path)

    @staticmethod
    def load(example: Optional[str], examples_dir: str) -> str:
        """Read a lab guide markdown file.

        Args:
            example: Lab identifier (sanitised before use).
            examples_dir: Absolute path to the examples directory.

        Returns:
            Markdown content as a string.

        Raises:
            ValueError: If the sanitised path escapes *examples_dir*.
            FileNotFoundError: If the guide file does not exist.
            OSError: If reading fails.
        """
        safe = LayoutRepository._sanitize_name(example)
        guide_path = os.path.realpath(
            os.path.join(examples_dir, 'guides', f"{safe}.md")
        )
        examples_root = os.path.realpath(examples_dir)

        if not guide_path.startswith(examples_root + os.sep):
            raise ValueError("Invalid guide path")

        if not os.path.isfile(guide_path):
            raise FileNotFoundError("Guide not found")

        with open(guide_path, 'r', encoding='utf-8') as f:
            return f.read()

    @staticmethod
    def available(
        example: Optional[str],
        examples_dir: Optional[str] = None,
        builder_file: Optional[str] = None,
    ) -> bool:
        """Whether a Lab has nonempty Guide content worth requesting.

        Args:
            example: Shipped, authored, or Preview Lab Example identifier.
            examples_dir: Shipped examples directory, defaulting to the server's.
            builder_file: Running authored Lab's immutable builder, when bound.

        Returns:
            False for an absent or blank Guide. Unreadable content returns True
            so loading it can report the actual error rather than hide it.
        """
        if not example:
            return False
        try:
            if example.startswith("preview-"):
                document = AuthoringRepository(authored_labs_root()).read(
                    example.removeprefix("preview-")
                )
                return bool(document["guide"].strip())
            if example.startswith("authored-"):
                if builder_file:
                    path = os.path.realpath(
                        os.path.join(os.path.dirname(builder_file), "guide.md")
                    )
                    root = os.path.realpath(
                        os.path.join(authored_labs_root(), "published")
                    )
                    if not path.startswith(root + os.sep):
                        return False
                else:
                    path = AuthoringRepository(authored_labs_root()).published_file(
                        example, "guide.md"
                    )
                with open(path, "r", encoding="utf-8") as source:
                    return any(line.strip() for line in source)
            return bool(
                GuideRepository.load(
                    example, examples_dir or resolve_examples_dir()
                ).strip()
            )
        except (FileNotFoundError, DraftNotFoundError):
            return False
        except (OSError, ValueError):
            return True


def resolve_examples_dir() -> str:
    """Return the absolute path to the examples/ directory.

    Tries the current working directory first, then falls back to the path
    relative to this package's root.
    """
    cwd_path = os.path.abspath(os.path.join(os.getcwd(), 'examples'))
    if os.path.isdir(cwd_path):
        return cwd_path
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'examples'
    ))


def load_labs_manifest() -> List[Dict[str, Any]]:
    """Load shipped and published authored Lab Examples."""
    manifest_path = os.path.join(resolve_examples_dir(), 'labs.json')
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        examples = data if isinstance(data, list) else []
    except (OSError, ValueError):
        examples = []
    try:
        authored = AuthoringRepository(authored_labs_root()).list_published()
    except ValueError:
        authored = []
    shipped_ids = {example.get("id") for example in examples}
    return examples + [example for example in authored if example.get("id") not in shipped_ids]


def resolve_lab_name(example_id: str) -> str:
    """Return a catalog or Preview name, falling back to its ID if unavailable.

    Args:
        example_id: The catalog ID or Preview's draft-prefixed ID.

    Returns:
        The Lab's display name.
    """
    if example_id.startswith("preview-"):
        try:
            draft = AuthoringRepository(authored_labs_root()).read(
                example_id.removeprefix("preview-")
            )
            return draft["catalog"]["name"] or example_id
        except (DraftNotFoundError, ValueError):
            return example_id
    return next(
        (item.get("name") or example_id for item in load_labs_manifest()
         if item["id"] == example_id),
        example_id,
    )
