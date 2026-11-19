"""Unit tests for the legacy-to-canonical layout converter.

``convert_layout_document`` accepts either the
retired absolute-pixel ``{id: {fx, fy, x, y}}`` shape or an already-canonical
``{version, nodes}`` envelope, always returns canonical output, is idempotent
on a second pass, and refuses (rather than guesses at) an unrecognised shape.
``convert_examples_dir`` is the thin driver that walks ``examples/layouts/*.json``.
"""

import json
import os
import tempfile
import unittest

from mniplab.layout_converter import convert_examples_dir, convert_layout_document
from mniplab.webserver.repositories import LAYOUT_VERSION, LayoutRepository


class ConvertLayoutDocumentTests(unittest.TestCase):
    """``convert_layout_document`` normalizes legacy input to canonical output."""

    LEGACY_SAMPLE = {
        'h1': {'fx': 200, 'fy': 300, 'x': 200, 'y': 300},
        'r1': {'fx': 400, 'fy': 300, 'x': 400, 'y': 300},
        'r2': {'fx': 600, 'fy': 300, 'x': 600, 'y': 300},
        'h2': {'fx': 800, 'fy': 300, 'x': 800, 'y': 300},
    }

    def test_converts_legacy_shape_to_canonical(self):
        """A legacy fx/fy/x/y document becomes ``{version, nodes:{id:{x,y}}}``."""
        result = convert_layout_document(self.LEGACY_SAMPLE)

        self.assertEqual(result['version'], LAYOUT_VERSION)
        self.assertEqual(set(result['nodes']), set(self.LEGACY_SAMPLE))
        for pos in result['nodes'].values():
            self.assertEqual(set(pos), {'x', 'y'})
            self.assertIsInstance(pos['x'], float)
            self.assertIsInstance(pos['y'], float)

    def test_stamps_version(self):
        """Output always carries the current LAYOUT_VERSION."""
        result = convert_layout_document(self.LEGACY_SAMPLE)
        self.assertEqual(result['version'], LAYOUT_VERSION)

    def test_is_idempotent(self):
        """Feeding the converted output back in reproduces it exactly."""
        once = convert_layout_document(self.LEGACY_SAMPLE)
        twice = convert_layout_document(once)
        self.assertEqual(once, twice)

    def test_empty_legacy_document_yields_empty_nodes(self):
        """An empty legacy document converts to an empty canonical envelope."""
        result = convert_layout_document({})
        self.assertEqual(result, {'version': LAYOUT_VERSION, 'nodes': {}})

    def test_refuses_non_object_document(self):
        """A JSON array is not a recognised layout shape."""
        with self.assertRaises(ValueError):
            convert_layout_document([1, 2, 3])

    def test_refuses_unrecognised_node_shape(self):
        """A top-level value that isn't a position object is refused loudly."""
        with self.assertRaises(ValueError):
            convert_layout_document({'r1': 5})

    def test_refuses_unknown_canonical_version(self):
        """A canonical-looking envelope with an unknown version is refused."""
        with self.assertRaises(ValueError):
            convert_layout_document({'version': 999, 'nodes': {'r1': {'x': 1, 'y': 2}}})

    def test_refuses_canonical_envelope_missing_nodes(self):
        """A ``version`` key without a matching ``nodes`` object is refused."""
        with self.assertRaises(ValueError):
            convert_layout_document({'version': LAYOUT_VERSION})

    def test_refuses_node_missing_numeric_coordinate(self):
        """A node lacking a numeric x/y fails loud, whichever shape it came from."""
        with self.assertRaises(ValueError):
            convert_layout_document({'r1': {'x': 1}})


class ConvertExamplesDirTests(unittest.TestCase):
    """``convert_examples_dir`` walks and rewrites ``layouts/*.json`` files."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix='mniplab-layout-convert-test-')
        os.chdir(self._tmp)
        os.makedirs(os.path.join(self._tmp, 'examples', 'layouts'), exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)

    def _write(self, name, data):
        path = os.path.join(self._tmp, 'examples', 'layouts', f'{name}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        return path

    def test_converts_all_files_and_they_load_cleanly(self):
        """Every converted file loads through LayoutRepository without raising."""
        self._write('demo-a', {
            'h1': {'fx': 0, 'fy': 0, 'x': 0, 'y': 0},
            'h2': {'fx': 100, 'fy': 100, 'x': 100, 'y': 100},
        })
        self._write('demo-b', {
            'r1': {'fx': 50, 'fy': 10, 'x': 50, 'y': 10},
        })

        converted = convert_examples_dir('examples')

        self.assertEqual(sorted(converted), ['demo-a.json', 'demo-b.json'])
        for name in ('demo-a', 'demo-b'):
            loaded = LayoutRepository.load(name)
            self.assertEqual(loaded['version'], LAYOUT_VERSION)

    def test_rerun_is_idempotent_on_disk(self):
        """A second pass over already-canonical files produces byte-identical output."""
        self._write('demo', {
            'h1': {'fx': 0, 'fy': 0, 'x': 0, 'y': 0},
            'h2': {'fx': 100, 'fy': 100, 'x': 100, 'y': 100},
        })

        convert_examples_dir('examples')
        path = os.path.join(self._tmp, 'examples', 'layouts', 'demo.json')
        with open(path, encoding='utf-8') as f:
            first_pass = f.read()

        convert_examples_dir('examples')
        with open(path, encoding='utf-8') as f:
            second_pass = f.read()

        self.assertEqual(first_pass, second_pass)

    def test_raises_on_unrecognised_file_and_names_it(self):
        """A file with an unrecognised shape stops the run with a clear error."""
        self._write('bad', {'r1': 5})

        with self.assertRaises(ValueError) as ctx:
            convert_examples_dir('examples')
        self.assertIn('bad.json', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
