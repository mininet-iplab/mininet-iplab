"""The Private Namespace Node capability seam.

Answers one question — does this Node have its own PID and UTS namespaces? —
for every caller that needs to key a decision off it. The attribute a Node
class carries names the *property* (``private_namespace_capable``), never the
mechanism that delivers it: a Container Host has the property from Docker, a
Host or Router will get it from ``unshare``, and both must answer the same
way. This is a fidelity feature and never a security boundary: it exists so
``ps`` and ``hostname`` inside a Node show that Node, not so a Node can be
trusted to contain what runs in it.

``MNIPLAB_PRIVATE_NAMESPACES=0`` is a temporary escape hatch, documented in
``.env.example``, that makes :func:`has_private_namespaces` answer no for
every Node — a Container Host included, since the hatch reports the truth
about what a Lab actually has rather than a class's opinion of itself.

This module also owns how a process *attaches* to a Node. ``mnexec -a`` is
abandoned: ``setns`` on a PID namespace moves only future children and
``mnexec`` never forks after it, so every attach would land in the root PID
namespace with no ``/proc`` entry. The shared recipe here — one flag set,
one argv — is what keeps the Web UI Terminal spawner and ``Node.popen()``
from drifting apart; ``util/connect_node.sh`` restates it in shell and its
header comment says so.

``Node.popen()`` gets the same treatment via
:func:`install_popen_guard`: a capable Node's spawned command runs through
the shared nsenter recipe, and the returned handle is a :class:`PopenGroup`
that signals the process group instead of a single pid, because nsenter's
forked wrapper is not the process a caller means to kill.

Finally, this module owns how a Node's shell is *launched* in the first
place: :class:`NamespaceMixin` replaces ``mnexec -cdn`` with stock
``unshare --net --mount --uts --pid``, so the Node the rest of this module
attaches to is real.
"""

import os
import pty
import select
from subprocess import Popen
from typing import Any, List, Optional, Sequence

# Re-exported so callers can `from mniplab.namespace import NamespaceError`
# alongside the capability helper, without needing to know it lives in the
# project's shared exception hierarchy.
from mniplab.errors import NamespaceError, NamespaceGuardError

PRIVATE_NAMESPACES_ENV_VAR = 'MNIPLAB_PRIVATE_NAMESPACES'

__all__ = [
    'NamespaceError',
    'NamespaceGuardError',
    'NamespaceMixin',
    'PopenGroup',
    'build_nsenter_argv',
    'has_private_namespaces',
    'install_netns_fallback_guard',
    'install_popen_guard',
    'PRIVATE_NAMESPACES_ENV_VAR',
]


def has_private_namespaces(node: Any) -> bool:
    """Return whether ``node`` has its own PID and UTS namespaces.

    Reads the ``private_namespace_capable`` class attribute a Node declares
    (``False`` for any Node that does not declare one) and honours the escape
    hatch: with ``MNIPLAB_PRIVATE_NAMESPACES=0`` this answers ``False`` for
    every Node regardless of what it declares, because the hatch describes
    what the Lab actually has, not what a class believes about itself.

    Args:
        node: A Node instance (or class) to check. Only its
            ``private_namespace_capable`` attribute is read; the Node need
            not be fully initialized.

    Returns:
        True if the Node has, or will acquire, its own PID and UTS
        namespaces.
    """
    if not _hatch_enabled():
        return False
    return bool(getattr(node, 'private_namespace_capable', False))


def _hatch_enabled() -> bool:
    """Return whether the private-namespaces feature is on.

    Off only when the environment variable is explicitly ``'0'``; on by
    default, including when the variable is unset or set to anything else.
    """
    return os.getenv(PRIVATE_NAMESPACES_ENV_VAR, '1') != '0'


# ----------------------------------------------------------------------------
# Attaching to a Node: the shared nsenter recipe
#
# One flag set, decided once. The Web UI Terminal spawner and ``Node.popen()``
# build their argv here; ``util/connect_node.sh`` restates the recipe in shell
# and carries a comment naming this module as the source of truth. The flag
# set is a single fact used in two languages.


