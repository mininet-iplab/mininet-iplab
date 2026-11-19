"""Builds kea-dhcp4's and kea-dhcp6's JSON configuration from a Service's Pools.

Kea's configuration is a nested JSON document, not the loop-and-conditional
text FRR and ExaBGP render from Mako templates, so this builds a plain dict
and serializes it — there is no template to keep in sync. Nothing here
should be tested against the *shape* of the
rendered JSON; only that the relevant kea-dhcpX binary accepts it and serves
the declared Pools. That format is a Kea implementation detail expected to
change under this project.
"""

from __future__ import annotations

import glob
import ipaddress
from typing import Any, Dict, Iterable, Mapping, Optional

from mniplab.dhcp.pool import Pool
from mniplab.errors import ConfigurationError

# Every lease4-* command Kea exposes over its control socket needs this hook
# loaded, or a healthy kea-dhcp4 answers "command not supported" — confusing to
# debug if config generation and lease listing are built independently. Kea 3.2
# enforces the Debian architecture-dependent multiarch path, so it must be
# resolved at runtime rather than hardcoded for one architecture. lease6-*
# carries the identical requirement: one hook serves both families.
_LEASE_CMDS_HOOK_GLOB = '/usr/lib/*/kea/hooks/libdhcp_lease_cmds.so'


def resolve_lease_cmds_hook() -> str:
    """Resolve the installed path of Kea's ``libdhcp_lease_cmds.so`` hook.

    Returns:
        The first matching path under ``/usr/lib/<triplet>/kea/hooks/``.

    Raises:
        ConfigurationError: No matching hook library was found, meaning
            ``isc-kea-hooks`` is not installed as expected.
    """
    matches = sorted(glob.glob(_LEASE_CMDS_HOOK_GLOB))
    if not matches:
        raise ConfigurationError(
            f"Could not find libdhcp_lease_cmds.so under {_LEASE_CMDS_HOOK_GLOB}\n"
            f"Install isc-kea-hooks, which ships this hook."
        )
    return matches[0]


