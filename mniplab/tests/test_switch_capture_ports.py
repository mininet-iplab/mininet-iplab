"""A Switch lists the ports a Packet Capture may be opened on.

A Switch's ports are veth ends in the host's root namespace, shared by
every Lab and the host, so the list is the whole defence: it holds exactly
the ports of this Switch, by the plain name a Learner reads, each paired
with the keyed interface that really exists.  The bridge's own internal
port, the loopback and anything else in the root namespace never appear.
"""

from __future__ import annotations

import unittest
from typing import Any
from types import SimpleNamespace

import pytest

from mniplab.labhandle import LocalLabHandle, OPERATIONS
from mniplab.tests.fakes import FakeIntf, FakeLab, FakeLink, FakeNode
from mniplab.tests.test_switch_unkeyed_ports import keyed_switch


def switch_with_ports() -> Any:
    """A Switch of Lab k7 whose namespace also holds its bridge and loopback."""
    switch = keyed_switch()
    for name in ("k7-s1-eth2", "k7-s1", "lo", "k9-s1-eth1"):
        switch.nameToIntf[name] = SimpleNamespace(name=name, link=None)
    return switch


@pytest.mark.unit
class CapturePortsTests(unittest.TestCase):
    """ManagedSwitch.capture_ports maps plain port names to keyed ones."""

    def test_each_port_is_listed_by_its_plain_name_with_its_keyed_interface(self) -> None:
        ports = switch_with_ports().capture_ports()

        self.assertEqual(ports, {"s1-eth1": "k7-s1-eth1", "s1-eth2": "k7-s1-eth2"})

    def test_the_bridge_the_loopback_and_another_labs_port_are_not_ports(self) -> None:
        keyed = set(switch_with_ports().capture_ports().values())

        for name in ("k7-s1", "lo", "k9-s1-eth1"):
            self.assertNotIn(name, keyed)

    def test_an_unkeyed_bridge_lists_its_ports_unchanged(self) -> None:
        from mniplab.tests.test_switch import FakeSwitch

        switch = FakeSwitch("s1")
        switch.nameToIntf = {"s1-eth1": SimpleNamespace(name="s1-eth1", link=None),
                             "s1": SimpleNamespace(name="s1", link=None)}

        self.assertEqual(switch.capture_ports(), {"s1-eth1": "s1-eth1"})


@pytest.mark.unit
class SwitchPortsHandleTests(unittest.TestCase):
    """The Lab handle serves a Switch's capturable ports, with their peers."""

    def setUp(self) -> None:
        self.switch = switch_with_ports()
        h1 = FakeNode("h1")
        h1_eth0 = FakeIntf("h1-eth0", h1)
        h1.addIntf(h1_eth0)
        self.switch.nameToIntf["k7-s1-eth1"].link = None
        link = FakeLink(h1_eth0, self.switch.nameToIntf["k7-s1-eth1"])
        self.switch.nameToIntf["k7-s1-eth1"].node = self.switch
        lab = FakeLab(h1, switches=[self.switch], links=[link], lab_key="k7")
        self.handle = LocalLabHandle(lab, example="x", key="k7")

    def test_the_operation_is_on_the_rpc_allowlist(self) -> None:
        self.assertIn("switch_ports", OPERATIONS)

    def test_ports_carry_plain_name_keyed_interface_and_peer(self) -> None:
        ports = {p["name"]: p for p in self.handle.switch_ports("s1")}

        self.assertEqual(sorted(ports), ["s1-eth1", "s1-eth2"])
        self.assertEqual(ports["s1-eth1"]["interface"], "k7-s1-eth1")
        self.assertEqual((ports["s1-eth1"]["peer"], ports["s1-eth1"]["drawn"]), ("h1", True))
        self.assertEqual((ports["s1-eth2"]["peer"], ports["s1-eth2"]["drawn"]), (None, False))

    def test_a_node_that_is_not_a_switch_has_no_ports_to_list(self) -> None:
        with self.assertRaises(ValueError):
            self.handle.switch_ports("h1")
        with self.assertRaises(ValueError):
            self.handle.switch_ports("s9")