def build_nsenter_argv(
    node_pid: int, command: Sequence[str]
) -> List[str]:
    """Build the argv that runs ``command`` inside a Node's namespaces.

    Enters the target's mount, UTS, network and PID namespaces — the four a
    Private Namespace Node gets — and never the IPC namespace: no Node in a
    Lab has a private one, so entering the target's would change nothing and
    the flag would only narrow which pids are valid targets.

    The PID-namespace entry forces ``nsenter`` to fork, so the process that
    runs ``command`` is a *child* of the ``nsenter`` process: callers that
    signal or reap must resolve that child rather than trusting the pid they
    spawned. ``--no-fork`` is not an option — it would leave the shell outside
    the PID namespace and missing from its own ``ps``.

    ``--`` separates the recipe from the command so a command whose first word
    looks like an ``nsenter`` option is passed through untouched. It is not
    required — option parsing stops at the first non-option — but both entry
    points interpolate caller-supplied words into the command.

    With the escape hatch off this enters the namespaces the target actually
    has, which for a stock Node is the same net and mount pair ``mnexec -a``
    entered — one recipe covers both modes and needs no hatch branch.

    Args:
        node_pid: PID of a process inside the Node's namespaces (the
            Namespace Anchor for a Private Namespace Node; ``node.pid``
            satisfies this invariant by construction).
        command: The command and its arguments to run inside the Node.

    Returns:
        The full argv, starting with ``nsenter``.
    """
    return [
        'nsenter',
        '-t', str(node_pid),
        '-m', '-u', '-n', '-p',
        '--',
        *command,
    ]


# ----------------------------------------------------------------------------
# The `netns 1` fallback guard
#
# Mininet's makeIntfPair (mininet/util.py) computes ``netns = 1 if not node2``
# and runs the veth creation through node1's shell: ``1`` means "the root
# network namespace" because Mininet assumes the shell lives in the root PID
# namespace. Once a Private Namespace Node's shell has its own PID namespace,
# the kernel resolves pid ``1`` in the *caller's* namespace, where it names
# the Node itself — the command exits 0 while placing the interface in the
# wrong namespace, and nothing downstream reports it. Succeeding while wrong
# is the worst outcome a link can have, so the ambiguous case must fail closed
# instead.


def _ambiguous_caller(node: Any) -> Optional[str]:
    """Return the name of a capable caller, or None when the call is safe.

    Reads the capability helper so the guard stays inert while the feature
    is off. A caller is ambiguous only when its shell carries the node's
    name (``mininet:<name>``); Mininet's own module-level calls pass no
    caller at all, and identifying them by pid ``1`` would misattribute the
    host's init to a Node.
    """
    if node is None:
        return None
    name = getattr(node, 'name', None)
    if not name or not has_private_namespaces(node):
        return None
    try:
        with open('/proc/self/cmdline', 'rb') as cmdline:
            argv = cmdline.read().decode(errors='replace').split('\0')
    except OSError:
        return None
    if f'mininet:{name}' not in argv:
        return None
    return str(name)


def guard_netns_fallback(caller: Any, description: str) -> None:
    """Fail closed when a Mininet ``netns 1`` fallback would resolve wrongly.

    Args:
        caller: The Node whose shell is issuing the command, or None when
            the command is issued from the host (Mininet's ``quietRun``).
        description: What Mininet was trying to do, for the error message.

    Raises:
        NamespaceGuardError: The caller is a Private Namespace Node, so a
            ``netns 1`` fallback would silently place the interface in the
            Node itself while returning success.
    """
    name = _ambiguous_caller(caller)
    if name is None:
        return
    raise NamespaceGuardError(
        f"Refusing to {description} from node '{name}': Mininet's 'netns 1' "
        f"fallback would silently place the interface in '{name}' itself "
        f"rather than the root namespace. Links touching a Private Namespace "
        f"Node must be created host-side (fast=False)."
    )


