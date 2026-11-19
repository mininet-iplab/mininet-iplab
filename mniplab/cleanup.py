"""Emergency cleanup utilities for orphaned FRR processes and resources.

Prefer net.stop() or 'exit' in the CLI for normal shutdown; this module is
for the case where neither ran and the host is left with orphans.
"""

import os
import re
import shutil
import subprocess
from typing import Any, List, Optional, Set, Tuple
from mininet.log import error, info

from mniplab.constants import (
    CONTAINER_NAME_PREFIX,
    LABS_DIR,
    MNIPLAB_TMP_DIR,
    MNIPLAB_WEB_LOG_FILE,
    NETNS_DIR,
)

MNIPLAB_LOG_FILE = MNIPLAB_WEB_LOG_FILE

# Everything the package writes on the host, taken from the constants a Lab
# binds rather than restated here so the one runtime-root override reaches
# cleanup too. Every Lab directory lives under the runtime root, and a
# crashed or abandoned Lab never reaches the net.stop() that removes it,
# so one left behind is inherited by the next Lab of that key.
EPHEMERAL_TMP_PATHS = (
    MNIPLAB_LOG_FILE,
    MNIPLAB_TMP_DIR,
)

# A root-namespace veth of an Internet Gateway, ``<key>-<gateway>-ethN``; the key
# grammar is LAB_KEY_PATTERN in mniplab.lab_key.
_KEYED_ROOT_INTF_PATTERN = re.compile(r'^[a-z0-9]{1,6}-.+-eth\d+$')

# Subprocess timeout constants (in seconds)
TIMEOUT_SHORT = 5
TIMEOUT_MEDIUM = 10
TIMEOUT_LONG = 30

# FRR daemon names for cleanup
FRR_DAEMONS = [
    'watchfrr', 'zebra', 'staticd', 'mgmtd', 'bgpd', 'ospfd',
    'ospf6d', 'isisd', 'ripd', 'ripngd', 'pimd', 'pim6d',
    'eigrpd', 'babeld', 'ldpd', 'nhrpd', 'sharpd', 'pbrd',
    'bfdd', 'fabricd', 'vrrpd', 'pathd'
]

# Lab process search patterns
LAB_PROCESS_PATTERNS = [
    'examples/.*-lab',
    '-m mniplab'
]


