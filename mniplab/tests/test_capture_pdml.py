"""Tests for the web server's half of the PDML stream behind a Packet Capture.

The server never parses a packet's field tree — the browser does, because
parsing it here would cost far more than forwarding it and be paid in the
process every Lab shares.  What the server does is cut tshark's
``-T pdml -l`` stream into whole packets as they arrive and read a summary
row out of each, which is what the pane shows when the rate guard is on.
Both are pure functions over text, so every case here runs against PDML
captured verbatim from the project's own Labs, committed under
``fixtures/pdml/``.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from mniplab.webserver.pdml import PacketSplitter, summarize_packet

FIXTURES = Path(__file__).parent / "fixtures" / "pdml"


def fixture(name: str) -> str:
    """Return one committed PDML fixture as text."""
    return (FIXTURES / f"{name}.pdml").read_text(encoding="utf-8")


def packets_of(name: str) -> list:
    """Split a whole fixture into its packets the way the live feed does."""
    splitter = PacketSplitter()
    return splitter.feed(fixture(name).encode("utf-8"))


def summaries_of(name: str) -> list:
    """Summarise every packet of one fixture."""
    return [summarize_packet(packet) for packet in packets_of(name)]


class PacketSplitterTests(unittest.TestCase):
    """tshark's stream is cut into whole packets, however it arrives."""

    def test_a_whole_document_yields_every_packet_and_nothing_else(self):
        packets = packets_of("dhcp")

        self.assertEqual(len(packets), 4)
        for packet in packets:
            self.assertTrue(packet.startswith("<packet>"))
            self.assertTrue(packet.endswith("</packet>"))

    def test_the_document_preamble_is_not_a_packet(self):
        packets = packets_of("ospf")

        self.assertNotIn("<?xml", "".join(packets))
        self.assertNotIn("<pdml", "".join(packets))

    def test_a_packet_split_across_reads_is_held_until_it_closes(self):
        data = fixture("icmp-arp").encode("utf-8")
        splitter = PacketSplitter()
        packets = []
        # Seven-byte reads land boundaries mid-tag, mid-attribute and
        # mid-"</packet>", which is what a pipe read under load does.
        for start in range(0, len(data), 7):
            packets.extend(splitter.feed(data[start:start + 7]))

        self.assertEqual(packets, packets_of("icmp-arp"))

    def test_a_packet_is_released_the_moment_it_closes(self):
        # -l flushes each packet as it is dissected, and a quiet link may
        # not carry another for seconds: the one that closed must not wait.
        data = fixture("ospf").encode("utf-8")
        first_close = data.index(b"</packet>") + len(b"</packet>")
        splitter = PacketSplitter()

        self.assertEqual(len(splitter.feed(data[:first_close])), 1)

    def test_a_multibyte_character_split_across_reads_survives(self):
        splitter = PacketSplitter()
        text = '<packet><proto name="x" showname="café"/></packet>'.encode("utf-8")
        cut = text.index("é".encode("utf-8")) + 1

        packets = splitter.feed(text[:cut]) + splitter.feed(text[cut:])

        self.assertEqual(packets, [text.decode("utf-8")])


