"""Lab directory path resolution for mnIPLab.

Every ephemeral path a Lab writes lives under one Lab directory,
``<runtime root>/labs/<key>/``. Inside it a Node's backing files mirror
the Node's own paths (``nodes/r1/etc/frr`` is what ``r1`` sees at
``/etc/frr``), Node homes are ``home/<node>/``, and the files the whole
Lab shares sit at the top: ``etc/hosts``, ``zone-keys/<zone>/`` and the
Lab's handle socket ``lab.sock``.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple, Union

from mniplab.constants import (
    FRR_ETC_DIR,
    FRR_VAR_LOG_DIR,
    FRR_VAR_RUN_DIR,
    LABS_DIR,
)

# The guest paths a Service's provisioner binds, by the short names its
# callers use: ``etc`` for configuration, ``run`` for sockets and pid files,
# ``lib`` for storage, ``logs`` for log files.
KEA_PATHS = {'etc': 'etc/kea', 'run': 'var/run/kea', 'lib': 'var/lib/kea'}
KNOT_PATHS = {'etc': 'etc/knot', 'run': 'run/knot', 'lib': 'var/lib/knot'}
UNBOUND_PATHS = {'etc': 'etc/unbound', 'run': 'run/unbound'}
RPKI_CA_PATHS = {'etc': 'etc/krill', 'run': 'run/krill', 'lib': 'var/lib/krill'}
ROUTINATOR_PATHS = {'etc': 'etc/routinator', 'run': 'run/routinator', 'lib': 'var/lib/routinator'}
EXABGP_PATHS = {'etc': 'etc/exabgp', 'logs': 'var/log/exabgp', 'run': 'var/run/exabgp'}

# The name placeholder Mininet's privateDirs expands per Node.
NAME_PLACEHOLDER = '%(name)s'


def lab_dir_for(lab_key: str) -> str:
    """Return the host-side Lab directory of the Lab with this key.

    Args:
        lab_key: The Lab key.

    Returns:
        ``<runtime root>/labs/<key>``.
    """
    return os.path.join(LABS_DIR, lab_key)


class LabPathResolverMixin:
    """Resolve every ephemeral path of a Lab under its Lab directory.

    Attributes:
        lab_key: The Lab key naming this Lab's directory.
    """

    lab_key: str

    @property
    def lab_dir(self) -> str:
        """Return the host-side root directory for this Lab."""
        return lab_dir_for(self.lab_key)

    @property
    def rpc_socket_path(self) -> str:
        """Return host-side path to this Lab's handle socket."""
        return os.path.join(self.lab_dir, 'lab.sock')

    @property
    def hosts_file(self) -> str:
        """Return path to this Lab's shared hosts file."""
        return os.path.join(self.lab_dir, 'etc', 'hosts')

    def node_dir(self, node_name: str, guest_path: str = '') -> str:
        """Return the host-side backing path for a Node's guest path.

        Args:
            node_name: The Node name, or ``%(name)s`` for a template.
            guest_path: The path as the Node sees it; omitted for the
                Node's whole backing tree.

        Returns:
            The host-side path.
        """
        node_dir = os.path.join(self.lab_dir, 'nodes', node_name)
        guest_path = guest_path.strip('/')
        return os.path.join(node_dir, guest_path) if guest_path else node_dir

    def node_home_dir(self, node_name: str) -> str:
        """Return host-side home directory for a node, bound at ``/root``.

        Args:
            node_name: The node name.

        Returns:
            The host-side home directory path.
        """
        return os.path.join(self.lab_dir, 'home', node_name)

    def frr_config_dir(self, node_name: str) -> str:
        """Return host-side ephemeral FRR config directory for a router.

        Args:
            node_name: The router node name.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, FRR_ETC_DIR)

    def frr_config_template(self) -> str:
        """Return the ephemeral FRR config directory with the name placeholder.

        This exact template is what marks a Router's config directory as
        ephemeral: a Router bound to it is regenerated on each add, so a Lab
        never inherits the daemons file a previous Lab of the same Node name
        left behind. Any other path is a caller's own, and is never
        overwritten.

        Returns:
            The host-side directory template string.
        """
        return self.frr_config_dir(NAME_PLACEHOLDER)

    def hostname_file(self, node_name: str) -> str:
        """Return host-side backing hostname file for a node.

        Args:
            node_name: The node name.

        Returns:
            The host-side file path.
        """
        return self.node_dir(node_name, '/etc/hostname')

    def resolv_conf_file(self, node_name: str) -> str:
        """Return host-side backing resolv.conf file for a node.

        Args:
            node_name: The node name.

        Returns:
            The host-side file path.
        """
        return self.node_dir(node_name, '/etc/resolv.conf')

    def dhcpcd_lib_dir(self, node_name: str) -> str:
        """Return host-side dhcpcd lease cache for a node.

        Args:
            node_name: The node name.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, '/var/lib/dhcpcd')

    def dhcpcd_run_dir(self, node_name: str) -> str:
        """Return host-side dhcpcd runtime directory for a node.

        Args:
            node_name: The node name.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, '/run/dhcpcd')

    def exabgp_dir(self, node_name: str, subdir: str = 'etc') -> str:
        """Return host-side ExaBGP directory for a speaker.

        Args:
            node_name: The node name.
            subdir: ``'etc'``, ``'logs'`` or ``'run'``.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, EXABGP_PATHS[subdir])

    def exabgp_private_dirs(self, node_name: str = NAME_PLACEHOLDER) -> List[Tuple[str, str]]:
        """Return privateDirs mappings for an ExaBGP speaker in this Lab.

        Args:
            node_name: The speaker node name; the name placeholder unless given.

        Returns:
            List of private directory mapping tuples.
        """
        return [
            ('/' + guest, self.exabgp_dir(node_name, subdir))
            for subdir, guest in EXABGP_PATHS.items()
        ]

    def dhcp_dir(self, node_name: str, subdir: str = '') -> str:
        """Return host-side DHCP Service (Kea) directory for a node.

        Args:
            node_name: The serving node name.
            subdir: ``'etc'``, ``'run'`` or ``'lib'``; omitted for the
                Node's whole backing tree.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, KEA_PATHS[subdir] if subdir else '')

    def dhcp_control_socket_path(self, node_name: str, family: int = 4) -> str:
        """Return host-side path to Kea control socket for a node and address family.

        Args:
            node_name: The serving node name.
            family: Address family (4 or 6).

        Returns:
            The host-side socket file path.
        """
        socket_name = 'kea4-ctrl-socket' if family == 4 else 'kea6-ctrl-socket'
        return os.path.join(self.dhcp_dir(node_name, 'run'), socket_name)

    def dhcp_relay_dir(self, node_name: str) -> str:
        """Return host-side DHCP Relay log directory for a node.

        Args:
            node_name: The relay node name.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, '/var/log/dnsmasq')

    def dhcp_relay_log_path(self, node_name: str) -> str:
        """Return host-side path to DHCP Relay log file for a node.

        Args:
            node_name: The relay node name.

        Returns:
            The host-side log file path.
        """
        return os.path.join(self.dhcp_relay_dir(node_name), 'dnsmasq.log')

    def dns_dir(self, node_name: str, subdir: str = '') -> str:
        """Return host-side DNS Service (Knot) directory for a node.

        Args:
            node_name: The DNS server node name.
            subdir: ``'etc'``, ``'run'`` or ``'lib'``; omitted for the
                Node's whole backing tree.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, KNOT_PATHS[subdir] if subdir else '')

    def dns_control_socket_path(self, node_name: str) -> str:
        """Return host-side path to Knot control socket for a node.

        Args:
            node_name: The DNS server node name.

        Returns:
            The host-side control socket file path.
        """
        return os.path.join(self.dns_dir(node_name, 'run'), 'knot.sock')

    def dns_log_path(self, node_name: str) -> str:
        """Return host-side path to Knot log file for a node.

        Args:
            node_name: The DNS server node name.

        Returns:
            The host-side log file path.
        """
        return os.path.join(self.dns_dir(node_name, 'run'), 'knot.log')

    def dns_zone_keys_dir(self, zone_name: str) -> str:
        """Return host-side fallback zone keys directory for a signed DNS zone.

        Args:
            zone_name: The DNS zone name.

        Returns:
            The host-side zone keys directory path.
        """
        cleaned_zone = re.sub(r'[^A-Za-z0-9]+', '_', zone_name.strip('.')).strip('_') or 'root'
        return os.path.join(self.lab_dir, 'zone-keys', cleaned_zone)

    def resolver_dir(self, node_name: str, subdir: str = '') -> str:
        """Return host-side Resolver Service (Unbound) directory for a node.

        Args:
            node_name: The resolver node name.
            subdir: ``'etc'`` or ``'run'``; omitted for the Node's whole
                backing tree.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, UNBOUND_PATHS[subdir] if subdir else '')

    def resolver_control_socket_path(self, node_name: str) -> str:
        """Return host-side path to Unbound control socket for a node.

        Args:
            node_name: The resolver node name.

        Returns:
            The host-side control socket file path.
        """
        return os.path.join(self.resolver_dir(node_name, 'run'), 'unbound.sock')

    def rpki_ca_dir(self, node_name: str, subdir: str = '') -> str:
        """Return host-side RPKI CA (Krill) directory for a node.

        Args:
            node_name: The RPKI CA node name.
            subdir: ``'etc'``, ``'run'`` or ``'lib'``; omitted for the
                Node's whole backing tree.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, RPKI_CA_PATHS[subdir] if subdir else '')

    def rpki_ca_socket_path(self, node_name: str) -> str:
        """Return host-side path to Krill's unix API socket for a node.

        The socket sits in the Node's bind-mounted run directory, so krillc
        finds it at its default path inside the Node.

        Args:
            node_name: The RPKI CA node name.

        Returns:
            The host-side socket file path.
        """
        return os.path.join(self.rpki_ca_dir(node_name, 'run'), 'krill.sock')

    def rpki_ca_root_cert_path(self, node_name: str) -> str:
        """Return host-side path to the private TLS root that signed Krill's certificate.

        Args:
            node_name: The RPKI CA node name.

        Returns:
            The host-side PEM file path.
        """
        return os.path.join(self.rpki_ca_dir(node_name, 'lib'), 'ssl', 'root.pem')

    def validator_dir(self, node_name: str, subdir: str = '') -> str:
        """Return host-side Validator (Routinator) directory for a node.

        Args:
            node_name: The Validator node name.
            subdir: ``'etc'``, ``'run'`` or ``'lib'``; omitted for the
                Node's whole backing tree.

        Returns:
            The host-side directory path.
        """
        return self.node_dir(node_name, ROUTINATOR_PATHS[subdir] if subdir else '')

    def container_dir(self, node_name: str, entry: str = '') -> str:
        """Return host-side backing directory for a container host privateDir entry.

        Args:
            node_name: The container host node name.
            entry: Optional container path entry.

        Returns:
            The host-side backing directory path.
        """
        return self.node_dir(node_name, entry)

    def _shared_private_dirs(self, name: str) -> List[Tuple[str, str]]:
        """Return the bindings every Host and Router carries.

        ``/etc/hosts`` is one file shared across the Lab: a Node must resolve
        its own name over the Lab, not on loopback. ``/etc/hostname`` and
        ``/etc/resolv.conf`` are per Node so a Node can write its own
        hostname while ``/etc`` stays shared, and so a DHCP client's rewrite
        of resolv.conf lands on the Node's own file, never on the host's
        resolver configuration. ``/var/lib/dhcpcd`` is per Node because a
        second run reusing a Node name would otherwise rebind a stale Lease
        instead of a fresh DISCOVER; ``/run/dhcpcd`` because dhcpcd's control
        socket and pid file are compiled in as ``/run/dhcpcd/<intf>-4.*``,
        and without the bind every Node -- and the host -- shares one
        directory.

        Args:
            name: The node name, or the name placeholder.

        Returns:
            List of private directory mapping tuples.
        """
        return [
            ('/etc/hosts', self.hosts_file),
            ('/etc/hostname', self.hostname_file(name)),
            ('/etc/resolv.conf', self.resolv_conf_file(name)),
            ('/var/lib/dhcpcd', self.dhcpcd_lib_dir(name)),
            ('/run/dhcpcd', self.dhcpcd_run_dir(name)),
        ]

    def router_private_dirs(
        self, name: str, frr_dir: Optional[str] = None
    ) -> List[Union[str, Tuple[str, str]]]:
        """Return privateDirs mappings for a router in this Lab.

        Args:
            name: The router node name.
            frr_dir: Optional custom FRR directory override.

        Returns:
            List of private directory mapping tuples and strings.
        """
        etc_frr_path = frr_dir if frr_dir is not None else self.frr_config_template()
        return [
            FRR_VAR_LOG_DIR,
            (FRR_ETC_DIR, etc_frr_path),
            FRR_VAR_RUN_DIR,
            '/var/mn',
            '/var/tmp',  # Private tmpfs: watchfrr's pid-keyed paths collide across Routers
            *self._shared_private_dirs(name),
        ]

    def host_private_dirs(self, name: str, home_dir: Optional[str] = None) -> List[Tuple[str, str]]:
        """Return privateDirs mappings for a host in this Lab.

        Args:
            name: The host node name.
            home_dir: The directory bound at ``/root``; the Node's home
                under the Lab directory unless given.

        Returns:
            List of private directory mapping tuples.
        """
        return [
            *self._shared_private_dirs(name),
            ('/root', home_dir if home_dir is not None else self.node_home_dir(name)),
        ]
