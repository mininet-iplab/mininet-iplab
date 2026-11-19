"""Unit tests for the canonical Authored Layout schema and normalization.

Covers the pure ``normalize`` projection into the virtual
canvas and the ``LayoutRepository`` canonical save/load round-trip, including its
loud refusal of files whose shape or version it does not recognise. The Authored
Layout is the source of truth; a Personal Layout is never persisted.
"""

import json
import os
import tempfile
import unittest

from mniplab.tests.fakes import FakeTerminalCloserMixin, LoginClientMixin
from mniplab.webserver.repositories import (
    LAYOUT_VERSION,
    LAYOUT_PADDING,
    VIRTUAL_SIZE,
    LayoutRepository,
    normalize,
)


class NormalizeTests(unittest.TestCase):
    """``normalize`` projects raw positions into the fixed virtual canvas."""

    def test_maps_all_nodes_inside_padded_canvas(self):
        """Every output coordinate lands within the padded virtual canvas."""
        result = normalize({
            'a': {'x': -500, 'y': 20},
            'b': {'x': 1200, 'y': 900},
            'c': {'x': 300, 'y': 450},
        })

        lower = LAYOUT_PADDING
        upper = VIRTUAL_SIZE - LAYOUT_PADDING
        for pos in result.values():
            self.assertGreaterEqual(pos['x'], lower - 1e-6)
            self.assertLessEqual(pos['x'], upper + 1e-6)
            self.assertGreaterEqual(pos['y'], lower - 1e-6)
            self.assertLessEqual(pos['y'], upper + 1e-6)

    def test_extreme_nodes_are_inset_by_padding(self):
        """The bounding box touches the padding, not the raw canvas edge."""
        result = normalize({
            'left': {'x': 0, 'y': 0},
            'right': {'x': 100, 'y': 100},
        })
        xs = [p['x'] for p in result.values()]
        ys = [p['y'] for p in result.values()]

        self.assertAlmostEqual(min(xs), LAYOUT_PADDING, places=2)
        self.assertAlmostEqual(min(ys), LAYOUT_PADDING, places=2)
        self.assertAlmostEqual(max(xs), VIRTUAL_SIZE - LAYOUT_PADDING, places=2)
        self.assertAlmostEqual(max(ys), VIRTUAL_SIZE - LAYOUT_PADDING, places=2)

    def test_preserves_left_of_and_above_relations(self):
        """Relative ordering on both axes survives the projection."""
        result = normalize({
            'tl': {'x': 10, 'y': 10},
            'tr': {'x': 90, 'y': 12},
            'bl': {'x': 8, 'y': 80},
        })
        # tl is left of tr, and above bl.
        self.assertLess(result['tl']['x'], result['tr']['x'])
        self.assertLess(result['tl']['y'], result['bl']['y'])
        self.assertLess(result['bl']['x'], result['tr']['x'])

    def test_preserves_aspect_ratio(self):
        """A wide arrangement stays wide: uniform scale keeps the span ratio."""
        result = normalize({
            'a': {'x': 0, 'y': 0},
            'b': {'x': 400, 'y': 0},
            'c': {'x': 0, 'y': 100},
        })
        span_x = max(p['x'] for p in result.values()) - min(p['x'] for p in result.values())
        span_y = max(p['y'] for p in result.values()) - min(p['y'] for p in result.values())
        # Input aspect is 400:100 == 4:1; uniform scaling preserves it.
        self.assertAlmostEqual(span_x / span_y, 4.0, places=2)

    def test_is_idempotent(self):
        """Normalizing already-canonical positions returns them unchanged."""
        once = normalize({
            'a': {'x': 17, 'y': 3},
            'b': {'x': 940, 'y': 220},
            'c': {'x': 512, 'y': 611},
        })
        twice = normalize(once)
        self.assertEqual(set(once), set(twice))
        for nid in once:
            self.assertAlmostEqual(once[nid]['x'], twice[nid]['x'], places=2)
            self.assertAlmostEqual(once[nid]['y'], twice[nid]['y'], places=2)

    def test_single_node_is_centered(self):
        """One node has no span, so it sits at the canvas centre."""
        result = normalize({'solo': {'x': 42, 'y': 99}})
        self.assertAlmostEqual(result['solo']['x'], VIRTUAL_SIZE / 2, places=2)
        self.assertAlmostEqual(result['solo']['y'], VIRTUAL_SIZE / 2, places=2)

    def test_vertical_line_centers_x_and_spreads_y(self):
        """A zero-width arrangement centres x while y fills the padded canvas."""
        result = normalize({
            'top': {'x': 5, 'y': 0},
            'bot': {'x': 5, 'y': 300},
        })
        self.assertAlmostEqual(result['top']['x'], VIRTUAL_SIZE / 2, places=2)
        self.assertAlmostEqual(result['bot']['x'], VIRTUAL_SIZE / 2, places=2)
        self.assertAlmostEqual(result['top']['y'], LAYOUT_PADDING, places=2)
        self.assertAlmostEqual(result['bot']['y'], VIRTUAL_SIZE - LAYOUT_PADDING, places=2)

    def test_empty_input_yields_empty_output(self):
        """No nodes in, no nodes out."""
        self.assertEqual(normalize({}), {})

    def test_output_drops_fx_fy(self):
        """Canonical output carries only x/y, never the retired fx/fy."""
        result = normalize({'a': {'x': 1, 'y': 2, 'fx': 1, 'fy': 2},
                            'b': {'x': 9, 'y': 8, 'fx': 9, 'fy': 8}})
        for pos in result.values():
            self.assertEqual(set(pos), {'x', 'y'})

    def test_missing_coordinate_raises(self):
        """A node without a numeric coordinate is refused loudly."""
        with self.assertRaises(ValueError):
            normalize({'a': {'x': 1}})
        with self.assertRaises(ValueError):
            normalize({'a': {'x': 1, 'y': 'nope'}})


