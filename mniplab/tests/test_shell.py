"""Unit and integration tests for the binary-safe Node shell decoder."""


import pytest
from mininet.util import getincrementaldecoder

from mniplab.host import mnIPLabHost
from mniplab.nodelib import MasqueradeNAT
from mniplab.router import FRRouter
from mniplab.shell import BinarySafeShellMixin
from mniplab.tests.labs import LabCase

# The opening bytes of a dhcpcd lease file: a raw BOOTREPLY packet, as
# `cat /var/lib/dhcpcd/h1-eth0.lease` puts on a Node's pty. 0x89 is not a
# valid UTF-8 start byte, which is what poisons a strictly-decoded shell.
LEASE_HEADER = b'\x02\x01\x06\x00\x89\xb2\xe5\xe9'


class _StubBase:
    """Stands in for Mininet's Node: owns a decoder, records startShell."""

    def __init__(self) -> None:
        self.decoder = getincrementaldecoder()
        self.started_with = 'not started'
        self.lastCmd = ''
        self.find_pid_seen = None

    def startShell(self, mnopts=None) -> None:
        self.started_with = mnopts

    def sendCmd(self, *args, **kwargs) -> None:
        self.lastCmd = args[0] if args else ''

    def waitOutput(self, verbose=False, findPid=True) -> str:
        self.find_pid_seen = findPid
        return ''


class _BinarySafeNode(BinarySafeShellMixin, _StubBase):
    """The mixin over a stub Node base."""


@pytest.mark.unit
class TestStockDecoderIsStrict:
    """Documents the defect the mixin exists to close."""

    def test_stock_decoder_raises_on_a_lease_file(self):
        with pytest.raises(UnicodeDecodeError):
            getincrementaldecoder().decode(LEASE_HEADER)


@pytest.mark.unit
class TestBinarySafeShellMixin:
    """A Node's shell survives binary output on its pty."""

    def test_decoder_replaces_undecodable_bytes(self):
        node = _BinarySafeNode()
        node.startShell()

        # 0x89 and 0xb2 are each undecodable; 0xe5 starts a 3-byte sequence
        # 0xe9 cannot continue, so it too is replaced. The trailing 0xe9 is
        # held back by the incremental decoder as a possible continuation.
        assert node.decoder.decode(LEASE_HEADER) == '\x02\x01\x06\x00\ufffd\ufffd\ufffd'

    def test_still_starts_the_underlying_shell(self):
        node = _BinarySafeNode()
        node.startShell('-cdn')

        assert node.started_with == '-cdn'

    @pytest.mark.parametrize('cls', [mnIPLabHost, FRRouter, MasqueradeNAT])
    def test_learner_facing_nodes_carry_the_mixin(self, cls):
        assert issubclass(cls, BinarySafeShellMixin)


@pytest.mark.unit
class TestPidMarkerGuard:
    """chr(1) in command output must not be mistaken for a PID marker.

    Mininet completes a marker with an unbounded blocking read, so a Node
    that cats binary containing 0x01 -- a DHCP lease's htype byte -- hangs
    forever unless the marker search is declined up front.
    """

    def test_a_plain_command_declines_the_marker_search(self):
        node = _BinarySafeNode()
        node.sendCmd('cat /var/lib/dhcpcd/h1-eth0.lease')
        node.waitOutput()

        assert node.find_pid_seen is False

    def test_a_backgrounded_command_keeps_it(self):
        node = _BinarySafeNode()
        node.sendCmd('sleep 30 &')
        node.waitOutput()

        assert node.find_pid_seen is True

    def test_an_explicit_printPid_keeps_it(self):
        node = _BinarySafeNode()
        node.sendCmd('sleep 30', printPid=True)
        node.waitOutput()

        assert node.find_pid_seen is True


@pytest.mark.unit
class TestContainerHostShell:
    """ContainerHost builds its own shell, so it installs the decoder itself."""

    def test_startShell_installs_the_lenient_decoder(self):
        from mniplab.container import ContainerHost

        node = object.__new__(ContainerHost)
        node.name = 'c1'
        node.decoder = getincrementaldecoder()
        # A shell already running makes startShell return early -- after the
        # decoder is installed, which is the whole point of the assertion.
        node.shell = object()

        node.startShell()

        assert node.decoder.decode(LEASE_HEADER) == '\x02\x01\x06\x00\ufffd\ufffd\ufffd'


class BinaryOutputDoesNotPoisonTheShellTests(LabCase):
    """The end-to-end failure a Learner hits: cat a binary file over the CLI."""

    def test_cat_of_a_binary_file_leaves_the_lab_usable(self):
        # The bytes carry both failure modes: 0x89 is undecodable (the crash)
        # and 0x01 looks like the start of a PID marker (the hang).
        net = self.lab()
        h1 = net.addHost('h1')
        h2 = net.addHost('h2')
        net.addLink(h1, h2, params1={'ip': '10.0.0.2/24'}, params2={'ip': '10.0.0.3/24'})
        self.start(net)

        h1.cmd(r"printf '\x02\x01\x06\x00\x89\xb2\xe5\xe9' > /tmp/binary.lease")
        h1.cmd('cat /tmp/binary.lease')

        assert 'alive' in h1.cmd('echo alive'), 'the Node shell was poisoned'
