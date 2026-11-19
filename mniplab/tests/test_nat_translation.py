"""Source NAT on the NAT Test Lab: both kinds of translator, their NAT Tables and the wire.

A plain Internet Gateway makes the Router it hangs off translate; a Transit
Internet Gateway makes the Router downstream of its Speaker translate.  These prove
both on the host: ``conntrack -L`` entered into the translating Router by its
Namespace Anchor, a Host's traffic appearing there as an entry from the
Host's address to the Router's outside one, its lifetime counting down until
it expires, two Hosts told apart by port — and neither the NAT Gateway's
own translation nor a second Lab's ever appearing, because each Router's
table is its own namespace's.  Captures either side of each translator show
the packet before and after the rewrite.  Both Internet Gateways are drawn
in the Learner's Topology, as the Internet, and the Transit one's downstream
Router routes out through the Speaker to it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Set, Tuple

from mniplab.labhandle import LocalLabHandle
from mniplab.tests.labs import LabCase, authored_nat_test_lab, collect
from mniplab.tests.test_multi_lab_concurrency import _host_uplink_address
from mniplab.utils import wait_until
from mniplab.webserver.capture import CaptureService, capture_target_error
from mniplab.webserver.nat_table import NATEntry, NATTableReader, parse_nat_table

# Each translator: the Host inside it, the Router's outside address, and
# what the Host pings beyond it — nat1's end of r1's Link, sp1's of r2's.
H1, R1_OUTSIDE, NAT_GATEWAY = "100.64.1.2", "100.64.0.2", "100.64.0.1"
H2 = "100.64.2.2"
H3, R2_OUTSIDE, SPEAKER = "100.64.3.2", "100.64.51.2", "100.64.51.1"
# nat2's end of sp1's Link: beyond the Speaker r2 routes its default through.
TRANSIT_GATEWAY = "100.64.50.1"

# The ICMP timeout set inside a translator for the expiry test, so it need
# not wait out the kernel's 30 seconds.  Per namespace: nothing else sees it.
SHORT_ICMP_TIMEOUT = 3


def read(net: Any, node: str) -> List[NATEntry]:
    """Read a Node's NAT Table the way the registry does."""
    attachment = LocalLabHandle(net).node_attachment(node)
    return parse_nat_table(NATTableReader().read(attachment))


def pings_from(entries: List[NATEntry], address: str) -> List[NATEntry]:
    """The ICMP entries a NAT Table holds for one inside address."""
    return [t for t in entries if t.protocol == "icmp" and t.inside_address == address]