class LayoutRepositoryTests(unittest.TestCase):
    """Canonical save/load round-trip and loud refusal of bad files."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix='mniplab-layout-test-')
        os.chdir(self._tmp)
        os.makedirs(os.path.join(self._tmp, 'examples', 'layouts'), exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)

    def _layout_path(self, name):
        return os.path.join(self._tmp, 'examples', 'layouts', f'{name}.json')

    def _write_raw(self, name, data):
        with open(self._layout_path(name), 'w', encoding='utf-8') as f:
            json.dump(data, f)

    def test_save_writes_canonical_versioned_shape(self):
        """The written file is ``{version, nodes:{id:{x,y}}}`` with no fx/fy."""
        LayoutRepository.save('demo', {'r1': {'x': 100, 'y': 200}})

        with open(self._layout_path('demo'), encoding='utf-8') as f:
            document = json.load(f)

        self.assertEqual(document['version'], LAYOUT_VERSION)
        self.assertEqual(document['nodes'], {'r1': {'x': 100, 'y': 200}})
        self.assertNotIn('fx', document['nodes']['r1'])

    def test_save_load_round_trip(self):
        """What ``save`` writes, ``load`` returns in canonical form."""
        nodes = {'r1': {'x': 100, 'y': 200}, 'r2': {'x': 400, 'y': 250}}
        LayoutRepository.save('demo', nodes)

        loaded = LayoutRepository.load('demo')

        self.assertEqual(loaded, {'version': LAYOUT_VERSION, 'nodes': nodes})

    def test_load_missing_returns_none(self):
        """A lab with no authored layout loads as ``None``, not an error."""
        self.assertIsNone(LayoutRepository.load('never-saved'))

    def test_load_refuses_legacy_shape(self):
        """The retired absolute-pixel fx/fy shape is refused loudly."""
        self._write_raw('legacy', {'r1': {'fx': 10, 'fy': 20, 'x': 10, 'y': 20}})
        with self.assertRaises(ValueError):
            LayoutRepository.load('legacy')

    def test_load_refuses_unknown_version(self):
        """A version the reader does not know is refused, not guessed at."""
        self._write_raw('future', {'version': 999, 'nodes': {'r1': {'x': 1, 'y': 2}}})
        with self.assertRaises(ValueError):
            LayoutRepository.load('future')

    def test_load_refuses_non_object(self):
        """A JSON array where an object is expected is refused."""
        self._write_raw('weird', [1, 2, 3])
        with self.assertRaises(ValueError):
            LayoutRepository.load('weird')

    def test_load_refuses_bad_node_position(self):
        """A node missing a numeric coordinate fails loud rather than silent."""
        self._write_raw('broken', {'version': LAYOUT_VERSION,
                                    'nodes': {'r1': {'x': 1}}})
        with self.assertRaises(ValueError):
            LayoutRepository.load('broken')


class SaveLayoutRoleGateTests(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The save_layout route is instructor-only.

    A Learner Session must be refused with 403; an Instructor Session is
    admitted and its write lands exactly where ``load_layout`` reads from.
    """

    def setUp(self):
        super().setUp()
        self._cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix='mniplab-save-gate-test-')
        os.chdir(self._tmp)
        os.makedirs(os.path.join(self._tmp, 'examples'), exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)

    def test_learner_is_refused_with_403(self):
        """A Learner Session may not save a layout."""
        self.open_session(role="learner")

        resp = self.client.post('/api/save_layout', json={
            'example': 'demo',
            'positions': {'r1': {'x': 0, 'y': 0}, 'r2': {'x': 100, 'y': 100}},
        })

        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(LayoutRepository.load('demo'))

    def test_instructor_is_admitted_with_200(self):
        """An Instructor Session may save a layout."""
        self.open_session(role="instructor")

        resp = self.client.post('/api/save_layout', json={
            'example': 'demo',
            'positions': {'r1': {'x': 0, 'y': 0}, 'r2': {'x': 100, 'y': 100}},
        })

        self.assertEqual(resp.status_code, 200)

    def test_saved_layout_appears_on_disk_as_committable_diff(self):
        """A save writes examples/layouts/<lab>.json, visible to ``git status``."""
        self.open_session(role="instructor")

        self.client.post('/api/save_layout', json={
            'example': 'demo',
            'positions': {'r1': {'x': 0, 'y': 0}, 'r2': {'x': 100, 'y': 100}},
        })

        layout_path = os.path.join(self._tmp, 'examples', 'layouts', 'demo.json')
        self.assertTrue(os.path.isfile(layout_path))

    def test_what_save_writes_is_what_load_layout_returns(self):
        """The instructor's save round-trips exactly through the load route."""
        self.open_session(role="instructor")

        save_resp = self.client.post('/api/save_layout', json={
            'example': 'demo',
            'positions': {'r1': {'x': 0, 'y': 0}, 'r2': {'x': 100, 'y': 100}},
        })
        self.assertEqual(save_resp.status_code, 200)

        load_resp = self.client.get('/api/load_layout', params={'example': 'demo'})
        self.assertEqual(load_resp.status_code, 200)

        self.assertEqual(load_resp.json()['layout'], LayoutRepository.load('demo'))


