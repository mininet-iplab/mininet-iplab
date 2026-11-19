"""Relay: a Node's offer to carry another Subnet's DHCP conversation.

An Instructor attaches a Relay to a Node, naming the Subnets it relays
for by CIDR and the address of the DHCP Service it forwards to:
    r1.addRelay(Relay('10.50.0.0/24', server='10.0.0.2'))

A Relay is NOT a Service — it holds no Pool, answers no client, and
belongs to the Lab's shape.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


class Relay:
    """A DHCP Relay: forwards DHCP exchanges for covered Subnets to a Service.

    Attributes:
        subnets: The Subnet CIDRs this Relay covers.
        server: The primary IP address of the DHCP Service to forward to.
        servers: Mapping of address family (4 or 6) to upstream Service IP.
        node: The Node this Relay is attached to (set by ``addRelay``).
        local_addresses: The local interface IP address derived for each
            covered Subnet, populated at Lab start.
    """

    name: str = "relay"

    def __init__(
        self,
        *subnets: Union[str, Sequence[str]],
        server: Optional[Union[str, Sequence[str]]] = None,
    ) -> None:
        """Initialize a Relay covering one or more Subnets.

        Args:
            *subnets: One or more Subnet CIDRs (e.g. ``'10.50.0.0/24'`` or ``'2001:db8:50::/64'``).
            server: The IP address(es) of the upstream DHCP Service to forward to.
                Can be a single IP string, a comma-separated string, or a sequence of IPs.

        Raises:
            ValueError: If no subnets are provided, server is omitted,
                or any subnet/server string is not a valid address.
        """
        raw_subnets: List[str] = []
        for s in subnets:
            if isinstance(s, str):
                raw_subnets.append(s)
            else:
                raw_subnets.extend(s)

        if not raw_subnets:
            raise ValueError("A Relay must cover at least one Subnet CIDR")

        if not server:
            raise ValueError(
                "A Relay currently requires a server address to forward to"
            )

        # Validate CIDRs
        for cidr in raw_subnets:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid Subnet CIDR '{cidr}': {exc}") from exc

        # Parse and validate server IP address(es)
        raw_servers: List[str] = []
        if isinstance(server, str):
            for s in server.split(","):
                s = s.strip()
                if s:
                    raw_servers.append(s)
        elif isinstance(server, (list, tuple)):
            for s in server:
                if isinstance(s, str):
                    s = s.strip()
                    if s:
                        raw_servers.append(s)

        if not raw_servers:
            raise ValueError(
                "A Relay currently requires a server address to forward to"
            )

        self.servers: Dict[int, str] = {}
        for s in raw_servers:
            try:
                ip_obj = ipaddress.ip_address(s)
                self.servers[ip_obj.version] = s
            except ValueError as exc:
                raise ValueError(f"Invalid server address '{s}': {exc}") from exc

        self.subnets: Tuple[str, ...] = tuple(raw_subnets)
        self.server: Optional[str] = raw_servers[0] if raw_servers else None
        self.node: Any = None
        self.local_addresses: Dict[str, str] = {}

    @property
    def families(self) -> Tuple[int, ...]:
        """Return the address families (4 and/or 6) covered by this Relay's Subnets."""
        fams = {ipaddress.ip_network(cidr, strict=False).version for cidr in self.subnets}
        return tuple(sorted(fams))

    def server_for_family(self, family: int, net: Optional[Any] = None) -> Optional[str]:
        """Return the upstream Service IP address for the given address family.

        If a server address was declared explicitly for ``family``, returns it.
        Otherwise, if ``net`` is provided, resolves the corresponding family address
        from the Node carrying the known server address.

        Args:
            family: Address family (4 or 6).
            net: Optional Lab network to look up peer Node addresses.

        Returns:
            The server IP address string, or None if unresolvable.
        """
        if family in self.servers:
            return self.servers[family]

        if net is not None and self.server:
            try:
                known_ip = ipaddress.ip_address(self.server)
            except ValueError:
                return None

            hosts = getattr(net, "hosts", [])
            for host in hosts:
                intf_list_fn = getattr(host, "intfList", None)
                if not callable(intf_list_fn):
                    continue
                found_intf = None
                for intf in intf_list_fn():
                    for addr_str in (getattr(intf, "ip", None), getattr(intf, "ip6", None)):
                        if not addr_str:
                            continue
                        try:
                            if ipaddress.ip_address(str(addr_str).split("/")[0]) == known_ip:
                                found_intf = intf
                                break
                        except ValueError:
                            continue
                    if found_intf is not None:
                        break

                if found_intf is not None:
                    intfs_to_check = [found_intf] + [i for i in intf_list_fn() if i != found_intf]
                    for intf in intfs_to_check:
                        addr_field = "ip" if family == 4 else "ip6"
                        addr_val = getattr(intf, addr_field, None)
                        if addr_val:
                            try:
                                candidate = ipaddress.ip_address(str(addr_val).split("/")[0])
                                if candidate.version == family and not (family == 6 and candidate.is_link_local):
                                    resolved_str = str(addr_val).split("/")[0]
                                    self.servers[family] = resolved_str
                                    return resolved_str
                            except ValueError:
                                pass
        return self.server if self.server and ipaddress.ip_address(self.server).version == family else None

    def local_address(self, subnet_cidr: str) -> str:
        """Return the derived local address used for ``subnet_cidr``.

        Args:
            subnet_cidr: The covered Subnet CIDR.

        Returns:
            The local IP address.

        Raises:
            KeyError: If the subnet has not been resolved.
        """
        return self.local_addresses[subnet_cidr]

    @classmethod
    def find_on(cls, node: Any) -> Relay:
        """Return the Relay attached to ``node``.

        Args:
            node: The Node to search.

        Returns:
            The Node's attached Relay.

        Raises:
            ValueError: If the Node has no Relay attached.
        """
        for relay in getattr(node, "relays", []):
            if isinstance(relay, cls):
                return relay
        raise ValueError(f"{node.name} has no Relay attached")


