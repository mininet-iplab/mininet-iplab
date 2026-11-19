"""Privileged execution of a generated Authoring Lab Example."""

import json
import os
import shlex
import shutil
import tempfile
import time
import unittest
from typing import Any, Dict
from unittest.mock import patch

from mniplab.dhcp.leases import LeaseQueryService
from mniplab.dhcp.relay_activity import RelayActivityQueryService
from mniplab.exabgp import make_v4_peer_routes, make_v6_peer_routes
from mniplab.persistent_config import CONFIG_COPY_ENV
from mniplab.tests.authoring_fixtures import dhcp_pool
from mniplab.tests.labs import (
    H1_MAC, LabCapture, LabCase, authored_dhcp_test_lab, dhcp_ra_config, fields,
    first as first_label, load_builder, preset,
)
from mniplab.labhandle import LocalLabHandle
from mniplab.utils import wait_until
from mniplab.webserver.authoring import AuthoringRepository
from mniplab.webserver.authoring_keep import apply_keep, review_keep, snapshot_preview


def command(stage, text, timeout=5, may_fail=False):
    """One Startup command in the definition shape."""
    return {"stage": stage, "command": text, "timeout": timeout, "may_fail": may_fail}


def dhcp_type(packet: Dict[str, Any]) -> str:
    """The DHCP message type a packet's option 53 carries, e.g. ``Discover``."""
    label = first_label(packet, "dhcp.option.dhcp")
    return label.split(": ", 1)[1].rsplit(" (", 1)[0] if label else ""


def is_dhcp(packet: Dict[str, Any]) -> bool:
    """Whether a packet is a DHCP message."""
    return bool(dhcp_type(packet))


