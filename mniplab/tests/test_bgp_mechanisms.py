"""BGP path selection, attributes on the wire, session resets and export policy, on the BGP Test Lab.

``sp1`` plays the outside ASes: it offers one network to ``r1`` as ``65001``
and to ``r2`` as ``65001 65002``, so ``r2`` holds two paths that tie on
LOCAL_PREF and differ in length.  Each test captures or reads exactly what
BGP decides or sends: the best path and its reason, the UPDATE that carries
a raised LOCAL_PREF inside the AS, the messages of a session re-opening, and
what an export policy lets reach the Speaker.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set

from mniplab.exabgp.query import SpeakerQueryService
from mniplab.tests.labs import (
    SPEAKER_R1_NETWORK,
    SPEAKER_R2_NETWORK,
    SPEAKER_V4_ROUTE,
    LabCapture,
    LabCase,
    build_bgp_test_lab,
    labels_under,
    preset,
)
from mniplab.utils import wait_until

#: How long the Lab may take to establish its sessions and converge.
CONVERGENCE = 40.0

#: ``SPEAKER_V4_ROUTE`` as the pane shows an announced prefix: its address alone.
ANNOUNCED = SPEAKER_V4_ROUTE.split("/")[0]

#: The ``Minor error Code`` a NOTIFICATION carries when a session is reset.
HARD_RESET = "Minor error Code (Cease): Hard Reset (9)"


class BGPMessage:
    """One BGP message, as one **Border Gateway Protocol** section of a row.

    TCP packs several BGP messages into one segment, so one captured packet
    can hold many: each is read on its own, with the packet's source.
    """

    def __init__(self, source: str, element: ET.Element) -> None:
        self.source = source
        self.fields = labels_under(element)

    def is_a(self, kind: str) -> bool:
        """Whether this is a message of one type, e.g. ``UPDATE``."""
        return any(label.startswith(f"Type: {kind} Message") for label in self.fields.get("bgp.type", []))

    def as_paths(self) -> List[str]:
        """The AS_PATHs this UPDATE carries, as the numbers the pane shows."""
        prefix = "Path Attribute - AS_PATH: "
        return [a[len(prefix):].strip() for a in self.fields.get("bgp.update.path_attribute", [])
                if a.startswith(prefix)]

    def announced(self) -> List[str]:
        """The prefixes this UPDATE announces, as the addresses the pane shows."""
        return [label.split(": ", 1)[1] for label in self.fields.get("bgp.nlri_prefix", [])]


def bgp_messages(packets: List[Dict[str, Any]]) -> List[BGPMessage]:
    """Every BGP message in a Capture's packets, in the order they crossed."""
    return [
        BGPMessage(packet["summary"]["source"], proto)
        for packet in packets
        for proto in ET.fromstring(packet["pdml"]).iter("proto")
        if proto.attrib.get("name") == "bgp"
    ]


def paths(router: Any, prefix: str) -> List[Dict[str, Any]]:
    """A Router's paths to ``prefix``, as ``show bgp ... json`` reports them."""
    return json.loads(router.cmd(f'vtysh -c "show bgp ipv4 unicast {prefix} json"')).get("paths", [])


def best(router: Any, prefix: str) -> Optional[Dict[str, Any]]:
    """The path a Router selected to ``prefix``, or None while it has none."""
    return next((p for p in paths(router, prefix) if p.get("bestpath", {}).get("overall")), None)


def received_by_sp1(net: Any, address: str) -> Set[str]:
    """The prefixes ``sp1`` holds from one Neighbor, read through the Speaker read service."""
    for neighbor in SpeakerQueryService.get_speaker(net, "sp1")["neighbors"]:
        if neighbor["address"] == address:
            return {route["prefix"] for route in neighbor["received"]}
    return set()


