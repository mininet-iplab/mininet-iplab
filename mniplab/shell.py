"""The binary-safe Node shell seam.

Mininet reads a Node's pty through a strict UTF-8 incremental decoder
(``Node.read``), so a single undecodable byte on that stream raises
``UnicodeDecodeError`` inside ``cmd()``. The exception escapes mid-command,
leaving ``self.waiting`` set, and every later ``cmd()`` on that Node trips
``sendCmd``'s ``assert self.shell and not self.waiting``. The Node is then
unusable for the rest of the Lab and ``net.stop()`` cannot tear it down,
leaking namespaces and the ephemeral private dirs with them.

Decoding leniently is necessary but not sufficient. ``Node.monitor`` treats
``chr(1)`` as the start of the PID marker a backgrounded command reports,
and completes it with an *unbounded* ``while not re.findall(marker, data):
data += self.read(1024)``. Binary output that happens to contain ``\x01``
and no marker makes that loop block on ``os.read`` forever. A DHCP lease
lands on this too: ``htype`` is the packet's second byte and its value for
Ethernet is 1. So the same ``cat`` that crashed the shell before would hang
it after -- which is why this mixin also declines the marker search for
commands that never asked for one.

Reaching that state takes one ordinary Learner command. ``cat`` on
``/var/lib/dhcpcd/<intf>.lease`` is the one the DHCP Lab invites: dhcpcd
stores a Lease as the raw BOOTREPLY packet, not as text.

The Web UI Terminal never had this problem -- it is a real pty(7) pair and
decodes with ``errors='ignore'`` (see :mod:`mniplab.webserver.websocket`) --
so this closes the gap between the two Terminal entry points.
"""

from typing import Any, Optional

from mininet.util import getincrementaldecoder


class BinarySafeShellMixin:
    """Decodes a Node's pty leniently, so binary output cannot poison it.

    Mixed into the Node classes a Learner drives directly, ahead of Mininet's
    ``Node`` in the MRO. ``errors='replace'`` rather than ``'ignore'``: a
    Learner who cats a binary file should see that the output was not text,
    which replacement characters show and dropped bytes hide.
    """

    def install_binary_safe_decoder(self) -> None:
        """Replace this Node's strict pty decoder with a lenient one.

        Called from ``startShell`` below, and separately by
        :meth:`mniplab.container.ContainerHost.startShell`, which builds its
        own ``docker exec`` shell and so never reaches this mixin's override
        while still reading it through ``Node.read``.
        """
        # Node.__init__ builds the strict decoder before it calls startShell,
        # so replacing it here catches every read the shell will ever serve.
        self.decoder = getincrementaldecoder('replace')

    def startShell(self, mnopts: Optional[str] = None, **kwargs: Any) -> None:
        """Install a lenient decoder, then start the shell as usual.

        Args:
            mnopts: Forwarded unchanged to the underlying ``startShell``.
            **kwargs: Forwarded unchanged, for Node classes that take more.
        """
        self.install_binary_safe_decoder()
        super().startShell(mnopts, **kwargs)  # type: ignore[misc]

    def sendCmd(self, *args: Any, **kwargs: Any) -> None:
        """Send a command, recording whether it can produce a PID marker.

        A marker is only ever emitted for a backgrounded command (``sendCmd``
        appends the ``printf`` itself) or when ``printPid`` was asked for.
        Anything else that shows ``chr(1)`` is command output, not a marker.
        """
        super().sendCmd(*args, **kwargs)  # type: ignore[misc]
        last_cmd = getattr(self, 'lastCmd', '') or ''
        self._pid_marker_expected = (
            bool(kwargs.get('printPid', False)) or last_cmd.rstrip().endswith('&')
        )

    def waitOutput(self, verbose: bool = False, findPid: bool = True) -> str:
        """Wait for the command to finish, skipping a marker never sent.

        Args:
            verbose: Forwarded unchanged.
            findPid: Forwarded, but forced off when the command in flight
                cannot have emitted a marker -- otherwise a ``chr(1)`` in its
                output sends ``monitor`` into an unbounded blocking read.
        """
        if findPid and not getattr(self, '_pid_marker_expected', True):
            findPid = False
        return super().waitOutput(verbose, findPid)  # type: ignore[misc]
