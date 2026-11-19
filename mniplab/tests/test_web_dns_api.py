"""Unit tests for the Authoritative DNS Web API routes in mniplab.webserver.api."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
from typing import Any

import pytest
import uvicorn
from mniplab.tests.browser import sync_playwright

from mniplab.errors import DNSControlError, DNSValidationError
from mniplab.tests.fakes import FAKE_LAB_KEY, FakeLab, FakeLabAliveMixin, FakeTerminalCloserMixin, LoginClientMixin
from mniplab.tests.labs import LabCase, build_dns_test_lab
from mniplab.webserver import create_app, registry


@pytest.mark.unit
class DNSNodesRouteTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """GET /api/dns/nodes lists DNS-serving Nodes and their hosted Zones."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_returns_dns_nodes_from_the_registry(self) -> None:
        nodes = [
            {"node": "dns1", "running": True, "zones": ["example.com."]},
            {"node": "dns2", "running": False, "zones": ["test.org."]},
        ]
        with patch(
            'mniplab.webserver.registry.list_dns_nodes',
            new=AsyncMock(return_value={"nodes": nodes}),
        ):
            response = self.client.get('/api/labs/fake/dns/nodes')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"nodes": nodes})

    def test_requires_authentication(self) -> None:
        self.client.cookies.clear()
        response = self.client.get('/api/labs/fake/dns/nodes')
        self.assertEqual(response.status_code, 401)


