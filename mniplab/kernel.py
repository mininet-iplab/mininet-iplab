"""Kernel networking for mnIPLab: per-node configuration and host preflight.

Encapsulates sysctl settings, IP forwarding, ICMP rate limiting, multipath
hashing, and VRF lifecycle behind a testable interface — plus the host-level
Open vSwitch check that switch-based labs depend on.
"""

import fcntl
import ipaddress
import os
import re
import shlex
import shutil
import subprocess
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from mininet.log import error, info
from mniplab.constants import IP_FORWARD_LEASE_DIR
from mniplab.errors import NetworkError, RouterError
from mniplab.logging import get_logger

logger = get_logger(__name__)

#: The root namespace's IPv4 forwarding flag.
IP_FORWARD_FILE = '/proc/sys/net/ipv4/ip_forward'

# Files in the lease directory that are not holders.
_LEASE_ORIGINAL = 'original'
_LEASE_LOCK = '.lock'

# Seconds to wait for ovs-vsctl. Without a timeout it blocks indefinitely when
# the daemon is absent, which is the hang this check exists to prevent.
OVS_PROBE_TIMEOUT = 5

_OVS_SETUP_HELP = (
    "Open vSwitch runs on the HOST kernel, so a container cannot supply it.\n"
    "\n"
    "  1. Load the module on the host:   sudo modprobe openvswitch\n"
    "  2. Confirm it is loaded:          lsmod | grep openvswitch\n"
    "  3. Make it persist across reboot: echo openvswitch | sudo tee /etc/modules-load.d/openvswitch.conf\n"
    "\n"
    "The container must also see the host's modules. docker-compose.yml mounts\n"
    "/lib/modules:/lib/modules:ro for this reason — keep that mount.\n"
    "\n"
    "Only labs that create switches need Open vSwitch. Router-only labs run\n"
    "without it."
)


