"""Unit tests for ManagedSwitch, 802.1Q sub-interfaces, and VLAN CLI commands."""

from __future__ import annotations

from typing import Any
import unittest
from unittest.mock import MagicMock, patch
import pytest

from mniplab.constants import VLAN_MIN, VLAN_MAX
from mniplab.switch import (
    ManagedSwitch,
    SubinterfaceMixin,
    validate_vlan_id,
    parse_vlan_list,
)
from mniplab.net import mnIPLab
from mniplab.router import FRRouter
from mniplab.host import mnIPLabHost
from mniplab.tests.fakes import FakeLab, FakeNode, CommandRecorder, cli_output


class FakeSwitch(ManagedSwitch):
    """Test fake for ManagedSwitch that records commands rather than calling OVS."""

    def __init__(self, name: str = "s1") -> None:
        self.name = name
        self.bridge_name = name
        self.recorder = CommandRecorder()
        self.intfs: dict = {}
        self.ports: dict = {}
        self.nameToIntf: dict = {}
        self._port_vlans: dict = {}

    def cmd(self, *args: Any, **kwargs: Any) -> str:
        cmd_str = " ".join(str(a) for a in args)
        self.recorder.commands.append(cmd_str)
        for match, resp in self.recorder._responses:
            if match in cmd_str:
                return resp
        for match, resp in self.recorder._failures:
            if match in cmd_str:
                return resp
        return ""

    def vsctl(self, *args: Any, **kwargs: Any) -> str:
        return self.cmd("ovs-vsctl", *args, **kwargs)