@pytest.mark.unit
class DNSRecordsRouteTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Record listing, addition, and deletion under /api/dns/{node}/zones/{zone}."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_lists_records(self) -> None:
        records = [{"owner": "web.example.com.", "ttl": 300, "type": "A", "data": "10.0.0.2"}]
        with patch(
            'mniplab.webserver.registry.get_dns_records',
            new=AsyncMock(return_value={"node": "dns1", "zone": "example.com.", "records": records}),
        ):
            response = self.client.get('/api/labs/fake/dns/dns1/zones/example.com./records')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["zone"], "example.com.")
        self.assertEqual(payload["records"], records)

    def test_adds_record_via_atomic_zone_set(self) -> None:
        committed = {
            "node": "dns1", "zone": "example.com.", "owner": "web",
            "type": "A", "data": "10.0.0.3", "ttl": 300,
        }
        with patch(
            'mniplab.webserver.registry.set_dns_record',
            new=AsyncMock(return_value=committed),
        ) as mock_set:
            response = self.client.post(
                '/api/labs/fake/dns/dns1/zones/example.com./records',
                json={"owner": "web", "type": "A", "data": "10.0.0.3", "ttl": 300},
            )

        self.assertEqual(response.status_code, 200)
        mock_set.assert_called_once_with(
            FAKE_LAB_KEY, "dns1", "example.com.", "web", "A", "10.0.0.3", 300,
            old_owner=None, old_type=None, old_data=None,
        )

    def test_updates_record_without_ttl(self) -> None:
        committed = {
            "node": "dns1", "zone": "example.com.", "owner": "alias",
            "type": "CNAME", "data": "web.example.com.", "ttl": None,
        }
        with patch(
            'mniplab.webserver.registry.set_dns_record',
            new=AsyncMock(return_value=committed),
        ) as mock_set:
            response = self.client.post(
                '/api/labs/fake/dns/dns1/zones/example.com./records',
                json={"owner": "alias", "type": "CNAME", "data": "web.example.com."},
            )

        self.assertEqual(response.status_code, 200)
        mock_set.assert_called_once_with(
            FAKE_LAB_KEY, "dns1", "example.com.", "alias", "CNAME", "web.example.com.", None,
            old_owner=None, old_type=None, old_data=None,
        )

    def test_updates_record_atomically_replacing_old_record(self) -> None:
        committed = {
            "node": "dns1", "zone": "example.com.", "owner": "web",
            "type": "A", "data": "10.0.0.4", "ttl": 300, "old_owner": "web",
        }
        with patch(
            'mniplab.webserver.registry.set_dns_record',
            new=AsyncMock(return_value=committed),
        ) as mock_set:
            response = self.client.post(
                '/api/labs/fake/dns/dns1/zones/example.com./records',
                json={
                    "owner": "web",
                    "type": "A",
                    "data": "10.0.0.4",
                    "ttl": 300,
                    "old_owner": "web",
                    "old_type": "A",
                    "old_data": "10.0.0.2",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), committed)
        mock_set.assert_called_once_with(
            FAKE_LAB_KEY, "dns1", "example.com.", "web", "A", "10.0.0.4", 300,
            old_owner="web", old_type="A", old_data="10.0.0.2",
        )

    def test_deletes_record_via_atomic_zone_unset(self) -> None:
        removed = {
            "node": "dns1", "zone": "example.com.", "owner": "web",
            "type": "A", "data": "10.0.0.2",
        }
        with patch(
            'mniplab.webserver.registry.unset_dns_record',
            new=AsyncMock(return_value=removed),
        ) as mock_unset:
            response = self.client.request(
                "DELETE",
                '/api/labs/fake/dns/dns1/zones/example.com./records',
                json={"owner": "web.example.com.", "type": "A", "data": "10.0.0.2"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), removed)
        mock_unset.assert_called_once_with(FAKE_LAB_KEY, "dns1", "example.com.", "web.example.com.", "A", "10.0.0.2")

    def test_invalid_record_returns_400_with_structured_error(self) -> None:
        """Knot semantic check failures surface as 400 with human-readable details."""
        error = DNSValidationError(
            "CNAME record collision at owner web.example.com.",
            command="zone-commit",
            error_code="semantic check failed",
            zone="example.com.",
            data={"owner": "web.example.com."},
        )
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'set_dns_record',
            side_effect=error,
        ):
            response = self.client.post(
                '/api/labs/fake/dns/dns1/zones/example.com./records',
                json={"owner": "web", "type": "CNAME", "data": "other.example.com."},
            )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("collision", payload["detail"])
        self.assertEqual(payload["command"], "zone-commit")
        self.assertEqual(payload["error_code"], "semantic check failed")
        self.assertEqual(payload["zone"], "example.com.")

    def test_control_failure_returns_503(self) -> None:
        error = DNSControlError(
            "Failed to communicate with Knot control socket",
            command="zone-begin",
        )
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'set_dns_record',
            side_effect=error,
        ):
            response = self.client.post(
                '/api/labs/fake/dns/dns1/zones/example.com./records',
                json={"owner": "web", "type": "A", "data": "10.0.0.3"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("control socket", response.json()["detail"])

    def test_empty_owner_is_rejected_by_request_validation(self) -> None:
        response = self.client.post(
            '/api/labs/fake/dns/dns1/zones/example.com./records',
            json={"owner": "", "type": "A", "data": "10.0.0.3"},
        )
        self.assertEqual(response.status_code, 422)

    def test_non_positive_ttl_is_rejected_by_request_validation(self) -> None:
        response = self.client.post(
            '/api/labs/fake/dns/dns1/zones/example.com./records',
            json={"owner": "web", "type": "A", "data": "10.0.0.3", "ttl": 0},
        )
        self.assertEqual(response.status_code, 422)

    def test_requires_authentication(self) -> None:
        self.client.cookies.clear()
        response = self.client.get('/api/labs/fake/dns/dns1/zones/example.com./records')
        self.assertEqual(response.status_code, 401)

    def test_node_not_found_returns_400(self) -> None:
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'get_dns_records',
            side_effect=ValueError("Node not found: dns-missing"),
        ):
            response = self.client.get('/api/labs/fake/dns/dns-missing/zones/example.com./records')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Node not found", response.json()["detail"])

    def test_deletes_record_without_optional_type_and_data(self) -> None:
        removed = {
            "node": "dns1", "zone": "example.com.", "owner": "web.example.com.",
            "type": None, "data": None,
        }
        with patch(
            'mniplab.webserver.registry.unset_dns_record',
            new=AsyncMock(return_value=removed),
        ) as mock_unset:
            response = self.client.request(
                "DELETE",
                '/api/labs/fake/dns/dns1/zones/example.com./records',
                json={"owner": "web.example.com."},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), removed)
        mock_unset.assert_called_once_with(FAKE_LAB_KEY, "dns1", "example.com.", "web.example.com.", None, None)

    def test_delete_empty_owner_is_rejected_by_request_validation(self) -> None:
        response = self.client.request(
            "DELETE",
            '/api/labs/fake/dns/dns1/zones/example.com./records',
            json={"owner": ""},
        )
        self.assertEqual(response.status_code, 422)


