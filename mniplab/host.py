"""Host node classes with IPv6 support."""

import shlex
from typing import Any, List, Optional
from mininet.node import Node
from mininet.log import error
from mniplab.errors import LinkError
from mniplab.namespace import NamespaceMixin
from mniplab.service import ServiceMixin
from mniplab.shell import BinarySafeShellMixin
from mniplab.switch import SubinterfaceMixin
from mniplab import ipv6
from mniplab.dns.diagnostics import normalize_nameservers

# Ensure Mininet's base Node class also carries add_subinterface
if not hasattr(Node, 'add_subinterface'):
    setattr(Node, 'add_subinterface', SubinterfaceMixin.add_subinterface)


class mnIPLabHost(SubinterfaceMixin, ServiceMixin, BinarySafeShellMixin, NamespaceMixin, Node):
    """Standard host node for mnIPLab with IPv6 support.

    Attributes:
        display_type (str): Node type for visualization ('host')
        private_namespace_capable (bool): Has its own PID and UTS namespaces;
            see :mod:`mniplab.namespace`.
    """

    display_type = "host"
    private_namespace_capable = True
    
    def __init__(self, name: str, **params: Any) -> None:
        """Initialize host with IPv6 and nameservers support.

        Args:
            name: Host hostname.
            **params: Configuration parameters including:
                defaultRoute6: Optional IPv6 default route specification.
                nameservers: Optional nameserver IP address or list of IP addresses.
                privateDirs: Optional private directory mounts.
                homeDir: Optional custom home directory.
        """
        # Extract and validate nameservers parameter
        raw_nameservers = params.get('nameservers')
        self._nameservers: List[str] = normalize_nameservers(raw_nameservers)

        super(mnIPLabHost, self).__init__(name, **params)

    @property
    def nameservers(self) -> List[str]:
        """Return the list of configured nameserver IP addresses for this host."""
        return list(self._nameservers)
    
    def setIP6(self, ip6: str, prefixLen: Optional[int] = None,
               intf: Optional[Any] = None) -> str:
        """Set the host's IPv6 address, mirroring :meth:`mininet.node.Node.setIP`.

        Delegates to the interface when it can carry IPv6 state, so the address
        is recorded where it belongs rather than only issued as a command.

        Args:
            ip6: IPv6 address, with its prefix unless prefixLen gives it.
            prefixLen: Prefix length, required when ip6 omits one.
            intf: Interface to configure; the default interface when omitted.

        Returns:
            The command output, or an empty string when there was nothing to do.

        Raises:
            LinkError: If the address is malformed or could not be applied.
        """
        target = intf if intf is not None else self.defaultIntf()
        if not ip6 or target is None:
            return ''

        if hasattr(target, 'setIP6'):
            return str(target.setIP6(ip6, prefixLen))

        address = ip6

        # A plain Mininet interface has nowhere to keep IPv6 state, so issue the
        # command directly. The address still reaches the kernel.
        if '/' not in address:
            if prefixLen is None:
                raise LinkError(
                    f"{self.name}: no prefix length set for IPv6 address {address}\n"
                    f"Pass one as setIP6('{address}', 64) or inline as '{address}/64'"
                )
            address = f'{address}/{prefixLen}'

        normalized = ipv6.normalize_ipv6_address(address, self.name)
        if not normalized:
            return ''

        result = self.cmd(
            ipv6.build_addr_add_command(target.name, normalized, ipv6.get_dad_mode())
        )
        if ipv6.is_ipv6_config_error(result):
            raise LinkError(
                f"{self.name}: Failed to set IPv6 address {normalized} on {target.name}\n"
                f"Error: {result}"
            )
        return result

    def setDefaultRoute6(self, via: str) -> str:
        """Set the host's IPv6 default route, mirroring :meth:`setDefaultRoute`.

        Args:
            via: Route specification, e.g. ``'via 2001:db8::1'``.

        Returns:
            The command output, or an empty string when there was nothing to do.

        Raises:
            LinkError: If the route could not be applied.
        """
        if not via:
            return ''

        # Quote per token: a route spec is several shell words ('via <addr>'),
        # so quoting the whole string would make it one meaningless argument.
        safe_route = ' '.join(shlex.quote(token) for token in str(via).split())
        result = self.cmd(f'ip -6 route add default {safe_route}')
        if result and ('error' in result.lower() or 'fail' in result.lower()):
            raise LinkError(
                f"{self.name}: Failed to set IPv6 default route to {via}\n"
                f"Error: {result}"
            )
        return result

    def config(self, **params: Any) -> None:
        """Configure host with IPv6 support.

        A gateway failure is logged rather than raised here: this runs for every
        host during Lab startup, and a Lab that cannot start teaches nothing.
        A Lab Example calling :meth:`setDefaultRoute6` directly gets the exception.

        Args:
            **params: Configuration parameters, including ``defaultRoute6``
        """
        super(mnIPLabHost, self).config(**params)

        default_route6 = params.get('defaultRoute6')
        if default_route6:
            try:
                self.setDefaultRoute6(default_route6)
            except LinkError as e:
                error(f'{self.name}: Failed to set IPv6 default route: {e}\n')

        # Disable ICMP rate limiting for lab environment
        from mniplab.utils import disable_icmp_rate_limiting
        disable_icmp_rate_limiting(self)
