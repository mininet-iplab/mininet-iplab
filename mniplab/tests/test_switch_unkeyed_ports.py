"""A Switch reports its ports by the unkeyed name a Learner writes."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import pytest

from mniplab.switch import SwitchService
from mniplab.tests.test_switch import FakeSwitch


def keyed_switch() -> FakeSwitch:
    """Build a Switch whose bridge and ports carry the Lab key."""
    switch = FakeSwitch("s1")
    switch.bridge_name = "k7-s1"
    switch.nameToIntf = {"k7-s1-eth1": SimpleNamespace(name="k7-s1-eth1", link=None)}
    switch.intfs = {1: switch.nameToIntf["k7-s1-eth1"]}
    return switch


@pytest.mark.unit
class UnkeyedPortNameTests(unittest.TestCase):
    """Keyed OVS port names leave ManagedSwitch without the Lab key."""

    def test_unkeyed_port_inverts_resolve_port_and_leaves_other_names(self) -> None:
        switch = keyed_switch()
        self.assertEqual(switch.unkeyed_port("k7-s1-eth1"), "s1-eth1")
        self.assertEqual(switch.resolve_port("s1-eth1"), "k7-s1-eth1")
        self.assertEqual(switch.unkeyed_port("other-eth1"), "other-eth1")
        self.assertEqual(switch.unkeyed_port("s1-eth1"), "s1-eth1")

    def test_mac_table_vlans_and_spanning_tree_show_plain_names(self) -> None:
        switch = keyed_switch()
        switch.recorder.respond_to("fdb/show", "port VLAN MAC Age\n1 10 00:00:00:00:00:01 7\n")
        switch.recorder.respond_to("list-ports", "k7-s1-eth1\n")
        switch.recorder.respond_to("get port k7-s1-eth1", "10\naccess\n[]\n")
        switch.recorder.respond_to("list Bridge", json.dumps({
            "headings": ["stp_enable", "rstp_enable", "status", "rstp_status"],
            "data": [[False, True, ["map", []], ["map", []]]]}))
        switch.recorder.respond_to("list Port", json.dumps({
            "headings": ["name", "status", "rstp_status"],
            "data": [["k7-s1-eth1", ["map", []], ["map", [["rstp_port_role", "Root"]]]]]}))

        self.assertEqual(switch.get_mac_table()[0]["port"], "s1-eth1")
        self.assertEqual(list(switch.get_vlans()), ["k7-s1-eth1"])
        vlans = switch.get_plain_vlans()
        self.assertEqual(list(vlans), ["s1-eth1"])
        peer = SimpleNamespace(name="k7-s2-eth3", node=SimpleNamespace(name="s2"))
        link = SimpleNamespace(intf1=switch.nameToIntf["k7-s1-eth1"], intf2=peer)
        switch.nameToIntf["k7-s1-eth1"].link = link
        self.assertEqual(switch.get_plain_vlans()["s1-eth1"]["peer_intf"], "s2-eth3")
        self.assertEqual(vlans["s1-eth1"]["port"], "s1-eth1")
        self.assertEqual(switch.get_spanning_tree()["ports"][0]["port"], "s1-eth1")

    def test_set_port_vlan_accepts_plain_name_and_reports_it(self) -> None:
        switch = keyed_switch()
        switch.recorder.respond_to("list-ports", "k7-s1-eth1\n")
        switch.recorder.respond_to("get port k7-s1-eth1", "50\naccess\n[]\n")
        lab = SimpleNamespace(switches=[switch])
        result = SwitchService.set_port_vlan(lab, "s1", "s1-eth1", "access", vlan=50)
        self.assertTrue(switch.recorder.issued("set port k7-s1-eth1"))
        self.assertEqual(result["port"], "s1-eth1")
        self.assertEqual(result["config"]["tag"], 50)
        self.assertEqual(list(result["ports"]), ["s1-eth1"])
