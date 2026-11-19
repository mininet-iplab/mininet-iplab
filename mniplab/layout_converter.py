"""One-off converter from the legacy layout shape to the canonical schema.

Rewrites ``examples/layouts/*.json`` files from the retired absolute-pixel
``{id: {fx, fy, x, y}}`` shape into the canonical Authored Layout envelope
``{version, nodes: {id: {x, y}}}`` (see :mod:`mniplab.webserver.repositories`).
Already-canonical files are accepted too — the raw positions are extracted
and re-projected through :func:`~mniplab.webserver.repositories.normalize`,
which is idempotent, so re-running the converter on converted files is a
no-op. A file matching neither shape is refused loudly rather than guessed
at.

Usage:
    python -m mniplab.layout_converter [examples_dir]

``examples_dir`` defaults to ``./examples`` relative to the current working
directory.
"""

import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional

from mniplab.webserver.repositories import LAYOUT_VERSION, LayoutRepository, normalize

DEFAULT_EXAMPLES_DIR = 'examples'


def convert_layout_document(raw: Any) -> Dict[str, Any]:
    """Convert a legacy- or canonical-shaped layout document to canonical form.

    Accepts either the retired ``{id: {fx, fy, x, y}}`` shape or an
    already-canonical ``{version, nodes: {id: {x, y}}}`` envelope, extracts
    the raw ``x``/``y`` positions, and re-projects them through
    :func:`normalize`. Because ``normalize`` is idempotent, converting an
    already-canonical document reproduces its positions exactly.

    Args:
        raw: Parsed JSON content of a layout file.

    Returns:
        Canonical ``{'version': LAYOUT_VERSION, 'nodes': {id: {x, y}}}``.

    Raises:
        ValueError: If *raw* is not an object, or its shape matches neither
            the legacy nor the canonical schema.
    """
    if not isinstance(raw, dict):
        raise ValueError("layout document must be a JSON object")

    if 'version' in raw or 'nodes' in raw:
        # Looks like an attempt at the canonical envelope: validate strictly
        # rather than guessing at a partial or malformed match.
        LayoutRepository.validate_canonical(raw)
        return {'version': LAYOUT_VERSION, 'nodes': normalize(raw['nodes'])}

    if not raw:
        return {'version': LAYOUT_VERSION, 'nodes': {}}

    for node_id, position in raw.items():
        if not isinstance(position, dict):
            raise ValueError(
                f"unrecognised layout shape: node {node_id!r} is not an object"
            )

    return {'version': LAYOUT_VERSION, 'nodes': normalize(raw)}


def convert_examples_dir(examples_dir: str = DEFAULT_EXAMPLES_DIR) -> List[str]:
    """Convert every ``layouts/*.json`` file under *examples_dir* in place.

    Args:
        examples_dir: Examples directory; its ``layouts/`` subdirectory is scanned.

    Returns:
        Sorted list of the basenames converted.

    Raises:
        ValueError: If any file's shape is not recognised (propagated from
            :func:`convert_layout_document`). Files converted earlier in the
            sorted order are already written to disk; already-canonical
            files are unaffected by re-running the converter after fixing
            the offending file.
    """
    pattern = os.path.join(examples_dir, 'layouts', '*.json')
    paths = sorted(glob.glob(pattern))
    converted = []
    for path in paths:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        try:
            canonical = convert_layout_document(raw)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        LayoutRepository.write_json(path, canonical)
        converted.append(os.path.basename(path))
    return converted


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point: convert every layout file in the given directory.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``). At most
            one positional argument: the examples directory.

    Returns:
        Process exit code: ``0`` on success, ``1`` on a recognised-shape
        failure.
    """
    args = sys.argv[1:] if argv is None else argv
    examples_dir = args[0] if args else DEFAULT_EXAMPLES_DIR
    try:
        converted = convert_examples_dir(examples_dir)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"converted {len(converted)} layout file(s) in {examples_dir}")
    for name in converted:
        print(f"  {name}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