class BGPTestLabTests(LabCase):
    """One mechanism of BGP each, on a freshly converged BGP Test Lab."""

    def setUp(self) -> None:
        self.net = self.lab()
        self.nodes = build_bgp_test_lab(self.net)
        self.start(self.net)
        r2 = self.nodes["r2"]
        self.assertTrue(wait_until(
            lambda: len(paths(r2, SPEAKER_V4_ROUTE)) == 2 and best(r2, SPEAKER_V4_ROUTE) is not None,
            CONVERGENCE, interval=0.5,
        ), f"r2 never held both paths: {paths(r2, SPEAKER_V4_ROUTE)}")

    def test_the_shorter_as_path_wins_a_tie(self) -> None:
        r2 = self.nodes["r2"]
        chosen = best(r2, SPEAKER_V4_ROUTE)
        assert chosen is not None

        [other] = [p for p in paths(r2, SPEAKER_V4_ROUTE) if not p.get("bestpath", {}).get("overall")]
        self.assertEqual((chosen["aspath"]["string"], chosen["peer"]["peerId"], chosen["locPrf"]),
                         ("65001", "10.0.3.1", 100))
        # The eBGP path carries no LOCAL_PREF, so it counts as the default 100:
        # the tie is broken further down, by the AS path.
        self.assertEqual((other["aspath"]["string"], other["peer"]["peerId"]),
                         ("65001 65002", "10.0.2.2"))
        self.assertNotIn("locPrf", other)
        self.assertEqual(chosen["bestpath"]["selectionReason"], "AS Path")

    def test_raising_local_pref_on_r2_moves_the_as_onto_the_longer_path(self) -> None:
        # The Speaker offers no route refresh, so the session is reset for
        # its routes to come in again through the route-map.
        raise_local_pref = (
            'vtysh -c "configure terminal" -c "route-map RAISE permit 10" '
            '-c "set local-preference 200" -c "exit" -c "router bgp 65000" '
            '-c "address-family ipv4 unicast" -c "neighbor 10.0.2.2 route-map RAISE in" '
            '-c "end" -c "clear ip bgp 10.0.2.2"')

        def r2_announces_the_route(packet: Dict[str, Any]) -> bool:
            return any(m.source == "10.0.3.2" and ANNOUNCED in m.announced()
                       for m in bgp_messages([packet]))

        captured = bgp_messages(LabCapture(self.net, "r2", "r2-eth1", preset("BGP")).watch(
            "r2", raise_local_pref, 1, r2_announces_the_route))

        # r2's iBGP UPDATE carries the raised LOCAL_PREF to r1 with the path.
        [update] = [m for m in captured if m.source == "10.0.3.2" and ANNOUNCED in m.announced()]
        self.assertEqual(update.as_paths(), ["65001 65002"])
        self.assertIn("Path Attribute - LOCAL_PREF: 200", update.fields["bgp.update.path_attribute"])
        r1, r2 = self.nodes["r1"], self.nodes["r2"]
        chosen = best(r2, SPEAKER_V4_ROUTE)
        assert chosen is not None
        self.assertEqual((chosen["aspath"]["string"], chosen["locPrf"], chosen["peer"]["peerId"]),
                         ("65001 65002", 200, "10.0.2.2"))
        # r1 follows r2 out, though its own exit is shorter.
        self.assertTrue(wait_until(
            lambda: (best(r1, SPEAKER_V4_ROUTE) or {}).get("peer", {}).get("peerId") == "10.0.3.2",
            10, interval=0.5), best(r1, SPEAKER_V4_ROUTE))
        self.assertEqual((best(r1, SPEAKER_V4_ROUTE) or {}).get("locPrf"), 200)
        self.assertTrue(wait_until(
            lambda: "via 10.0.2.2 " in r2.cmd(f"ip route show {SPEAKER_V4_ROUTE}"), 10, interval=0.5),
            r2.cmd(f"ip route show {SPEAKER_V4_ROUTE}"))

    def test_clearing_a_session_shows_it_reopen_and_the_updates_arrive(self) -> None:
        reset_seen: List[BGPMessage] = []

        def speaker_route_after_the_reset(packet: Dict[str, Any]) -> bool:
            # An UPDATE from before the reset does not end the watch.
            for message in bgp_messages([packet]):
                if HARD_RESET in message.fields.get("bgp.notify.minor_error_cease", []):
                    reset_seen.append(message)
                if reset_seen and ANNOUNCED in message.announced():
                    return True
            return False

        # Between two FRR Routers: each announces it may send a Hard Reset.
        captured = bgp_messages(LabCapture(self.net, "r1", "r1-eth2", preset("BGP")).watch(
            "r1", 'vtysh -c "clear ip bgp 10.0.3.2"', 1, speaker_route_after_the_reset, settle=1))

        # The connection closes: everything from here on is the re-opened one.
        resets = [i for i, m in enumerate(captured)
                  if HARD_RESET in m.fields.get("bgp.notify.minor_error_cease", [])]
        self.assertTrue(resets, "no Hard Reset NOTIFICATION was captured")
        before, after = captured[:resets[0]], captured[resets[0]:]
        self.assertTrue([m for m in before if m.is_a("KEEPALIVE")])
        self.assertEqual(after[0].source, "10.0.3.1")

        # It re-opens with each side naming its Autonomous System, then updates.
        opens = [m for m in after if m.is_a("OPEN")]
        self.assertEqual({m.source for m in opens}, {"10.0.3.1", "10.0.3.2"})
        self.assertEqual({label for m in opens for label in m.fields["bgp.open.myas"]},
                         {"My AS: 65000"})
        self.assertEqual({label for m in opens for label in m.fields["bgp.open.holdtime"]},
                         {"Hold Time: 3"})
        kinds = [kind for m in after for kind in ("OPEN", "KEEPALIVE", "UPDATE") if m.is_a(kind)]
        self.assertIn("KEEPALIVE", kinds)
        self.assertLess(kinds.index("OPEN"), kinds.index("UPDATE"))
        from_r1 = [m for m in after if m.source == "10.0.3.1" and ANNOUNCED in m.announced()]
        self.assertEqual([m.as_paths() for m in from_r1], [["65001"]])

    def test_an_export_policy_sends_the_speaker_only_r1s_own_network(self) -> None:
        self.assertTrue(wait_until(
            lambda: {SPEAKER_R1_NETWORK, SPEAKER_R2_NETWORK} <= received_by_sp1(self.net, "10.0.1.1"),
            CONVERGENCE, interval=0.5), received_by_sp1(self.net, "10.0.1.1"))

        self.nodes["r1"].cmd(
            'vtysh -c "configure terminal" '
            f'-c "ip prefix-list OWN seq 5 permit {SPEAKER_R1_NETWORK}" '
            '-c "route-map EXPORT permit 10" -c "match ip address prefix-list OWN" -c "exit" '
            '-c "router bgp 65000" -c "address-family ipv4 unicast" '
            '-c "neighbor 10.0.1.2 route-map EXPORT out" -c "end" -c "clear ip bgp 10.0.1.2 soft out"')

        self.assertTrue(wait_until(
            lambda: received_by_sp1(self.net, "10.0.1.1") == {SPEAKER_R1_NETWORK},
            10, interval=0.5), received_by_sp1(self.net, "10.0.1.1"))
        # r2's own session to sp1 is not r1's to filter.
        self.assertIn(SPEAKER_R2_NETWORK, received_by_sp1(self.net, "10.0.2.1"))