@pytest.mark.unit
class DNSMutationBroadcastTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Successful record mutations broadcast DNS_RECORD_UPDATED to all sessions."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_record_addition_broadcasts_dns_record_update(self) -> None:
        committed = {
            "node": "dns1", "zone": "example.com.", "owner": "web",
            "type": "A", "data": "10.0.0.3", "ttl": 300,
        }
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'set_dns_record', return_value=committed,
        ):
            with patch.object(registry.feed(FAKE_LAB_KEY), 'broadcast', new=AsyncMock()) as mock_broadcast:
                response = self.client.post(
                    '/api/labs/fake/dns/dns1/zones/example.com./records',
                    json={"owner": "web", "type": "A", "data": "10.0.0.3", "ttl": 300},
                )

        self.assertEqual(response.status_code, 200)
        mock_broadcast.assert_called_once_with({
            "type": "DNS_RECORD_UPDATED",
            "data": {**committed, "action": "set"},
        })

    def test_record_deletion_broadcasts_dns_record_update(self) -> None:
        removed = {
            "node": "dns1", "zone": "example.com.", "owner": "web",
            "type": "A", "data": "10.0.0.2", "ttl": None,
        }
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'unset_dns_record', return_value=removed,
        ):
            with patch.object(registry.feed(FAKE_LAB_KEY), 'broadcast', new=AsyncMock()) as mock_broadcast:
                response = self.client.request(
                    "DELETE",
                    '/api/labs/fake/dns/dns1/zones/example.com./records',
                    json={"owner": "web.example.com.", "type": "A", "data": "10.0.0.2"},
                )

        self.assertEqual(response.status_code, 200)
        mock_broadcast.assert_called_once_with({
            "type": "DNS_RECORD_UPDATED",
            "data": {**removed, "action": "delete"},
        })

    def test_failed_mutation_does_not_broadcast(self) -> None:
        error = DNSValidationError("duplicate record", command="zone-commit")
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'set_dns_record', side_effect=error,
        ):
            with patch.object(registry.feed(FAKE_LAB_KEY), 'broadcast', new=AsyncMock()) as mock_broadcast:
                response = self.client.post(
                    '/api/labs/fake/dns/dns1/zones/example.com./records',
                    json={"owner": "web", "type": "A", "data": "10.0.0.3"},
                )

        self.assertEqual(response.status_code, 400)
        mock_broadcast.assert_not_called()


@pytest.mark.unit
class DNSStatsAndStatusRouteTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Stats retrieval, reset, and zone status under /api/dns/{node}."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_returns_parsed_mod_stats(self) -> None:
        stats = {
            "server-operation": {"query": 42},
            "response-code": {"NOERROR": 40, "NXDOMAIN": 2},
        }
        with patch(
            'mniplab.webserver.registry.get_dns_stats',
            new=AsyncMock(return_value={"node": "dns1", "stats": stats}),
        ):
            response = self.client.get('/api/labs/fake/dns/dns1/stats')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["node"], "dns1")
        self.assertEqual(payload["stats"]["response-code"]["NOERROR"], 40)

    def test_resets_stats(self) -> None:
        with patch(
            'mniplab.webserver.registry.reset_dns_stats',
            new=AsyncMock(return_value={"node": "dns1", "reset": True}),
        ) as mock_reset:
            response = self.client.post('/api/labs/fake/dns/dns1/stats/reset')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["reset"])
        mock_reset.assert_called_once_with(FAKE_LAB_KEY, "dns1")

    def test_returns_zone_status_with_soa_serial(self) -> None:
        status_payload = {
            "node": "dns1",
            "zone": "example.com.",
            "status": {"role": "master", "serial": 2026090701, "primary_ns": "ns1.example.com."},
            "serial": 2026090701,
            "primary_ns": "ns1.example.com.",
        }
        with patch(
            'mniplab.webserver.registry.get_dns_zone_status',
            new=AsyncMock(return_value=status_payload),
        ):
            response = self.client.get('/api/labs/fake/dns/dns1/zones/example.com./status')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"]["serial"], 2026090701)
        self.assertEqual(payload["primary_ns"], "ns1.example.com.")

    def test_stats_reset_control_error_returns_503(self) -> None:
        error = DNSControlError("Socket failure on reset", command="stats-reset")
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'reset_dns_stats',
            side_effect=error,
        ):
            response = self.client.post('/api/labs/fake/dns/dns1/stats/reset')
        self.assertEqual(response.status_code, 503)

    def test_zone_status_node_not_found_returns_400(self) -> None:
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'get_dns_zone_status',
            side_effect=ValueError("Node not found: missing"),
        ):
            response = self.client.get('/api/labs/fake/dns/missing/zones/example.com./status')
        self.assertEqual(response.status_code, 400)

    def test_requires_authentication(self) -> None:
        self.client.cookies.clear()
        response = self.client.get('/api/labs/fake/dns/dns1/stats')
        self.assertEqual(response.status_code, 401)