def ensure_ovs_available(timeout: int = OVS_PROBE_TIMEOUT) -> None:
    """Verify Open vSwitch is usable before a switch is created.

    Mininet's switches are Open vSwitch bridges, and OVS needs a kernel module
    the host must provide. When it is missing, ``mn`` and Mininet's own switch
    setup block rather than fail, which gives no clue what is wrong — this
    check converts that silence into a message.

    Called when a switch is declared rather than at Lab construction, because
    router-only Labs do not need Open vSwitch at all.

    Args:
        timeout: Seconds to wait for the ``ovs-vsctl`` probe.

    Raises:
        NetworkError: If ovs-vsctl is missing, times out, or reports failure.
    """
    if shutil.which('ovs-vsctl') is None:
        raise NetworkError(
            "Open vSwitch is not installed (ovs-vsctl not found).\n"
            "\n"
            "Install it:\n"
            "  Debian/Ubuntu: apt-get install openvswitch-switch\n"
            "  RHEL/CentOS:   yum install openvswitch\n"
            "\n" + _OVS_SETUP_HELP
        )

    try:
        probe = subprocess.run(
            ['ovs-vsctl', f'--timeout={timeout}', 'show'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 2,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise NetworkError(
            "Open vSwitch did not respond — this is the hang that would "
            "otherwise happen silently when a lab starts.\n"
            "\n" + _OVS_SETUP_HELP
        ) from None
    except OSError as e:
        raise NetworkError(
            f"Could not run ovs-vsctl: {e}\n"
            "\n" + _OVS_SETUP_HELP
        ) from e

    if probe.returncode != 0:
        detail = (probe.stderr or b'').decode('utf-8', 'replace').strip()
        raise NetworkError(
            "Open vSwitch is installed but not usable.\n"
            f"ovs-vsctl exited {probe.returncode}: {detail or '(no output)'}\n"
            "\n"
            "Start the daemon inside the container:\n"
            "  service openvswitch-switch start\n"
            "\n" + _OVS_SETUP_HELP
        )

    logger.debug('Open vSwitch available')


@contextmanager
def _ip_forward_lease_locked() -> Iterator[str]:
    """Hold the lease directory's lock, creating the directory if needed.

    Yields:
        The lease directory path.
    """
    os.makedirs(IP_FORWARD_LEASE_DIR, exist_ok=True)
    lock_path = os.path.join(IP_FORWARD_LEASE_DIR, _LEASE_LOCK)
    with open(lock_path, 'w', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield IP_FORWARD_LEASE_DIR
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _ip_forward_holders() -> List[str]:
    """Return the holder names currently recorded in the lease directory."""
    if not os.path.isdir(IP_FORWARD_LEASE_DIR):
        return []
    return [
        entry for entry in os.listdir(IP_FORWARD_LEASE_DIR)
        if entry not in (_LEASE_ORIGINAL, _LEASE_LOCK)
    ]


def _read_ip_forward() -> str:
    with open(IP_FORWARD_FILE, encoding='utf-8') as f:
        return f.read().strip()


def _write_ip_forward(value: str) -> None:
    with open(IP_FORWARD_FILE, 'w', encoding='utf-8') as f:
        f.write(value)


def _restore_ip_forward_if_unheld() -> None:
    """Restore the recorded original once no holder remains.

    The lock file and its directory stay: unlinking a lock file under a
    waiter hands it a lock nobody else shares.
    """
    if _ip_forward_holders():
        return
    original = os.path.join(IP_FORWARD_LEASE_DIR, _LEASE_ORIGINAL)
    if os.path.exists(original):
        with open(original, encoding='utf-8') as f:
            _write_ip_forward(f.read().strip() or '0')
        os.remove(original)


def ip_forward_holder(lab_key: Optional[str], node_name: str) -> str:
    """Name a NAT Gateway's claim on root ``ip_forward``: ``<lab key>.<node>``.

    Args:
        lab_key: The owning Lab's key; a gateway built without a Lab has none.
        node_name: The gateway's Node name.

    Returns:
        The holder name.
    """
    return f'{lab_key}.{node_name}' if lab_key else node_name


def acquire_ip_forward_lease(holder: str) -> None:
    """Turn root ``ip_forward`` on for a holder, remembering what it was.

    Root forwarding is one flag shared by every Lab on the host, so it is
    reference-counted: the first holder records the value it found and the
    last holder to release restores it, whichever Lab that turns out to be.

    Args:
        holder: A name unique on the host, from :func:`ip_forward_holder`.
    """
    with _ip_forward_lease_locked() as lease_dir:
        if not _ip_forward_holders():
            with open(os.path.join(lease_dir, _LEASE_ORIGINAL), 'w', encoding='utf-8') as f:
                f.write(_read_ip_forward())
        with open(os.path.join(lease_dir, holder), 'w', encoding='utf-8'):
            pass
        _write_ip_forward('1')


def release_ip_forward_lease(holder: str) -> None:
    """Give up a holder's claim on root ``ip_forward``; the last one restores it.

    Args:
        holder: The name given to :func:`acquire_ip_forward_lease`.
    """
    if not os.path.isdir(IP_FORWARD_LEASE_DIR):
        return
    with _ip_forward_lease_locked() as lease_dir:
        try:
            os.remove(os.path.join(lease_dir, holder))
        except FileNotFoundError:
            pass
        _restore_ip_forward_if_unheld()


def release_ip_forward_leases_of(lab_key: str) -> None:
    """Drop every holder a Lab left behind; the last one on the host restores.

    For the keyed emergency sweep, when the Lab's Nodes are already gone.

    Args:
        lab_key: The Lab key holders were named under.
    """
    if not os.path.isdir(IP_FORWARD_LEASE_DIR):
        return
    with _ip_forward_lease_locked() as lease_dir:
        for holder in _ip_forward_holders():
            if holder.startswith(f'{lab_key}.'):
                try:
                    os.remove(os.path.join(lease_dir, holder))
                except OSError:
                    pass
        _restore_ip_forward_if_unheld()


def release_all_ip_forward_leases() -> None:
    """Drop every holder on the host and restore root ``ip_forward``.

    For the host-wide sweep, which stops every Lab on the host.
    """
    if not os.path.isdir(IP_FORWARD_LEASE_DIR):
        return
    with _ip_forward_lease_locked() as lease_dir:
        for holder in _ip_forward_holders():
            try:
                os.remove(os.path.join(lease_dir, holder))
            except OSError:
                pass
        _restore_ip_forward_if_unheld()


class KernelConfigManager:
    """Manages kernel networking configuration for a single node.

    The interface is small — ``enable_forwarding()``, ``disable_icmp_rate_limiting()``,
    etc. — but the implementation encapsulates the sysctl command strings and
    error-checking logic that was previously scattered through :class:`FRRouter`.
    """

    def __init__(self, node: Any) -> None:
        """Initialise with the node that commands will run against.

        Args:
            node: A Mininet-style node with ``cmd()`` and ``name`` attributes.
        """
        self.node = node

    def apply_sysctl(self, setting: str, expected_value: str,
                     critical: bool = False, error_context: str = "") -> None:
        """Apply a sysctl setting with optional error checking.

        Args:
            setting: Sysctl setting name (e.g. ``net.ipv4.ip_forward``).
            expected_value: Expected value for the setting.
            critical: If ``True``, raises :class:`RouterError` on failure.
            error_context: Additional error context for critical failures.

        Raises:
            RouterError: If *critical* is ``True`` and the setting fails.
        """
        result = self.node.cmd(f'sysctl -w {setting}={expected_value}')
        expected_output = f'{setting} = {expected_value}'

        if critical and ('error' in result.lower() or expected_output not in result):
            raise RouterError(
                f"{self.node.name}: Failed to set {setting}={expected_value}\n"
                f"Command output: {result}\n"
                f"{error_context}"
            )

    def enable_forwarding(self) -> None:
        """Enable IPv4 and IPv6 forwarding (critical for routing)."""
        self.apply_sysctl(
            'net.ipv4.ip_forward', '1', critical=True,
            error_context=(
                "Possible causes:\n"
                "  1. Not running as root (use: sudo python3 script.py)\n"
                "  2. Kernel doesn't support IP forwarding\n"
                "  3. SELinux/AppArmor preventing sysctl changes\n"
                "Verify: cat /proc/sys/net/ipv4/ip_forward (should be 1)"
            )
        )
        self.apply_sysctl(
            'net.ipv6.conf.all.forwarding', '1', critical=True,
            error_context=(
                "Possible causes:\n"
                "  1. IPv6 is disabled in kernel\n"
                "  2. Not running as root (use: sudo python3 script.py)\n"
                "Verify: cat /proc/sys/net/ipv6/conf/all/forwarding (should be 1)"
            )
        )

    def disable_icmp_rate_limiting(self) -> None:
        """Disable ICMP rate limiting for lab environment."""
        self.apply_sysctl('net.ipv4.icmp_ratelimit', '0')
        self.apply_sysctl('net.ipv4.icmp_ratemask', '0')
        self.apply_sysctl('net.ipv6.icmp.ratelimit', '0')
        self.node.cmd('sysctl -w net.ipv6.icmp.ratemask=')
        logger.debug(f"Disabled ICMP rate limiting on {self.node.name}")

    def enable_multipath(self) -> None:
        """Enable multipath hash policy for load balancing."""
        multipath_settings = {
            'net.ipv4.fib_multipath_hash_seed': '0',
            'net.ipv4.fib_multipath_hash_policy': '1',
            'net.ipv4.fib_multipath_use_neigh': '1',
            'net.ipv4.fib_multipath_hash_fields': '3',
            'net.ipv6.fib_multipath_hash_policy': '1',
            'net.ipv6.fib_multipath_hash_fields': '3',
        }
        for setting, value in multipath_settings.items():
            self.apply_sysctl(setting, value)

    def setup_vrfs(self, vrfs: Dict[str, int], vrf_attachments: Dict[str, str]) -> None:
        """Create VRF devices and attach interfaces, preserving existing IPs.

        Args:
            vrfs: Mapping of VRF name to routing table ID.
            vrf_attachments: Mapping of interface name to VRF name.
        """
        for vrf_name, table_id in vrfs.items():
            self.node.cmd(
                f'ip link add {shlex.quote(vrf_name)} type vrf table {shlex.quote(str(table_id))}'
            )
            self.node.cmd(f'ip link set {shlex.quote(vrf_name)} up')

        for intf, vrf in vrf_attachments.items():
            if vrf not in vrfs:
                error(f"VRF '{vrf}' not found on router '{self.node.name}'\n")
                continue

            # Preserve interface IPs — moving to a VRF clears them
            output = self.node.cmd(f'ip addr show {shlex.quote(intf)}')
            preserved_addrs: List[str] = []
            for line in output.splitlines():
                if re.search(r'\bscope\s+link\b', line):
                    continue
                match = re.search(r'\binet6?\s+([0-9a-fA-F:./]+)', line)
                if match:
                    raw_addr = match.group(1)
                    try:
                        ipaddress.ip_interface(raw_addr)
                    except ValueError:
                        continue
                    preserved_addrs.append(raw_addr)

            if preserved_addrs:
                info(f"*** Attaching {intf} to VRF {vrf} on {self.node.name} (preserving {', '.join(preserved_addrs)})\n")

            self.node.cmd(f'ip link set {shlex.quote(intf)} master {shlex.quote(vrf)}')
            for ip_addr in preserved_addrs:
                self.node.cmd(f'ip addr add {shlex.quote(ip_addr)} dev {shlex.quote(intf)}')
