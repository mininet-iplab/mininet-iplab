"""Tests for the command recorder test doubles."""

import pytest

from mniplab.tests.fakes import CommandRecorder, FakeIntf, FakeLab, FakeNode


class TestCommandRecording:
    """Commands issued to a node are captured in order."""

    def test_records_commands_in_order(self, fake_node: FakeNode) -> None:
        fake_node.cmd('ip -6 addr add 2001:db8::1/64 dev h1-eth0')
        fake_node.cmd('ip -6 route add default via 2001:db8::2')

        assert fake_node.recorder.commands == [
            'ip -6 addr add 2001:db8::1/64 dev h1-eth0',
            'ip -6 route add default via 2001:db8::2',
        ]

    def test_joins_multipart_commands_like_mininet(self, fake_node: FakeNode) -> None:
        fake_node.cmd('ifconfig', 'h1-eth0', 'up')

        assert fake_node.recorder.commands == ['ifconfig h1-eth0 up']

    def test_issued_filters_by_substring(self, fake_node: FakeNode) -> None:
        fake_node.cmd('ip -6 addr add 2001:db8::1/64 dev h1-eth0')
        fake_node.cmd('tc qdisc change dev h1-eth0 root netem delay 10ms')

        assert fake_node.recorder.issued('ip -6 addr') == [
            'ip -6 addr add 2001:db8::1/64 dev h1-eth0'
        ]
        assert fake_node.recorder.issued_any('tc qdisc') is True
        assert fake_node.recorder.issued_any('iptables') is False

    def test_returns_empty_string_by_default(self, fake_node: FakeNode) -> None:
        assert fake_node.cmd('ip -6 addr add 2001:db8::1/64 dev h1-eth0') == ''


class TestForcedFailures:
    """A test can make a chosen command fail the way a real one would."""

    def test_failure_returns_error_text(self, fake_node: FakeNode) -> None:
        fake_node.recorder.fail_on('ip -6 addr add')

        result = fake_node.cmd('ip -6 addr add 2001:db8::1/64 dev h1-eth0')

        assert 'Operation not permitted' in result

    def test_failure_text_is_configurable(self, fake_node: FakeNode) -> None:
        fake_node.recorder.fail_on('tc qdisc', 'RTNETLINK answers: No such file or directory')

        result = fake_node.cmd('tc qdisc change dev h1-eth0 root netem delay 10ms')

        assert result == 'RTNETLINK answers: No such file or directory'

    def test_only_matching_commands_fail(self, fake_node: FakeNode) -> None:
        fake_node.recorder.fail_on('ip -6 addr add')

        failed = fake_node.cmd('ip -6 addr add 2001:db8::1/64 dev h1-eth0')
        succeeded = fake_node.cmd('ip -6 route add default via 2001:db8::2')

        assert 'Operation not permitted' in failed
        assert succeeded == ''

    def test_failed_commands_are_still_recorded(self, fake_node: FakeNode) -> None:
        fake_node.recorder.fail_on('ip -6 addr add')
        fake_node.cmd('ip -6 addr add 2001:db8::1/64 dev h1-eth0')

        assert fake_node.recorder.issued_any('ip -6 addr add')


class TestCannedResponses:
    """Read-back commands can be given output to parse."""

    def test_responds_to_matching_command(self, fake_node: FakeNode) -> None:
        fake_node.recorder.respond_to(
            'ip -6 addr show', 'inet6 2001:db8::1/64 scope global'
        )

        result = fake_node.cmd('ip -6 addr show dev h1-eth0')

        assert '2001:db8::1/64' in result

    def test_failures_take_precedence_over_responses(self, fake_node: FakeNode) -> None:
        fake_node.recorder.respond_to('ip -6 addr show', 'inet6 2001:db8::1/64')
        fake_node.recorder.fail_on('ip -6 addr show', 'Device does not exist')

        assert fake_node.cmd('ip -6 addr show dev h1-eth0') == 'Device does not exist'


class TestFakeNodeAndIntf:
    """The fakes carry enough node interface for address code to run."""

    def test_fake_intf_belongs_to_its_node(self, fake_intf: FakeIntf, fake_node: FakeNode) -> None:
        assert fake_intf.node is fake_node
        assert fake_intf.name == 'h1-eth0'

    def test_default_intf_returns_first_interface(self, fake_node: FakeNode) -> None:
        first = FakeIntf('h1-eth0', fake_node)
        second = FakeIntf('h1-eth1', fake_node)
        fake_node.intfs = {0: first, 1: second}

        assert fake_node.defaultIntf() is first

    def test_default_intf_is_none_without_interfaces(self) -> None:
        assert FakeNode('h9').defaultIntf() is None

    def test_node_and_recorder_share_history(self, fake_node: FakeNode, command_recorder: CommandRecorder) -> None:
        fake_node.cmd('ip link show')

        assert command_recorder.commands == ['ip link show']

    def test_with_addresses_names_one_interface_per_address(self) -> None:
        node = FakeNode.with_addresses('r1', '10.0.0.1', ('10.1.0.1', '2001:db8::1'), (None, '2001:db8:2::1'))

        assert [intf.name for intf in node.intfList()] == ['r1-eth0', 'r1-eth1', 'r1-eth2']
        assert [(intf.IP(), intf.IP6()) for intf in node.intfList()] == [
            ('10.0.0.1', None), ('10.1.0.1', '2001:db8::1'), (None, '2001:db8:2::1'),
        ]
        assert node.IP() == '10.0.0.1'

    def test_node_defaults_describe_a_plain_visible_host(self) -> None:
        node = FakeNode('h1')

        assert (node.display_type, node.hidden, node.services, node.relays) == ('host', False, [], [])
        assert node.intfList() == []
        assert node.IP() is None


class TestFakeLab:
    """The Lab fake answers the lookups the query services make."""

    def test_get_finds_hosts_and_switches_by_name(self) -> None:
        h1, s1 = FakeNode('h1'), FakeNode('s1')
        lab = FakeLab(h1, switches=[s1])

        assert lab.get('h1') is h1
        assert lab.get('s1') is s1

    def test_get_raises_key_error_like_mininet(self) -> None:
        with pytest.raises(KeyError):
            FakeLab().get('nope')