@pytest.mark.unit
class VLANValidationTests(unittest.TestCase):
    """Tests for VLAN ID and VLAN list validation helpers."""

    def test_validate_vlan_id_valid_integers(self) -> None:
        self.assertEqual(validate_vlan_id(1), 1)
        self.assertEqual(validate_vlan_id(100), 100)
        self.assertEqual(validate_vlan_id(4094), 4094)

    def test_validate_vlan_id_valid_strings(self) -> None:
        self.assertEqual(validate_vlan_id("10"), 10)
        self.assertEqual(validate_vlan_id("4094"), 4094)

    def test_validate_vlan_id_out_of_range(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_vlan_id(0)
        self.assertIn("out of valid range", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_vlan_id(4095)
        self.assertIn("out of valid range", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_vlan_id(-5)
        self.assertIn("out of valid range", str(ctx.exception))

    def test_validate_vlan_id_invalid_types(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_vlan_id("not_a_number")
        self.assertIn("must be an integer", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_vlan_id(None)
        self.assertIn("must be an integer", str(ctx.exception))

    def test_parse_vlan_list_strings_and_sequences(self) -> None:
        self.assertEqual(parse_vlan_list("10,20,30"), [10, 20, 30])
        self.assertEqual(parse_vlan_list("  10 , 20 "), [10, 20])
        self.assertEqual(parse_vlan_list([10, 20]), [10, 20])
        self.assertEqual(parse_vlan_list(["10", "20"]), [10, 20])

    def test_parse_vlan_list_empty_or_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_vlan_list("")
        with self.assertRaises(ValueError):
            parse_vlan_list([])
        with self.assertRaises(ValueError):
            parse_vlan_list("10,invalid")


@pytest.mark.unit
class ManagedSwitchMethodsTests(unittest.TestCase):
    """Tests for ManagedSwitch set_access, set_trunk, clear_vlan, and get_vlans."""

    def setUp(self) -> None:
        self.switch = FakeSwitch("s1")

    def test_set_access_issues_correct_command(self) -> None:
        self.switch.set_access("s1-eth1", 10)
        cmds = self.switch.recorder.issued("set port s1-eth1")
        self.assertEqual(len(cmds), 1)
        self.assertIn("tag=10", cmds[0])
        self.assertIn("vlan_mode=access", cmds[0])
        self.assertIn("trunks=[]", cmds[0])
        self.assertEqual(self.switch._port_vlans["s1-eth1"]["mode"], "access")
        self.assertEqual(self.switch._port_vlans["s1-eth1"]["tag"], 10)

    def test_set_access_validates_vlan(self) -> None:
        with self.assertRaises(ValueError):
            self.switch.set_access("s1-eth1", 5000)

    def test_set_trunk_without_native(self) -> None:
        self.switch.set_trunk("s1-eth2", [10, 20])
        cmds = self.switch.recorder.issued("set port s1-eth2")
        self.assertEqual(len(cmds), 1)
        self.assertIn("tag=[]", cmds[0])
        self.assertIn("vlan_mode=trunk", cmds[0])
        self.assertIn("trunks=[10,20]", cmds[0])
        self.assertEqual(self.switch._port_vlans["s1-eth2"]["mode"], "trunk")
        self.assertEqual(self.switch._port_vlans["s1-eth2"]["trunks"], [10, 20])
        self.assertIsNone(self.switch._port_vlans["s1-eth2"]["native"])

    def test_set_trunk_with_native(self) -> None:
        self.switch.set_trunk("s1-eth2", [10, 20], native=1)
        cmds = self.switch.recorder.issued("set port s1-eth2")
        self.assertEqual(len(cmds), 1)
        self.assertIn("tag=1", cmds[0])
        self.assertIn("vlan_mode=native-untagged", cmds[0])
        self.assertIn("trunks=[10,20]", cmds[0])
        self.assertEqual(self.switch._port_vlans["s1-eth2"]["native"], 1)

    def test_clear_vlan_issues_correct_command(self) -> None:
        self.switch.clear_vlan("s1-eth1")
        cmds = self.switch.recorder.issued("clear port s1-eth1")
        self.assertEqual(len(cmds), 1)
        self.assertIn("tag trunks vlan_mode", cmds[0])
        self.assertEqual(self.switch._port_vlans["s1-eth1"]["mode"], "none")

    def test_get_vlans_parsing(self) -> None:
        self.switch.recorder.respond_to("list-ports s1", "s1-eth1\ns1-eth2\ns1-eth3\n")
        self.switch.recorder.respond_to(
            "get port s1-eth1 tag vlan_mode trunks",
            "10\naccess\n[]\n",
        )
        self.switch.recorder.respond_to(
            "get port s1-eth2 tag vlan_mode trunks",
            "1\ntrunk\n[10, 20]\n",
        )
        self.switch.recorder.respond_to(
            "get port s1-eth3 tag vlan_mode trunks",
            "[]\n[]\n[]\n",
        )

        vlans = self.switch.get_vlans()
        self.assertEqual(len(vlans), 3)

        self.assertEqual(vlans["s1-eth1"]["mode"], "access")
        self.assertEqual(vlans["s1-eth1"]["tag"], 10)
        self.assertEqual(vlans["s1-eth1"]["trunks"], [])

        self.assertEqual(vlans["s1-eth2"]["mode"], "trunk")
        self.assertEqual(vlans["s1-eth2"]["native"], 1)
        self.assertEqual(vlans["s1-eth2"]["trunks"], [10, 20])

        self.assertEqual(vlans["s1-eth3"]["mode"], "none")
        self.assertIsNone(vlans["s1-eth3"]["tag"])
        self.assertIsNone(vlans["s1-eth3"]["native"])


@pytest.mark.unit
class SubinterfaceTests(unittest.TestCase):
    """Tests for add_subinterface helper method on nodes."""

    def test_add_subinterface_commands_and_registration(self) -> None:
        node = FakeNode("r1")
        node.inNamespace = True  # type: ignore[attr-defined]
        node.nameToIntf = {}  # type: ignore[attr-defined]
        node.intfs = {}

        intf = node.add_subinterface(
            "r1-eth1",
            10,
            ip="192.168.10.1/24",
            ip6="2001:db8:10::1/64",
        )

        # Check Linux commands issued
        add_link = node.recorder.issued("ip link add link r1-eth1 name r1-eth1.10 type vlan id 10")
        self.assertEqual(len(add_link), 1)

        set_up = node.recorder.issued("ip link set dev r1-eth1.10 up")
        self.assertEqual(len(set_up), 1)

        add_ip4 = node.recorder.issued("ip addr add 192.168.10.1/24 dev r1-eth1.10")
        self.assertEqual(len(add_ip4), 1)

        add_ip6 = node.recorder.issued("ip -6 addr add 2001:db8:10::1/64 dev r1-eth1.10")
        self.assertEqual(len(add_ip6), 1)

        # Check interface registration
        self.assertIn("r1-eth1.10", node.nameToIntf)  # type: ignore[attr-defined]
        self.assertEqual(node.nameToIntf["r1-eth1.10"], intf)  # type: ignore[attr-defined]
        self.assertEqual(intf.name, "r1-eth1.10")
        self.assertEqual(intf.ip, "192.168.10.1")
        self.assertEqual(intf.prefixLen, "24")
        self.assertEqual(intf.ip6, "2001:db8:10::1")

    def test_add_subinterface_validation_failures(self) -> None:
        node = FakeNode("r1")
        with self.assertRaises(ValueError):
            node.add_subinterface("r1-eth1", 0)

        with self.assertRaises(ValueError):
            node.add_subinterface("r1-eth1", 10, ip="192.168.10.1")  # missing prefix

        with self.assertRaises(ValueError):
            node.add_subinterface("r1-eth1", 10, ip6="2001:db8::1")  # missing prefix

    def test_subinterface_included_in_show_ips(self) -> None:
        node = FakeNode("r1")
        node.add_subinterface("r1-eth1", 10, ip="192.168.10.1/24", ip6="2001:db8:10::1/64")
        node.recorder.respond_to(
            "ip -o -4 addr show",
            "2: r1-eth1.10 inet 192.168.10.1/24 scope global r1-eth1.10\n",
        )
        node.recorder.respond_to(
            "ip -o -6 addr show",
            "2: r1-eth1.10 inet6 2001:db8:10::1/64 scope global r1-eth1.10\n",
        )
        net = FakeLab(node)
        output = cli_output("show_ips", "", net=net)
        self.assertIn("r1 r1-eth1.10: 192.168.10.1", output)
        self.assertIn("r1 r1-eth1.10: 2001:db8:10::1", output)


@pytest.mark.unit
class DeclarativeVLANQueueingTests(unittest.TestCase):
    """Tests for declarative link parameter parsing and queueing."""

    def test_queue_and_apply_vlans(self) -> None:
        net = object.__new__(mnIPLab)
        net._queued_vlan_configs = []
        net._started = False

        sw1 = FakeSwitch("s1")
        h1 = FakeNode("h1")

        # Test queueing access port via params1
        net._queue_switch_vlan(sw1, "s1-eth1", {"vlan": 10})
        self.assertEqual(len(net._queued_vlan_configs), 1)
        self.assertEqual(net._queued_vlan_configs[0], (sw1, "access", "s1-eth1", {"vlan": 10}))

        # Test queueing trunk port via params1
        net._queue_switch_vlan(sw1, "s1-eth2", {"trunks": [10, 20], "native": 1})
        self.assertEqual(len(net._queued_vlan_configs), 2)
        self.assertEqual(
            net._queued_vlan_configs[1],
            (sw1, "trunk", "s1-eth2", {"trunks": [10, 20], "native": 1}),
        )

        # Host node ignored by _queue_switch_vlan
        net._queue_switch_vlan(h1, "h1-eth0", {"vlan": 10})
        self.assertEqual(len(net._queued_vlan_configs), 2)

        # Apply queued configurations
        net._apply_queued_vlans()
        self.assertEqual(len(net._queued_vlan_configs), 0)
        self.assertEqual(sw1._port_vlans["s1-eth1"]["mode"], "access")
        self.assertEqual(sw1._port_vlans["s1-eth1"]["tag"], 10)
        self.assertEqual(sw1._port_vlans["s1-eth2"]["mode"], "trunk")
        self.assertEqual(sw1._port_vlans["s1-eth2"]["trunks"], [10, 20])
        self.assertEqual(sw1._port_vlans["s1-eth2"]["native"], 1)


@pytest.mark.unit
class ShowVLANsCLICommandTests(unittest.TestCase):
    """Tests for mnIPLabCLI.do_show_vlans."""

    def test_show_vlans_no_switches(self) -> None:
        net = FakeLab(switches=[])
        output = cli_output("show_vlans", "", net=net)
        self.assertIn("*** No switches in network", output)

    def test_show_vlans_switch_not_found(self) -> None:
        sw1 = FakeSwitch("s1")
        net = FakeLab(switches=[sw1])
        output = cli_output("show_vlans", "unknown_sw", net=net)
        self.assertIn("Error: Switch 'unknown_sw' not found", output)

    def test_show_vlans_target_not_a_switch(self) -> None:
        sw1 = FakeSwitch("s1")
        h1 = FakeNode("h1")
        net = FakeLab(h1, switches=[sw1])
        output = cli_output("show_vlans", "h1", net=net)
        self.assertIn("Error: Node 'h1' is not a switch", output)

    def test_show_vlans_aligned_output(self) -> None:
        sw1 = FakeSwitch("s1")
        sw1._port_vlans = {
            "s1-eth1": {
                "port": "s1-eth1",
                "mode": "access",
                "tag": 10,
                "trunks": [],
                "native": None,
                "peer_node": "h1",
                "peer_intf": "h1-eth0",
            },
            "s1-eth2": {
                "port": "s1-eth2",
                "mode": "trunk",
                "tag": None,
                "trunks": [10, 20],
                "native": 1,
                "peer_node": "r1",
                "peer_intf": "r1-eth1",
            },
        }
        net = FakeLab(switches=[sw1])
        output = cli_output("show_vlans", "", net=net)

        self.assertIn("Switch", output)
        self.assertIn("Port", output)
        self.assertIn("Connected Peer", output)
        self.assertIn("Mode", output)
        self.assertIn("VLAN Tag(s)", output)

        self.assertIn("s1-eth1", output)
        self.assertIn("h1 (h1-eth0)", output)
        self.assertIn("access", output)
        self.assertIn("10", output)

        self.assertIn("s1-eth2", output)
        self.assertIn("r1 (r1-eth1)", output)
        self.assertIn("trunk", output)
        self.assertIn("10, 20 (native: 1)", output)


@pytest.mark.unit
class VLANCLICommandTests(unittest.TestCase):
    """Tests for mnIPLabCLI.do_vlan."""

    def setUp(self) -> None:
        self.sw1 = FakeSwitch("s1")
        self.sw1.nameToIntf = {"s1-eth1": MagicMock(name="s1-eth1")}
        self.net = FakeLab(switches=[self.sw1])

    def test_vlan_usage_on_missing_args(self) -> None:
        output = cli_output("vlan", "", net=self.net)
        self.assertIn("Usage: vlan <switch> <port> <access|trunk|none>", output)

        output = cli_output("vlan", "s1 s1-eth1", net=self.net)
        self.assertIn("Usage: vlan <switch> <port> <access|trunk|none>", output)

    def test_vlan_unknown_switch(self) -> None:
        output = cli_output("vlan", "unknown s1-eth1 access 10", net=self.net)
        self.assertIn("Error: Switch 'unknown' not found", output)

    def test_vlan_unknown_port(self) -> None:
        output = cli_output("vlan", "s1 non-existent-port access 10", net=self.net)
        self.assertIn("Error: Port 'non-existent-port' not found on switch 's1'", output)

    def test_vlan_invalid_mode(self) -> None:
        output = cli_output("vlan", "s1 s1-eth1 invalid_mode 10", net=self.net)
        self.assertIn("Error: Mode must be 'access', 'trunk', or 'none'", output)

    def test_vlan_access_configuration(self) -> None:
        # Missing VLAN ID
        output = cli_output("vlan", "s1 s1-eth1 access", net=self.net)
        self.assertIn("Error: Access mode requires exactly one VLAN ID", output)

        # Extra tokens
        output = cli_output("vlan", "s1 s1-eth1 access 10 extra", net=self.net)
        self.assertIn("Error: Access mode requires exactly one VLAN ID", output)

        # Invalid VLAN ID
        output = cli_output("vlan", "s1 s1-eth1 access 5000", net=self.net)
        self.assertIn("out of valid range", output)

        # Successful configuration
        output = cli_output("vlan", "s1 s1-eth1 access 10", net=self.net)
        self.assertIn("*** Configured s1 port s1-eth1 as access VLAN 10", output)
        self.assertEqual(self.sw1._port_vlans["s1-eth1"]["mode"], "access")
        self.assertEqual(self.sw1._port_vlans["s1-eth1"]["tag"], 10)

    def test_vlan_trunk_configuration(self) -> None:
        # Missing VLAN IDs
        output = cli_output("vlan", "s1 s1-eth1 trunk", net=self.net)
        self.assertIn("Error: Trunk mode requires VLAN ID(s)", output)

        # Extra tokens (more than 5)
        output = cli_output("vlan", "s1 s1-eth1 trunk 10,20 1 extra", net=self.net)
        self.assertIn("Error: Trunk mode requires VLAN ID(s)", output)

        # Successful configuration without native
        output = cli_output("vlan", "s1 s1-eth1 trunk 10,20", net=self.net)
        self.assertIn("*** Configured s1 port s1-eth1 as trunk VLANs [10, 20]", output)
        self.assertEqual(self.sw1._port_vlans["s1-eth1"]["mode"], "trunk")
        self.assertEqual(self.sw1._port_vlans["s1-eth1"]["trunks"], [10, 20])
        self.assertIsNone(self.sw1._port_vlans["s1-eth1"]["native"])

        # Successful configuration with native
        output = cli_output("vlan", "s1 s1-eth1 trunk 10,20 1", net=self.net)
        self.assertIn("with native VLAN 1", output)
        self.assertEqual(self.sw1._port_vlans["s1-eth1"]["native"], 1)

    def test_vlan_none_configuration(self) -> None:
        # Extra tokens
        output = cli_output("vlan", "s1 s1-eth1 none extra", net=self.net)
        self.assertIn("Error: None mode takes no additional arguments", output)

        output = cli_output("vlan", "s1 s1-eth1 none", net=self.net)
        self.assertIn("*** Cleared VLAN configuration on s1 port s1-eth1", output)
        self.assertEqual(self.sw1._port_vlans["s1-eth1"]["mode"], "none")