def _run_command_silent(cmd: List[str], timeout: int = TIMEOUT_SHORT) -> subprocess.CompletedProcess:
    """Run command silently with timeout.
    
    Args:
        cmd: Command to run as list
        timeout: Timeout in seconds
        
    Returns:
        CompletedProcess result
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout
    )


def _get_process_ids(process_name: str) -> List[str]:
    """Get PIDs for a specific process name.
    
    Args:
        process_name: Name of process to find
        
    Returns:
        List of PID strings
    """
    result = _run_command_silent(['pidof', process_name])
    if result.returncode == 0:
        return result.stdout.strip().split()
    return []


def _kill_processes(pids: List[str]) -> None:
    """Kill processes by PID list.
    
    Args:
        pids: List of PID strings
    """
    if pids:
        _run_command_silent(['kill', '-9'] + pids)


def _remove_path_safe(path: str, verbose: bool = True) -> None:
    """Safely remove a file or directory.
    
    Args:
        path: Path to remove
        verbose: Print status messages
    """
    if not os.path.exists(path):
        return
    
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
        else:
            shutil.rmtree(path)
        if verbose:
            info(f'  Removed {path}\n')
    except OSError as e:
        if verbose:
            error(f'Failed to remove {path}: {e}\n')


def kill_frr_processes(verbose: bool = True) -> int:
    """Kill all lingering FRR daemon processes (emergency use only).

    Prefer net.stop() for normal shutdown; use this only for orphaned processes.

    Belt-and-braces rather than load-bearing for a Private Namespace Node
    (see :mod:`mniplab.namespace`): killing a namespace's PID 1
    SIGKILLs everything inside it, so a Router's FRR daemons are gone before
    this sweep would ever find them. It still matters for a Lab run with the
    escape hatch off, or for a daemon that somehow survives its namespace.
    """
    if verbose:
        info('*** Killing FRR daemon processes\n')
    
    killed_count = 0
    for daemon in FRR_DAEMONS:
        try:
            pids = _get_process_ids(daemon)
            if pids:
                _kill_processes(pids)
                killed_count += len(pids)
                if verbose:
                    info(f'  Killed {daemon} ({len(pids)} processes)\n')
        except (OSError, subprocess.SubprocessError):
            _run_command_silent(['pkill', '-9', daemon])

    return killed_count


def _ancestor_pids() -> Set[int]:
    """Return this process and every ancestor of it, up to init.

    ``kill_lingering_labs`` matches on full command lines, and the documented
    way to run this module puts the pattern into its own launcher's command
    line: ``sudo python -m mniplab cleanup`` makes ``sudo`` -- the parent --
    a match for ``-m mniplab``. Killing it takes the Learner's shell down
    with it. Excluding the whole ancestor chain is the only reliable guard,
    since a wrapper may sit any number of levels up.

    Returns:
        Every PID from this process to init. Truncated at the first
        unreadable ``/proc`` entry, which only ever loses ancestors that
        already exited.
    """
    pids: Set[int] = set()
    pid = os.getpid()
    while pid > 0 and pid not in pids:
        pids.add(pid)
        try:
            with open(f'/proc/{pid}/stat', 'r', encoding='utf-8') as stat_file:
                stat = stat_file.read()
            # comm sits in parens and may itself contain spaces or parens, so
            # fields are only unambiguous after the last ')': state, then PPid.
            pid = int(stat[stat.rindex(')') + 1:].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def kill_lingering_labs(verbose: bool = True) -> int:
    """Kill lingering lab processes, excluding this process and its ancestors.

    See :func:`_ancestor_pids` for why the whole chain is spared and not just
    this process: the patterns match command lines, and cleanup's own
    launcher usually carries one.
    """
    if verbose:
        info('*** Killing lingering lab processes\n')
    
    killed_count = 0
    try:
        protected_pids = _ancestor_pids()
        pattern = '|'.join(LAB_PROCESS_PATTERNS)
        result = _run_command_silent(['pgrep', '-f', pattern])
        if result.returncode == 0:
            pids_to_kill = [p for p in result.stdout.strip().split()
                            if int(p) not in protected_pids]
            if pids_to_kill:
                _kill_processes(pids_to_kill)
                killed_count = len(pids_to_kill)
                if verbose:
                    info(f'  Killed {killed_count} lingering lab process(es)\n')
    except (ValueError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        if verbose:
            error(f'Failed to kill lingering lab processes: {e}\n')

    return killed_count


def kill_container_processes(verbose: bool = True) -> int:
    """Stop and remove Docker containers created by ContainerHost nodes.

    ContainerHost names all containers with the ``mn.`` prefix so they can
    be identified and removed independently of the network lifecycle.  Also
    removes any leftover ``/var/run/netns/mn.*`` symlinks that were created
    to expose container network namespaces to host tools.

    Args:
        verbose: Print status messages (default: True)

    Returns:
        Number of containers removed
    """
    if verbose:
        info('*** Removing Docker containers\n')

    removed_count = 0
    try:
        result = _run_command_silent(
            ['docker', 'ps', '-aq', '--filter', f'name={CONTAINER_NAME_PREFIX}'],
            timeout=TIMEOUT_MEDIUM
        )
        container_ids = result.stdout.strip().split() if result.returncode == 0 else []

        if container_ids:
            rm_result = _run_command_silent(
                ['docker', 'rm', '-f'] + container_ids,
                timeout=TIMEOUT_LONG
            )
            if rm_result.returncode == 0:
                removed_count = len(container_ids)
                if verbose:
                    info(f'  Removed {removed_count} container(s)\n')
            else:
                if verbose:
                    error(f'  docker rm failed: {rm_result.stderr.strip()}\n')
    except FileNotFoundError:
        # Docker not installed — not an error in this context
        if verbose:
            info('  Docker not available, skipping container cleanup\n')
    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        error(f'Failed to remove containers: {e}\n')

    # Remove leftover /var/run/netns/mn.* symlinks
    _cleanup_netns_symlinks(verbose)

    return removed_count


def _cleanup_netns_symlinks(verbose: bool) -> None:
    """Remove leftover /var/run/netns/mn.* symlinks.
    
    Args:
        verbose: Print status messages
    """
    try:
        if os.path.isdir(NETNS_DIR):
            for entry in os.listdir(NETNS_DIR):
                if entry.startswith(CONTAINER_NAME_PREFIX):
                    netns_path = os.path.join(NETNS_DIR, entry)
                    try:
                        os.remove(netns_path)
                        if verbose:
                            info(f'  Removed netns symlink {netns_path}\n')
                    except OSError as e:
                        error(f'Failed to remove {netns_path}: {e}\n')
    except OSError as e:
        error(f'Failed to clean netns symlinks: {e}\n')


def cleanup_frr_configs(keep_committed: bool = True, verbose: bool = True) -> None:
    """Remove every Lab directory, the ephemeral FRR configs with them.

    Does not touch /var/run/frr; use FRRouter.terminate() or frrinit.sh stop
    for graceful daemon shutdown.
    """
    if verbose:
        info('*** Cleaning up FRR configurations\n')
    
    _remove_path_safe(LABS_DIR, verbose)


def cleanup_python_cache(verbose: bool = True) -> None:
    """Remove __pycache__, .pytest_cache directories and .pyc files."""
    if verbose:
        info('*** Cleaning up Python cache\n')

    cwd = os.getcwd()
    for root, dirs, files in os.walk(cwd):
        for cache_dir in ('__pycache__', '.pytest_cache'):
            if cache_dir in dirs:
                path = os.path.join(root, cache_dir)
                _remove_path_safe(path, verbose)
        for f in files:
            if f.endswith('.pyc'):
                pyc_file = os.path.join(root, f)
                _remove_path_safe(pyc_file, verbose)


def cleanup_mniplab_files(verbose: bool = True) -> None:
    """Remove the runtime root and the web server's log.

    Every Lab directory lives under the runtime root, and only ``net.stop()``
    removes one, which a crashed or abandoned Lab never reaches. One left
    behind is inherited by the next Lab of that key.

    A Learner's authored files are not touched: an Authored Layout saved to
    ``/tmp`` when the workspace is read-only is their work, not a Lab's.

    Args:
        verbose: Print status messages.
    """
    if verbose:
        info('*** Cleaning up mnIPLab temporary files\n')

    for path in EPHEMERAL_TMP_PATHS:
        _remove_path_safe(path, verbose)


def release_ip_forward(verbose: bool = True) -> None:
    """Drop every Internet Gateway's claim on root ``ip_forward`` and restore it.

    Args:
        verbose: Print status messages.
    """
    from mniplab.kernel import release_all_ip_forward_leases

    if verbose:
        info('*** Restoring root ip_forward\n')
    try:
        release_all_ip_forward_leases()
    except OSError as e:
        error(f'Failed to restore ip_forward: {e}\n')


def kill_resolver_daemons(verbose: bool = True) -> None:
    """Kill Unbound daemons a crashed Lab left running.

    A clean ``net.stop()`` already kills each Resolver inside its own Node,
    but removing the ephemeral tree under a still-running daemon would leave
    it holding a deleted socket, so this sweep runs before those files go.

    Args:
        verbose: Print status messages.
    """
    if verbose:
        info('*** Killing Resolver daemon processes\n')
    _run_command_silent(['pkill', '-9', '-x', 'unbound'])


def kill_capture_processes(verbose: bool = True) -> None:
    """Kill Packet Capture processes a crashed web server left running.

    A Capture's tshark (and the dumpcap beneath it) runs inside a Node and
    writes its pcap under a Lab directory, so it is found by that path: no
    other tshark on the host writes there.  A clean Lab stop has already
    stopped each one; this sweep runs before the Lab directories go.

    Args:
        verbose: Print status messages.
    """
    if verbose:
        info('*** Killing Packet Capture processes\n')
    pattern = f'(tshark|dumpcap) .*-w {re.escape(LABS_DIR)}/[^ ]+/captures/'
    _run_command_silent(['pkill', '-9', '-f', '--', pattern])


def _parse_nat_mark_rule(args: List[str]) -> Optional[Tuple[str, int]]:
    """Read an Internet Gateway's mark rule from one ``iptables -S`` line.

    The Gateway marks what arrives on its Lab-facing veth
    (``-A PREROUTING -i <veth> -j MARK --set-xmark <mark>/0xffffffff``);
    every other sealing rule is derived from that pair, so this is the
    one line the sweeps need to read.

    Args:
        args: One line of ``iptables -t mangle -S PREROUTING``, split.

    Returns:
        The ingress interface and the mark, or None for any other line.
    """
    if len(args) < 3 or args[0] != '-A' or '-i' not in args:
        return None
    idx = args.index('-i')
    if idx > 0 and args[idx - 1] == '!':
        return None
    if '-j' not in args or args[args.index('-j') + 1] != 'MARK':
        return None
    for flag in ('--set-xmark', '--set-mark'):
        if flag in args:
            try:
                return args[idx + 1], int(args[args.index(flag) + 1].split('/', 1)[0], 0)
            except ValueError:
                return None
    return None


def _delete_nat_sealing_rules(intf: str, mark: int) -> None:
    """Delete everything an Internet Gateway's mark rule implies, from that rule's pair.

    The masquerade rule, the conntrack zone, the three CONNMARK rules, the
    policy rule and its table are all named by the mark and the veth, so
    the mark rule alone is enough to find and remove them.

    Args:
        intf: The Gateway's Lab-facing veth.
        mark: The Gateway's mark; its Lab base (``mark >> 8``) is the zone.
    """
    mark_str = str(mark)
    _run_command_silent(['iptables', '-t', 'nat', '-D', 'POSTROUTING',
                         '-m', 'mark', '--mark', mark_str, '-j', 'MASQUERADE'])
    _run_command_silent(['iptables', '-t', 'raw', '-D', 'PREROUTING',
                         '-i', intf, '-j', 'CT', '--zone-orig', str(mark >> 8)])
    _run_command_silent(['iptables', '-t', 'mangle', '-D', 'PREROUTING',
                         '-i', intf, '-j', 'CONNMARK', '--save-mark'])
    _run_command_silent(['iptables', '-t', 'mangle', '-D', 'PREROUTING',
                         '!', '-i', intf, '-m', 'connmark', '--mark', mark_str,
                         '-j', 'CONNMARK', '--restore-mark'])
    _run_command_silent(['iptables', '-t', 'mangle', '-D', 'OUTPUT',
                         '-m', 'connmark', '--mark', mark_str,
                         '-j', 'CONNMARK', '--restore-mark'])
    _run_command_silent(['ip', 'rule', 'del', 'fwmark', mark_str, 'lookup', mark_str])
    _run_command_silent(['ip', 'route', 'flush', 'table', mark_str])


def _sweep_nat_mark_rules(intf_prefix: str = '') -> None:
    """Remove every Internet Gateway's sealing whose veth starts with a prefix.

    Args:
        intf_prefix: ``<key>-`` for one Lab; empty for every keyed veth
            on the host.
    """
    try:
        for line in _run_command_silent(['iptables', '-t', 'mangle', '-S', 'PREROUTING']).stdout.splitlines():
            args = line.split()
            parsed = _parse_nat_mark_rule(args)
            if parsed is None:
                continue
            intf, mark = parsed
            if not intf.startswith(intf_prefix) or not _KEYED_ROOT_INTF_PATTERN.match(intf):
                continue
            _run_command_silent(['iptables', '-t', 'mangle', '-D'] + args[1:])
            _delete_nat_sealing_rules(intf, mark)
    except (OSError, subprocess.SubprocessError):
        pass


def cleanup_nat_rules(verbose: bool = True) -> None:
    """Remove every Internet Gateway's mark, masquerade, zone and policy routing.

    Only rules on a keyed root-namespace veth are a Lab's; a host's own
    mangle, conntrack or policy rules are left alone.

    Args:
        verbose: Print status messages.
    """
    if verbose:
        info('*** Cleaning up NAT rules and routing tables\n')
    _sweep_nat_mark_rules()


def cleanup_mininet_leftovers(verbose: bool = True) -> None:
    """Run ``mn -c`` to remove Mininet network namespaces and artifacts."""
    if verbose:
        info('*** Cleaning up Mininet leftovers\n')
    
    try:
        _run_command_silent(['mn', '-c'], timeout=TIMEOUT_MEDIUM)
        if verbose:
            info('  Ran "mn -c" cleanup command\n')
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        error(f'Failed to run "mn -c": {e}\n')


def full_cleanup(keep_committed: bool = True, verbose: bool = True) -> None:
    """Remove the runtime root and stop every Lab on the host.

    Kills every Lab process and daemon, removes every root-namespace object
    a Lab left behind, and removes the runtime root with every Lab directory
    in it.

    Prefer net.stop() or 'exit' in the CLI for normal shutdown.

    Args:
        keep_committed: Keep committed configs in ./frr-config/ (default: True)
        verbose: Print status messages (default: True)
    """
    if verbose:
        info('\n=== mnIPLab Full Cleanup ===\n')
    
    kill_lingering_labs(verbose=verbose)
    kill_frr_processes(verbose=verbose)
    kill_container_processes(verbose=verbose)
    kill_resolver_daemons(verbose=verbose)
    kill_capture_processes(verbose=verbose)
    release_ip_forward(verbose=verbose)
    cleanup_nat_rules(verbose=verbose)
    cleanup_frr_configs(keep_committed=keep_committed, verbose=verbose)
    cleanup_python_cache(verbose=verbose)
    cleanup_mniplab_files(verbose=verbose)
    cleanup_mininet_leftovers(verbose=verbose)
    
    if verbose:
        info('\n=== Cleanup Complete ===\n')


def sweep_root_namespace_by_key(key: str) -> None:
    """Remove what one Lab left in the host's namespace, by its key alone.

    Every object a Lab creates there carries its key: bridges ``<key>-*``,
    veths ``<key>-*``, containers and namespace links ``mn.<key>.*``, NAT
    mark rules on ``<key>-*`` interfaces, and ``ip_forward`` holders
    ``<key>.*``. This is the sweep for a Lab whose process is gone, so no
    Node object can be asked to tidy up; a Lab that stopped cleanly leaves
    nothing for it to find.

    Args:
        key: The Lab key.
    """
    from mniplab.kernel import release_ip_forward_leases_of

    prefix = f'{key}-'
    try:
        bridges = _run_command_silent(['ovs-vsctl', '--timeout=5', 'list-br']).stdout.split()
        for bridge in bridges:
            if bridge.startswith(prefix):
                _run_command_silent(['ovs-vsctl', '--timeout=5', '--if-exists', 'del-br', bridge])
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        for line in _run_command_silent(['ip', '-o', 'link']).stdout.splitlines():
            parts = line.split(':', 2)
            if len(parts) < 2:
                continue
            name = parts[1].strip().split('@', 1)[0]
            if name.startswith(prefix):
                _run_command_silent(['ip', 'link', 'del', name])
    except (OSError, subprocess.SubprocessError):
        pass

    _sweep_nat_mark_rules(prefix)

    container_prefix = f'{CONTAINER_NAME_PREFIX}{key}.'
    try:
        names = _run_command_silent(['docker', 'ps', '-aq', '--filter', f'name={container_prefix}'],
                                    timeout=TIMEOUT_MEDIUM).stdout.split()
        if names:
            _run_command_silent(['docker', 'rm', '-f'] + names, timeout=TIMEOUT_LONG)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        if os.path.isdir(NETNS_DIR):
            for entry in os.listdir(NETNS_DIR):
                if entry.startswith(container_prefix):
                    try:
                        os.remove(os.path.join(NETNS_DIR, entry))
                    except OSError:
                        pass
    except OSError:
        pass

    try:
        release_ip_forward_leases_of(key)
    except OSError:
        pass


def emergency_cleanup(net: Optional[Any] = None, lab_key: Optional[str] = None) -> None:
    """Emergency cleanup with minimal output - for use in error handlers.

    When net or lab_key is provided, cleans only resources belonging to that
    Lab: its Nodes and switches are asked to stop when ``net`` is in hand, and
    whatever still carries the key in the host's namespace is swept after.
    When neither is provided, performs a host-wide emergency sweep terminating all
    FRR processes and removing critical temporary files for all labs on the host.

    Args:
        net: Optional Lab instance to clean up.
        lab_key: Optional Lab key to clean up if net is not provided.
    """
    key = lab_key
    if net is not None and hasattr(net, 'lab_key') and isinstance(net.lab_key, str):
        key = net.lab_key

    if key:
        # Scoped emergency cleanup for this lab
        if net is not None and hasattr(net, 'hosts'):
            for node in net.hosts:
                try:
                    if hasattr(node, 'terminate'):
                        node.terminate()
                except Exception:
                    pass
        # The Lab's bridges and their veths live in the host's namespace;
        # a switch's stop deletes both, and nothing else here reaches them.
        if net is not None and hasattr(net, 'switches'):
            for switch in net.switches:
                try:
                    switch.stop()
                except Exception:
                    pass

        lab_dir = os.path.join(LABS_DIR, key)
        if os.path.exists(lab_dir):
            try:
                shutil.rmtree(lab_dir)
            except OSError:
                pass
        if os.path.exists(LABS_DIR):
            try:
                if not os.listdir(LABS_DIR):
                    os.rmdir(LABS_DIR)
            except OSError:
                pass

        sweep_root_namespace_by_key(key)

        from mniplab.lab_key import DEFAULT_ALLOCATOR
        DEFAULT_ALLOCATOR.release(key)
        if net is not None and hasattr(net, '_allocated_key'):
            net._allocated_key = None
        return

    # Kill lab processes and FRR processes silently
    kill_lingering_labs(verbose=False)
    _run_command_silent(['pkill', '-9', '-f', 'watchfrr|zebra|staticd|mgmtd'])

    # Remove mnIPLab containers silently
    try:
        result = _run_command_silent(
            ['docker', 'ps', '-aq', '--filter', f'name={CONTAINER_NAME_PREFIX}'],
            timeout=TIMEOUT_MEDIUM
        )
        ids = result.stdout.strip().split() if result.returncode == 0 else []
        if ids:
            _run_command_silent(['docker', 'rm', '-f'] + ids, timeout=TIMEOUT_LONG)
    except (OSError, subprocess.SubprocessError):
        pass

    # Remove leftover netns symlinks silently
    try:
        if os.path.isdir(NETNS_DIR):
            for entry in os.listdir(NETNS_DIR):
                if entry.startswith(CONTAINER_NAME_PREFIX):
                    try:
                        os.remove(os.path.join(NETNS_DIR, entry))
                    except OSError:
                        pass
    except (OSError, subprocess.SubprocessError):
        pass
    
    # Remove the runtime root
    _run_command_silent(['pkill', '-9', '-x', 'knotd'])
    _run_command_silent(['pkill', '-9', '-x', 'unbound'])
    cleanup_nat_rules(verbose=False)
    for path in EPHEMERAL_TMP_PATHS:
        if os.path.exists(path):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except OSError:
                pass