class DHCPRelayDiagnostics:
    """Start-time diagnostic checks for DHCP Relay configuration.

    A diagnostic is a warning when the failure is teachable, so
    a Relay pointing at an address no Node carries is a warning, not a failure.
    The Lab starts so an instructor can deliberately stage an unreachable
    service scenario.
    """

    @classmethod
    def check_relay(cls, node: Any, relay: Relay, net: Any) -> List[str]:
        """Check whether the upstream Service address declared on ``relay`` is reachable.

        Derives status purely from recorded Interface state,
        never by probing the network.

        Args:
            node: The relaying Node.
            relay: The Relay instance to check.
            net: The Lab network containing all nodes.

        Returns:
            List of warning messages (empty if a Service is found on a Node with that address).
        """
        if not relay.server or net is None:
            return []

        servers_to_check = (
            list(relay.servers.values()) if getattr(relay, "servers", None) else [relay.server]
        )
        hosts = getattr(net, "hosts", [])
        from mniplab.dhcp.service import DHCPService

        warnings: List[str] = []
        for server_addr in servers_to_check:
            try:
                target_ip = ipaddress.ip_address(server_addr)
            except ValueError:
                continue

            server_node: Optional[Any] = None
            for host in hosts:
                intf_list_fn = getattr(host, "intfList", None)
                if not callable(intf_list_fn):
                    continue
                for intf in intf_list_fn():
                    for addr_str in (getattr(intf, "ip", None), getattr(intf, "ip6", None)):
                        if not addr_str:
                            continue
                        try:
                            intf_ip = ipaddress.ip_address(str(addr_str).split("/")[0])
                            if intf_ip == target_ip:
                                server_node = host
                                break
                        except ValueError:
                            continue
                    if server_node is not None:
                        break
                if server_node is not None:
                    break

            if server_node is None:
                warnings.append(
                    f"Relay on {node.name} forwards to Service address {server_addr}, "
                    f"which is not assigned to any Node in the Lab. Assign {server_addr} "
                    f"to a Service Node's Interface or update the server address in "
                    f"{node.name}.addRelay(Relay(..., server='<address>'))"
                )
            else:
                services = getattr(server_node, "services", [])
                has_dhcp = any(isinstance(s, DHCPService) for s in services)
                if not has_dhcp:
                    warnings.append(
                        f"Relay on {node.name} forwards to Service address {server_addr} "
                        f"on {server_node.name}, but {server_node.name} has no DHCP Service attached. "
                        f"Declare a DHCP Service on {server_node.name} "
                        f"(e.g. {server_node.name}.addService(DHCPService(...))) or update the Relay declaration"
                    )

        return warnings

    @classmethod
    def diagnose_reservations(cls, service: Any, net: Any) -> List[str]:
        """Diagnose hardware-address Reservations declared on directly attached IPv6 Pools.

        A hardware-address Reservation on an IPv6 Pool is only reliable when a
        Relay stands in the path (inserting RFC 6939 Option 79). On a directly
        attached Subnet, Kea's match against a hardware address is contingent on
        client DUID format (e.g. DUID-LLT).

        Args:
            service: The DHCP Service to inspect.
            net: The Lab network.

        Returns:
            List of warning messages for directly attached IPv6 Pools carrying MAC reservations.
        """
        if not service or net is None:
            return []

        pools = getattr(service, "pools", {})
        if not isinstance(pools, dict):
            return []

        warnings: List[str] = []
        for cidr, pool in pools.items():
            if getattr(pool, "family", None) != 6:
                continue
            reservations = getattr(pool, "reservations", ())
            mac_reservations = [r for r in reservations if getattr(r, "mac", "")]
            if not mac_reservations:
                continue

            from mniplab.provisioners import DHCPProvisioner
            relay_match = DHCPProvisioner._find_relay_covering_subnet(net, cidr)
            if relay_match is None:
                for res in mac_reservations:
                    warnings.append(
                        f"DHCPv6 Pool on Subnet {cidr}: Reservation for {res.ip} "
                        f"identifies client by hardware address ({res.mac}) on a directly attached Subnet. "
                        f"Hardware-address matching on directly attached IPv6 Subnets depends on client "
                        f"DUID format and is not reliable without a Relay (RFC 6939); "
                        f"identify by DUID instead, or use a DHCP Relay."
                    )
        return warnings

