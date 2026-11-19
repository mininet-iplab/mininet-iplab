"""Unit tests for the web app itself: Lab lifecycle, health probes, composition.

The routes and layers with a subject of their own live beside this module —
test_web_sessions, test_web_session_cap, test_web_instructor_panel,
test_web_topology, test_web_settings, test_web_dhcp_api, test_web_dns_api
and test_web_resolver_api.
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from mniplab.tests.fakes import FakeTerminalRegistry
from mniplab.webserver import auth
from mniplab.labhandle import LocalLabHandle
from mniplab.webserver.services import LabLifecycleService


class LabLifecycleServiceTests(unittest.IsolatedAsyncioTestCase):
    """start_lab holds the Lab without naming its Nodes; stop_lab lets it go."""

    async def test_start_then_stop_round_trips_the_lab(self):
        """The service holds the Lab it started and none after stopping it."""
        service = LabLifecycleService()
        lab = Mock(hosts=[], switches=[])

        with patch('mniplab.utils.update_hosts_file') as update_hosts:
            started = await service.start_lab(lab, 'test_lab')
        self.assertEqual(started['status'], 'running')
        self.assertIs(service.lab, lab)
        update_hosts.assert_not_called()

        stopped = await service.stop_lab(started['key'], FakeTerminalRegistry())
        self.assertEqual(stopped, {'status': 'stopped', 'key': started['key'],
                                   'group': None, 'example': 'test_lab'})
        self.assertIsNone(service.lab)
        lab.stop.assert_called_once_with()


class LabStatusDriftTests(unittest.TestCase):
    """Lab status counts drifted links, and survives a failed read."""

    def _drift(self, lab):
        return LocalLabHandle(lab).status()["link_condition_drift"]

    def test_counts_drifted_links(self):
        lab = Mock(hosts=[], switches=[])
        lab.link_condition_drift.return_value = [{'source': 'r1'}, {'source': 'r2'}]

        self.assertEqual(self._drift(lab), 2)

    def test_unreadable_traffic_control_is_not_drift(self):
        lab = Mock(hosts=[], switches=[])
        lab.link_condition_drift.side_effect = OSError('no such device')

        self.assertEqual(self._drift(lab), 0)


class HealthEndpointTests(unittest.TestCase):
    """Health and readiness endpoints report Lab status without authentication."""

    def _get(self, path: str, status: dict) -> dict:
        """Read one unauthenticated probe while the registry reports *status*.

        Args:
            path: The probe to read, ``/health`` or ``/ready``.
            status: What ``registry.get_status`` answers.

        Returns:
            The probe's JSON body, after asserting the request was accepted.
        """
        from fastapi.testclient import TestClient
        from mniplab.webserver import create_app

        with patch('mniplab.webserver.registry.get_status',
                   new=AsyncMock(return_value=status)):
            response = TestClient(create_app()).get(path)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_health_without_a_lab_reports_ok_version_and_no_drift(self):
        from mniplab import __version__

        body = self._get('/health', {'running': False})

        self.assertEqual(body['status'], 'ok')
        self.assertFalse(body['lab_running'])
        self.assertEqual(body['version'], __version__)
        self.assertEqual(body['link_condition_drift'], 0)

    def test_health_reports_running_lab_details_and_drift(self):
        body = self._get('/health', {
            'running': True,
            'example': 'static-lab',
            'uptime': 42,
            'nodes': 7,
            'active_terminals': 2,
            'link_condition_drift': 2,
        })

        self.assertTrue(body['lab_running'])
        self.assertEqual(body['lab_example'], 'static-lab')
        self.assertEqual(body['nodes'], 7)
        self.assertEqual(body['active_terminals'], 2)
        self.assertEqual(body['link_condition_drift'], 2)

    def test_ready_is_false_without_a_lab(self):
        body = self._get('/ready', {'running': False})

        self.assertFalse(body['ready'])
        self.assertEqual(body['reason'], 'no lab running')

    def test_ready_is_true_with_a_running_lab(self):
        body = self._get('/ready', {'running': True, 'example': 'bgp-lab', 'nodes': 4})

        self.assertTrue(body['ready'])
        self.assertEqual(body['lab'], 'bgp-lab')
        self.assertEqual(body['nodes'], 4)


class StaticCachingTests(unittest.TestCase):
    """The page's scripts are revalidated on every load, never reused unchecked.

    Only ``main.js`` carries a ``?v=`` version, so every module it imports
    is fetched by a bare URL. Without a caching directive a browser may reuse
    a stale module on heuristic freshness: a new ``index.html`` then meets
    old JavaScript, and a new control renders with no handler behind it.
    """

    def _get(self, path: str):
        from fastapi.testclient import TestClient
        from mniplab.webserver import create_app

        return TestClient(create_app()).get(path)

    def test_an_imported_module_must_be_revalidated(self) -> None:
        response = self._get('/static/js/console/network-tools.js')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('cache-control'), 'no-cache')
        self.assertIn('etag', response.headers)

    def test_an_unchanged_module_costs_a_304_not_a_download(self) -> None:
        from fastapi.testclient import TestClient
        from mniplab.webserver import create_app

        client = TestClient(create_app())
        etag = client.get('/static/js/main.js').headers['etag']

        again = client.get('/static/js/main.js', headers={'If-None-Match': etag})

        self.assertEqual(again.status_code, 304)


class LabPageRouteTests(unittest.TestCase):
    """``/labs/{key}`` serves the same app shell as ``/``, so a bookmarked or
    shared Lab URL — and a page reload while on one — never 404s.  The client
    resolves the key against its own status once signed in; this route only
    has to exist.
    """

    def _get(self, path: str):
        from fastapi.testclient import TestClient
        from mniplab.webserver import create_app

        return TestClient(create_app()).get(path)

    def test_a_lab_key_path_serves_the_same_shell_as_the_root(self) -> None:
        root = self._get('/')
        keyed = self._get('/labs/abc123')

        self.assertEqual(keyed.status_code, 200)
        self.assertEqual(keyed.headers['content-type'], root.headers['content-type'])
        self.assertEqual(keyed.text, root.text)

    def test_an_unrelated_path_under_labs_is_not_matched(self) -> None:
        # Only the one-segment `/labs/{key}` form is a page route; the JSON
        # API's own `/api/labs/{key}/...` routes are untouched by this.
        response = self._get('/labs/abc123/nested')

        self.assertEqual(response.status_code, 404)


class CompositionRootTests(unittest.TestCase):
    """create_app connects the auth layer to the real Terminal registry.

    The AST guard in AuthDependencyDirectionTests proves auth never imports the
    registry.  That leaves the opposite risk unchecked: an app that never wires
    the two together at all, in which case destroying a Session would silently
    leak every Terminal it owned.
    """

    def setUp(self) -> None:
        """Restore whatever closer was registered before this test ran."""
        original_closer = auth._terminal_closer
        self.addCleanup(auth.set_terminal_closer, original_closer)

    def test_create_app_registers_the_registry_as_terminal_closer(self) -> None:
        """create_app should hand auth the real registry's close method."""
        from mniplab.webserver import create_app
        from mniplab.webserver.session import registry

        auth.set_terminal_closer(None)
        create_app()

        # Reads the private attribute deliberately: the wire itself is what is
        # under test, and auth exposes a setter but no getter.
        self.assertEqual(auth._terminal_closer, registry.close_session_terminals)
