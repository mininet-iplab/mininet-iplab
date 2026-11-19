"""Copy allowlisted shipped recipes without interpreting their Python source."""

import base64
import json
from pathlib import Path
from typing import Any, Dict

from mniplab.webserver.authoring import AuthoringRepository, DraftNotFoundError
from mniplab.webserver.authoring_files import canonical_path


def copy_shipped_recipe(repository: AuthoringRepository, example_id: str,
                        examples_dir: str) -> Dict[str, Any]:
    """Create an independent Python draft from source and declared assets.

    Args:
        repository: The shared draft store.
        example_id: An allowlisted shipped Catalog identity.
        examples_dir: The shipped examples directory beside project assets.

    Returns:
        A new unpublished Python draft retaining the recipe's paths and bytes.

    Raises:
        DraftNotFoundError: If the shipped Catalog identity does not exist.
        ValueError: If a declared file is missing, unsafe or invalid.
    """
    examples = Path(examples_dir).resolve()
    root = examples.parent
    with (examples / 'labs.json').open(encoding='utf-8') as catalog_source:
        row = next((item for item in json.load(catalog_source) if item['id'] == example_id), None)
    if row is None:
        raise DraftNotFoundError(example_id)

    def read(relative: str) -> bytes:
        canonical_path(relative)
        path = root / relative
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError(f"Cannot Copy {example_id}: declared file {relative!r} is missing "
                             "or outside the shipped project; restore the shipped asset")
        return path.read_bytes()

    source = read(f"examples/{row['file']}").decode('utf-8')
    seeds: Dict[str, str] = {}
    supporting: Dict[str, Any] = {}
    for relative in row['assets']:
        data = read(relative)
        if relative.startswith('frr-config/') and relative.endswith('/frr.conf'):
            seeds[relative] = data.decode('utf-8')
        else:
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                text = None
            if text is not None and not any(ord(char) < 32 and char not in '\t\r\n' for char in text):
                supporting[relative] = {'encoding': 'utf-8', 'content': text, 'role': 'build'}
            else:
                supporting[relative] = {'encoding': 'base64',
                                       'content': base64.b64encode(data).decode('ascii'),
                                       'role': 'build'}
    guide = read(row['guide']).decode('utf-8') if row.get('guide') else ''
    layout = json.loads(read(row['layout'])) if row.get('layout') else {'version': 1, 'nodes': {}}
    return repository.create_python(row, source, seeds, guide, layout,
                                    lesson_files=supporting)
