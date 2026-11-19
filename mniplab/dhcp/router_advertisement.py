"""Router Advertisement state querying and parsing for FRR routers.

This adapter isolates every vtysh query to a running Router, reading what
FRR believes at runtime rather than what configuration files say.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Any, Dict, List, Optional, Set, Tuple, Union


@dataclass(frozen=True)
class InterfaceRouterAdvertisement:
    """Router Advertisement state and flags for one interface on an FRRouter.

    Attributes:
        interface_name: The router interface name (e.g. ``r1-eth0``).
        ra_sent: Whether Router Advertisements are actively sent on this
            interface (i.e. ``no ipv6 nd suppress-ra`` is configured).
        managed_flag: Whether the Managed (M) flag is set (``ipv6 nd
            managed-config-flag``).
        other_config_flag: Whether the Other Configuration (O) flag is set
            (``ipv6 nd other-config-flag``).
        advertises_autoconfig: Whether the served prefix (or connected prefixes
            in general) advertises SLAAC autoconfiguration (A=1).
        prefixes_no_autoconfig: Tuple of IPv6 prefixes explicitly configured
            with ``no-autoconfig`` (A=0).
    """

    interface_name: str
    ra_sent: bool
    managed_flag: bool
    other_config_flag: bool
    advertises_autoconfig: bool = True
    prefixes_no_autoconfig: Tuple[ipaddress.IPv6Network, ...] = ()

    @property
    def sets_managed_flag(self) -> bool:
        """Return True if this interface actively sends RAs with M=1."""
        return self.ra_sent and self.managed_flag


class RouterAdvertisementAdapter:
    """Adapter that queries and parses Router Advertisement state from an FRRouter.

    This class serves as the single seam for running Router queries in the
    DHCPv6 feature, mirroring :class:`mniplab.dhcp.control_client.KeaControlClient`.
    All interactions with ``vtysh`` for Router Advertisement inspection pass
    through this adapter.
    """

    def __init__(self, router: Any) -> None:
        """Initialize the adapter for one router node.

        Args:
            router: An :class:`~mniplab.router.FRRouter` or test double
                providing a ``cmd`` method.
        """
        self.router = router

    def _query_running_config(self) -> str:
        """Query the running router for its running configuration via vtysh.

        Extracted so unit tests can fake the command output, mirroring
        :meth:`KeaControlClient._connect`.

        Returns:
            The raw text output from ``vtysh -c "show running-config"``.
        """
        return self.router.cmd('vtysh -c "show running-config"')

    @classmethod
    def parse_running_config(
        cls,
        config_text: str,
        prefix: Optional[Union[str, ipaddress.IPv6Network, ipaddress.IPv4Network]] = None,
    ) -> Dict[str, InterfaceRouterAdvertisement]:
        """Parse FRR running configuration text into per-interface RA reports.

        Args:
            config_text: Output from ``vtysh -c "show running-config"``.
            prefix: Optional target prefix to evaluate autoconfiguration for.

        Returns:
            Mapping of interface name to :class:`InterfaceRouterAdvertisement`.
        """
        target_net: Optional[ipaddress.IPv6Network] = None
        if prefix is not None:
            parsed = (
                ipaddress.ip_network(prefix, strict=False)
                if isinstance(prefix, str)
                else prefix
            )
            if isinstance(parsed, ipaddress.IPv6Network):
                target_net = parsed

        interfaces: Dict[str, InterfaceRouterAdvertisement] = {}
        current_intf: Optional[str] = None
        intf_lines: List[str] = []

        def _finish_intf() -> None:
            nonlocal current_intf, intf_lines
            if not current_intf:
                return

            ra_sent = False
            managed_flag = False
            other_config_flag = False
            prefixes_no_autoconfig: Set[ipaddress.IPv6Network] = set()

            for raw_line in intf_lines:
                line = raw_line.strip()
                if not line:
                    continue

                if line == "no ipv6 nd suppress-ra":
                    ra_sent = True
                elif line == "ipv6 nd suppress-ra":
                    ra_sent = False
                elif line == "ipv6 nd managed-config-flag":
                    managed_flag = True
                elif line == "no ipv6 nd managed-config-flag":
                    managed_flag = False
                elif line == "ipv6 nd other-config-flag":
                    other_config_flag = True
                elif line == "no ipv6 nd other-config-flag":
                    other_config_flag = False
                elif line.startswith("ipv6 nd prefix"):
                    tokens = line.split()
                    if len(tokens) >= 4:
                        prefix_str = tokens[3]
                        try:
                            p_net = ipaddress.ip_network(prefix_str, strict=False)
                            if isinstance(p_net, ipaddress.IPv6Network) and "no-autoconfig" in tokens[4:]:
                                prefixes_no_autoconfig.add(p_net)
                        except ValueError:
                            pass

            if not ra_sent:
                advertises_autoconfig = False
            elif target_net is not None:
                advertises_autoconfig = target_net not in prefixes_no_autoconfig
            else:
                advertises_autoconfig = True

            interfaces[current_intf] = InterfaceRouterAdvertisement(
                interface_name=current_intf,
                ra_sent=ra_sent,
                managed_flag=managed_flag,
                other_config_flag=other_config_flag,
                advertises_autoconfig=advertises_autoconfig,
                prefixes_no_autoconfig=tuple(prefixes_no_autoconfig),
            )
            current_intf = None
            intf_lines = []

        for line in config_text.splitlines():
            stripped = line.strip()
            if stripped.startswith('interface ') and not line.startswith(' '):
                _finish_intf()
                parts = stripped.split()
                if len(parts) >= 2:
                    current_intf = parts[1]
            elif current_intf is not None:
                if stripped in ('exit', '!') or (
                    stripped and not line.startswith(' ') and not line.startswith('	')
                ):
                    _finish_intf()
                else:
                    intf_lines.append(line)

        _finish_intf()
        return interfaces

    def query_interface(
        self,
        interface_name: str,
        prefix: Optional[Union[str, ipaddress.IPv6Network, ipaddress.IPv4Network]] = None,
    ) -> InterfaceRouterAdvertisement:
        """Query RA configuration for a specific interface.

        Args:
            interface_name: Interface name on the router (e.g. ``r1-eth0``).
            prefix: Optional served prefix to evaluate autoconfiguration for.

        Returns:
            An :class:`InterfaceRouterAdvertisement` report. If the interface
            has no configuration block in FRR, an unconfigured report (RAs
            suppressed) is returned.
        """
        config_text = self._query_running_config()
        interfaces = self.parse_running_config(config_text, prefix=prefix)
        if interface_name in interfaces:
            return interfaces[interface_name]

        return InterfaceRouterAdvertisement(
            interface_name=interface_name,
            ra_sent=False,
            managed_flag=False,
            other_config_flag=False,
            advertises_autoconfig=False,
            prefixes_no_autoconfig=(),
        )


class RouterAdvertisementDiagnostics:
    """Start-time diagnostic checks for Router Advertisement and DHCPv6 Pool alignment.

    These diagnostics are warnings, never fatal errors. The lab starts, the servers start,
    and an instructor or learner can drive the scenario.
    """

    @staticmethod
    def _is_router(node: Any) -> bool:
        """Return True if node is an FRRouter or compatible router double."""
        from mniplab.router import FRRouter

        return (
            isinstance(node, FRRouter)
            or getattr(node, "is_router", False) is True
            or isinstance(getattr(node, "frr_daemons", None), (list, tuple))
        )

    @staticmethod
    def _find_interfaces_on_network(
        node: Any, network: ipaddress.IPv6Network
    ) -> List[str]:
        """Find the names of all interfaces on node attached to network.

        Args:
            node: Node to inspect.
            network: IPv6 network to match.

        Returns:
            List of matching interface names.
        """
        intf_list_fn = getattr(node, "intfList", None)
        if not callable(intf_list_fn):
            return []
        matched: List[str] = []
        for intf in intf_list_fn():
            if getattr(intf, "name", "") == "lo":
                continue
            addr_str = getattr(intf, "ip6", None)
            if not addr_str or not isinstance(addr_str, str):
                continue
            try:
                addr = addr_str.split("/")[0]
                ip = ipaddress.ip_address(addr)
                if ip in network:
                    name = getattr(intf, "name", None)
                    if name:
                        matched.append(name)
            except ValueError:
                continue
        return matched

    @classmethod
    def check_pool(
        cls,
        pool: Any,
        net: Any,
    ) -> List[str]:
        """Check whether attached routers and a DHCPv6 Pool agree.

        Args:
            pool: The IPv6 Pool to diagnose.
            net: The Lab containing hosts and routers.

        Returns:
            List of warning messages (empty if everything agrees).
        """
        if getattr(pool, "family", None) != 6:
            return []

        network = ipaddress.ip_network(pool.cidr, strict=False)
        if not isinstance(network, ipaddress.IPv6Network):
            return []

        attached_routers: List[Tuple[Any, str]] = []
        hosts = getattr(net, "hosts", []) if net is not None else []
        for host in hosts:
            if not cls._is_router(host):
                continue
            for intf_name in cls._find_interfaces_on_network(host, network):
                attached_routers.append((host, intf_name))

        warnings: List[str] = []

        any_m_set = False
        routers_with_autoconfig: List[Tuple[Any, str]] = []
        suppressed_with_m: List[Tuple[Any, str]] = []

        for router, intf_name in attached_routers:
            adapter = RouterAdvertisementAdapter(router)
            ra = adapter.query_interface(intf_name, prefix=network)
            if ra.sets_managed_flag:
                any_m_set = True
            elif ra.managed_flag and not ra.ra_sent:
                suppressed_with_m.append((router, intf_name))
            if ra.advertises_autoconfig:
                routers_with_autoconfig.append((router, intf_name))

        if not any_m_set:
            checked_routers = list(dict.fromkeys(r.name for r, _ in attached_routers))
            checked_desc = ", ".join(checked_routers) if checked_routers else "none"
            if suppressed_with_m:
                warnings.append(
                    f"DHCPv6 Pool on Subnet {pool.cidr}: no attached Router sets the M flag "
                    f"(checked: {checked_desc}). Router Advertisements are suppressed. "
                    f"Configure 'no ipv6 nd suppress-ra' (and 'ipv6 nd managed-config-flag') "
                    f"on the router interface."
                )
            else:
                warnings.append(
                    f"DHCPv6 Pool on Subnet {pool.cidr}: no attached Router sets the M flag "
                    f"(checked: {checked_desc}). Clients will not request DHCPv6 without "
                    f"'ipv6 nd managed-config-flag' configured on the router interface."
                )

        for router, intf_name in routers_with_autoconfig:
            warnings.append(
                f"DHCPv6 Pool on Subnet {pool.cidr}: Router {router.name} ({intf_name}) "
                f"still advertises autoconfiguration for this prefix. Hosts will hold an "
                f"address before the client runs. Configure 'ipv6 nd prefix {pool.cidr} "
                f"no-autoconfig' on the router interface to turn it off."
            )

        return warnings

    @classmethod
    def diagnose_service(
        cls,
        service: Any,
        net: Any,
    ) -> List[str]:
        """Diagnose all IPv6 Pools held by a DHCP Service.

        Args:
            service: The DHCP Service to diagnose.
            net: The Lab containing hosts and routers.

        Returns:
            List of warning messages across all IPv6 Pools.
        """
        warnings: List[str] = []
        pools_of_family = getattr(service, "pools_of_family", None)
        if callable(pools_of_family):
            v6_pools = list(pools_of_family(6).values())
        else:
            v6_pools = [
                p
                for p in getattr(service, "pools", {}).values()
                if getattr(p, "family", None) == 6
            ]

        for pool in v6_pools:
            warnings.extend(cls.check_pool(pool, net))

        return warnings
