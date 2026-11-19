"""Router NAT: a Router's own translation, declared by the Lab and rendered as iptables rules.

An Instructor marks a Router's Interface as its outside one with
``'nat': 'outside'`` on a Link end, and lists port forwards and static 1:1
mappings on the Router with ``addRouter(nat=[...])``::

    r1 = net.addRouter('r1', nat=[PortForward('tcp', 8080, '192.168.3.10', 80)])
    net.addLink(r1, isp, params1={'ip': '203.0.113.2/30', 'nat': 'outside'},
                params2={'ip': '203.0.113.1/30'})

At start the Lab checks the declaration against the Nodes it built, then
renders it as iptables rules inside the Router's own namespace. Nothing is
written on the Host machine, and every start restores the rules from the
declaration.
"""

from __future__ import annotations

import ipaddress
import shlex
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from mniplab.errors import ConfigurationError, RouterError
from mniplab.nodelib import MasqueradeNAT, TransitNAT

#: The Link-end mark that makes an Interface its Router's outside one.
OUTSIDE = "outside"
_PROTOCOLS = ("tcp", "udp")
# Printed after a rule's command so its exit status comes back with its output.
_STATUS_MARKER = "__mniplab_status="


def _check_port(value: Any, field: str) -> None:
    """Refuse a port that is not an integer from 1 to 65535."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"{field} must be a port from 1 to 65535, not {value!r}")


def _check_ipv4(value: Any, field: str) -> None:
    """Refuse an address that is not text naming an IPv4 address."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an IPv4 address, not {value!r}")
    try:
        ipaddress.IPv4Address(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an IPv4 address, not {value!r}") from exc


@dataclass(frozen=True)
class PortForward:
    """A port forward: outside clients reach an inside server through the outside address.

    Attributes:
        protocol: ``'tcp'`` or ``'udp'``.
        outside_port: The port clients use on the outside address.
        inside: The inside server's IPv4 address.
        inside_port: The port the inside server listens on.
        hairpin: True to let inside clients reach the forward through the
            outside address too.
        interface: The outside Interface's name. May be omitted when the
            Router has exactly one outside Interface.
    """

    protocol: str
    outside_port: int
    inside: str
    inside_port: int
    hairpin: bool = False
    interface: Optional[str] = None

    def __post_init__(self) -> None:
        """Check the protocol, both ports and the inside address.

        Raises:
            ValueError: If any of them is not valid.
        """
        if self.protocol not in _PROTOCOLS:
            raise ValueError(
                f"A port forward's protocol must be 'tcp' or 'udp', not {self.protocol!r}"
            )
        _check_port(self.outside_port, "outside_port")
        _check_port(self.inside_port, "inside_port")
        _check_ipv4(self.inside, "inside")


@dataclass(frozen=True)
class StaticNAT:
    """A static 1:1 mapping: one inside address is reached at, and leaves as, an outside address.

    Attributes:
        inside: The inside address.
        outside: The address the inside address is mapped to. The upstream
            Router must route it toward the outside Interface.
        interface: The outside Interface's name. May be omitted when the
            Router has exactly one outside Interface.
    """

    inside: str
    outside: str
    interface: Optional[str] = None

    def __post_init__(self) -> None:
        """Check both addresses.

        Raises:
            ValueError: If either is not an IPv4 address.
        """
        _check_ipv4(self.inside, "inside")
        _check_ipv4(self.outside, "outside")


def _describe(entry: Any) -> str:
    """Name one entry the way a refusal's message reads it."""
    if isinstance(entry, PortForward):
        return (f"port forward {entry.protocol} {entry.outside_port} "
                f"to {entry.inside}:{entry.inside_port}")
    return f"static mapping {entry.outside} to {entry.inside}"


def _mark(intf: Any) -> Any:
    """The ``nat`` value a Link end declared for an Interface, or None."""
    return getattr(intf, "params", {}).get("nat")


def _outside(node: Any) -> List[Any]:
    """The Interfaces of a Node marked outside, in port order."""
    return [intf for intf in node.intfs.values() if _mark(intf) == OUTSIDE]


def _is_router(node: Any) -> bool:
    """True for a Router, the only Node that can translate."""
    return getattr(node, "display_type", None) == "router"


def _far_node(intf: Any) -> Any:
    """The Node at the far end of an Interface's Link, or None when it has none."""
    link = getattr(intf, "link", None)
    if link is None:
        return None
    other = link.intf2 if link.intf1 is intf else link.intf1
    return other.node


def _interface_named(node: Any, name: str) -> Any:
    """The Interface of ``node`` with this name, or None."""
    for intf in node.intfs.values():
        if intf.name == name:
            return intf
    return None


def _ipv4_interface(intf: Any) -> Optional[ipaddress.IPv4Interface]:
    """The IPv4 address and prefix an Interface was declared with, or None."""
    raw = getattr(intf, "params", {}).get("ip")
    if not raw:
        return None
    try:
        iface = ipaddress.ip_interface(raw)
    except ValueError:
        return None
    return iface if isinstance(iface, ipaddress.IPv4Interface) else None


def _ipv4_address(intf: Any) -> Optional[str]:
    """The IPv4 address an Interface was declared with, or None."""
    iface = _ipv4_interface(intf)
    return None if iface is None else str(iface.ip)


def _inside_networks(node: Any, outside: List[Any]) -> List[str]:
    """The IPv4 networks of a Router's Interfaces other than its outside ones."""
    networks = set()
    for intf in node.intfs.values():
        if intf in outside:
            continue
        iface = _ipv4_interface(intf)
        if iface is not None:
            networks.add(str(iface.network))
    return sorted(networks)


def _entry_interface(node: Any, entry: Any, outside: List[Any]) -> Any:
    """The outside Interface an entry applies to.

    Raises:
        ConfigurationError: If the Router has no outside Interface, the entry
            names an Interface that is not one of its own or not outside, or
            the entry names none and the Router has several outside Interfaces.
    """
    if not outside:
        raise ConfigurationError(
            f"{node.name}: {_describe(entry)} needs an outside Interface; mark one on its "
            f"Link end with 'nat': 'outside'"
        )
    if entry.interface is None:
        if len(outside) > 1:
            names = ", ".join(intf.name for intf in outside)
            raise ConfigurationError(
                f"{node.name}: {_describe(entry)} names no Interface, and {node.name} has "
                f"{len(outside)} outside Interfaces ({names}); name one with interface="
            )
        return outside[0]
    named = _interface_named(node, entry.interface)
    if named is None:
        raise ConfigurationError(
            f"{node.name}: {_describe(entry)} names {entry.interface}, which is not an "
            f"Interface of {node.name}"
        )
    if named not in outside:
        raise ConfigurationError(
            f"{node.name}: {_describe(entry)} names {entry.interface}, which is not marked "
            f"outside; mark it with 'nat': 'outside' on its Link end"
        )
    return named


def _check_far_end(node: Any, mark: Any) -> None:
    """Refuse an outside Interface whose far end already translates.

    Raises:
        ConfigurationError: If the Interface faces an Internet Gateway, or
            faces the Speaker in front of a Transit one.
    """
    far = _far_node(mark)
    if far is None:
        return
    if isinstance(far, MasqueradeNAT):
        raise ConfigurationError(
            f"{node.name}: {mark.name} is marked outside, but faces the Internet Gateway "
            f"{far.name}, which already translates there"
        )
    for intf in getattr(far, "intfs", {}).values():
        beyond = _far_node(intf)
        if isinstance(beyond, TransitNAT):
            raise ConfigurationError(
                f"{node.name}: {mark.name} is marked outside, but faces {far.name}, the "
                f"Speaker in front of the Internet Gateway {beyond.name}, which already "
                f"translates there"
            )


def validate_network(net: Any) -> None:
    """Refuse a Router NAT declaration that could not take effect, before anything starts.

    Called at start, after the Links exist and before any Node is started.

    Args:
        net: The Lab, whose Nodes are read.

    Raises:
        ConfigurationError: Naming the Router and the Interface at fault.
    """
    for node in [*net.hosts, *getattr(net, "switches", [])]:
        for intf in node.intfs.values():
            mark = _mark(intf)
            if mark not in (None, OUTSIDE):
                raise ConfigurationError(
                    f"{node.name}: {intf.name} is marked {mark!r}; the only mark is 'outside'"
                )
        outside = _outside(node)
        if not _is_router(node):
            if outside:
                raise ConfigurationError(
                    f"{node.name}: {outside[0].name} is marked outside, but {node.name} is "
                    f"not a Router; only a Router masquerades"
                )
            continue
        entries = list(getattr(node, "nat", ()))
        for entry in entries:
            if not isinstance(entry, (PortForward, StaticNAT)):
                raise ConfigurationError(
                    f"{node.name}: NAT entry {entry!r} is neither a port forward nor a "
                    f"static mapping"
                )
        for mark in outside:
            _check_far_end(node, mark)
        for entry in entries:
            target = _entry_interface(node, entry, outside)
            if isinstance(entry, PortForward) and _ipv4_address(target) is None:
                raise ConfigurationError(
                    f"{node.name}: {_describe(entry)} needs an IPv4 address on its outside "
                    f"Interface {target.name}"
                )


def _render(node: Any) -> List[str]:
    """The iptables rules a Router's Router NAT needs, in the order they are appended.

    Static mappings come first so their source rewrite precedes the
    masquerade; each rule is written after ``iptables -t nat``.
    """
    outside = _outside(node)
    entries = list(getattr(node, "nat", ()))
    rules: List[str] = []
    for entry in entries:
        if not isinstance(entry, StaticNAT):
            continue
        name = shlex.quote(_entry_interface(node, entry, outside).name)
        inside = shlex.quote(f"{entry.inside}/32")
        mapped = shlex.quote(f"{entry.outside}/32")
        rules.append(f"-A PREROUTING -i {name} -d {mapped} -j DNAT "
                     f"--to-destination {shlex.quote(entry.inside)}")
        rules.append(f"-A POSTROUTING -o {name} -s {inside} -j SNAT "
                     f"--to-source {shlex.quote(entry.outside)}")
    for entry in entries:
        if not isinstance(entry, PortForward):
            continue
        target = _entry_interface(node, entry, outside)
        protocol = shlex.quote(entry.protocol)
        address = shlex.quote(f"{_ipv4_address(target)}/32")
        destination = shlex.quote(f"{entry.inside}:{entry.inside_port}")
        inside = shlex.quote(f"{entry.inside}/32")
        if entry.hairpin:
            rules.append(f"-A PREROUTING -d {address} -p {protocol} --dport {entry.outside_port} "
                         f"-j DNAT --to-destination {destination}")
            for network in _inside_networks(node, outside):
                rules.append(f"-A POSTROUTING -s {shlex.quote(network)} -d {inside} "
                             f"-p {protocol} --dport {entry.inside_port} "
                             f"-m conntrack --ctstate DNAT -j MASQUERADE")
        else:
            rules.append(f"-A PREROUTING -i {shlex.quote(target.name)} -d {address} "
                         f"-p {protocol} --dport {entry.outside_port} "
                         f"-j DNAT --to-destination {destination}")
    for intf in outside:
        rules.append(f"-A POSTROUTING -o {shlex.quote(intf.name)} -j MASQUERADE")
    return rules


def _refusal(node: Any, rule: str) -> Optional[str]:
    """Run one rule in the Router's namespace; return why iptables refused it, or None.

    The verdict is the exit status. iptables also prints harmless warnings
    with status 0, so printed output alone never refuses a rule.

    Args:
        node: The Router.
        rule: The rule, as it follows ``iptables -t nat``.

    Returns:
        The exit status and what iptables printed, or None when it succeeded.
    """
    output = node.cmd(f"iptables -t nat {rule} 2>&1; echo {_STATUS_MARKER}$?")
    text, marker, status = output.rpartition(_STATUS_MARKER)
    if not marker:
        return f"no exit status was reported, output: {output.strip()}"
    if status.strip() == "0":
        return None
    return f"exit status {status.strip()}: {text.strip() or 'no output'}"


def apply_network(net: Any) -> None:
    """Render every Router's Router NAT into its namespace.

    Called at every start, from the declaration, so a restart restores it.

    Args:
        net: The Lab, whose Nodes are read.

    Raises:
        RouterError: If iptables refuses a rule, naming the Router and the rule.
    """
    for node in net.hosts:
        for rule in _render(node):
            refusal = _refusal(node, rule)
            if refusal is not None:
                raise RouterError(
                    f"{node.name}: Router NAT rule was refused: iptables -t nat {rule}: {refusal}"
                )


def router_translators(node: Any) -> List[Tuple[Any, str]]:
    """Name a Router's outside Interfaces as the Interfaces it translates on.

    Args:
        node: Any Node of the Lab.

    Returns:
        ``(node, interface name)`` per outside Interface of a Router; empty for
        any other Node.
    """
    if not _is_router(node):
        return []
    return [(node, intf.name) for intf in _outside(node)]