class AuthoredLabExecutionTests(LabCase):
    """A canonical Host/Link recipe generates and starts a real Mininet Lab."""

    def test_generated_builder_applies_dual_stack_addresses_gateways_and_interface_ports(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = AuthoringRepository(root)
            draft = repository.create()
            draft["address_family"] = "dual"
            draft["catalog"]["name"] = "Generated dual-stack Lab"
            draft["topology"] = {
                "nodes": [
                    {"id": "host-a", "name": "h1", "type": "host",
                     "ipv4_gateway": "", "ipv6_gateway": "2001:db8::2", "startup": []},
                    {"id": "host-b", "name": "h2", "type": "host",
                     "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
                ],
                "links": [{"id": "link-a", "ends": [
                    {"node_id": "host-a", "interface_id": "if-000000000001", "port": 3,
                     "ipv4": "192.0.2.1/24", "ipv6": "2001:db8::1/64"},
                    {"node_id": "host-b", "interface_id": "if-000000000002", "port": 5,
                     "ipv4": "192.0.2.2/24", "ipv6": "2001:db8::2/64"},
                ]}],
            }
            draft["layout"]["nodes"] = {
                "host-a": {"x": 120, "y": 300},
                "host-b": {"x": 780, "y": 300},
            }
            saved = repository.save(draft["id"], draft["revision"], draft)
            self.assertEqual(saved["revision"], 1)

            builder_path = os.path.join(root, "lab.py")
            repository.write_builder(saved, builder_path)
            network = self.start(load_builder(builder_path).build_network())

            first = network.get("h1").intf("h1-eth3")
            second = network.get("h2").intf("h2-eth5")
            self.assertEqual(first.IP(), "192.0.2.1")
            self.assertEqual(first.IP6(), "2001:db8::1")
            self.assertEqual(second.IP(), "192.0.2.2")
            self.assertEqual(second.IP6(), "2001:db8::2")
            self.assertEqual(network.get("h1").ports[first], 3)
            self.assertEqual(network.get("h2").ports[second], 5)
            self.assertIn("default via 2001:db8::2",
                          network.get("h1").cmd("ip -6 route show default"))

    def test_generated_gateways_leave_by_the_lowest_port_beside_a_dhcp_client_on_another(self) -> None:
        """The DHCP client Link comes first in the recipe and takes the higher port."""
        def host(node_id: str, ipv4_gateway: str = "", ipv6_gateway: str = "") -> dict:
            return {"id": node_id, "name": node_id, "type": "host", "ipv4_gateway": ipv4_gateway,
                    "ipv6_gateway": ipv6_gateway, "startup": []}

        network = self.start_draft({
            "nodes": [host("h1", "192.0.2.2", "2001:db8::2"), host("h2"), host("h3")],
            "links": [
                {"id": "dhcp", "ends": [
                    {"node_id": "h1", "interface_id": "if-000000000001", "port": 1,
                     "ipv4": "", "ipv6": "", "dhcp4": True, "dhcp6": True},
                    {"node_id": "h3", "interface_id": "if-000000000002", "port": 0,
                     "ipv4": "198.51.100.1/24", "ipv6": "2001:db8:1::1/64"},
                ]},
                {"id": "static", "ends": [
                    {"node_id": "h1", "interface_id": "if-000000000003", "port": 0,
                     "ipv4": "192.0.2.1/24", "ipv6": "2001:db8::1/64"},
                    {"node_id": "h2", "interface_id": "if-000000000004", "port": 0,
                     "ipv4": "192.0.2.2/24", "ipv6": "2001:db8::2/64"},
                ]},
            ],
        })

        h1 = network.get("h1")
        self.assertIn("default via 192.0.2.2 dev h1-eth0", h1.cmd("ip route show default"))
        self.assertIn("default via 2001:db8::2 dev h1-eth0", h1.cmd("ip -6 route show default"))

    def test_vlan_access_trunk_and_router_subinterfaces_route_and_isolate_hosts(self) -> None:
        nodes = [
            {"id": "h10", "name": "h1", "type": "host", "ipv4_gateway": "192.168.10.1",
             "ipv6_gateway": "", "startup": []},
            {"id": "h20", "name": "h2", "type": "host", "ipv4_gateway": "192.168.20.1",
             "ipv6_gateway": "", "startup": []},
            {"id": "hn", "name": "h3", "type": "host", "ipv4_gateway": "",
             "ipv6_gateway": "", "startup": []},
            {"id": "hu", "name": "h4", "type": "host", "ipv4_gateway": "",
             "ipv6_gateway": "", "startup": []},
            {"id": "hm", "name": "h5", "type": "host", "ipv4_gateway": "",
             "ipv6_gateway": "", "startup": []},
            {"id": "s", "name": "s1", "type": "switch"},
            {"id": "r", "name": "r1", "type": "router", "protocols": [],
             "frr_config": "", "fast_timers": False,
             "startup": [], "subinterfaces": [
                 {"id": "sub10", "parent_interface_id": "if-000000000006",
                  "vlan": 10, "ipv4": "192.168.10.1/24", "ipv6": ""},
                 {"id": "sub20", "parent_interface_id": "if-000000000006",
                  "vlan": 20, "ipv4": "192.168.20.1/24", "ipv6": "2001:db8:20::1/64"},
             ]},
        ]
        links = [
            {"id": "l1", "ends": [
                {"node_id": "h10", "interface_id": "if-000000000001", "port": 0,
                 "ipv4": "192.168.10.2/24", "ipv6": ""},
                {"node_id": "s", "interface_id": "if-000000000002", "port": 1,
                 "ipv4": "", "ipv6": "", "vlan": {"mode": "access", "vlan": 10}},
            ]},
            {"id": "l2", "ends": [
                {"node_id": "h20", "interface_id": "if-000000000003", "port": 0,
                 "ipv4": "192.168.20.2/24", "ipv6": ""},
                {"node_id": "s", "interface_id": "if-000000000004", "port": 2,
                 "ipv4": "", "ipv6": "", "vlan": {"mode": "access", "vlan": 20}},
            ]},
            {"id": "l3", "ends": [
                {"node_id": "s", "interface_id": "if-000000000005", "port": 3,
                 "ipv4": "", "ipv6": "", "vlan": {"mode": "trunk", "trunks": [10, 20], "native": None}},
                {"node_id": "r", "interface_id": "if-000000000006", "port": 0,
                 "ipv4": "", "ipv6": ""},
            ]},
            # Two Hosts that send no tags onto trunk ports: only the native VLAN admits them.
            {"id": "l4", "ends": [
                {"node_id": "hn", "interface_id": "if-000000000007", "port": 0,
                 "ipv4": "192.168.10.4/24", "ipv6": ""},
                {"node_id": "s", "interface_id": "if-000000000008", "port": 4,
                 "ipv4": "", "ipv6": "", "vlan": {"mode": "trunk", "trunks": [10, 20], "native": 10}},
            ]},
            {"id": "l5", "ends": [
                {"node_id": "hu", "interface_id": "if-000000000009", "port": 0,
                 "ipv4": "192.168.10.5/24", "ipv6": ""},
                {"node_id": "s", "interface_id": "if-00000000000a", "port": 5,
                 "ipv4": "", "ipv6": "", "vlan": {"mode": "trunk", "trunks": [10, 20], "native": None}},
            ]},
            # A second access port on VLAN 10, which the test moves to VLAN 20.
            {"id": "l6", "ends": [
                {"node_id": "hm", "interface_id": "if-00000000000b", "port": 0,
                 "ipv4": "192.168.10.6/24", "ipv6": ""},
                {"node_id": "s", "interface_id": "if-00000000000c", "port": 6,
                 "ipv4": "", "ipv6": "", "vlan": {"mode": "access", "vlan": 10}},
            ]},
        ]
        network = self.start_draft({"nodes": nodes, "links": links},
                                   catalog={"name": "VLAN lesson"})
        self.assertIn("192.168.10.1", network.get("r1").cmd("ip -4 addr show dev r1-eth0.10"))
        self.assertIn("192.168.20.1", network.get("r1").cmd("ip -4 addr show dev r1-eth0.20"))
        self.assertIn("2001:db8:20::1", network.get("r1").cmd("ip -6 addr show dev r1-eth0.20"))
        self.assertIn(" 0% packet loss", network.get("h1").cmd("ping -c2 -W1 192.168.20.2"))
        # The native VLAN carries untagged frames as VLAN 10; a trunk without one drops them.
        self.assertIn(" 0% packet loss", network.get("h3").cmd("ping -c2 -W1 192.168.10.2"))
        self.assertIn(" 0 received", network.get("h4").cmd("ping -c2 -W1 192.168.10.2"))
        # Giving h4's trunk the native VLAN is the only change that lets it through;
        # OVS's cached flows and h4's failed ARP lookup for h1 are cleared so the
        # next ping meets the new VLAN and asks again.
        network.get("s1").set_trunk("s1-eth5", [10, 20], native=10)
        network.get("s1").cmd("ovs-appctl revalidator/purge")
        network.get("h4").cmd("ip neigh flush all")
        self.assertIn(" 0% packet loss", network.get("h4").cmd("ping -c2 -W1 192.168.10.2"))
        # Two access ports on one VLAN switch between themselves; moving one to
        # another VLAN cuts its Host off, though it keeps the same Subnet. Until
        # OVS revalidates, its cached flow would still carry h5's ping to h1.
        self.assertIn(" 0% packet loss", network.get("h5").cmd("ping -c2 -W1 192.168.10.2"))
        network.get("s1").set_access("s1-eth6", 20)
        network.get("s1").cmd("ovs-appctl revalidator/purge")
        self.assertIn(" 0 received", network.get("h5").cmd("ping -c2 -W1 192.168.10.2"))

    def test_container_host_uses_its_image_and_reaches_a_host(self) -> None:
        network = self.start_draft({
            "nodes": [
                {"id": "h", "name": "h1", "type": "host", "ipv4_gateway": "",
                 "ipv6_gateway": "", "startup": []},
                {"id": "d", "name": "d1", "type": "container", "image": "alpine:3.20",
                 "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
            ],
            "links": [{"id": "h-d", "ends": [
                {"node_id": "h", "interface_id": "if-000000000001", "port": 0,
                 "ipv4": "10.0.0.1/24", "ipv6": ""},
                {"node_id": "d", "interface_id": "if-000000000002", "port": 0,
                 "ipv4": "10.0.0.2/24", "ipv6": ""},
            ]}],
        }, catalog={"name": "Container Test Lab"})
        self.assertEqual(network.get("d1").dimage, "alpine:3.20")
        self.assertIn(" 0% packet loss", network.get("h1").cmd("ping -c2 -W1 10.0.0.2"))

    def test_initial_link_condition_applies_to_real_interfaces(self) -> None:
        interface_ids = (f"if-{number:012x}" for number in range(1, 100))

        def conditioned_link(first, second, subnet, **condition):
            return {"id": f"{first}-{second}", "ends": [
                {"node_id": first, "interface_id": next(interface_ids), "port": 0,
                 "ipv4": f"{subnet}.1/24", "ipv6": ""},
                {"node_id": second, "interface_id": next(interface_ids), "port": 0,
                 "ipv4": f"{subnet}.2/24", "ipv6": ""},
            ], "condition": {"status": "up", "delay": None, "jitter": None,
                             "loss": None, **condition}}

        network = self.start_draft({"nodes": [
            {"id": name, "name": name, "type": "host", "ipv4_gateway": "",
             "ipv6_gateway": "", "startup": []}
            for name in ("h1", "h2", "h3", "h4", "h5", "h6")
        ], "links": [
            conditioned_link("h1", "h2", "192.0.2", status="down", delay="10ms", jitter="2ms",
                             loss="5%", bandwidth=100, scheduler="htb"),
            conditioned_link("h3", "h4", "192.0.3", bandwidth=50, scheduler="tbf"),
            conditioned_link("h5", "h6", "192.0.4", bandwidth=50, scheduler="hfsc"),
        ]})
        condition = network.link_conditions.get("h1", "h2")
        self.assertEqual(condition.status, "down")
        self.assertEqual(condition.delay, "10ms")
        self.assertEqual(condition.loss, "5%")
        self.assertEqual(condition.scheduler, "htb")
        self.assertIn("DOWN", network.get("h1").cmd("ip -o link show dev h1-eth0"))
        for first, second, scheduler in (("h3", "h4", "tbf"), ("h5", "h6", "hfsc")):
            self.assertEqual(network.link_conditions.get(first, second).scheduler, scheduler)
            for host in (first, second):
                self.assertIn(f"qdisc {scheduler} ", network.get(host).cmd(
                    f"tc qdisc show dev {host}-eth0"), host)

    def test_speaker_global_and_neighbor_generators_and_rows_reach_their_routers(self) -> None:
        routers = []
        for index in (1, 2):
            peer = f"10.0.{index}.2"
            routers.append({"id": f"r{index}", "name": f"r{index}", "type": "router",
                "protocols": ["BGP"], "fast_timers": False,
                "frr_config": "router bgp 65000\n bgp router-id 10.0.%d.1\n"
                              " no bgp ebgp-requires-policy\n"
                              " neighbor %s remote-as 65001\n"
                              " neighbor %s timers connect 1\n"
                              " address-family ipv4 unicast\n"
                              "  neighbor %s activate\n exit-address-family\n" %
                              (index, peer, peer, peer), "startup": []})
        speaker = {"id": "sp", "name": "sp1", "type": "speaker", "local_as": 65001,
            "route_generator": {"count": 3, "sub_prefixes": 2, "prefix_seed": 42, "as_path_seed": 88},
            "routes": [{"prefix": "203.0.113.0/24", "next_hop": "10.0.1.2"}],
            "neighbors": [
                {"peer": "10.0.1.1", "local_address": "10.0.1.2",
                 "local_as": 65001, "peer_as": 65000, "hold_time": None, "routes": []},
                {"peer": "10.0.2.1", "local_address": "10.0.2.2",
                 "local_as": 65001, "peer_as": 65000, "hold_time": None,
                 "route_generator": {"count": 2, "sub_prefixes": 0, "prefix_seed": 43, "as_path_seed": 84},
                 "routes": [{"prefix": "198.51.100.0/24", "next_hop": "10.0.2.2"}]},
            ], "startup": []}
        links = []
        for index in (1, 2):
            links.append({"id": f"sp-r{index}", "ends": [
                {"node_id": "sp", "interface_id": f"if-{2*index-1:012d}",
                 "port": index - 1, "ipv4": f"10.0.{index}.2/24", "ipv6": ""},
                {"node_id": f"r{index}", "interface_id": f"if-{2*index:012d}",
                 "port": 0, "ipv4": f"10.0.{index}.1/24", "ipv6": ""},
            ]})
        network = self.start_draft({"nodes": [speaker, *routers], "links": links})
        self.assertTrue(wait_until(lambda: "203.0.113.0/24" in network.get("r1").cmd(
            'vtysh -c "show bgp ipv4 unicast"'), 20))
        self.assertTrue(wait_until(lambda: "198.51.100.0/24" in network.get("r2").cmd(
            'vtysh -c "show bgp ipv4 unicast"'), 20))
        self.assertNotIn("198.51.100.0/24", network.get("r1").cmd(
            'vtysh -c "show bgp ipv4 unicast"'))
        observed = LocalLabHandle(network).get_speaker("sp1")
        for neighbor in observed["neighbors"]:
            expected = "203.0.113.0/24" if neighbor["address"] == "10.0.1.1" else "198.51.100.0/24"
            index = 1 if neighbor["address"] == "10.0.1.1" else 2
            generated = make_v4_peer_routes(
                num_prefixes=3 if index == 1 else 2, local_asn=65001,
                next_hop=f"10.0.{index}.2", prefix_seed=42 if index == 1 else 43,
                as_path_seed=88 if index == 1 else 84,
                num_sub_prefixes=2 if index == 1 else 0,
            )
            prefixes = {route.prefix for route in generated} | {expected}
            self.assertEqual({route["prefix"] for route in neighbor["announced"]}, prefixes)
            self.assertTrue(wait_until(lambda: all(prefix in network.get(f"r{index}").cmd(
                'vtysh -c "show bgp ipv4 unicast"') for prefix in prefixes), 20))
            self.assertTrue(all(route["next_hop"] == f"10.0.{index}.2"
                                for route in neighbor["announced"]))

    def test_an_ipv6_neighbor_generator_reaches_its_router(self) -> None:
        router = {"id": "r1", "name": "r1", "type": "router", "protocols": ["BGP"],
                  "fast_timers": False, "startup": [],
                  "frr_config": "router bgp 65000\n bgp router-id 10.0.1.1\n"
                                " no bgp ebgp-requires-policy\n"
                                " neighbor 2001:db8:1::2 remote-as 65001\n"
                                " neighbor 2001:db8:1::2 timers connect 1\n"
                                " address-family ipv6 unicast\n"
                                "  neighbor 2001:db8:1::2 activate\n"
                                " exit-address-family\n"}
        speaker = {"id": "sp", "name": "sp1", "type": "speaker", "local_as": 65001,
                   "routes": [], "startup": [], "neighbors": [
                       {"peer": "2001:db8:1::1", "local_address": "2001:db8:1::2",
                        "local_as": 65001, "peer_as": 65000, "hold_time": 9,
                        "route_generator": {"count": 3, "sub_prefixes": 0,
                                            "prefix_seed": 7, "as_path_seed": 8},
                        "routes": []}]}
        network = self.start_draft({"nodes": [speaker, router], "links": [{"id": "sp-r1", "ends": [
            {"node_id": "sp", "interface_id": "if-000000000001", "port": 0,
             "ipv4": "", "ipv6": "2001:db8:1::2/64"},
            {"node_id": "r1", "interface_id": "if-000000000002", "port": 0,
             "ipv4": "", "ipv6": "2001:db8:1::1/64"},
        ]}]}, address_family="ipv6")
        r1 = network.get("r1")

        generated = make_v6_peer_routes(num_prefixes=3, local_asn=65001,
                                        next_hop="2001:db8:1::2", prefix_seed=7,
                                        as_path_seed=8, num_sub_prefixes=0)
        self.assertTrue(wait_until(lambda: all(route.prefix in r1.cmd(
            'vtysh -c "show bgp ipv6 unicast"') for route in generated), 20))
        # FRR proposes its 180 s default; the Neighbor's 9 s is the lower.
        session = json.loads(r1.cmd('vtysh -c "show bgp neighbor 2001:db8:1::2 json"'))
        self.assertEqual(session["2001:db8:1::2"]["bgpState"], "Established")
        self.assertEqual(session["2001:db8:1::2"]["bgpTimerHoldTimeMsecs"], 9000)


class AuthoredRouterLabTests(LabCase):
    """Routers, Seeds, gateways and Startup commands in a real Lab."""

    def _root(self) -> str:
        """A temporary directory removed after the Lab stops, not before."""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def _builder(self, root, nodes, links, persistent_config=False):
        repository = AuthoringRepository(root)
        draft = repository.create()
        draft["address_family"] = "dual"
        draft["persistent_config"] = persistent_config
        draft["topology"] = {"nodes": nodes, "links": links}
        saved = repository.save(draft["id"], draft["revision"], draft)
        path = os.path.join(root, "published", "lab.py")
        repository.write_builder(saved, path)
        return path

    @staticmethod
    def _router(name, frr_config, startup=()):
        return {"id": name, "name": name, "type": "router", "protocols": ["OSPF"],
                "frr_config": frr_config, "fast_timers": True,
                "startup": list(startup)}

    @staticmethod
    def _link(link_id, first, second, a, b, port_a=0, port_b=0):
        return {"id": link_id, "ends": [
            {"node_id": first, "interface_id": f"if-{a:012x}", "port": port_a,
             "ipv4": f"10.0.{a}.1/24", "ipv6": ""},
            {"node_id": second, "interface_id": f"if-{b:012x}", "port": port_b,
             "ipv4": f"10.0.{a}.2/24", "ipv6": ""},
        ]}

    def test_routers_start_with_startup_stages_gateways_and_an_isolated_seed(self) -> None:
        ospf = "router ospf\n network 10.0.0.0/8 area 0\n"
        nodes = [
            self._router("r1", ospf, [
                command("before-daemons", "test ! -e /var/run/frr/zebra.pid"),
                command("before-daemons", "ip addr add 10.9.9.9/32 dev lo"),
                command("after-start", "test -e /var/run/frr/zebra.pid"),
            ]),
            self._router("r2", ospf),
            {"id": "h1", "name": "h1", "type": "host", "ipv4_gateway": "10.0.2.1",
             "ipv6_gateway": "", "startup": [
                 command("after-start", "ping -c1 -W1 192.0.2.99", may_fail=True),
             ]},
        ]
        links = [self._link("l1", "r1", "r2", 1, 101),
                 self._link("l2", "r2", "h1", 2, 102, port_a=1)]
        root = self._root()
        path = self._builder(root, nodes, links, persistent_config=True)
        seed = os.path.join(root, "published", "frr-config", "r1", "frr.conf")
        with open(seed, encoding="utf-8") as source:
            seed_text = source.read()
        copy_dir = os.path.join(root, "preview-copy")
        with patch.dict(os.environ, {CONFIG_COPY_ENV: copy_dir}):
            network = self.start(load_builder(path).build_network())

        r1, h1 = network.get("r1"), network.get("h1")
        self.assertIn("10.9.9.9/32", r1.cmd("ip -4 addr show dev lo"))
        self.assertIn("default via 10.0.2.1", h1.cmd("ip route show default"))
        self.assertEqual(len(network.startup_warnings), 1)
        self.assertIn("h1", network.startup_warnings[0])
        self.assertIn("ospfd", r1.cmd("pgrep -a ospfd"))
        # zebra learns the Interface shortly after the daemons start.
        deadline = time.monotonic() + 10
        timers = ""
        while "Hello 1s, Dead 3s" not in timers and time.monotonic() < deadline:
            time.sleep(0.5)
            timers = r1.cmd('vtysh -c "show ip ospf interface r1-eth0"')
        self.assertIn("Hello 1s, Dead 3s", timers)

        r1.cmd("vtysh -c 'configure terminal' -c 'router ospf' "
               "-c 'ospf router-id 9.9.9.9' -c 'end' -c 'write memory'")
        with open(os.path.join(copy_dir, "r1", "frr.conf"), encoding="utf-8") as source:
            self.assertIn("router-id 9.9.9.9", source.read())
        with open(seed, encoding="utf-8") as source:
            self.assertEqual(source.read(), seed_text)

    def test_a_vrf_holds_its_interfaces_and_their_routes_apart(self) -> None:
        router = {"id": "r1", "name": "r1", "type": "router", "protocols": [],
                  "frr_config": "", "fast_timers": False, "startup": [],
                  "vrfs": [{"name": "vrf-a", "table": 10}]}
        hosts = [{"id": name, "name": name, "type": "host", "ipv4_gateway": gateway,
                  "ipv6_gateway": "", "startup": []}
                 for name, gateway in (("h1", "10.0.1.1"), ("h2", "10.0.2.1"))]
        links = [self._link("l1", "r1", "h1", 1, 101),
                 self._link("l2", "r1", "h2", 2, 102, port_a=1)]
        links[0]["ends"][0]["vrf"] = "vrf-a"
        network = self.start_draft({"nodes": [router, *hosts], "links": links})
        r1, h1 = network.get("r1"), network.get("h1")

        self.assertIn("master vrf-a", r1.cmd("ip link show r1-eth0"))
        self.assertNotIn("master", r1.cmd("ip link show r1-eth1"))
        self.assertIn("10.0.1.0/24", r1.cmd("ip route show vrf vrf-a"))
        self.assertNotIn("10.0.2.0/24", r1.cmd("ip route show vrf vrf-a"))
        self.assertNotIn("10.0.1.0/24", r1.cmd("ip route show"))
        self.assertIn(" 0% packet loss", h1.cmd("ping -c2 -W1 10.0.1.1"))
        self.assertIn("100% packet loss", h1.cmd("ping -c2 -W1 10.0.2.2"))


class AuthoredDHCPLabTests(LabCase):
    """The DHCP Test Lab, authored, with the Service each test attaches."""

    @staticmethod
    def _node(topology, name):
        return next(node for node in topology["nodes"] if node["name"] == name)

    def test_ipv4_pools_lease_each_subnet_and_an_unserved_subnet_gets_none(self) -> None:
        topology = authored_dhcp_test_lab()
        self._node(topology, "r1")["dhcp"] = {"pools": [
            dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200", router="10.50.0.1"),
            dhcp_pool("10.51.0.0/24", "10.51.0.100", "10.51.0.200"),
        ]}
        network = self.start_draft(topology)
        h1, h2, h3 = network.get("h1"), network.get("h2"), network.get("h3")

        self.assertNotIn("inet ", h1.cmd("ip -4 addr show dev h1-eth0"))
        output = h1.cmd("dhcpcd -1 -B -d -4 h1-eth0")
        self.assertIn("10.50.0.100", h1.cmd("ip -4 addr show dev h1-eth0"), output)
        self.assertIn("10.50.0.1", h1.cmd("ip route show default"))

        output = h2.cmd("dhcpcd -1 -B -d -4 h2-eth0")
        self.assertIn("10.51.0.100", h2.cmd("ip -4 addr show dev h2-eth0"), output)

        h3.cmd("timeout 3 dhcpcd -1 -B -d -4 h3-eth0")
        self.assertNotIn("inet ", h3.cmd("ip -4 addr show dev h3-eth0"))

    def test_reservation_names_a_pinned_mac_and_unpinned_hosts_get_random_macs(self) -> None:
        topology = authored_dhcp_test_lab()
        self._node(topology, "r1")["dhcp"] = {"pools": [
            dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200", lease_time=120,
                          dns_server="192.0.2.53", reservations=[
                              {"ip": "10.50.0.50", "mac": H1_MAC, "duid": "", "hostname": "h1"}]),
        ]}
        network = self.start_draft(topology)
        h1 = network.get("h1")

        self.assertEqual(h1.intf("h1-eth0").MAC(), H1_MAC)
        output = h1.cmd("dhcpcd -1 -B -d -4 h1-eth0")
        self.assertIn("10.50.0.50", h1.cmd("ip -4 addr show dev h1-eth0"), output)
        self.assertIn("192.0.2.53", h1.cmd("cat /etc/resolv.conf"))
        # Without autoSetMacs Mininet leaves each unpinned Interface its
        # kernel-chosen random address, not the 00:00:00:00:00:0N sequence.
        unpinned = {network.get(name).intf(f"{name}-eth0").MAC() for name in ("h2", "h3", "hr")}
        self.assertFalse(any(mac.startswith("00:00:00:00:00:") for mac in unpinned), unpinned)

    def test_managed_router_advertisement_in_frr_text_starts_a_dhcpv6_lease(self) -> None:
        topology = authored_dhcp_test_lab()
        r1 = self._node(topology, "r1")
        r1["frr_config"] = dhcp_ra_config("r1-eth3", "2001:db8:1::/64")
        r1["dhcp"] = {"pools": [dhcp_pool(
            "2001:db8:1::/64", "2001:db8:1::100", "2001:db8:1::200",
            lease_time=600, preferred_lifetime=120, dns_server="2001:db8:1::1")]}
        network = self.start_draft(topology)
        h6 = network.get("h6")

        self.assertNotIn("2001:db8:1:", h6.cmd("ip -6 addr show dev h6-eth0 scope global"))
        output = h6.cmd("dhcpcd -1 -B -d -6 h6-eth0")
        self.assertIn("2001:db8:1::100", h6.cmd("ip -6 addr show dev h6-eth0 scope global"), output)
        self.assertIn("2001:db8:1::1", h6.cmd("cat /etc/resolv.conf"))

    def test_reservation_by_duid_gives_a_dhcpv6_client_its_pinned_address(self) -> None:
        topology = authored_dhcp_test_lab()
        r1 = self._node(topology, "r1")
        r1["frr_config"] = dhcp_ra_config("r1-eth3", "2001:db8:1::/64")
        # A client with a pinned MAC and `duid ll` has a DUID the Reservation can name.
        mac = "aa:bb:cc:dd:ee:06"
        topology["links"][3]["ends"][1]["mac"] = mac
        r1["dhcp"] = {"pools": [dhcp_pool(
            "2001:db8:1::/64", "2001:db8:1::100", "2001:db8:1::200",
            lease_time=600, preferred_lifetime=120, dns_server="2001:db8:1::1",
            reservations=[{"ip": "2001:db8:1::50", "mac": "",
                           "duid": f"00:03:00:01:{mac}", "hostname": "h6"}])]}
        network = self.start_draft(topology)
        h6 = network.get("h6")

        with tempfile.NamedTemporaryFile("w", suffix=".conf") as config:
            config.write("duid ll\n")
            config.flush()
            output = h6.cmd(f"dhcpcd -f {shlex.quote(config.name)} -1 -B -d -6 h6-eth0")
        addresses = h6.cmd("ip -6 addr show dev h6-eth0 scope global")
        self.assertIn("2001:db8:1::50", addresses, output)
        self.assertNotIn("2001:db8:1::100", addresses)

    def test_dhcpv6_starts_only_as_the_router_advertisement_flags_say(self) -> None:
        topology = authored_dhcp_test_lab()
        r1, r2 = self._node(topology, "r1"), self._node(topology, "r2")
        r1["frr_config"] = ("interface r1-eth3\n"
                            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
                            " ipv6 nd other-config-flag\n"
                            " no ipv6 nd suppress-ra\n")
        r1["dhcp"] = {"pools": [dhcp_pool(
            "2001:db8:1::/64", "2001:db8:1::100", "2001:db8:1::200",
            dns_server="2001:db8:1::1")]}
        # r2 serves hr's link but sends no Router Advertisement at all.
        r2["dhcp"] = {"pools": [dhcp_pool(
            "2001:db8:70::/64", "2001:db8:70::100", "2001:db8:70::200")]}
        network = self.start_draft(topology)
        h6, hr = network.get("h6"), network.get("hr")

        # The other-config flag asks for options only: a name server, no address.
        output = h6.cmd("timeout 15 dhcpcd -1 -B -d -t 10 -6 h6-eth0")
        self.assertIn("2001:db8:1::1", h6.cmd("cat /etc/resolv.conf"), output)
        self.assertNotIn("2001:db8:1:", h6.cmd("ip -6 addr show dev h6-eth0 scope global"))

        # No Router Advertisement, so the client never asks the Pool.
        hr.cmd("timeout 8 dhcpcd -1 -B -t 5 -6 hr-eth0")
        self.assertNotIn("2001:db8:70:", hr.cmd("ip -6 addr show dev hr-eth0 scope global"))

    def test_a_relay_whose_servers_are_unreachable_leaves_its_client_bare(self) -> None:
        topology = authored_dhcp_test_lab()
        r2 = self._node(topology, "r2")
        r2["frr_config"] = dhcp_ra_config("r2-eth1", "2001:db8:70::/64")
        r2["relay"] = {"subnets": ["10.70.0.0/24", "2001:db8:70::/64"],
                       "servers": ["10.99.0.1", "2001:db8:99::1"]}
        network = self.start_draft(topology)
        hr = network.get("hr")

        hr.cmd("timeout 6 dhcpcd -1 -B -t 4 -4 hr-eth0")
        self.assertNotIn("inet ", hr.cmd("ip -4 addr show dev hr-eth0"))
        hr.cmd("timeout 6 dhcpcd -1 -B -t 4 -6 hr-eth0")
        self.assertNotIn("2001:db8:70:", hr.cmd("ip -6 addr show dev hr-eth0 scope global"))

    def test_two_services_on_one_subnet_race_and_the_client_takes_one_lease(self) -> None:
        def server(name, start, end):
            return {"id": name, "name": name, "type": "host", "ipv4_gateway": "",
                    "ipv6_gateway": "", "startup": [],
                    "dhcp": {"pools": [dhcp_pool("10.60.0.0/24", start, end)]}}

        topology = {"nodes": [
            {"id": "sw1", "name": "sw1", "type": "switch"},
            server("s1", "10.60.0.100", "10.60.0.150"),
            server("s2", "10.60.0.151", "10.60.0.200"),
            {"id": "hc", "name": "hc", "type": "host", "ipv4_gateway": "",
             "ipv6_gateway": "", "startup": []},
        ], "links": [
            {"id": f"sw1-{name}", "ends": [
                {"node_id": "sw1", "interface_id": f"if-{port * 2 - 1:012x}", "port": port,
                 "ipv4": "", "ipv6": ""},
                {"node_id": name, "interface_id": f"if-{port * 2:012x}", "port": 0,
                 "ipv4": address, "ipv6": "", **({"dhcp4": True} if name == "hc" else {})}]}
            for port, name, address in ((1, "s1", "10.60.0.2/24"), (2, "s2", "10.60.0.3/24"),
                                        (3, "hc", ""))]}
        network = self.start_draft(topology)
        hc = network.get("hc")

        output = hc.cmd("dhcpcd -1 -B -d -4 hc-eth0")
        leased = [line.split()[3].split("/")[0] for line in hc.cmd(
            "ip -o -4 addr show dev hc-eth0").splitlines()[:1]]
        self.assertEqual(len(leased), 1, output)
        held = {name: {lease["ip-address"] for lease in LeaseQueryService.get_leases(
            network, name)["v4"]} for name in ("s1", "s2")}
        # Whichever Service answered first holds the Lease; the other holds none.
        self.assertEqual(sorted(len(addresses) for addresses in held.values()), [0, 1], held)
        self.assertIn(leased[0], held["s1"] | held["s2"])

    def test_relay_carries_a_dual_stack_client_to_the_central_service(self) -> None:
        topology = authored_dhcp_test_lab()
        r1, r2 = self._node(topology, "r1"), self._node(topology, "r2")
        hr_mac = "aa:bb:cc:dd:ee:07"
        topology["links"][5]["ends"][1]["mac"] = hr_mac
        reserved = {"mac": hr_mac, "duid": "", "hostname": "hr"}
        r1["frr_config"] = ("ip route 10.70.0.0/24 10.0.0.2\n"
                            "ipv6 route 2001:db8:70::/64 2001:db8:0::2\n"
                            + dhcp_ra_config("r1-eth3", "2001:db8:1::/64"))
        r1["dhcp"] = {"pools": [
            dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200"),
            dhcp_pool("2001:db8:1::/64", "2001:db8:1::100", "2001:db8:1::200"),
            dhcp_pool("10.70.0.0/24", "10.70.0.100", "10.70.0.200", router="10.70.0.1",
                      reservations=[{"ip": "10.70.0.50", **reserved}]),
            dhcp_pool("2001:db8:70::/64", "2001:db8:70::100", "2001:db8:70::200",
                      reservations=[{"ip": "2001:db8:70::50", **reserved}]),
        ]}
        r2["frr_config"] = dhcp_ra_config("r2-eth1", "2001:db8:70::/64")
        r2["relay"] = {"subnets": ["10.70.0.0/24", "2001:db8:70::/64"],
                       "servers": ["10.0.0.1", "2001:db8:0::1"]}
        network = self.start_draft(topology)
        h1, h6, hr = network.get("h1"), network.get("h6"), network.get("hr")

        # Direct clients of r1, one per family, beside the relayed one.
        output = h1.cmd("dhcpcd -1 -B -d -4 h1-eth0")
        self.assertIn("10.50.0.100", h1.cmd("ip -4 addr show dev h1-eth0"), output)
        output = h6.cmd("dhcpcd -1 -B -d -6 h6-eth0")
        self.assertIn("2001:db8:1::100", h6.cmd("ip -6 addr show dev h6-eth0 scope global"), output)
        # A relayed DHCPv4 request still carries the client's hardware address.
        output = hr.cmd("dhcpcd -1 -B -d -4 hr-eth0")
        self.assertIn("10.70.0.50", hr.cmd("ip -4 addr show dev hr-eth0"), output)
        # Behind a Relay a DHCPv6 Reservation by MAC is the reliable kind: the
        # Relay forwards the client's hardware address (Option 79).
        output = hr.cmd("dhcpcd -1 -B -d -6 hr-eth0")
        self.assertIn("2001:db8:70::50", hr.cmd("ip -6 addr show dev hr-eth0 scope global"), output)

        leases = LeaseQueryService.get_leases(network, "r1")
        [direct4] = [lease for lease in leases["v4"] if lease["ip-address"] == "10.50.0.100"]
        [direct6] = [lease for lease in leases["v6"] if lease["ip-address"] == "2001:db8:1::100"]
        [v4] = [lease for lease in leases["v4"] if lease["ip-address"] == "10.70.0.50"]
        [v6] = [lease for lease in leases["v6"] if lease["ip-address"] == "2001:db8:70::50"]
        self.assertIs(direct4["relayed"], False)
        self.assertIs(direct6["relayed"], False)
        self.assertIs(v4["relayed"], True)
        self.assertIs(v6["relayed"], True)
        self.assertEqual((v4["hw-address"], v6["hw-address"]), (hr_mac, hr_mac))
        activity = RelayActivityQueryService.get_activity(network, "r2")
        self.assertIs(activity["success"], True)
        self.assertIn({"source": "10.70.0.1", "server": "10.0.0.1"}, activity["activity"])
        self.assertIn({"source": "2001:db8:70::1", "server": "2001:db8::1"}, activity["activity"])

    def test_pool_keep_reads_a_running_services_edit_into_the_draft(self) -> None:
        topology = authored_dhcp_test_lab()
        self._node(topology, "r1")["dhcp"] = {"pools": [
            dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")]}
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        repository = AuthoringRepository(root)
        draft = repository.create()
        draft["address_family"] = "dual"
        draft["topology"] = topology
        draft = repository.save(draft["id"], draft["revision"], draft)
        path = os.path.join(root, "preview", "lab.py")
        repository.write_builder(draft, path)
        network = self.start(load_builder(path).build_network())
        handle = LocalLabHandle(network, example="preview")
        snapshot_preview(repository, draft["id"], "k", handle)

        handle.set_node_pool("r1", "10.50.0.0/24", "10.50.0.100", "10.50.0.150",
                             lease_time=300, reservations=[
                                 {"mac": H1_MAC, "duid": "", "ip": "10.50.0.50", "hostname": "h1"}])
        h1 = network.get("h1")
        h1.cmd("dhcpcd -1 -B -d -4 h1-eth0")
        self.assertIn("10.50.0.50", h1.cmd("ip -4 addr show dev h1-eth0"))
        feature_id = "dhcp:r1:10.50.0.0/24"
        review = review_keep(repository, draft["id"], "k", handle)
        digest = next(item["digest"] for item in review["features"] if item["id"] == feature_id)

        saved = apply_keep(repository, draft["id"], draft["revision"], "k", handle, [],
                           [{"id": feature_id, "digest": digest}])
        self.assertEqual(self._node(saved["topology"], "r1")["dhcp"]["pools"], [dhcp_pool(
            "10.50.0.0/24", "10.50.0.100", "10.50.0.150", lease_time=300, reservations=[
                {"ip": "10.50.0.50", "mac": H1_MAC, "duid": "", "hostname": "h1"}])])
        self.assertNotIn("leases", json.dumps(saved["topology"]))

    def test_a_fresh_lease_shows_discover_offer_request_ack(self) -> None:
        topology = authored_dhcp_test_lab()
        self._node(topology, "r1")["dhcp"] = {"pools": [
            dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")]}
        network = self.start_draft(topology)

        packets = LabCapture(network, "h1", "h1-eth0", preset("DHCP")).watch(
            "h1", "dhcpcd -1 -B -d -4 h1-eth0", 4, is_dhcp)
        dhcp = [p for p in packets if is_dhcp(p)]

        self.assertEqual([dhcp_type(p) for p in dhcp], ["Discover", "Offer", "Request", "ACK"])
        discover, offer, request, ack = dhcp
        for message, label in zip(dhcp, ("Discover", "Offer", "Request", "ACK")):
            self.assertIn(f"Option: (53) DHCP Message Type ({label})", fields(message)["dhcp.option.type"])
        # The client has no address yet, so it broadcasts from 0.0.0.0 ...
        for message in (discover, request):
            self.assertEqual(message["summary"]["source"], "0.0.0.0")
            self.assertEqual(message["summary"]["destination"], "255.255.255.255")
            self.assertEqual(first_label(message, "eth.dst"), "Destination: ff:ff:ff:ff:ff:ff")
            self.assertEqual(first_label(message, "dhcp.ip.your"), "Your (client) IP address: 0.0.0.0")
            self.assertEqual(first_label(message, "dhcp.flags.bc").split(" = ")[1], "Broadcast flag: Unicast")
        # ... and the Service answers the address it is proposing, unicast.
        for message in (offer, ack):
            self.assertEqual(message["summary"]["source"], "10.50.0.1")
            self.assertEqual(message["summary"]["destination"], "10.50.0.100")
            self.assertEqual(first_label(message, "dhcp.ip.your"), "Your (client) IP address: 10.50.0.100")
            self.assertEqual(first_label(message, "eth.dst"), f"Destination: {H1_MAC}")
        self.assertEqual(first_label(request, "dhcp.option.requested_ip_address"),
                         "Requested IP Address: 10.50.0.100")

    def test_a_cached_lease_is_confirmed_with_request_and_ack_alone(self) -> None:
        topology = authored_dhcp_test_lab()
        self._node(topology, "r1")["dhcp"] = {"pools": [
            dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")]}
        network = self.start_draft(topology)
        client = "dhcpcd -1 -B -d -4 h1-eth0"
        self.assertIn("leased 10.50.0.100", network.get("h1").cmd(client))

        packets = LabCapture(network, "h1", "h1-eth0", preset("DHCP")).watch("h1", client, 2, is_dhcp)

        self.assertEqual([dhcp_type(p) for p in packets if is_dhcp(p)], ["Request", "ACK"])


if __name__ == "__main__":
    unittest.main()
