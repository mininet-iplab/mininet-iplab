"""Router NAT on the Router NAT Test Lab: each declared item works from the wire, and its NAT Table says so.

r1 translates for its inside Hosts, forwards 203.0.113.2 to srv, and maps h2
to 203.0.113.5. isp, the outside Router, has no route to the private ranges,
so a ping from h1 that gets a reply proves server answered the outside
address: the only one it could have answered. A port forward shows in the
wire and in the NAT Table: a forward rewrites only the destination, so the
table shows it as a destination entry. Hairpinning is shown there too, as a
source entry, since its masquerade rewrites the source. The rules strip reads
what the Router runs, so a rule typed into it by hand appears too.
"""

from __future__ import annotations

import shlex
from typing import Any, List

from mniplab.errors import ConfigurationError
from mniplab.labhandle import LocalLabHandle
from mniplab.routernat import PortForward
from mniplab.tests.labs import LabCase, authored_router_nat_test_lab
from mniplab.utils import wait_until
from mniplab.webserver.nat_table import (
    NATEntry,
    NATRule,
    NATTableReader,
    parse_nat_rules,
    parse_nat_table,
)

INSIDE_H1, INSIDE_H2, INSIDE_SRV = "192.168.1.10", "192.168.2.10", "192.168.3.10"
R1_INSIDE_SRV_SIDE = "192.168.3.1"
R1_OUTSIDE, STATIC_OUTSIDE = "203.0.113.2", "203.0.113.5"
SERVER = "198.51.100.10"
FORWARD, FORWARD_WITHOUT_HAIRPIN = 8080, 9090


def read(net: Any, node: str) -> List[NATEntry]:
    """Read a Node's NAT Table the way the registry does."""
    attachment = LocalLabHandle(net).node_attachment(node)
    return parse_nat_table(NATTableReader().read(attachment))


def read_rules(net: Any, node: str) -> List[NATRule]:
    """Read a Node's NAT rules the way the rules strip does."""
    attachment = LocalLabHandle(net).node_attachment(node)
    return parse_nat_rules(NATTableReader().read_rules(attachment))


def pings_from(entries: List[NATEntry], address: str) -> List[NATEntry]:
    """The ICMP entries a NAT Table holds for one inside address."""
    return [entry for entry in entries if entry.protocol == "icmp" and entry.inside_address == address]


def fetch(node: Any, url: str) -> str:
    """The HTTP status ``node`` gets from ``url``, or ``'error'`` when it gets none."""
    script = ("import sys, urllib.request\n"
              "try:\n"
              "    print(urllib.request.urlopen(sys.argv[1], timeout=3).status)\n"
              "except Exception:\n"
              "    print('error')\n")
    output = node.cmd(f"python3 -c {shlex.quote(script)} {shlex.quote(url)}")
    # The Node's shell echoes a continuation prompt per line of the script.
    return output.strip().split()[-1] if output.strip() else "error"