def install_netns_fallback_guard() -> None:
    """Wrap Mininet's makeIntfPair/moveIntfNoRetry with the fallback guard.

    Idempotent. Installed at the mnIPLab composition root so plain-Mininet
    callers keep upstream behaviour.
    """
    from mininet import util  # deferred: Mininet import cost is not namespace's

    if getattr(util.makeIntfPair, '_mniplab_netns_guarded', False):
        return

    def _check(caller: Any, description: str) -> None:
        # The hatch is consulted per call, not at install time, so a Lab that
        # flips it mid-process sees the guard change state immediately.
        if _hatch_enabled():
            guard_netns_fallback(caller, description)

    def makeIntfPairGuarded(  # pylint: disable=too-many-arguments,unused-argument
        intf1: str, intf2: str,
        addr1: Any = None, addr2: Any = None,
        node1: Any = None, node2: Any = None,
        *args: Any, **kwargs: Any,
    ) -> Any:
        """Reject the fallback when node2 is absent and node1's shell is private."""
        if node2 is None:
            _check(node1, f"create veth pair ({intf1}, {intf2})")
        return _original_make_intf_pair(
            intf1, intf2, addr1, addr2, node1, node2, *args, **kwargs
        )

    def moveIntfNoRetryGuarded(
        intf: str, dstNode: Any, *args: Any, **kwargs: Any
    ) -> Any:
        """Reject moving an interface to a capable Node from its own shell."""
        if dstNode is not None and dstNode.inNamespace:
            _check(dstNode, f"move interface '{intf}' to it")
        return _original_move_intf_no_retry(intf, dstNode, *args, **kwargs)

    _original_make_intf_pair = util.makeIntfPair
    _original_move_intf_no_retry = util.moveIntfNoRetry
    makeIntfPairGuarded._mniplab_netns_guarded = True  # type: ignore[attr-defined]
    util.makeIntfPair = makeIntfPairGuarded
    util.moveIntfNoRetry = moveIntfNoRetryGuarded

    # mininet.link binds its own reference (`from mininet.util import
    # makeIntfPair`) at import time, so rebinding on mininet.util alone leaves
    # the real fast-path call site — Link.__init__ -> Link.makeIntfPair ->
    # mininet.link.makeIntfPair — unguarded. Patch both names.
    from mininet import link as mininet_link  # deferred, as above

    mininet_link.makeIntfPair = makeIntfPairGuarded


# ----------------------------------------------------------------------------
# Node.popen(): the shared nsenter attach, with a process-group-signalling
# handle
#
# The third mnexec -a call site after the Terminal spawner and
# connect_node.sh. Node.popen is where Mininet's own iperf and the
# network-tools iperf3 server start — precisely the processes a Learner
# would look for in ps — so they attach with the same recipe. Because
# nsenter must fork to enter a PID namespace, the Popen pid is the
# wrapper's; a plain terminate() kills the wrapper and orphans the command
# inside the Node. The handle that comes back therefore signals the
# process group, and the child is started in a new session so that it has
# one. The override defaults the Mininet command prefix rather than
# forcing it, so a caller-supplied mncmd — ContainerHost's `docker exec` —
# still wins.