@pytest.mark.unit
class SessionRegistryDNSTests(unittest.TestCase):
    """Unit tests for SessionRegistry's DNS methods and per-Node concurrency lock."""

    def setUp(self) -> None:
        from mniplab.webserver.session import SessionRegistry
        self.reg = SessionRegistry()
        self.reg.lab_service.lab = FakeLab()
        self.key = FAKE_LAB_KEY

    def test_dns_lock_serializes_concurrent_mutations_for_same_node(self) -> None:
        """Mutations on the same node must acquire the same lock and execute in series."""
        order = []

        def fake_cmd(node: str, zone: str, owner: str, rtype: str, data: str, **kwargs: Any) -> dict[str, Any]:
            order.append(f"start-{owner}")
            time.sleep(0.05)
            order.append(f"end-{owner}")
            return {"node": node, "owner": owner}

        async def run() -> None:
            with patch.object(self.reg.lab(self.key).handle, 'set_dns_record', side_effect=fake_cmd):
                t1 = asyncio.create_task(
                    self.reg.set_dns_record(self.key, "dns1", "example.com.", "host1", "A", "10.0.0.1")
                )
                t2 = asyncio.create_task(
                    self.reg.set_dns_record(self.key, "dns1", "example.com.", "host2", "A", "10.0.0.2")
                )
                await asyncio.gather(t1, t2)

        asyncio.run(run())
        self.assertEqual(order, ["start-host1", "end-host1", "start-host2", "end-host2"])

    def test_dns_lock_independent_across_different_nodes(self) -> None:
        """Mutations on different nodes obtain separate locks."""
        lock1 = self.reg._dns_lock(self.key, "dns1")
        lock2 = self.reg._dns_lock(self.key, "dns2")
        self.assertIsNot(lock1, lock2)

    def test_stop_lab_clears_dns_locks(self) -> None:
        """stop_lab lets the Lab go, and its per-node locks with it."""
        self.reg._dns_lock(self.key, "dns1")
        self.assertIn("dns1", self.reg.lab(self.key).dns_locks)
        with patch(
            "mniplab.webserver.session.KnotControlClient.clear_baselines"
        ) as clear_baselines, patch("mniplab.labhandle.LocalLabHandle._op_stop"):
            asyncio.run(self.reg.stop_lab(self.key))
        with self.assertRaises(ValueError):
            self.reg._dns_lock(self.key, "dns1")
        clear_baselines.assert_called_once_with()

    def test_query_results_are_wrapped_for_the_browser(self) -> None:
        """Each query hands the browser a dict keyed the way its panel reads it."""
        with patch.object(self.reg.lab(self.key).handle, 'list_dns_nodes', return_value=[{"node": "dns1"}]):
            res = asyncio.run(self.reg.list_dns_nodes(self.key))
            self.assertEqual(res, {"nodes": [{"node": "dns1"}]})

        with patch.object(self.reg.lab(self.key).handle, 'get_dns_records', return_value=[]):
            res = asyncio.run(self.reg.get_dns_records(self.key, "dns1", "example.com."))
            self.assertEqual(res["records"], [])

        with patch.object(
            self.reg.lab(self.key).handle,
            'get_dns_zone_status',
            return_value={"node": "dns1", "zone": "example.com.", "status": {"role": "master"}},
        ):
            res = asyncio.run(self.reg.get_dns_zone_status(self.key, "dns1", "example.com."))
            self.assertEqual(res["status"], {"role": "master"})

        with patch.object(self.reg.lab(self.key).handle, 'get_dns_stats', return_value={"queries": 10}):
            res = asyncio.run(self.reg.get_dns_stats(self.key, "dns1"))
            self.assertEqual(res["stats"], {"queries": 10})

        with patch.object(self.reg.lab(self.key).handle, 'reset_dns_stats', return_value=None):
            res = asyncio.run(self.reg.reset_dns_stats(self.key, "dns1"))
            self.assertEqual(res, {"node": "dns1", "reset": True})