def ends(packets: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """Each packet's source and destination, as the pane's summary shows them."""
    return {(p["summary"]["source"], p["summary"]["destination"]) for p in packets}


class NATTestLabTests(LabCase):
    """r1 translates for nat1, r2 for nat2; each table and each wire says so."""

    def setUp(self) -> None:
        self.net = self.start_draft(authored_nat_test_lab(), address_family="ipv4")

    def test_the_lab_names_no_translator_and_draws_both_gateways_as_the_internet(self) -> None:
        topology = LocalLabHandle(self.net).topology()

        self.assertEqual(topology["translators"], [])
        kinds = {node["id"]: node["type"] for node in topology["nodes"]}
        self.assertEqual({name: kinds.get(name) for name in ("nat1", "nat2")},
                         {"nat1": "internet", "nat2": "internet"})

    def test_the_transit_gateways_downstream_router_goes_out_through_the_speaker(self) -> None:
        r2 = self.net.get("r2")

        self.assertIn(f"default via {SPEAKER}", r2.cmd("ip route show default"))
        self.assertIn(" 0% packet loss", r2.cmd(f"ping -c2 -W1 {TRANSIT_GATEWAY}"))

    def test_a_hosts_ping_is_translated_to_the_routers_outside_address(self) -> None:
        for host, inside, router, outside, target in (("h1", H1, "r1", R1_OUTSIDE, NAT_GATEWAY),
                                                      ("h3", H3, "r2", R2_OUTSIDE, SPEAKER)):
            with self.subTest(router=router):
                self.net.get(host).cmd(f"ping -c 1 -W 2 {target}")

                [entry] = pings_from(read(self.net, router), inside)
                self.assertEqual(entry.outside_address, outside)
                self.assertEqual(entry.destination, target)
                self.assertEqual(entry.state, "REPLIED")

    def test_the_nat_gateways_translation_never_appears(self) -> None:
        # Out through the Gateway to the host's own address: the Gateway
        # translates 100.64.0.2 again, in the root namespace.
        self.net.get("h1").cmd(f"ping -c 1 -W 2 {_host_uplink_address()}")

        entries = read(self.net, "r1")
        self.assertTrue(pings_from(entries, H1))
        self.assertEqual({t.inside_address for t in entries}, {H1})
        self.assertEqual({t.outside_address for t in entries}, {R1_OUTSIDE})

    def test_the_lifetime_counts_down_and_the_entry_expires(self) -> None:
        pairs = (("h1", H1, "r1", NAT_GATEWAY), ("h3", H3, "r2", SPEAKER))
        for host, _inside, router, target in pairs:
            self.net.get(router).cmd(
                f"sysctl -w net.netfilter.nf_conntrack_icmp_timeout={SHORT_ICMP_TIMEOUT}")
            self.net.get(host).cmd(f"ping -c 1 -W 2 {target}")

        first = {router: pings_from(read(self.net, router), inside)
                 for _host, inside, router, _target in pairs}
        time.sleep(1.5)
        later = {router: pings_from(read(self.net, router), inside)
                 for _host, inside, router, _target in pairs}
        expired = wait_until(lambda: not any(
            pings_from(read(self.net, router), inside) for _host, inside, router, _target in pairs
        ), SHORT_ICMP_TIMEOUT + 10, interval=0.5)

        for _host, _inside, router, _target in pairs:
            with self.subTest(router=router):
                [held], [aged] = first[router], later[router]
                self.assertEqual(held.port_kind, "id")
                self.assertLessEqual(held.lifetime, SHORT_ICMP_TIMEOUT)
                self.assertLess(aged.lifetime, held.lifetime)
        self.assertTrue(expired, "a translator never forgot its entry")

    def test_both_sides_of_each_translator_show_the_source_rewritten(self) -> None:
        handle = LocalLabHandle(self.net)
        interfaces = {i["name"]: i for i in handle.list_interfaces("r1")}
        # r1's outside Interface faces the Internet Gateway nat1: drawn, with
        # nat1 as its peer, and still a target a Capture may open.
        self.assertEqual((interfaces["r1-eth0"]["drawn"], interfaces["r1-eth0"]["peer"]),
                         (True, "nat1"))
        drawn = {(link["source"], link["target"]) for link in handle.topology()["links"]}
        self.assertTrue(any("nat1" in pair for pair in drawn))
        self.assertIsNone(capture_target_error(handle.topology(), "r1", "r1-eth0", list(interfaces)))
        sides = (("r1", "r1-eth1"), ("r1", "r1-eth0"), ("r2", "r2-eth1"), ("r2", "r2-eth0"))

        async def scenario() -> List[List[Dict[str, Any]]]:
            service = CaptureService(grace_seconds=1)
            try:
                watching = [
                    await service.subscribe(self.net.lab_key, self.net.lab_dir,
                                            handle.node_attachment(node), interface,
                                            capture_filter="icmp")
                    for node, interface in sides
                ]
                # Let the tsharks come up before the only traffic is sent.
                await asyncio.sleep(2)
                loop = asyncio.get_running_loop()
                await asyncio.gather(
                    loop.run_in_executor(None, self.net.get("h1").cmd,
                                         f"ping -c 2 -i 0.2 {NAT_GATEWAY}"),
                    loop.run_in_executor(None, self.net.get("h3").cmd,
                                         f"ping -c 2 -i 0.2 {SPEAKER}"),
                )
                return [await collect(viewer, 4, lambda p: True) for viewer in watching]
            finally:
                await service.stop_all("test over")

        r1_inside, r1_outside, r2_inside, r2_outside = asyncio.run(scenario())

        self.assertEqual(ends(r1_inside), {(H1, NAT_GATEWAY), (NAT_GATEWAY, H1)})
        self.assertEqual(ends(r1_outside), {(R1_OUTSIDE, NAT_GATEWAY), (NAT_GATEWAY, R1_OUTSIDE)})
        self.assertEqual(ends(r2_inside), {(H3, SPEAKER), (SPEAKER, H3)})
        self.assertEqual(ends(r2_outside), {(R2_OUTSIDE, SPEAKER), (SPEAKER, R2_OUTSIDE)})

    def test_two_hosts_share_the_outside_address_by_port(self) -> None:
        send = f"echo hi | socat -u - UDP:{NAT_GATEWAY}:9999,sourceport=40000"
        for host in ("h1", "h2"):
            self.assertEqual(self.net.get(host).cmd(send).strip(), "")

        sent = [t for t in read(self.net, "r1") if t.protocol == "udp" and t.inside_port == "40000"]

        self.assertEqual({t.inside_address for t in sent}, {H1, H2})
        self.assertEqual({t.outside_address for t in sent}, {R1_OUTSIDE})
        self.assertEqual(len({t.outside_port for t in sent}), 2)
        self.assertEqual([t.outside_port for t in sent].count("40000"), 1)
        self.assertEqual({t.state for t in sent}, {"UNREPLIED"})


class TwoNATLabsTests(LabCase):
    """Two Labs on one definition, on one host: each Router's table is its own."""

    def test_a_second_labs_entries_never_appear(self) -> None:
        first = self.start_draft(authored_nat_test_lab(), address_family="ipv4")
        second = self.start_draft(authored_nat_test_lab(), address_family="ipv4")
        self.assertNotEqual(first.lab_key, second.lab_key)

        first.get("h1").cmd(f"ping -c 1 -W 2 {_host_uplink_address()}")

        self.assertTrue(pings_from(read(first, "r1"), H1))
        self.assertEqual(pings_from(read(second, "r1"), H1), [])
