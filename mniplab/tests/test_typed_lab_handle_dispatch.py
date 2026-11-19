"""Unit tests verifying typed LabHandle dispatch in SessionRegistry (Ticket 12)."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch
import pytest

from mniplab.labhandle import LabHandle
from mniplab.tests.fakes import FAKE_LAB_KEY, FakeLab
from mniplab.webserver.session import SessionRegistry


@pytest.mark.unit
class TestTypedLabHandleDispatch(unittest.TestCase):
    """Verify that SessionRegistry operations dispatch to typed LabHandle methods."""

    def setUp(self) -> None:
        self.reg = SessionRegistry()
        self.reg.lab_service.lab = FakeLab()
        self.key = FAKE_LAB_KEY
        self.mock_handle = MagicMock(spec=LabHandle)
        # Attach the mock handle to the running lab
        self.reg.lab(self.key).handle = self.mock_handle

    def test_link_status_dispatch(self) -> None:
        self.mock_handle.set_link_status.return_value = {"success": True}
        with patch.object(self.reg, "broadcast_topology_update", return_value=None) as mock_bcast:
            res = asyncio.run(self.reg.disconnect_link(self.key, "h1", "r1"))
            self.assertEqual(res, {"success": True})
            self.mock_handle.set_link_status.assert_called_once_with("h1", "r1", "down")
            mock_bcast.assert_called_once_with(self.key)

    def test_configure_link_dispatch(self) -> None:
        self.mock_handle.configure_link.return_value = {"success": True}
        with patch.object(self.reg, "broadcast_topology_update", return_value=None) as mock_bcast:
            res = asyncio.run(self.reg.link_configure(
                self.key, "h1", "r1", delay="10ms", jitter="2ms", loss="1%", bandwidth="100m", scheduler="tbf"
            ))
            self.assertEqual(res, {"success": True})
            self.mock_handle.configure_link.assert_called_once_with(
                "h1", "r1", delay="10ms", jitter="2ms", loss="1%", bandwidth="100m", scheduler="tbf"
            )
            mock_bcast.assert_called_once_with(self.key)

    def test_network_tests_dispatch(self) -> None:
        self.mock_handle.run_ping.return_value = {"sent": 4, "received": 4}
        res = asyncio.run(self.reg.run_ping(self.key, "h1", "h2", count=5))
        self.assertEqual(res, {"sent": 4, "received": 4})
        self.mock_handle.run_ping.assert_called_once_with("h1", "h2", count=5, ipv6=False)

        self.mock_handle.run_traceroute.return_value = {"hops": []}
        res = asyncio.run(self.reg.run_traceroute(self.key, "h1", "h2", ipv6=True))
        self.assertEqual(res, {"hops": []})
        self.mock_handle.run_traceroute.assert_called_once_with("h1", "h2", ipv6=True, resolve=False)

        self.mock_handle.run_iperf.return_value = {"bandwidth": "100Mbps"}
        res = asyncio.run(self.reg.run_iperf(self.key, "h1", "h2"))
        self.assertEqual(res, {"bandwidth": "100Mbps"})
        self.mock_handle.run_iperf.assert_called_once_with("h1", "h2")

        self.mock_handle.run_iperf3.return_value = {"bandwidth": "1Gbps"}
        res = asyncio.run(self.reg.run_iperf3(self.key, "h1", "h2", seconds=5, protocol="udp", json_output=True))
        self.assertEqual(res, {"bandwidth": "1Gbps"})
        self.mock_handle.run_iperf3.assert_called_once_with(
            "h1", "h2", seconds=5, protocol="udp", json_output=True
        )

    def test_routing_queries_dispatch(self) -> None:
        self.mock_handle.get_node_routes.return_value = {"routes": []}
        res = asyncio.run(self.reg.get_node_routes(self.key, "r1"))
        self.assertEqual(res, {"routes": []})
        self.mock_handle.get_node_routes.assert_called_once_with("r1")

        self.mock_handle.get_node_ospf.return_value = {"neighbors": []}
        res = asyncio.run(self.reg.get_node_ospf(self.key, "r1"))
        self.assertEqual(res, {"neighbors": []})
        self.mock_handle.get_node_ospf.assert_called_once_with("r1")

        self.mock_handle.get_node_bgp.return_value = {"summary": {}}
        res = asyncio.run(self.reg.get_node_bgp(self.key, "r1"))
        self.assertEqual(res, {"summary": {}})
        self.mock_handle.get_node_bgp.assert_called_once_with("r1")

    def test_dhcp_dispatch(self) -> None:
        self.mock_handle.get_node_leases.return_value = {"leases": []}
        res = asyncio.run(self.reg.get_node_leases(self.key, "h1"))
        self.assertEqual(res, {"leases": []})
        self.mock_handle.get_node_leases.assert_called_once_with("h1")

        self.mock_handle.get_node_relay_activity.return_value = {"relayed": 0}
        res = asyncio.run(self.reg.get_node_relay_activity(self.key, "r1"))
        self.assertEqual(res, {"relayed": 0})
        self.mock_handle.get_node_relay_activity.assert_called_once_with("r1")

        self.mock_handle.get_node_pools.return_value = {"pools": []}
        res = asyncio.run(self.reg.get_node_pools(self.key, "h1"))
        self.assertEqual(res, {"pools": []})
        self.mock_handle.get_node_pools.assert_called_once_with("h1")

        self.mock_handle.set_node_pool.return_value = {"pool": {}, "pools": []}
        with patch.object(self.reg.feed(self.key), "broadcast", return_value=None):
            res = asyncio.run(self.reg.set_node_pool(
                self.key, "h1", "10.0.0.0/24", "10.0.0.10", "10.0.0.50",
                lease_time=3600, preferred_lifetime=1800, router="10.0.0.1",
                dns_server="10.0.0.2", reservations=[]
            ))
            self.assertEqual(res, {"pool": {}, "pools": []})
            self.mock_handle.set_node_pool.assert_called_once_with(
                "h1", "10.0.0.0/24", "10.0.0.10", "10.0.0.50",
                lease_time=3600, preferred_lifetime=1800, router="10.0.0.1",
                dns_server="10.0.0.2", reservations=[]
            )

    def test_dns_dispatch(self) -> None:
        self.mock_handle.list_dns_nodes.return_value = [{"node": "dns1"}]
        res = asyncio.run(self.reg.list_dns_nodes(self.key))
        self.assertEqual(res, {"nodes": [{"node": "dns1"}]})
        self.mock_handle.list_dns_nodes.assert_called_once_with()

        self.mock_handle.get_dns_records.return_value = [{"owner": "web"}]
        res = asyncio.run(self.reg.get_dns_records(self.key, "dns1", "example.com."))
        self.assertEqual(res, {"node": "dns1", "zone": "example.com.", "records": [{"owner": "web"}]})
        self.mock_handle.get_dns_records.assert_called_once_with("dns1", "example.com.")

        self.mock_handle.get_dns_zone_status.return_value = {"serial": 1}
        res = asyncio.run(self.reg.get_dns_zone_status(self.key, "dns1", "example.com."))
        self.assertEqual(res, {"serial": 1})
        self.mock_handle.get_dns_zone_status.assert_called_once_with("dns1", "example.com.")

        self.mock_handle.get_dns_transfers.return_value = {"transfers": []}
        res = asyncio.run(self.reg.get_dns_transfers(self.key, "dns1", zone="example.com.", include_notify=True))
        self.assertEqual(res, {"transfers": []})
        self.mock_handle.get_dns_transfers.assert_called_once_with("dns1", zone="example.com.", include_notify=True)

        self.mock_handle.refresh_dns_zone.return_value = {"refreshed": True}
        res = asyncio.run(self.reg.refresh_dns_zone(self.key, "dns1", "example.com."))
        self.assertEqual(res, {"refreshed": True})
        self.mock_handle.refresh_dns_zone.assert_called_once_with("dns1", "example.com.")

        self.mock_handle.retransfer_dns_zone.return_value = {"retransferred": True}
        res = asyncio.run(self.reg.retransfer_dns_zone(self.key, "dns1", "example.com."))
        self.assertEqual(res, {"retransferred": True})
        self.mock_handle.retransfer_dns_zone.assert_called_once_with("dns1", "example.com.")

        self.mock_handle.get_dns_stats.return_value = {"queries": 42}
        res = asyncio.run(self.reg.get_dns_stats(self.key, "dns1"))
        self.assertEqual(res, {"node": "dns1", "stats": {"queries": 42}})
        self.mock_handle.get_dns_stats.assert_called_once_with("dns1")

        self.mock_handle.reset_dns_stats.return_value = None
        res = asyncio.run(self.reg.reset_dns_stats(self.key, "dns1"))
        self.assertEqual(res, {"node": "dns1", "reset": True})
        self.mock_handle.reset_dns_stats.assert_called_once_with("dns1")

        self.mock_handle.set_dns_record.return_value = {"owner": "web"}
        with patch.object(self.reg.feed(self.key), "broadcast", return_value=None):
            res = asyncio.run(self.reg.set_dns_record(
                self.key, "dns1", "example.com.", "web", "A", "10.0.0.1", ttl=300,
                old_owner="old", old_type="A", old_data="10.0.0.2"
            ))
            self.assertEqual(res, {"owner": "web"})
            self.mock_handle.set_dns_record.assert_called_once_with(
                "dns1", "example.com.", "web", "A", "10.0.0.1", ttl=300,
                old_owner="old", old_type="A", old_data="10.0.0.2"
            )

        self.mock_handle.unset_dns_record.return_value = {"owner": "web"}
        with patch.object(self.reg.feed(self.key), "broadcast", return_value=None):
            res = asyncio.run(self.reg.unset_dns_record(
                self.key, "dns1", "example.com.", "web", rtype="A", data="10.0.0.1"
            ))
            self.assertEqual(res, {"owner": "web"})
            self.mock_handle.unset_dns_record.assert_called_once_with(
                "dns1", "example.com.", "web", rtype="A", data="10.0.0.1"
            )

        self.mock_handle.get_dnssec_details.return_value = {"state": "signed"}
        res = asyncio.run(self.reg.get_dnssec_details(self.key, "dns1", "example.com."))
        self.assertEqual(res, {"state": "signed"})
        self.mock_handle.get_dnssec_details.assert_called_once_with("dns1", "example.com.")

        self.mock_handle.trigger_dnssec_rollover.return_value = {"rollover": True}
        with patch.object(self.reg.feed(self.key), "broadcast", return_value=None):
            res = asyncio.run(self.reg.trigger_dnssec_rollover(self.key, "dns1", "example.com.", key_type="ksk"))
            self.assertEqual(res, {"rollover": True})
            self.mock_handle.trigger_dnssec_rollover.assert_called_once_with(
                "dns1", "example.com.", key_type="ksk"
            )

    def test_resolver_dispatch(self) -> None:
        self.mock_handle.get_resolver_cache.return_value = {"entries": [], "metrics": {}}
        res = asyncio.run(self.reg.get_resolver_cache(self.key, "res1"))
        self.assertEqual(res, {"node": "res1", "entries": [], "metrics": {}})
        self.mock_handle.get_resolver_cache.assert_called_once_with("res1")

        self.mock_handle.flush_resolver_cache.return_value = {"flushed": 1}
        res = asyncio.run(self.reg.flush_resolver_cache(self.key, "res1", target="example.com", scope="zone"))
        self.assertEqual(res, {"flushed": 1})
        self.mock_handle.flush_resolver_cache.assert_called_once_with("res1", target="example.com", scope="zone")

        self.mock_handle.get_resolver_access.return_value = [{"subnet": "10.0.0.0/24", "action": "allow"}]
        res = asyncio.run(self.reg.get_resolver_access(self.key, "res1"))
        self.assertEqual(res, {"node": "res1", "rules": [{"subnet": "10.0.0.0/24", "action": "allow"}]})
        self.mock_handle.get_resolver_access.assert_called_once_with("res1")

        self.mock_handle.set_resolver_access.return_value = [{"subnet": "10.0.0.0/24", "action": "allow", "family": 4}]
        with patch.object(self.reg.feed(self.key), "broadcast", return_value=None):
            res = asyncio.run(self.reg.set_resolver_access(
                self.key, "res1", [{"subnet": "10.0.0.0/24", "action": "allow"}]
            ))
            self.assertEqual(res, {"node": "res1", "rules": [{"subnet": "10.0.0.0/24", "action": "allow", "family": 4}]})
            self.mock_handle.set_resolver_access.assert_called_once_with(
                "res1", [{"subnet": "10.0.0.0/24", "action": "allow", "family": 4}]
            )

    def test_speaker_dispatch(self) -> None:
        self.mock_handle.get_speaker.return_value = {"neighbors": []}
        res = asyncio.run(self.reg.get_speaker(self.key, "ex1"))
        self.assertEqual(res, {"neighbors": []})
        self.mock_handle.get_speaker.assert_called_once_with("ex1")

        self.mock_handle.announce_route.return_value = {"announced": True}
        res = asyncio.run(self.reg.announce_route(self.key, "ex1", {"prefix": "10.1.0.0/24"}, neighbor="10.0.0.1"))
        self.assertEqual(res, {"announced": True})
        self.mock_handle.announce_route.assert_called_once_with(
            "ex1", {"prefix": "10.1.0.0/24"}, neighbor="10.0.0.1"
        )

        self.mock_handle.generate_routes.return_value = {"announced": 5}
        res = asyncio.run(self.reg.generate_routes(
            self.key, "ex1", "10.0.0.1", 5, sub_prefixes=2, prefix_seed="10.1.0.0/16", as_path_seed=65001
        ))
        self.assertEqual(res, {"announced": 5})
        self.mock_handle.generate_routes.assert_called_once_with(
            "ex1", "10.0.0.1", 5, sub_prefixes=2, prefix_seed="10.1.0.0/16", as_path_seed=65001
        )

        self.mock_handle.withdraw_route.return_value = {"withdrawn": "10.1.0.0/24"}
        res = asyncio.run(self.reg.withdraw_route(self.key, "ex1", "10.1.0.0/24", withdraw_all=False, neighbor="10.0.0.1"))
        self.assertEqual(res, {"withdrawn": "10.1.0.0/24"})
        self.mock_handle.withdraw_route.assert_called_once_with("ex1", "10.1.0.0/24", neighbor="10.0.0.1")

        self.mock_handle.withdraw_all_routes.return_value = {"withdrawn": "all"}
        res = asyncio.run(self.reg.withdraw_route(self.key, "ex1", None, withdraw_all=True, neighbor="10.0.0.1"))
        self.assertEqual(res, {"withdrawn": "all"})
        self.mock_handle.withdraw_all_routes.assert_called_once_with("ex1", neighbor="10.0.0.1")

    def test_switch_dispatch(self) -> None:
        self.mock_handle.get_switch_vlans.return_value = {"ports": {}}
        res = asyncio.run(self.reg.get_switch_vlans(self.key, "s1"))
        self.assertEqual(res, {"ports": {}})
        self.mock_handle.get_switch_vlans.assert_called_once_with("s1")

        self.mock_handle.set_switch_port_vlan.return_value = {"port": "s1-eth1"}
        with patch.object(self.reg, "broadcast_topology_update", return_value=None) as mock_bcast:
            res = asyncio.run(self.reg.set_switch_port_vlan(
                self.key, "s1", "s1-eth1", "access", vlan=10, trunks=None, native=None
            ))
            self.assertEqual(res, {"port": "s1-eth1"})
            self.mock_handle.set_switch_port_vlan.assert_called_once_with(
                "s1", "s1-eth1", "access", vlan=10, trunks=None, native=None
            )
            mock_bcast.assert_called_once_with(self.key)