class PopenGroup(Popen):
    """A :class:`subprocess.Popen` that signals its process group.

    The process this class represents was started with
    ``start_new_session=True``, so it is a session leader whose pid and
    group id agree: one ``killpg`` reaches the wrapper, the command, and
    anything the command spawned — nothing inside the Node is orphaned.

    ``send_signal`` is the single point where ``terminate()`` and
    ``kill()`` deliver their signal in CPython, so overriding it keeps
    the stock contract — poll first, skip a process that already died,
    swallow the exit race — and changes only the target. Attach binaries
    that do not make their child a group leader (a bare ``docker exec``
    client) leave ``killpg`` with no group to find; the signal then falls
    back to the pid, which is what stock ``Popen`` did.

    Every other caller of :class:`subprocess.Popen` keeps stock behaviour.
    """

    def send_signal(self, sig: int) -> None:
        """Send ``sig`` to this process's group rather than the pid alone.

        Args:
            sig: The signal number (set by ``terminate()``/``kill()``).

        Raises:
            (nothing): a process that exited between the poll and the
                signal is swallowed, exactly like
                :meth:`subprocess.Popen.send_signal`.
        """
        self.poll()
        if self.returncode is not None:
            # Skip signalling a process that we know has already died.
            return
        try:
            os.killpg(self.pid, sig)
        except (ProcessLookupError, PermissionError):
            # The group is gone — or the attach binary never made its
            # child a group leader, so there is no group to signal.
            try:
                os.kill(self.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass


def install_popen_guard() -> None:
    """Route capable Nodes' ``popen()`` through the shared nsenter recipe.

    Installs a ``Node.popen``/``Node._popen`` override on Mininet's class,
    mirroring :func:`install_netns_fallback_guard`: installed at the
    mnIPLab composition root so plain-Mininet callers keep upstream
    behaviour, idempotent, and the escape hatch answered per call. A
    capable Node's command is wrapped in :func:`build_nsenter_argv`
    against ``node.pid`` (the Namespace Anchor); every other Node keeps
    stock ``mnexec -da`` — with the feature off, both paths do what
    ``mnexec -a`` did. A caller-supplied ``mncmd`` (ContainerHost's
    ``docker exec``) always wins over the default, so the override
    *defaults* the prefix rather than forcing it.

    The nsenter child is started with ``start_new_session`` unless the
    caller supplied its own ``preexec_fn`` — a session is what gives the
    child the process group :class:`PopenGroup` signals — and the
    returned handle is a :class:`PopenGroup`, so terminating it kills the
    process inside the Node and orphans nothing. Callers of ``popen()`` —
    the network-tools path included — keep their existing contract
    unchanged.
    """
    from mininet import node as mininet_node  # deferred, as the netns guard

    if getattr(mininet_node.Node.popen, '_mniplab_popen_guarded', False):
        return

    _stock_popen = mininet_node.Node.popen

    def popenGuarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Node.popen, with capable Nodes attaching through nsenter.

        Defaults the Mininet command prefix to the shared nsenter recipe
        against ``self.pid`` — a caller-supplied ``mncmd`` (ContainerHost's
        ``docker exec``) still wins, and every other Node keeps stock
        ``mnexec -da``. The stock method then parses the command and
        applies its defaults unchanged.
        """
        if (
            'mncmd' not in kwargs
            and has_private_namespaces(self)
            and getattr(self, 'pid', None)
        ):
            kwargs['mncmd'] = build_nsenter_argv(self.pid, [])
        return _stock_popen(self, *args, **kwargs)

    def _popenGuarded(self: Any, cmd: List[str], **params: Any) -> Any:
        """Node._popen, with the process-group-signalling handle in its
        place — and a new session, for the nsenter path only.

        The nsenter attach must make its child a session leader so the
        wrapper, the command and the command's children share one process
        group for :class:`PopenGroup` to signal. ``start_new_session``
        cannot be defaulted across the board, though: the stock path's
        ``mnexec -da`` also calls ``setsid()`` in its child, and a child
        that already leads a process group makes ``setsid`` fork — the
        wrapper would exit immediately and orphan the command. (Measured
        2026-08-29: ``mnexec -da <pid> sleep 4`` under
        ``start_new_session`` returned rc 0 while the sleep was still
        running, reparented to init.)
        """
        from mininet.log import debug  # deferred, as above

        if (
            cmd
            and os.path.basename(cmd[0]) == 'nsenter'
            and params.get('preexec_fn') is None
            and 'start_new_session' not in params
        ):
            params['start_new_session'] = True
        popen = PopenGroup(cmd, **params)
        debug('_popen', cmd, popen.pid)
        return popen

    popenGuarded._mniplab_popen_guarded = True  # type: ignore[attr-defined]
    mininet_node.Node.popen = popenGuarded
    mininet_node.Node._popen = _popenGuarded


# ----------------------------------------------------------------------------
# Launching a Private Namespace Node's shell
#
# The mechanism: stock `unshare --net --mount --uts --pid --fork
# --kill-child` in place of `mnexec -cdn`. `unshare(2)`'s PID namespace
# applies only to *future children* of the calling thread, so the process
# Mininet spawns (node.shell.pid, the Launch Supervisor) stays in the
# caller's PID namespace throughout; only its `--fork`ed child enters the
# new one and becomes the Namespace Anchor `node.pid` must name. `--fork`
# guarantees that child is the launcher's only one, so
# `/proc/<launcher>/task/<launcher>/children` names it -- recovering the
# anchor pid a second way was ruled out; a mismatch here is exactly the
# class of silent-wrong-namespace bug this code already fails closed
# against, so recovery failure raises rather than falling back to the
# launcher's own pid.


def _recover_namespace_anchor_pid(launcher_pid: int, node_name: str) -> int:
    """Recover the Namespace Anchor pid of a just-launched unshare wrapper.

    Args:
        launcher_pid: pid of the ``unshare`` process Mininet spawned (the
            Launch Supervisor, i.e. ``node.shell.pid``).
        node_name: Node name, used only to make the error message useful.

    Returns:
        The pid of the launcher's forked child -- the process that is PID 1
        inside the Node's new PID namespace.

    Raises:
        NamespaceError: The launcher's child list could not be read, or did
            not contain exactly one pid. Never falls back to the launcher's
            own pid: that pid is a member of the *caller's* PID namespace,
            not the Node's, and using it would silently reintroduce the
            broken-`/proc` failure this whole mechanism exists to avoid.
    """
    children_path = f'/proc/{launcher_pid}/task/{launcher_pid}/children'
    try:
        with open(children_path) as f:
            children = f.read().split()
    except OSError as e:
        raise NamespaceError(
            f"{node_name}: could not recover the Namespace Anchor pid: "
            f"reading {children_path} failed: {e}"
        ) from e
    if len(children) != 1:
        raise NamespaceError(
            f"{node_name}: could not recover the Namespace Anchor pid: "
            f"expected the unshare launcher (pid {launcher_pid}) to have "
            f"exactly one child, found {children or 'none'}"
        )
    try:
        return int(children[0])
    except ValueError as e:
        raise NamespaceError(
            f"{node_name}: could not recover the Namespace Anchor pid: "
            f"launcher child {children[0]!r} is not a pid"
        ) from e


class NamespaceMixin:
    """Gives a Node its own PID and UTS namespaces when it starts its shell.

    Mixed into :class:`mniplab.host.mnIPLabHost` and
    :class:`mniplab.router.FRRouter`, ahead of Mininet's ``Node`` in the MRO,
    so ``super().startShell()`` still reaches the stock ``mnexec -cdn`` path
    when the escape hatch is off. :class:`mniplab.container.ContainerHost`
    defines its own ``startShell`` and never runs this one -- Docker already
    gives it the property (see :mod:`mniplab.namespace`'s module docstring).

    A subclass rather than mixin was rejected: the FRR provisioner names
    ``FRRouter`` explicitly when it creates a Router, so a subclass would
    simply never be instantiated there.
    """

    def startShell(self, mnopts: Optional[str] = None) -> None:
        """Start the Node's shell, privately namespaced when capable.

        With the escape hatch off, or for a Node that never declared the
        capability, this defers to the stock Mininet path unchanged. On the
        capable path it launches under ``unshare``, recovers the Namespace
        Anchor pid (see :func:`_recover_namespace_anchor_pid`), and -- before
        the shell is usable by anything else -- mounts a private ``/proc``
        and ``sysfs`` and sets the UTS hostname to the Node's name. ``/proc``
        is not cosmetic: without it a PID namespace's own shell cannot
        resolve ``/proc/self`` and ``ps`` refuses to run at all.

        Args:
            mnopts: Forwarded to the stock path; unused on the unshare path,
                whose flag set is fixed rather than caller-tunable.
        """
        if not has_private_namespaces(self):
            super().startShell(mnopts)  # type: ignore[misc]
            return

        from mininet.log import error  # deferred: see module docstring

        if self.shell:  # type: ignore[has-type]
            error(f"{self.name}: shell is already running\n")  # type: ignore[attr-defined]
            return

        # unshare --fork's forked child is a member of the launcher's process
        # group, never a group leader itself, so bash's own attempt to claim
        # the pty as its controlling terminal (TIOCSCTTY) is refused with no
        # error a Learner would ever see -- sendInt's chr(3) then has no
        # foreground process group to deliver SIGINT to, and a running
        # command cannot be interrupted. `setsid --ctty` runs in place (the
        # forked child is not a group leader, so setsid(2) does not need to
        # fork again) and claims the terminal explicitly, rather than
        # depending on bash's internal job-control heuristics. (Measured
        # 2026-08-29: without it, sendInt() during a foreground `sleep`
        # leaves the sleep running and the CLI blocked in waitOutput.)
        #
        # bash --norc -is 'mininet:<name>': argv[0] is 'bash', not an
        # absolute path, matching the stock mnexec path -- connect_node.sh
        # and the Web UI discover a Node's shell by matching on it.
        cmd = [
            'unshare', '--net', '--mount', '--uts', '--pid',
            '--fork', '--kill-child',
            'setsid', '--ctty',
            'env', 'PS1=' + chr(127),
            'bash', '--norc', '--noediting',
            '-is', 'mininet:' + self.name,  # type: ignore[attr-defined]
        ]

        self.master, self.slave = pty.openpty()
        # close_fds replaces mnexec's -c; start_new_session (setsid) replaces
        # mnexec's -d, giving the launcher its own process group so
        # Node.terminate()'s os.killpg(self.shell.pid, ...) still reaches it
        # and (via --kill-child) the Namespace Anchor beneath it.
        self.shell = self._popen(  # type: ignore[attr-defined]
            cmd,
            stdin=self.slave, stdout=self.slave, stderr=self.slave,
            close_fds=True, start_new_session=True,
        )
        self.stdin = os.fdopen(self.master, 'r')
        self.stdout = self.stdin
        self.pid = self.shell.pid  # provisional: overwritten once recovered
        self.pollOut = select.poll()
        self.pollOut.register(self.stdout)
        self.outToNode[self.stdout.fileno()] = self  # type: ignore[attr-defined]
        self.inToNode[self.stdin.fileno()] = self  # type: ignore[attr-defined]
        self.execed = False
        self.lastCmd = None
        self.lastPid = None
        self.readbuf = ''

        # Wait for the shell's prompt sentinel, exactly as the stock path
        # does -- unshare's --fork execs the same bash, so the same wait
        # applies.
        while True:
            data = self.read(1024)  # type: ignore[attr-defined]
            if data[-1] == chr(127):
                break
            self.pollOut.poll()
        self.waiting = False

        self.pid = _recover_namespace_anchor_pid(
            self.shell.pid, self.name  # type: ignore[attr-defined]
        )

        self.cmd('unset HISTFILE; stty -echo; set +m')  # type: ignore[attr-defined]
        # Mandatory, not cosmetic: unshare --pid does not mount /proc (a
        # bare PID namespace has none), so without this ps/top/pgrep -- and
        # this class's own /proc/self-based invariants -- fail outright.
        # sysfs replaces what mnexec -n mounted for free; networking works
        # either way, so a missing /sys/class/net is silent unless checked.
        self.cmd('mount -t proc proc /proc')  # type: ignore[attr-defined]
        self.cmd('mount -t sysfs sysfs /sys')  # type: ignore[attr-defined]
        self.cmd('hostname ' + self.name)  # type: ignore[attr-defined]
