"""Start-time configuration diagnostics for the Recursive Caching Resolver Service.

A diagnostic is a warning when the failure is teachable.

Fatal (``ConfigurationError``, the Lab never starts) covers configurations that
can never resolve anything — a Resolver with neither Root Hints nor forwarders
and no default route to reach the public root, a Root Hint Node carrying no
addresses, or a hint or forwarder address the Resolver has no route to.

Teachable (a warning, the Lab runs) covers configurations a Learner is meant to
observe failing — Root Hints aimed at a Node that does not host the root zone,
a forwarder that is nobody's Resolver or nameserver, and Root Hints shadowed by
forwarders declared alongside them.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Callable, Dict, Iterable, List, Optional

from mininet.log import warn

from mniplab.errors import ConfigurationError
from mniplab.resolver.models import interface_addresses
from mniplab.resolver.service import ResolverService, resolver_services

ROOT_ZONE = "."


class ResolverDiagnostics:
    """Start-time diagnostic analyzer for Resolver Service configurations."""

    @staticmethod
    def node_subnets(node: Any) -> List[Any]:
        """Return the subnets directly attached to a Node's interfaces.

        Args:
            node: The Node to inspect.

        Returns:
            One ``ip_network`` per configured interface address.
        """
        subnets: List[Any] = []
        for intf in getattr(node, "intfList", lambda: [])():
            for address, prefix in (
                (getattr(intf, "ip", None), getattr(intf, "prefixLen", None)),
                (getattr(intf, "ip6", None), getattr(intf, "prefixLen6", None)),
            ):
                if not address:
                    continue
                try:
                    subnets.append(
                        ipaddress.ip_network(f"{address}/{prefix}", strict=False)
                    )
                except ValueError:
                    continue
        return subnets

    @staticmethod
    def has_default_route(node: Any) -> bool:
        """Report whether a Node declared a default route.

        Args:
            node: The Node to inspect.

        Returns:
            True if the Node was declared with a ``defaultRoute``.
        """
        return bool(getattr(node, "params", {}).get("defaultRoute"))

    @staticmethod
    def is_router(node: Any) -> bool:
        """Report whether a Node routes rather than merely being routed to.

        Args:
            node: The Node to inspect.

        Returns:
            True for an FRRouter or a compatible test double.
        """
        from mniplab.router import FRRouter

        return (
            isinstance(node, FRRouter)
            or getattr(node, "is_router", False) is True
            or isinstance(getattr(node, "frr_daemons", None), (list, tuple))
        )

    @classmethod
    def reaches_beyond_its_subnets(cls, node: Any) -> bool:
        """Report whether a Node can send traffic off its own attached subnets.

        Args:
            node: The Node to inspect.

        Returns:
            True if the Node has a default route, or is a Router, whose routes
            come from its routing daemons rather than from a declared
            ``defaultRoute`` and so cannot be read before the Lab starts.
        """
        return cls.has_default_route(node) or cls.is_router(node)

    @classmethod
    def is_routable_from(cls, address: str, node: Any) -> bool:
        """Report whether a Node can plausibly reach an address.

        Args:
            address: The target IPv4 or IPv6 address.
            node: The Resolver Node the query would leave from.

        Returns:
            True if the address falls in one of the Node's own subnets, or the
            Node can send traffic beyond them at all.
        """
        if cls.reaches_beyond_its_subnets(node):
            return True
        try:
            target = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(target in subnet for subnet in cls.node_subnets(node))

    @staticmethod
    def address_owners(nodes: Iterable[Any]) -> Dict[str, Any]:
        """Map every interface address in the Lab to the Node carrying it.

        Args:
            nodes: The Nodes to index.

        Returns:
            Address strings mapped to their owning Node.
        """
        owners: Dict[str, Any] = {}
        for node in nodes:
            for address in interface_addresses(node):
                owners.setdefault(address, node)
        return owners

    @staticmethod
    def hosts_root_zone(node: Any) -> bool:
        """Report whether a Node hosts the root zone authoritatively.

        Args:
            node: The Node to inspect, or None.

        Returns:
            True if the Node has an Authoritative DNS Service hosting ``.``.
        """
        if node is None:
            return False
        for service in getattr(node, "services", []):
            zones = getattr(service, "zones", None)
            if isinstance(zones, dict) and ROOT_ZONE in zones:
                return True
        return False

    @staticmethod
    def offers_dns(node: Any) -> bool:
        """Report whether a Node answers DNS queries at all.

        Args:
            node: The Node to inspect, or None.

        Returns:
            True if the Node offers either an Authoritative DNS Service or a
            Resolver Service.
        """
        if node is None:
            return False
        return any(
            getattr(service, "name", "") in ("dns", "resolver")
            for service in getattr(node, "services", [])
        )

    @classmethod
    def check_reachability(cls, node: Any, service: ResolverService) -> None:
        """Verify a Resolver has a hierarchy it can actually reach.

        Args:
            node: The resolving Node.
            service: The Resolver Service attached to it.

        Raises:
            ConfigurationError: If the Resolver has no hierarchy at all, a Root
                Hint Node carries no addresses, or a hint or forwarder address
                is unroutable from this Node.
        """
        if service.mode == "default" and not cls.reaches_beyond_its_subnets(node):
            raise ConfigurationError(
                f"Node '{node.name}': Resolver Service declares neither root_hints "
                f"nor forwarders, and the Node has no default route to reach the "
                f"public root nameservers. Declare root_hints=[<root node>] for an "
                f"in-lab hierarchy, or forwarders=['<upstream ip>']."
            )

        for hint in service.root_hints:
            addresses = hint.resolve_addresses()
            if not addresses:
                raise ConfigurationError(
                    f"Node '{node.name}': root hint '{hint.label}' resolves to no "
                    f"IP address. Give the hinted Node an address on a Link, or "
                    f"declare the hint as an IP address string."
                )
            for address in addresses:
                if not cls.is_routable_from(address, node):
                    raise ConfigurationError(
                        f"Node '{node.name}': root hint '{hint.label}' address "
                        f"{address} is not on any of this Node's subnets, and the "
                        f"Node has neither a default route nor routing daemons to "
                        f"reach it."
                    )

        for forwarder in service.forwarders:
            if not cls.is_routable_from(forwarder, node):
                raise ConfigurationError(
                    f"Node '{node.name}': forwarder {forwarder} is not on any of "
                    f"this Node's subnets, and the Node has neither a default route "
                    f"nor routing daemons to reach it."
                )

    @classmethod
    def diagnose_hierarchy(
        cls,
        node: Any,
        service: ResolverService,
        owners: Dict[str, Any],
    ) -> List[str]:
        """Diagnose teachable hierarchy misconfigurations on one Resolver.

        Args:
            node: The resolving Node.
            service: The Resolver Service attached to it.
            owners: Every Lab interface address mapped to its owning Node.

        Returns:
            Warning messages for configurations that run but will not resolve
            the way a Learner expects.
        """
        warnings: List[str] = []

        if service.forwarders and service.root_hints:
            warnings.append(
                f"Node '{node.name}': Resolver Service declares both root_hints and "
                f"forwarders. Forwarding takes precedence, so queries will not walk "
                f"the hierarchy the root hints describe."
            )

        for hint in service.root_hints:
            target = hint.node
            if target is None:
                target = next(
                    (
                        owners[address]
                        for address in hint.resolve_addresses()
                        if address in owners
                    ),
                    None,
                )
            if not cls.hosts_root_zone(target):
                named = getattr(target, "name", None) or hint.label
                warnings.append(
                    f"Node '{node.name}': root hint '{hint.label}' points at "
                    f"'{named}', which does not host the root zone ('.'). The "
                    f"Iterative Walk will fail at its first step until that Node "
                    f"is given a DNSService hosting Zone('.')."
                )

        for forwarder in service.forwarders:
            if not cls.offers_dns(owners.get(forwarder)):
                warnings.append(
                    f"Node '{node.name}': forwarder {forwarder} is not a Node "
                    f"offering DNS in this Lab. Queries forwarded there will time "
                    f"out rather than resolve."
                )

        return warnings

    @classmethod
    def validate_network(
        cls,
        net: Any,
        warn_fn: Optional[Callable[[str], None]] = None,
    ) -> List[str]:
        """Validate Resolver configurations across every Node in the network.

        Runs the fatal checks and collects the teachable warnings, emitting
        each through warn_fn (or Mininet's own ``warn``).

        Args:
            net: The network orchestrator holding the Lab's Nodes.
            warn_fn: Optional warning sink callable.

        Returns:
            The warning messages emitted.

        Raises:
            ConfigurationError: If any fatal misconfiguration is detected.
        """
        hosts = list(getattr(net, "hosts", []))
        resolving = [host for host in hosts if resolver_services(host)]
        if not resolving:
            return []

        owners = cls.address_owners(hosts)
        warnings: List[str] = []
        for node in resolving:
            for service in resolver_services(node):
                cls.check_reachability(node, service)
                warnings.extend(cls.diagnose_hierarchy(node, service, owners))

        for message in warnings:
            if warn_fn is not None:
                warn_fn(message)
            else:
                warn(f"*** Warning: {message}\n")

        return warnings