@pytest.mark.unit
class DNSSECRouteTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """GET /api/dns/{node}/zones/{zone}/dnssec and POST /api/dns/{node}/zones/{zone}/dnssec/rollover."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_get_dnssec_details_success(self) -> None:
        payload = {
            "node": "dns1",
            "zone": "example.com.",
            "dnssec": True,
            "nsec3": False,
            "nsec3_salt": None,
            "is_secondary": False,
            "keys": [{"tag": 2371, "ksk": True, "zsk": False}],
            "ds_records": [{"key_tag": 2371, "digest": "abcdef"}],
        }
        with patch(
            "mniplab.webserver.registry.get_dnssec_details",
            new=AsyncMock(return_value=payload),
        ) as mock_get:
            response = self.client.get("/api/labs/fake/dns/dns1/zones/example.com./dnssec")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        mock_get.assert_called_once_with(FAKE_LAB_KEY, "dns1", "example.com.")

    def test_get_dnssec_details_requires_authentication(self) -> None:
        self.client.cookies.clear()
        response = self.client.get("/api/labs/fake/dns/dns1/zones/example.com./dnssec")
        self.assertEqual(response.status_code, 401)

    def test_trigger_dnssec_rollover_success(self) -> None:
        payload = {"status": "success", "key_type": "zsk"}
        with patch(
            "mniplab.webserver.registry.trigger_dnssec_rollover",
            new=AsyncMock(return_value=payload),
        ) as mock_roll:
            response = self.client.post(
                "/api/labs/fake/dns/dns1/zones/example.com./dnssec/rollover",
                json={"key_type": "zsk"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        mock_roll.assert_called_once_with(FAKE_LAB_KEY, "dns1", "example.com.", "zsk")

    def test_trigger_dnssec_rollover_invalid_key_type(self) -> None:
        response = self.client.post(
            "/api/labs/fake/dns/dns1/zones/example.com./dnssec/rollover",
            json={"key_type": "invalid"},
        )
        self.assertIn(response.status_code, (400, 422))

    def test_trigger_dnssec_rollover_requires_authentication(self) -> None:
        self.client.cookies.clear()
        response = self.client.post(
            "/api/labs/fake/dns/dns1/zones/example.com./dnssec/rollover",
            json={"key_type": "zsk"},
        )
        self.assertEqual(response.status_code, 401)


class DNSWebAPIIntegrationTests(LoginClientMixin, FakeTerminalCloserMixin, LabCase):
    """End-to-end: records added through the Web API resolve via dig immediately.

    Boots the DNS Test Lab, registers it with the global SessionRegistry, and
    exercises the REST endpoints through a TestClient exactly as the Web UI
    does; ``auth1`` is the knotd-backed Node and ``client1`` runs dig.
    """

    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient
        from mniplab.webserver import create_app

        self.client = TestClient(create_app())
        self.open_session()

        from mniplab.dns.control_client import KnotControlClient
        KnotControlClient.clear_baselines()

        self.net = self.lab()
        self.client_node = build_dns_test_lab(self.net)["client1"]

        self.start(self.net)
        result = asyncio.run(registry.start_lab(self.net, "dns-web-api-test"))
        self.assertNotIn("error", result)

    def tearDown(self) -> None:
        try:
            assert registry.lab_key is not None
            asyncio.run(registry.stop_lab(registry.lab_key))
        finally:
            super().tearDown()

    def test_dns_web_api_end_to_end_scenario(self) -> None:
        """Full scenario: record listing, dynamic record add/delete, stats tracking, and error handling."""
        # 1. Listing and zone status
        response = self.client.get(f'/api/labs/{registry.lab_key}/dns/auth1/zones/example.com./records')
        self.assertEqual(response.status_code, 200)
        owners = {r["owner"] for r in response.json()["records"]}
        self.assertIn("example.com.", owners)
        self.assertIn("web.example.com.", owners)

        response = self.client.get(f'/api/labs/{registry.lab_key}/dns/auth1/zones/example.com./status')
        self.assertEqual(response.status_code, 200)
        status = response.json()["status"]
        self.assertIn("serial", status)
        self.assertEqual(status.get("role"), "master")

        # 2. Add and delete record dynamically
        response = self.client.post(
            f'/api/labs/{registry.lab_key}/dns/auth1/zones/example.com./records',
            json={"owner": "newhost", "type": "A", "data": "10.0.0.99", "ttl": 300},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["owner"], "newhost")

        out = self.client_node.cmd("dig @10.0.3.2 newhost.example.com A +short")
        self.assertEqual(out.strip(), "10.0.0.99")

        response = self.client.request(
            "DELETE",
            f'/api/labs/{registry.lab_key}/dns/auth1/zones/example.com./records',
            json={"owner": "newhost.example.com.", "type": "A", "data": "10.0.0.99"},
        )
        self.assertEqual(response.status_code, 200)

        out = self.client_node.cmd("dig @10.0.3.2 newhost.example.com A")
        self.assertIn("status: NXDOMAIN", out)

        # 3. Query stats tracking and reset
        self.client_node.cmd("dig @10.0.3.2 web.example.com A +short")
        self.client_node.cmd("dig @10.0.3.2 missing.example.com A")

        response = self.client.get(f'/api/labs/{registry.lab_key}/dns/auth1/stats')
        self.assertEqual(response.status_code, 200)
        stats = response.json()["stats"]
        self.assertGreaterEqual(stats.get("server-operation", {}).get("query", 0), 2)
        self.assertGreaterEqual(stats.get("response-code", {}).get("NOERROR", 0), 1)
        self.assertGreaterEqual(stats.get("response-code", {}).get("NXDOMAIN", 0), 1)

        response = self.client.post(f'/api/labs/{registry.lab_key}/dns/auth1/stats/reset')
        self.assertEqual(response.status_code, 200)

        response = self.client.get(f'/api/labs/{registry.lab_key}/dns/auth1/stats')
        stats = response.json()["stats"]
        self.assertEqual(stats.get("server-operation", {}).get("query", 0), 0)

        # 4. CNAME collision rejection
        response = self.client.post(
            f'/api/labs/{registry.lab_key}/dns/auth1/zones/example.com./records',
            json={"owner": "web", "type": "CNAME", "data": "other.example.com.", "ttl": 300},
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("detail", payload)
        self.assertIn("example.com.", payload["zone"])

    @pytest.mark.requires_browser
    def test_record_added_via_web_ui_resolves_via_dig(self) -> None:
        """Submit a record through the DNS modal and resolve it from a client host."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        server = uvicorn.Server(uvicorn.Config(
            create_app(),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        ))
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("live browser server did not start within 10s")
                time.sleep(0.05)

            session_id = next(
                cookie.value for cookie in self.client.cookies.jar
                if cookie.name == "session"
            )
            self.assertIsNotNone(session_id)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context()
                context.add_cookies([{
                    "name": "session",
                    "value": session_id,
                    "url": f"http://127.0.0.1:{port}",
                }])
                page = context.new_page()
                try:
                    page.goto(f"http://127.0.0.1:{port}/")
                    page.wait_for_selector("#app:not(.hidden)")
                    page.wait_for_function("""
                        () => document.querySelector('.node[aria-label*="auth1"]')
                    """)
                    dns_badge = page.locator(".node[aria-label*='auth1'] .service-badge-dns")
                    dns_badge.wait_for()
                    dns_badge.press("Enter")
                    page.wait_for_selector("#dns-modal[style*='flex']")
                    page.wait_for_function(
                        "() => document.querySelector('#dns-zone-select').value === 'example.com.'"
                    )
                    page.locator("#dns-record-owner").fill("newhost")
                    page.locator("#dns-record-data").fill("10.0.0.99")
                    page.locator("#dns-record-submit").click()
                    page.wait_for_function(
                        "() => document.querySelector('#dns-records-tbody').textContent.includes('newhost')"
                    )
                finally:
                    context.close()
                    browser.close()
        finally:
            server.should_exit = True
            server_thread.join(timeout=5)

        out = self.client_node.cmd("dig @10.0.3.2 newhost.example.com A +short")
        self.assertEqual(out.strip(), "10.0.0.99")

