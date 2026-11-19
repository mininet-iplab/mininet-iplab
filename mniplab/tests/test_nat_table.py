"""Tests for reading a Router's NAT Table out of its connection tracking table.

The table is ``conntrack -L`` text read inside the translating Router's
namespace.  Turning it into NAT Table entries is a pure function over that text,
so every case here runs against tables captured verbatim from the project's
own NAT Labs, committed under ``fixtures/conntrack/``:

* ``static-lab-nat-r1`` — ``r1`` masquerading ``h1``'s ICMP, UDP and TCP
  (one connection open, one in TIME_WAIT) on ``r1-eth0``, beside an ICMP
  flow from ``h1`` to ``h2`` that ``r1`` forwards untranslated.
* ``bgp-isp-lab-isp1`` — ``isp1`` masquerading ``h11`` and ``h12`` on
  ``isp1-eth0``; both sent from UDP port 40000 and pinged with the same
  ICMP id, so ``isp1`` had to substitute a new port and a new id for
  ``h12``.  Its own BGP sessions are there too, untranslated.
* ``bgp-isp-lab-tr1`` — ``tr1``'s table for the same traffic, which
  ended at ``tr1`` and so was never translated there.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, patch

from mniplab.labhandle import NodeAttachment
from mniplab.routernat import PortForward, StaticNAT, _render
from mniplab.tests.fakes import (
    FAKE_LAB_KEY,
    FakeIntf,
    FakeLab,
    FakeLink,
    FakeNode,
    FakeTerminalCloserMixin,
    LoginClientMixin,
)
from mniplab.tests import test_routernat as routernat_tests
from mniplab.tests.test_web_capture import two_node_lab
from mniplab.topology import TopologyBuilder
from mniplab.webserver.session import SessionRegistry
from mniplab.webserver.nat_table import (
    NATEntry,
    NATRule,
    build_nat_rules_command,
    build_nat_table_command,
    parse_nat_rules,
    parse_nat_table,
)

FIXTURES = Path(__file__).parent / "fixtures" / "conntrack"


def table(name: str) -> str:
    """Return one committed ``conntrack -L`` table as text."""
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


def by_inside(entries: list) -> dict:
    """Key entries by protocol and inside address and port."""
    return {(t.protocol, t.inside_address, t.inside_port): t for t in entries}


class ParseNATTableTests(unittest.TestCase):
    """The table becomes one entry per translated connection."""

    def test_each_translated_connection_is_one_entry(self) -> None:
        entries = parse_nat_table(table("static-lab-nat-r1"))

        self.assertEqual(
            sorted((t.protocol, t.inside_port) for t in entries),
            [("icmp", "39"), ("tcp", "48072"), ("tcp", "51936"), ("udp", "51330")],
        )

    def test_a_connection_forwarded_untranslated_is_not_an_entry(self) -> None:
        # h1 to h2 crosses r1 but not its NAT-facing Interface.
        entries = parse_nat_table(table("static-lab-nat-r1"))

        self.assertNotIn("100.64.2.2", [t.destination for t in entries])

    def test_the_inside_pair_and_the_outside_pair_are_side_by_side(self) -> None:
        udp = by_inside(parse_nat_table(table("static-lab-nat-r1")))[("udp", "100.64.1.2", "51330")]

        self.assertEqual(
            (udp.inside_address, udp.inside_port, udp.outside_address, udp.outside_port),
            ("100.64.1.2", "51330", "100.64.0.2", "51330"),
        )
        self.assertEqual((udp.destination, udp.destination_port), ("100.64.0.1", "9999"))

    def test_a_substituted_port_is_the_outside_port(self) -> None:
        entries = by_inside(parse_nat_table(table("bgp-isp-lab-isp1")))

        h11 = entries[("udp", "192.168.11.100", "40000")]
        h12 = entries[("udp", "192.168.12.100", "40000")]
        self.assertEqual((h11.outside_address, h11.outside_port), ("203.0.113.1", "40000"))
        self.assertEqual((h12.outside_address, h12.outside_port), ("203.0.113.1", "30075"))

    def test_an_icmp_entry_substitutes_the_echo_identifier(self) -> None:
        entries = by_inside(parse_nat_table(table("bgp-isp-lab-isp1")))

        h12 = entries[("icmp", "192.168.12.100", "38")]
        self.assertEqual((h12.outside_port, h12.port_kind), ("33293", "id"))
        self.assertIsNone(h12.destination_port)

    def test_tcp_and_udp_name_their_ports_as_ports(self) -> None:
        kinds = {t.protocol: t.port_kind for t in parse_nat_table(table("static-lab-nat-r1"))}

        self.assertEqual(kinds, {"icmp": "id", "tcp": "port", "udp": "port"})

    def test_a_routers_own_sessions_are_not_entries(self) -> None:
        # isp1's BGP sessions start and end at isp1: nothing is substituted.
        entries = parse_nat_table(table("bgp-isp-lab-isp1"))

        self.assertNotIn("179", [t.destination_port for t in entries])
        self.assertEqual(len(entries), 4)

    def test_traffic_ending_at_a_node_is_not_translated_there(self) -> None:
        self.assertEqual(parse_nat_table(table("bgp-isp-lab-tr1")), [])

    def test_the_remaining_lifetime_is_read_in_seconds(self) -> None:
        entries = by_inside(parse_nat_table(table("static-lab-nat-r1")))

        self.assertEqual(entries[("icmp", "100.64.1.2", "39")].lifetime, 28)
        self.assertEqual(entries[("tcp", "100.64.1.2", "51936")].lifetime, 431998)

    def test_a_tcp_connection_carries_its_tcp_state(self) -> None:
        entries = by_inside(parse_nat_table(table("static-lab-nat-r1")))

        self.assertEqual(entries[("tcp", "100.64.1.2", "51936")].state, "ESTABLISHED")
        self.assertEqual(entries[("tcp", "100.64.1.2", "48072")].state, "TIME_WAIT")

    def test_a_connection_without_tcp_state_says_whether_it_was_answered(self) -> None:
        entries = by_inside(parse_nat_table(table("static-lab-nat-r1")))

        self.assertEqual(entries[("udp", "100.64.1.2", "51330")].state, "UNREPLIED")
        self.assertEqual(entries[("icmp", "100.64.1.2", "39")].state, "REPLIED")

    def test_entries_are_ordered_by_inside_address_protocol_and_port(self) -> None:
        # The kernel lists in hash order, which reshuffles between reads;
        # a table that refreshes must not.
        entries = parse_nat_table(table("bgp-isp-lab-isp1"))

        self.assertEqual(
            [(t.inside_address, t.protocol, t.inside_port) for t in entries],
            [
                ("192.168.11.100", "icmp", "38"),
                ("192.168.11.100", "udp", "40000"),
                ("192.168.12.100", "icmp", "38"),
                ("192.168.12.100", "udp", "40000"),
            ],
        )

    def test_an_empty_table_has_no_entries(self) -> None:
        self.assertEqual(parse_nat_table(""), [])

    def test_lines_that_are_not_entries_are_ignored(self) -> None:
        text = (
            "conntrack v1.4.7 (conntrack-tools): 5 flow entries have been shown.\n"
            + table("static-lab-nat-r1")
            + "\n  \n"
        )

        self.assertEqual(parse_nat_table(text), parse_nat_table(table("static-lab-nat-r1")))

    def test_the_extended_format_reads_the_same(self) -> None:
        # ``-o extended`` prefixes each line with the address family.
        extended = "\n".join(
            "ipv4     2 " + line for line in table("static-lab-nat-r1").splitlines()
        )

        self.assertEqual(parse_nat_table(extended), parse_nat_table(table("static-lab-nat-r1")))

    def test_an_ipv6_entry_is_read(self) -> None:
        line = (
            "tcp      6 117 SYN_SENT src=2001:db8:1::2 dst=2001:db8:ff::1 sport=40000 dport=80"
            " [UNREPLIED] src=2001:db8:ff::1 dst=2001:db8:0::1 sport=80 dport=40000 mark=0 use=1\n"
        )

        [entry] = parse_nat_table(line)
        self.assertEqual(
            (entry.inside_address, entry.outside_address, entry.state),
            ("2001:db8:1::2", "2001:db8:0::1", "SYN_SENT"),
        )

    def test_a_port_forward_is_a_destination_entry(self) -> None:
        # A client reached 203.0.113.2:8080; the Router sent it to srv:80.
        # Only the destination was rewritten, so the source pair is the client's.
        text = (
            "tcp      6 431999 ESTABLISHED src=198.51.100.10 dst=203.0.113.2 sport=40000 dport=8080"
            " src=192.168.3.10 dst=198.51.100.10 sport=80 dport=40000 [ASSURED] mark=0 use=1\n"
        )

        [entry] = parse_nat_table(text)
        self.assertEqual(entry.kind, "destination")
        self.assertEqual(
            (entry.inside_address, entry.inside_port),
            ("192.168.3.10", "80"),
        )
        self.assertEqual(
            (entry.outside_address, entry.outside_port),
            ("203.0.113.2", "8080"),
        )
        self.assertEqual(
            (entry.destination, entry.destination_port),
            ("198.51.100.10", "40000"),
        )
        self.assertEqual((entry.port_kind, entry.state, entry.lifetime), ("port", "ESTABLISHED", 431999))

    def test_an_inbound_static_mapping_is_a_destination_entry_by_its_echo_identifier(self) -> None:
        # server pings 203.0.113.5, which the Router maps to h2.
        text = (
            "icmp     1 29 src=198.51.100.10 dst=203.0.113.5 type=8 code=0 id=5"
            " src=192.168.2.10 dst=198.51.100.10 type=0 code=0 id=5 mark=0 use=1\n"
        )

        [entry] = parse_nat_table(text)
        self.assertEqual(entry.kind, "destination")
        self.assertEqual((entry.inside_address, entry.inside_port), ("192.168.2.10", "5"))
        self.assertEqual((entry.outside_address, entry.outside_port), ("203.0.113.5", "5"))
        self.assertEqual((entry.destination, entry.destination_port), ("198.51.100.10", None))
        self.assertEqual(entry.port_kind, "id")

    def test_a_hairpinned_connection_is_a_source_entry_naming_the_forward_it_reached(self) -> None:
        # h1 reached its own forward at 203.0.113.2:8080: the Router rewrote
        # the destination and, with hairpin, the source too. The source is
        # what the entry shows, and the destination is the forward's address.
        text = (
            "tcp      6 431999 ESTABLISHED src=192.168.1.10 dst=203.0.113.2 sport=40000 dport=8080"
            " src=192.168.3.10 dst=192.168.3.1 sport=80 dport=40000 [ASSURED] mark=0 use=1\n"
        )

        [entry] = parse_nat_table(text)
        self.assertEqual(entry.kind, "source")
        self.assertEqual((entry.outside_address, entry.outside_port), ("192.168.3.1", "40000"))
        self.assertEqual((entry.destination, entry.destination_port), ("203.0.113.2", "8080"))

    def test_an_entry_serialises_to_the_fields_the_pane_reads(self) -> None:
        [entry] = [
            t for t in parse_nat_table(table("static-lab-nat-r1")) if t.protocol == "udp"
        ]

        self.assertEqual(entry.as_dict(), {
            "protocol": "udp",
            "inside_address": "100.64.1.2",
            "inside_port": "51330",
            "outside_address": "100.64.0.2",
            "outside_port": "51330",
            "destination": "100.64.0.1",
            "destination_port": "9999",
            "port_kind": "port",
            "state": "UNREPLIED",
            "lifetime": 28,
            "kind": "source",
        })
        self.assertIsInstance(entry, NATEntry)


# What ``iptables -t nat -S`` printed for one Router (iptables 1.8.9, nf_tables):
# a hairpinned forward, a forward without hairpin, a static mapping, a rule
# typed in by hand, and the masquerade on the outside Interface. The outside
# Interface is named r1-eth3 here; the rest is as iptables printed it.
IPTABLES_S = """\
-P PREROUTING ACCEPT
-P INPUT ACCEPT
-P OUTPUT ACCEPT
-P POSTROUTING ACCEPT
-A PREROUTING -d 203.0.113.2/32 -p tcp -m tcp --dport 8080 -j DNAT --to-destination 192.168.3.10:80
-A PREROUTING -d 203.0.113.2/32 -i r1-eth3 -p tcp -m tcp --dport 9090 -j DNAT --to-destination 192.168.3.10:80
-A PREROUTING -d 203.0.113.5/32 -i r1-eth3 -j DNAT --to-destination 192.168.2.10
-A PREROUTING -p udp -m udp --dport 53 -j REDIRECT --to-ports 5353
-A POSTROUTING -s 192.168.1.0/24 -d 192.168.3.10/32 -p tcp -m tcp --dport 80 -m conntrack --ctstate DNAT -j MASQUERADE
-A POSTROUTING -s 192.168.2.10/32 -o r1-eth3 -j SNAT --to-source 203.0.113.5
-A POSTROUTING -o r1-eth3 -j MASQUERADE
-A POSTROUTING -s 10.9.0.0/16 -o r1-eth3 -j MASQUERADE
"""


class ParseNATRulesTests(unittest.TestCase):
    """The rules strip reads ``iptables -t nat -S`` into the items the Router runs."""

    def test_iptables_prints_a_routers_rules_as_the_items_they_declare(self) -> None:
        self.assertEqual(parse_nat_rules(IPTABLES_S), [
            NATRule(kind="port_forward", protocol="tcp", outside_address="203.0.113.2",
                    outside_port="8080", inside_address="192.168.3.10", inside_port="80",
                    hairpin=True),
            NATRule(kind="port_forward", interface="r1-eth3", protocol="tcp",
                    outside_address="203.0.113.2", outside_port="9090",
                    inside_address="192.168.3.10", inside_port="80", hairpin=False),
            NATRule(kind="static", interface="r1-eth3", inside_address="192.168.2.10",
                    outside_address="203.0.113.5"),
            NATRule(kind="other", raw="-A PREROUTING -p udp -m udp --dport 53 -j REDIRECT --to-ports 5353"),
            NATRule(kind="masquerade", interface="r1-eth3"),
            NATRule(kind="other", raw="-A POSTROUTING -s 10.9.0.0/16 -o r1-eth3 -j MASQUERADE"),
        ])

    def test_the_rules_routernat_renders_are_read_back_as_the_items_they_declare(self) -> None:
        r1 = routernat_tests.router(
            PortForward("tcp", 8080, "192.168.3.10", 80, hairpin=True),
            PortForward("tcp", 9090, "192.168.3.10", 80),
            StaticNAT("192.168.2.10", "203.0.113.5"),
        )
        routernat_tests.interface(r1, "r1-eth0", ip="192.168.1.1/24")
        routernat_tests.interface(r1, "r1-eth3", ip="203.0.113.2/30", nat="outside")

        rules = parse_nat_rules("\n".join(_render(r1)))

        self.assertEqual(rules, [
            NATRule(kind="static", interface="r1-eth3", inside_address="192.168.2.10",
                    outside_address="203.0.113.5"),
            NATRule(kind="port_forward", protocol="tcp", outside_address="203.0.113.2",
                    outside_port="8080", inside_address="192.168.3.10", inside_port="80",
                    hairpin=True),
            NATRule(kind="port_forward", interface="r1-eth3", protocol="tcp",
                    outside_address="203.0.113.2", outside_port="9090",
                    inside_address="192.168.3.10", inside_port="80", hairpin=False),
            NATRule(kind="masquerade", interface="r1-eth3"),
        ])

    def test_a_rule_the_module_does_not_read_is_shown_as_its_raw_line(self) -> None:
        lines = [
            "-A POSTROUTING -s 192.168.1.0/24 -o r1-eth3 -j MASQUERADE",
            "-A PREROUTING -d 203.0.113.0/24 -p tcp -m tcp --dport 80 -j DNAT --to-destination 192.168.3.10:80",
            "-A PREROUTING -p tcp -m tcp --dport 22 -m comment --comment ssh -j REDIRECT --to-ports 2222",
            "-A OUTPUT -d 10.0.0.1/32 -j DNAT --to-destination 10.0.0.2",
        ]

        self.assertEqual(
            parse_nat_rules("\n".join(lines)),
            [NATRule(kind="other", raw=line) for line in lines],
        )

    def test_a_static_dnat_without_its_snat_is_shown_raw(self) -> None:
        line = "-A PREROUTING -i r1-eth3 -d 203.0.113.5/32 -j DNAT --to-destination 192.168.2.10"

        self.assertEqual(parse_nat_rules(line), [NATRule(kind="other", raw=line)])

    def test_a_hairpin_masquerade_no_forward_claims_is_shown_raw(self) -> None:
        line = ("-A POSTROUTING -s 192.168.1.0/24 -d 192.168.3.10/32 -p tcp -m tcp --dport 80"
                " -m conntrack --ctstate DNAT -j MASQUERADE")

        self.assertEqual(parse_nat_rules(line), [NATRule(kind="other", raw=line)])

    def test_each_outside_interface_is_a_masquerade_item(self) -> None:
        text = ("-A POSTROUTING -o r1-eth3 -j MASQUERADE\n"
                "-A POSTROUTING -o r1-eth4 -j MASQUERADE")

        self.assertEqual(parse_nat_rules(text), [
            NATRule(kind="masquerade", interface="r1-eth3"),
            NATRule(kind="masquerade", interface="r1-eth4"),
        ])

    def test_policy_and_chain_lines_are_not_rules(self) -> None:
        self.assertEqual(parse_nat_rules("-P PREROUTING ACCEPT\n-N MYCHAIN\n"), [])
        self.assertEqual(parse_nat_rules(""), [])


class NATTableCommandTests(unittest.TestCase):
    """The table is read inside the translating Node, by its Namespace Anchor."""

    def test_conntrack_runs_in_the_nodes_namespaces(self) -> None:
        argv = build_nat_table_command(NodeAttachment(node="r1", pid=4101, home_dir="/tmp"))

        self.assertEqual(argv[:3], ["nsenter", "-t", "4101"])
        self.assertEqual(argv[argv.index("--") + 1:], ["conntrack", "-L"])

    def test_a_container_host_is_refused(self) -> None:
        attachment = NodeAttachment(node="c1", pid=4101, home_dir="/tmp", container_name="c1")

        with self.assertRaises(ValueError):
            build_nat_table_command(attachment)

    def test_iptables_lists_the_rules_in_the_nodes_namespaces(self) -> None:
        argv = build_nat_rules_command(NodeAttachment(node="r1", pid=4101, home_dir="/tmp"))

        self.assertEqual(argv[:3], ["nsenter", "-t", "4101"])
        self.assertEqual(argv[argv.index("--") + 1:], ["iptables", "-t", "nat", "-S"])

    def test_a_container_host_has_no_rules_to_list(self) -> None:
        attachment = NodeAttachment(node="c1", pid=4101, home_dir="/tmp", container_name="c1")

        with self.assertRaises(ValueError):
            build_nat_rules_command(attachment)


def nat_lab(*outside: str) -> FakeLab:
    """A Router r1 linked to two Hosts and to a hidden NAT Gateway, in that port order.

    Args:
        *outside: The names of r1's Interfaces marked outside, as a Link end
            declares them.
    """
    r1 = FakeNode("r1", display_type="router")
    hosts = [FakeNode("h1"), FakeNode("h2")]
    nat = FakeNode("nat0", hidden=True)
    links = []
    for index, peer in enumerate([*hosts, nat]):
        r1_intf = FakeIntf(f"r1-eth{index}", r1)
        peer_intf = FakeIntf(f"{peer.name}-eth0", peer)
        peer.addIntf(peer_intf)
        r1.addIntf(r1_intf)
        if r1_intf.name in outside:
            r1_intf.params["nat"] = "outside"
        links.append(FakeLink(r1_intf, peer_intf))
    return FakeLab(r1, *hosts, nat, links=links)


class TopologyTranslatorsTests(unittest.TestCase):
    """The Topology payload says which Nodes translate, so the pane knows to appear."""

    def test_a_lab_without_router_nat_has_no_translators(self) -> None:
        self.assertEqual(TopologyBuilder.build(FakeLab(FakeNode("h1")))["translators"], [])

    def test_a_router_with_an_outside_interface_is_listed_with_it(self) -> None:
        self.assertEqual(
            TopologyBuilder.build(nat_lab("r1-eth1"))["translators"],
            [{"node": "r1", "interface": "r1-eth1"}],
        )

    def test_a_router_with_two_outside_interfaces_is_listed_once_per_interface(self) -> None:
        self.assertEqual(
            TopologyBuilder.build(nat_lab("r1-eth0", "r1-eth1"))["translators"],
            [{"node": "r1", "interface": "r1-eth0"}, {"node": "r1", "interface": "r1-eth1"}],
        )

    def test_an_internet_gateway_adds_no_translator_of_its_own(self) -> None:
        # A Gateway once named the Router it hangs off as translating. Only
        # Router NAT makes a Router translate, so that claim must not count.
        lab = nat_lab()
        lab.get("nat0").translators = lambda: [(lab.get("r1"), "r1-eth2")]

        self.assertEqual(TopologyBuilder.build(lab)["translators"], [])


class FakeNATTableReader:
    """Hands back a committed table and rules per Node; lists nothing itself.

    Attributes:
        tables: The ``conntrack -L`` text to return, by Node name.
        rules: The ``iptables -t nat -S`` text to return, by Node name.
        attachments: Every attachment whose table was read, in order.
    """

    def __init__(self, tables: Dict[str, str], rules: Optional[Dict[str, str]] = None) -> None:
        self.tables = tables
        self.rules = rules or {}
        self.attachments: List[NodeAttachment] = []

    def read(self, attachment: NodeAttachment) -> str:
        self.attachments.append(attachment)
        return self.tables[attachment.node]

    def read_rules(self, attachment: NodeAttachment) -> str:
        return self.rules.get(attachment.node, "")


class RegistryNATTableTests(unittest.IsolatedAsyncioTestCase):
    """The registry reads every translating Node's table and rules, and only theirs."""

    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.reader = FakeNATTableReader(
            {"r1": table("static-lab-nat-r1")},
            {"r1": "-P POSTROUTING ACCEPT\n-A POSTROUTING -o r1-eth1 -j MASQUERADE\n"},
        )
        self.registry.nat_table_reader = self.reader
        self.registry.lab_service.lab = two_node_lab()

    async def test_a_router_with_two_outside_interfaces_is_read_once_under_its_first(self) -> None:
        lab = two_node_lab()
        lab.get("r1").intfs[0].params["nat"] = "outside"  # r1-eth0, ahead of r1-eth1
        self.registry.lab_service.lab = lab

        tables = await self.registry.nat_table(FAKE_LAB_KEY)

        self.assertEqual([(t["node"], t["interface"]) for t in tables], [("r1", "r1-eth0")])
        self.assertEqual(len(self.reader.attachments), 1)

    async def test_each_translating_node_carries_the_rules_it_runs(self) -> None:
        tables = await self.registry.nat_table(FAKE_LAB_KEY)

        self.assertEqual(tables[0]["rules"], [NATRule(kind="masquerade", interface="r1-eth1").as_dict()])

    async def test_each_translating_node_gets_a_table_named_by_it(self) -> None:
        tables = await self.registry.nat_table(FAKE_LAB_KEY)

        self.assertEqual([(t["node"], t["interface"]) for t in tables], [("r1", "r1-eth1")])
        self.assertEqual(len(tables[0]["entries"]), 4)
        self.assertEqual(tables[0]["entries"][0]["outside_address"], "100.64.0.2")

    async def test_the_table_is_read_in_the_router_by_its_namespace_anchor(self) -> None:
        await self.registry.nat_table(FAKE_LAB_KEY)

        [attachment] = self.reader.attachments
        self.assertEqual((attachment.node, attachment.pid), ("r1", 4101))

    async def test_the_nat_gateway_is_never_read(self) -> None:
        await self.registry.nat_table(FAKE_LAB_KEY)

        self.assertNotIn("nat0", [a.node for a in self.reader.attachments])

    async def test_a_lab_without_translators_reads_nothing(self) -> None:
        self.registry.lab_service.lab = two_node_lab(outside=False)

        self.assertEqual(await self.registry.nat_table(FAKE_LAB_KEY), [])
        self.assertEqual(self.reader.attachments, [])

    async def test_no_lab_running_is_refused(self) -> None:
        self.registry.lab_service.lab = None

        with self.assertRaises(ValueError):
            await self.registry.nat_table(FAKE_LAB_KEY)


class NATTableRouteTests(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The pane polls one route for every translating Node's table."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_the_tables_are_served_as_the_registry_read_them(self) -> None:
        tables = [{"node": "r1", "interface": "r1-eth0", "entries": []}]
        with patch("mniplab.webserver.registry.nat_table",
                   new=AsyncMock(return_value=tables)) as read:
            response = self.client.get("/api/labs/fake/nat-table")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"tables": tables})
        read.assert_awaited_once_with("fake")

    def test_a_table_that_cannot_be_read_is_read_out(self) -> None:
        with patch("mniplab.webserver.registry.nat_table",
                   new=AsyncMock(side_effect=ValueError("conntrack is not installed on this host"))):
            response = self.client.get("/api/labs/fake/nat-table")

        self.assertEqual(response.status_code, 400)
        self.assertIn("conntrack", response.json()["detail"])

    def test_signing_in_is_required(self) -> None:
        self.client.cookies.clear()

        self.assertEqual(self.client.get("/api/labs/fake/nat-table").status_code, 401)


if __name__ == "__main__":
    unittest.main()