class SaveLayoutPayloadTests(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The save_layout route accepts canonical positions and rejects malformed input.

    The role gate (403 learner / 200 instructor) is covered by
    ``SaveLayoutRoleGateTests`` above; these cover only payload validation and
    the normalize-then-write path.
    """

    def setUp(self):
        super().setUp()
        self._cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix='mniplab-save-test-')
        os.chdir(self._tmp)
        os.makedirs(os.path.join(self._tmp, 'examples'), exist_ok=True)
        self.open_session(role="instructor")

    def tearDown(self):
        os.chdir(self._cwd)

    def test_accepts_canonical_positions_and_writes_normalized(self):
        """Raw x/y positions are accepted and stored in canonical form."""
        resp = self.client.post('/api/save_layout', json={
            'example': 'demo',
            'positions': {'r1': {'x': 0, 'y': 0}, 'r2': {'x': 100, 'y': 100}},
        })
        self.assertEqual(resp.status_code, 200)

        loaded = LayoutRepository.load('demo')
        self.assertEqual(loaded['version'], LAYOUT_VERSION)
        self.assertEqual(set(loaded['nodes']), {'r1', 'r2'})
        for pos in loaded['nodes'].values():
            self.assertEqual(set(pos), {'x', 'y'})

    def test_rejects_non_numeric_coordinate(self):
        """A non-numeric coordinate is a 400, not a silent write."""
        resp = self.client.post('/api/save_layout', json={
            'example': 'demo',
            'positions': {'r1': {'x': 'left', 'y': 0}},
        })
        self.assertEqual(resp.status_code, 400)

    def test_rejects_missing_coordinate(self):
        """A node missing y is rejected before it can be normalized."""
        resp = self.client.post('/api/save_layout', json={
            'example': 'demo',
            'positions': {'r1': {'x': 10}},
        })
        self.assertEqual(resp.status_code, 400)

    def test_rejects_non_object_position(self):
        """A scalar where a position object is expected is rejected."""
        resp = self.client.post('/api/save_layout', json={
            'example': 'demo',
            'positions': {'r1': 5},
        })
        self.assertEqual(resp.status_code, 400)


if __name__ == '__main__':
    unittest.main()