class SummaryTests(unittest.TestCase):
    """Each packet yields the row a Learner scans: time, ends, protocol, description."""

    def test_the_four_dhcp_messages_are_named_in_order(self):
        summaries = summaries_of("dhcp")

        self.assertEqual([s["protocol"] for s in summaries], ["DHCP"] * 4)
        self.assertEqual(
            [s["description"] for s in summaries],
            ["Discover", "Offer", "Request", "ACK"],
        )

    def test_a_discover_goes_from_nowhere_to_everyone(self):
        discover = summaries_of("dhcp")[0]

        self.assertEqual(discover["source"], "0.0.0.0")
        self.assertEqual(discover["destination"], "255.255.255.255")

    def test_the_summary_carries_number_length_and_capture_time(self):
        discover = summaries_of("dhcp")[0]

        self.assertEqual(discover["number"], 1)
        self.assertEqual(discover["length"], 342)
        self.assertAlmostEqual(discover["time"], 1790096213.760745728, places=6)

    def test_bgp_open_and_update_are_named(self):
        descriptions = [s["description"] for s in summaries_of("bgp")]

        self.assertEqual(descriptions, ["OPEN Message", "UPDATE Message"])

    def test_ospf_is_named_rather_than_unknown(self):
        summaries = summaries_of("ospf")

        self.assertEqual([s["protocol"] for s in summaries], ["OSPF", "OSPF"])
        self.assertEqual([s["description"] for s in summaries], ["Hello Packet", "LS Update"])

    def test_the_four_dhcpv6_messages_are_named_as_dhcp_ones_are(self):
        summaries = summaries_of("dhcpv6")

        self.assertEqual([s["protocol"] for s in summaries], ["DHCPV6"] * 4)
        self.assertEqual(
            [s["description"] for s in summaries],
            ["Solicit", "Advertise", "Request", "Reply"],
        )

    def test_icmp_names_its_type_and_icmpv6_too(self):
        icmp = [s["description"] for s in summaries_of("icmp-arp") if s["protocol"] == "ICMP"]
        advertisement = summaries_of("router-advertisement")[0]

        self.assertEqual(icmp, ["Echo (ping) request", "Echo (ping) reply"])
        self.assertEqual(advertisement["description"], "Router Advertisement")

    def test_arp_names_its_opcode(self):
        arp = [s["description"] for s in summaries_of("icmp-arp") if s["protocol"] == "ARP"]

        self.assertEqual(arp, ["Request", "Reply"])

    def test_the_message_is_read_from_the_last_layer_not_the_first(self):
        # TCP packs several BGP messages into one segment: the row names the
        # last, as it names the last layer.
        packet = (
            '<packet><proto name="bgp" showname="Border Gateway Protocol - OPEN Message">'
            '<field name="bgp.type" showname="Type: OPEN Message (1)" show="1"/></proto>'
            '<proto name="bgp" showname="Border Gateway Protocol - KEEPALIVE Message">'
            '<field name="bgp.type" showname="Type: KEEPALIVE Message (4)" show="4"/></proto>'
            '</packet>'
        )

        self.assertEqual(summarize_packet(packet)["description"], "KEEPALIVE Message")

    def test_dns_with_and_without_dnssec_records_is_named(self):
        for name in ("dns", "dnssec"):
            with self.subTest(fixture=name):
                descriptions = [s["description"] for s in summaries_of(name)]
                self.assertEqual(descriptions, ["Query", "Response"])

    def test_arp_falls_back_to_link_layer_addresses(self):
        # ARP carries no IP header, so the ends a Learner sees are the
        # Ethernet addresses, as in Wireshark's own packet list.
        request = summaries_of("icmp-arp")[0]

        self.assertEqual(request["protocol"], "ARP")
        self.assertRegex(request["source"], r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")

    def test_icmp_carries_its_ip_ends(self):
        echo = summaries_of("icmp-arp")[2]

        self.assertEqual(echo["protocol"], "ICMP")
        self.assertRegex(echo["source"], r"^\d+\.\d+\.\d+\.\d+$")

    def test_a_router_advertisement_carries_its_ipv6_ends(self):
        advertisement = summaries_of("router-advertisement")[0]

        self.assertEqual(advertisement["protocol"], "ICMPV6")
        self.assertEqual(advertisement["destination"], "ff02::1")
        self.assertTrue(advertisement["source"].startswith("fe80::"))

    def test_no_fixture_packet_is_left_unnamed(self):
        for path in sorted(FIXTURES.glob("*.pdml")):
            for summary in summaries_of(path.stem):
                with self.subTest(fixture=path.stem, number=summary["number"]):
                    self.assertTrue(summary["protocol"])
                    self.assertTrue(summary["description"])
                    self.assertTrue(summary["source"])
                    self.assertTrue(summary["destination"])

    def test_escaped_attribute_text_is_unescaped(self):
        packet = (
            '<packet><proto name="geninfo" pos="0" showname="General information" size="1">'
            '<field name="num" show="1"/><field name="len" show="1"/>'
            '<field name="timestamp" show="x" value="1.5"/></proto>'
            '<proto name="eth" showname="Ethernet"><field name="eth.src" show="a"/>'
            '<field name="eth.dst" show="b"/></proto>'
            '<proto name="tcp" showname="Transmission Control Protocol, Src Port: 1 &#x2192; 2 &amp; more"/>'
            '</packet>'
        )

        self.assertEqual(
            summarize_packet(packet)["description"],
            "Transmission Control Protocol, Src Port: 1 → 2 & more",
        )


if __name__ == "__main__":
    unittest.main()