class RouterNATTestLabTests(LabCase):
    """r1's declared NAT, seen from its inside Hosts and from the outside Router's side."""

    def setUp(self) -> None:
        self.net = self.start_draft(authored_router_nat_test_lab(), address_family="ipv4")
        for node in ("srv", "h2"):
            self.net.get(node).cmd("python3 -m http.server 80 >/dev/null 2>&1 &")
        for node in ("srv", "h2"):
            def served(node: str = node) -> bool:
                return fetch(self.net.get(node), "http://127.0.0.1/") == "200"
            self.assertTrue(wait_until(served, 20), f"{node} never served HTTP")

    def test_the_outside_interface_is_the_routers_only_translator(self) -> None:
        topology = LocalLabHandle(self.net).topology()

        self.assertEqual(topology["translators"], [{"node": "r1", "interface": "r1-eth3"}])

    def test_a_masquerade_translates_an_inside_host_to_the_outside_address(self) -> None:
        self.assertIn(" 0% packet loss", self.net.get("h1").cmd(f"ping -c 2 -W 2 {SERVER}"))

        entries = pings_from(read(self.net, "r1"), INSIDE_H1)
        self.assertTrue(entries)
        self.assertEqual({entry.outside_address for entry in entries}, {R1_OUTSIDE})
        self.assertEqual({entry.destination for entry in entries}, {SERVER})

    def test_a_port_forward_reaches_the_inside_server_from_outside(self) -> None:
        self.assertEqual(fetch(self.net.get("server"), f"http://{R1_OUTSIDE}:{FORWARD}/"), "200")

    def test_a_port_forward_is_a_destination_entry_in_the_nat_table(self) -> None:
        self.assertEqual(fetch(self.net.get("server"), f"http://{R1_OUTSIDE}:{FORWARD}/"), "200")

        forwarded = [entry for entry in read(self.net, "r1") if entry.kind == "destination"]
        self.assertEqual(
            [(e.inside_address, e.inside_port, e.outside_address, e.outside_port, e.destination)
             for e in forwarded],
            [(INSIDE_SRV, "80", R1_OUTSIDE, str(FORWARD), SERVER)],
        )

    def test_a_static_mapping_reaches_the_inside_host_from_outside(self) -> None:
        self.assertEqual(fetch(self.net.get("server"), f"http://{STATIC_OUTSIDE}/"), "200")

    def test_the_rules_strip_reads_what_the_router_runs_including_a_rule_typed_in(self) -> None:
        self.net.get("r1").cmd(
            "iptables -t nat -A PREROUTING -p tcp --dport 2222 -j REDIRECT --to-ports 22")

        rules = read_rules(self.net, "r1")

        self.assertIn(NATRule(kind="masquerade", interface="r1-eth3"), rules)
        self.assertIn(NATRule(
            kind="port_forward", protocol="tcp", outside_address=R1_OUTSIDE,
            outside_port=str(FORWARD), inside_address=INSIDE_SRV, inside_port="80",
            hairpin=True), rules)
        self.assertIn(NATRule(
            kind="port_forward", interface="r1-eth3", protocol="tcp", outside_address=R1_OUTSIDE,
            outside_port=str(FORWARD_WITHOUT_HAIRPIN), inside_address=INSIDE_SRV, inside_port="80",
            hairpin=False), rules)
        self.assertIn(NATRule(
            kind="static", interface="r1-eth3", inside_address=INSIDE_H2,
            outside_address=STATIC_OUTSIDE), rules)
        self.assertTrue(any(rule.kind == "other" and "REDIRECT" in (rule.raw or "")
                            for rule in rules))

    def test_a_static_mapping_sends_the_inside_hosts_traffic_out_as_its_mapped_address(self) -> None:
        self.assertIn(" 0% packet loss", self.net.get("h2").cmd(f"ping -c 2 -W 2 {SERVER}"))

        entries = pings_from(read(self.net, "r1"), INSIDE_H2)
        self.assertTrue(entries)
        self.assertEqual({entry.outside_address for entry in entries}, {STATIC_OUTSIDE})

    def test_hairpin_lets_an_inside_host_reach_the_forward_through_the_outside_address(self) -> None:
        self.assertEqual(fetch(self.net.get("h1"), f"http://{R1_OUTSIDE}:{FORWARD}/"), "200")

        entries = [entry for entry in read(self.net, "r1")
                   if entry.inside_address == INSIDE_H1 and entry.destination == R1_OUTSIDE]
        self.assertTrue(entries)
        self.assertEqual({entry.outside_address for entry in entries}, {R1_INSIDE_SRV_SIDE})

    def test_without_hairpin_an_inside_host_does_not_reach_the_forward(self) -> None:
        # The forward itself works from outside, so the refusal is the hairpin's.
        self.assertEqual(
            fetch(self.net.get("server"), f"http://{R1_OUTSIDE}:{FORWARD_WITHOUT_HAIRPIN}/"), "200")

        self.assertNotEqual(
            fetch(self.net.get("h1"), f"http://{R1_OUTSIDE}:{FORWARD_WITHOUT_HAIRPIN}/"), "200")


class RouterNATRefusalTests(LabCase):
    """A declaration that could not take effect stops the Lab before any Node starts."""

    def test_an_entry_on_a_router_with_no_outside_interface_stops_the_lab(self) -> None:
        net = self.lab()
        r1 = net.addRouter("r1", nat=[PortForward("tcp", 8080, INSIDE_SRV, 80)])
        h1 = net.addHost("h1")
        net.addLink(r1, h1, params1={"ip": "192.168.1.1/24"}, params2={"ip": "192.168.1.10/24"})

        with self.assertRaisesRegex(ConfigurationError, "r1.*outside"):
            self.start(net)

    def test_an_outside_mark_facing_an_internet_gateway_stops_the_lab(self) -> None:
        net = self.lab()
        r1 = net.addRouter("r1")
        nat1 = net.addNAT("nat1", subnet="100.64.0.0/24")
        net.addLink(nat1, r1, params1={"ip": "100.64.0.1/24"},
                    params2={"ip": "100.64.0.2/24", "nat": "outside"})

        with self.assertRaisesRegex(ConfigurationError, "r1-eth0.*nat1"):
            self.start(net)
