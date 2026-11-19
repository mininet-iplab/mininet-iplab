"""Unit tests for the /api/dhcp routes, with the registry's facade methods patched."""

import unittest
from unittest.mock import AsyncMock, patch

from mniplab.tests.fakes import FAKE_LAB_KEY, FakeTerminalCloserMixin, LoginClientMixin


class DHCPRouteTests(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The /api/dhcp routes hand the registry's answer to the browser as is.

    Each route is a pass-through to one ``SessionRegistry`` facade method,
    so what is pinned here is the wiring: which method each path reaches,
    what the POST model normalises before the call, and that the app-wide
    ValueError handler and the login guard stand in front of all of them.
    """

    ROUTES = (
        ("GET", "/api/labs/fake/dhcp/leases/r1", "get_node_leases", None),
        ("GET", "/api/labs/fake/dhcp/relay/activity/r1", "get_node_relay_activity", None),
        ("GET", "/api/labs/fake/dhcp/pools/r1", "get_node_pools", None),
        ("POST", "/api/labs/fake/dhcp/pools/r1", "set_node_pool",
         {"cidr": "10.50.0.0/24", "start": "10.50.0.1", "end": "10.50.0.2"}),
    )

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_dhcp_web_api_end_to_end_scenario(self) -> None:
        """Full scenario: leases inspection, relay activity, pool manipulation, error handling, and auth."""
        # 1. Leases query (dual-stack v4/v6, relayed flags, DUID, lifetimes)
        with self.subTest("leases query"):
            held_leases = {
                "success": True,
                "v4": [
                    {"ip-address": "10.50.0.100", "hw-address": "aa:bb:cc:dd:ee:ff", "relayed": True},
                    {"ip-address": "10.60.0.100", "hw-address": "aa:bb:cc:dd:ee:00", "relayed": False},
                ],
                "v6": [{
                    "ip-address": "2001:db8:1::101",
                    "duid": "00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9",
                    "preferred-lft": 4500,
                    "valid-lft": 7200,
                }],
            }
            with patch('mniplab.webserver.registry.get_node_leases',
                       new=AsyncMock(return_value=held_leases)):
                response = self.client.get('/api/labs/fake/dhcp/leases/r1')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), held_leases)

        # 2. Relay activity query (v4/v6 forwarding tracking)
        with self.subTest("relay activity query"):
            activity = {"success": True, "activity": [{"source": "10.50.0.1", "server": "10.0.0.2"}]}
            with patch("mniplab.webserver.registry.get_node_relay_activity",
                       new=AsyncMock(return_value=activity)):
                response = self.client.get("/api/labs/fake/dhcp/relay/activity/r1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), activity)

        # 3. Pool inspection
        with self.subTest("pools inspection"):
            pools = {"success": True,
                     "pools": [{"cidr": "10.50.0.0/24", "start": "10.50.0.100", "end": "10.50.0.200"}]}
            with patch('mniplab.webserver.registry.get_node_pools',
                       new=AsyncMock(return_value=pools)):
                response = self.client.get('/api/labs/fake/dhcp/pools/r1')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), pools)

        # 4. Pool update with defaults filled in
        with self.subTest("pool update with defaults"):
            payload_defaults = {"cidr": "10.50.0.0/24", "start": "10.50.0.150", "end": "10.50.0.220"}
            expected_defaults = {"success": True, "pool": payload_defaults, "pools": [payload_defaults]}
            with patch('mniplab.webserver.registry.set_node_pool',
                       new=AsyncMock(return_value=expected_defaults)) as set_node_pool:
                response = self.client.post('/api/labs/fake/dhcp/pools/r1', json=payload_defaults)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), expected_defaults)
            set_node_pool.assert_awaited_once_with(
                FAKE_LAB_KEY, "r1", "10.50.0.0/24", "10.50.0.150", "10.50.0.220",
                lease_time=None, preferred_lifetime=None, router="", dns_server="", reservations=[],
            )

        # 5. IPv4 pool update with lease time options and reservations
        with self.subTest("v4 pool update with options and reservations"):
            payload_v4 = {
                "cidr": "10.50.0.0/24", "start": "10.50.0.150", "end": "10.50.0.220",
                "lease_time": 90, "router": "10.50.0.1", "dns_server": "8.8.8.8",
                "reservations": [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.50.0.50", "hostname": "h1"}],
            }
            with patch('mniplab.webserver.registry.set_node_pool',
                       new=AsyncMock(return_value={"success": True})) as set_node_pool:
                response = self.client.post('/api/labs/fake/dhcp/pools/r1', json=payload_v4)
            self.assertEqual(response.status_code, 200)
            set_node_pool.assert_awaited_once_with(
                FAKE_LAB_KEY, "r1", "10.50.0.0/24", "10.50.0.150", "10.50.0.220",
                lease_time=90, preferred_lifetime=None, router="10.50.0.1", dns_server="8.8.8.8",
                reservations=[{"mac": "aa:bb:cc:dd:ee:ff", "duid": "", "ip": "10.50.0.50", "hostname": "h1"}],
            )

        # 6. IPv6 pool update passing preferred_lifetime
        with self.subTest("v6 pool update with preferred_lifetime"):
            payload_v6 = {
                "cidr": "2001:db8:1::/64", "start": "2001:db8:1::100", "end": "2001:db8:1::200",
                "lease_time": 120, "preferred_lifetime": 60,
            }
            with patch('mniplab.webserver.registry.set_node_pool',
                       new=AsyncMock(return_value={"success": True})) as set_node_pool:
                response = self.client.post('/api/labs/fake/dhcp/pools/r1', json=payload_v6)
            self.assertEqual(response.status_code, 200)
            set_node_pool.assert_awaited_once_with(
                FAKE_LAB_KEY, "r1", "2001:db8:1::/64", "2001:db8:1::100", "2001:db8:1::200",
                lease_time=120, preferred_lifetime=60, router="", dns_server="", reservations=[],
            )

        # 7. Error handling (registry ValueError -> 400 with detail)
        for method, path, facade, body in self.ROUTES:
            with self.subTest(route=f"{method} {path} error"):
                with patch(f"mniplab.webserver.registry.{facade}",
                           new=AsyncMock(side_effect=ValueError("r1 has no DHCP Service attached"))):
                    response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "r1 has no DHCP Service attached")

        # 8. Authentication guard across all routes
        self.client.cookies.clear()
        for method, path, _facade, body in self.ROUTES:
            with self.subTest(route=f"{method} {path} unauthenticated"):
                self.assertEqual(self.client.request(method, path, json=body).status_code, 401)

