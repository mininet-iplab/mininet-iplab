"""Extended Link and Interface classes with IPv6 support."""

import time
from typing import Optional, Any
from mininet.link import Intf, TCIntf
from mniplab.errors import LinkError
from mniplab.logging import get_logger
from mniplab import ipv6

logger = get_logger(__name__)

# DAD timing constants (kept for backward compatibility)
DEFAULT_DAD_MAX_WAIT = 3
DEFAULT_DAD_POLL_INTERVAL = 0.1


class _IPv6ConfigMixin:
    """Shared IPv6-capable config implementation for Mininet interfaces.

    Mininet has no IPv6 support: an interface stores exactly one address and
    one prefix length, applies it with ``ifconfig``, and reads it back with a
    dotted-quad regular expression.  This mixin gives IPv6 the same seams under
    the same names with a ``6`` suffix — :meth:`setIP6`, :meth:`updateIP6`,
    :meth:`IP6` — so an interface carrying an IPv6 address has somewhere to
    keep it.

    Attributes:
        name: The interface name supplied by Mininet.
        ip6: The configured IPv6 address, without prefix length.
        prefixLen6: The configured IPv6 prefix length.
    """

    name: str

    def __init__(self, *args: Any, **params: Any) -> None:
        """Initialize IPv6 state before Mininet's constructor runs config().

        Args:
            *args: Positional arguments for the wrapped interface class.
            **params: Keyword arguments for the wrapped interface class.
        """
        # Intf.__init__ ends by calling config(), which may set these, so they
        # must exist before the super() call rather than after it.
        self.ip6: Optional[str] = None
        self.prefixLen6: Optional[str] = None
        super().__init__(*args, **params)

    def setIP6(self, ipstr: str, prefixLen: Optional[int] = None,
               dad_mode: Optional[str] = None) -> str:
        """Set our IPv6 address, mirroring :meth:`mininet.link.Intf.setIP`.

        Args:
            ipstr: IPv6 address, with or without a prefix length.
            prefixLen: Prefix length, required when ipstr omits one.
            dad_mode: 'fast' or 'strict'; resolved from the environment when
                omitted.

        Returns:
            The command output.

        Raises:
            LinkError: If the address is malformed, the prefix length is
                missing, or the address could not be applied.
        """
        if '/' not in ipstr:
            if prefixLen is None:
                raise LinkError(
                    f"{self._get_node_name()}: no prefix length set for IPv6 address {ipstr}\n"
                    f"Pass one as setIP6('{ipstr}', 64) or inline as '{ipstr}/64'"
                )
            ipstr = f'{ipstr}/{prefixLen}'

        normalized = ipv6.normalize_ipv6_address(ipstr, self._get_node_name())
        if not normalized:
            raise LinkError(f"{self._get_node_name()}: empty IPv6 address")

        mode = dad_mode or ipv6.get_dad_mode()
        result = self.cmd(  # type: ignore[attr-defined]
            ipv6.build_addr_add_command(self.name, normalized, mode)
        )

        if ipv6.is_ipv6_config_error(result):
            raise LinkError(
                f"{self._get_node_name()}: Failed to configure IPv6 on interface {self.name}\n"
                f"Address: {normalized}\n"
                f"Error: {result}\n"
                f"Possible causes:\n"
                f"  1. IPv6 is disabled: sysctl net.ipv6.conf.all.disable_ipv6\n"
                f"  2. Invalid address format (needs /prefixlen, e.g., 2001:db8::1/64)\n"
                f"  3. Interface doesn't exist: ip link show {self.name}\n"
                f"Debug: Run 'ip -6 addr show dev {self.name}' on node {self._get_node_name()}"
            )

        address, _, prefix = normalized.partition('/')
        self.ip6, self.prefixLen6 = address, prefix

        # Keep the address the way IPv4 keeps its own when the link goes
        # down; set once the address exists, so a failed add leaves nothing
        # behind. The interface's sysctl node exists only while the interface
        # does, so the setting cannot be made earlier than this.
        self.cmd(ipv6.build_keep_addr_on_down_command(self.name))  # type: ignore[attr-defined]

        if mode == ipv6.DAD_MODE_STRICT:
            self._wait_for_dad(normalized)

        return result

    def updateIP6(self) -> Optional[str]:
        """Re-read our IPv6 address from the node, mirroring :meth:`updateIP`.

        Reads what the kernel actually accepted rather than trusting what was
        configured — the two differ after a failed apply or an external change.

        Returns:
            The first global IPv6 address on the interface, or None.
        """
        output = self.cmd(  # type: ignore[attr-defined]
            ipv6.build_addr_show_command(self.name)
        )
        addresses = ipv6.parse_addresses(output)
        if addresses:
            address, _, prefix = addresses[0].partition('/')
            self.ip6 = address
            self.prefixLen6 = prefix or self.prefixLen6
        else:
            self.ip6 = None
        return self.ip6

    def IP6(self) -> Optional[str]:
        """Return our configured IPv6 address, mirroring :meth:`Intf.IP`.

        Returns:
            The IPv6 address without prefix length, or None.
        """
        return self.ip6

    def _wait_for_dad(self, ipv6_addr: str,
                      max_wait: int = ipv6.DEFAULT_DAD_MAX_WAIT,
                      poll_interval: float = ipv6.DEFAULT_DAD_POLL_INTERVAL) -> None:
        """Wait for Duplicate Address Detection to settle on this interface.

        Args:
            ipv6_addr: Address being waited on.
            max_wait: Seconds to wait before giving up.
            poll_interval: Seconds between polls.

        Raises:
            LinkError: If a duplicate address is detected on the network.
        """
        show_cmd = ipv6.build_addr_show_command(self.name)
        elapsed = 0.0
        while elapsed < max_wait:
            output = self.cmd(show_cmd)  # type: ignore[attr-defined]
            if ipv6.dad_failed(output):
                raise LinkError(
                    f"{self._get_node_name()}: IPv6 DAD failed for {ipv6_addr} on {self.name}\n"
                    f"Duplicate address detected on the network.\n"
                    f"This usually means another node has the same IPv6 address.\n"
                    f"Debug: {show_cmd}"
                )
            if ipv6.dad_settled(output, ipv6_addr):
                return
            time.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(
            '%s: IPv6 address %s on %s still tentative after %ss',
            self._get_node_name(), ipv6_addr, self.name, max_wait
        )

    def _get_node_name(self) -> str:
        """Get node name or 'unknown' if node not set."""
        return self.node.name if self.node else 'unknown'  # type: ignore[attr-defined]

    def _get_and_validate_dad_mode(self, params: dict) -> str:
        """Extract and validate DAD mode from params."""
        # Pop dad_mode so it doesn't propagate to parent config()
        dad_mode = str(params.pop('dad_mode', '')).strip().lower()
        if dad_mode:
            params['dad_mode'] = dad_mode
        return ipv6.get_dad_mode(params)

    def _normalize_ipv6_address(self, ip6: Optional[str]) -> Optional[str]:
        """Normalize and validate IPv6 address."""
        return ipv6.normalize_ipv6_address(ip6, self._get_node_name())

    def config(self, mac: Optional[str] = None, ip: Optional[str] = None, ifconfig: Optional[str] = None, up: bool = True, **params: Any) -> None:
        """Configure interface with IPv6 support.
        
        Args:
            mac: MAC address
            ip: IPv4 address
            ifconfig: Legacy ifconfig parameter
            up: Bring interface up
            **params: Additional parameters
            
        Raises:
            LinkError: If IPv6 address configuration fails or validation fails
        """
        ip6 = params.pop('ip6', None)
        dad_mode = self._get_and_validate_dad_mode(params)
        target_ip6 = self._normalize_ipv6_address(ip6)

        # Apply default Mininet config first (IPv4/mac/up)
        super().config(mac=mac, ip=ip, ifconfig=ifconfig, up=up, **params)  # type: ignore[misc]

        # Apply IPv6 configuration if present. setIP6 records the address on
        # this interface and waits for DAD when strict mode asks for it.
        if target_ip6 and self.node:  # type: ignore[attr-defined]
            self.setIP6(target_ip6, dad_mode=dad_mode)


class IPv6Intf(_IPv6ConfigMixin, Intf):
    """Intf class with IPv6 support for non-TC link classes."""


class IPv6TCIntf(_IPv6ConfigMixin, TCIntf):
    """TCIntf class with IPv6 support for TCLink."""