class KeaConfigBuilder:
    """Renders a Kea server's JSON configuration for one DHCP Service.

    One builder renders either family: the document's top-level key is
    ``Dhcp4`` or ``Dhcp6`` per ``family``, and only the per-Subnet details
    differ (a DHCPv6 Subnet names its serving Interface explicitly — see
    :meth:`build`).
    """

    @staticmethod
    def build(
        pools: Iterable[Pool],
        interface_names: Iterable[str],
        control_socket_path: str,
        lease_database_path: str,
        family: int = 4,
        subnet_interfaces: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        """Build the ``Dhcp4``/``Dhcp6`` configuration document for a Service's Pools.

        Args:
            pools: Every Pool of this family the Service serves, one per Subnet.
            interface_names: Every Interface Kea should listen on — exactly
                the set the declared Pools resolved to, and nothing else.
            control_socket_path: Path (inside the Node) of the unix control
                socket the server opens for ``config-set``/``leaseX-*`` calls.
            lease_database_path: Path (inside the Node) of the memfile lease
                database.
            family: The address family served: 4 renders a ``Dhcp4`` document
                with ``subnet4`` entries; 6 renders ``Dhcp6`` with ``subnet6``.
            subnet_interfaces: For family 6, the Interface name each Pool's
                CIDR resolved to, keyed by CIDR. Required for family 6: Kea
                3.2 cannot select a directly-connected Subnet6 from a
                link-local client source without it.

        Returns:
            A JSON-serializable configuration document.

        Raises:
            ConfigurationError: Family 6 was asked for without
                ``subnet_interfaces``.
        """
        pools = list(pools)
        if family == 6:
            if subnet_interfaces is None:
                raise ConfigurationError(
                    "A DHCPv6 configuration needs each Pool's resolved "
                    "Interface (subnet_interfaces); Kea cannot select a "
                    "directly-connected subnet6 without it."
                )
            subnets = [
                KeaConfigBuilder._build_subnet6(
                    subnet_id, pool, subnet_interfaces.get(pool.cidr) if subnet_interfaces else None
                )
                for subnet_id, pool in enumerate(pools, start=1)
            ]
        else:
            subnets = [
                KeaConfigBuilder._build_subnet4(subnet_id, pool)
                for subnet_id, pool in enumerate(pools, start=1)
            ]

        return {
            f"Dhcp{family}": {
                "interfaces-config": {
                    "interfaces": list(interface_names),
                },
                "control-socket": {
                    "socket-type": "unix",
                    # Kea 3.2 enforces control sockets under /var/run/kea.
                    "socket-name": control_socket_path,
                },
                "lease-database": {
                    "type": "memfile",
                    "lfc-interval": 0,
                    "name": lease_database_path,
                },
                "hooks-libraries": [
                    {"library": resolve_lease_cmds_hook()},
                ],
                f"subnet{family}": subnets,
            }
        }

    @staticmethod
    def _build_subnet4(subnet_id: int, pool: Pool) -> Dict[str, Any]:
        """Build one ``subnet4`` entry, with a Pool's optional fields folded in.

        Args:
            subnet_id: The unique, positive ID Kea requires for this Subnet.
            pool: The Pool this Subnet entry renders.

        Returns:
            One ``subnet4`` document, omitting ``valid-lifetime``,
            ``option-data`` and ``reservations`` entirely when the Pool
            declares none of them, rather than rendering them empty.
        """
        subnet: Dict[str, Any] = {
            "id": subnet_id,
            "subnet": str(ipaddress.ip_network(pool.cidr, strict=False)),
            "pools": [{"pool": f"{pool.start} - {pool.end}"}] if pool.start and pool.end else [],
        }

        if pool.lease_time is not None:
            subnet["valid-lifetime"] = pool.lease_time

        option_data = []
        if pool.router:
            option_data.append({"name": "routers", "data": pool.router})
        if pool.dns_server:
            option_data.append({"name": "domain-name-servers", "data": pool.dns_server})
        if option_data:
            subnet["option-data"] = option_data

        if pool.reservations:
            subnet["reservations"] = [
                {
                    "hw-address": reservation.mac,
                    "ip-address": reservation.ip,
                    **({"hostname": reservation.hostname} if reservation.hostname else {}),
                }
                for reservation in pool.reservations
            ]

        return subnet

    @staticmethod
    def _build_subnet6(subnet_id: int, pool: Pool, interface: Optional[str] = None) -> Dict[str, Any]:
        """Build one ``subnet6`` entry, with a Pool's optional fields folded in.

        The ``interface`` parameter is load-bearing: directly-connected DHCPv6
        clients send from a link-local source that matches no global Subnet,
        so Kea 3.2 needs the serving Interface named inside the ``subnet6``
        entry to associate the link with the Subnet. For relayed Subnets,
        ``interface`` is omitted; Kea selects the subnet purely using
        the relay's link-addr option.

        A DHCPv6 Pool renders no router option: DHCPv6 has none to give, and
        the default route comes from the Router Advertisement instead.
        Ticket 03 turns a declared ``router=`` on an IPv6 Pool into a
        validation error, so none can reach this renderer.

        Args:
            subnet_id: The unique, positive ID Kea requires for this Subnet.
            pool: The Pool this Subnet entry renders.
            interface: The Interface this Pool's CIDR resolved to, or None if relayed.

        Returns:
            One ``subnet6`` document, omitting ``valid-lifetime``,
            ``option-data`` and ``reservations`` entirely when the Pool
            declares none of them, rather than rendering them empty.
        """
        subnet: Dict[str, Any] = {
            "id": subnet_id,
            "subnet": str(ipaddress.ip_network(pool.cidr, strict=False)),
            "pools": [{"pool": f"{pool.start} - {pool.end}"}] if pool.start and pool.end else [],
        }
        if interface:
            subnet["interface"] = interface

        if pool.lease_time is not None:
            subnet["valid-lifetime"] = pool.lease_time

        if pool.preferred_lifetime is not None:
            subnet["preferred-lifetime"] = pool.preferred_lifetime

        if pool.dns_server:
            # DHCPv6 names the DNS-server option differently from DHCPv4.
            subnet["option-data"] = [{"name": "dns-servers", "data": pool.dns_server}]

        if pool.reservations:
            # Exactly one identifier per Reservation (see
            # mniplab.dhcp.pool.Reservation): Kea matches a DUID verbatim
            # and a hardware address either from the DUID or, behind a
            # Relay, from RFC 6939's client link-layer address option.
            subnet["reservations"] = [
                {
                    **({"hw-address": reservation.mac} if reservation.mac
                       else {"duid": reservation.duid}),
                    "ip-addresses": [reservation.ip],
                    **({"hostname": reservation.hostname} if reservation.hostname else {}),
                }
                for reservation in pool.reservations
            ]

        return subnet
