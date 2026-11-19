"""ResolverAccessService: Learner-authored Access Control, applied hot.

Access Control rules are Exercise Configuration: a Learner
authors them at runtime, they take effect immediately, and they vanish when
the Lab stops. Applying one rewrites ``unbound.conf`` and issues
``reload_keep_cache``, which is the whole reason this is a separate service
from the provisioner — a plain reload would drop the cache a Learner is in
the middle of watching, turning an Access Control exercise into an
accidental cache flush.

Unbound matches ``access-control`` by longest prefix rather than by file
order, so a ``/24`` refusal overrides the default ``0.0.0.0/0 allow``
regardless of where either sits in the list.

A reload keeps the cache but restarts Unbound's own query counters, so the
hit ratio a Learner was watching returns to zero after they author a rule.
The cache entries themselves — the thing the exercise is about — survive.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from mniplab.errors import ResolverControlError
from mniplab.resolver.models import AccessRule
from mniplab.resolver.provisioner import ResolverProvisioner
from mniplab.resolver.query import ResolverQueryService
from mniplab.resolver.service import ResolverService


def _without_subnet(
    rules: List[AccessRule], subnet: str
) -> List[AccessRule]:
    """Return the rules that do not cover one subnet, in match order.

    Args:
        rules: The rules currently in force.
        subnet: The normalized subnet to drop.

    Returns:
        Every other rule, so a subnet never carries two rules at once.
    """
    return [rule for rule in rules if rule.subnet != subnet]


class ResolverAccessService:
    """Mutation service for a running Resolver's Access Control rules."""

    @classmethod
    def list_rules(cls, net: Any, node_name: str) -> List[Dict[str, Any]]:
        """Return the Access Control rules a Resolver is enforcing.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.

        Returns:
            One dict per rule, in match order.

        Raises:
            ValueError: If the Node is absent or offers no Resolver Service.
        """
        _node, service = ResolverQueryService.find_service(net, node_name)
        return [rule.to_dict() for rule in service.access_control]

    @classmethod
    def set_rules(
        cls, net: Any, node_name: str, rules: Iterable[AccessRule]
    ) -> List[Dict[str, Any]]:
        """Replace a Resolver's Access Control rules and apply them hot.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.
            rules: The rules to enforce, in match order.

        Returns:
            The rules now in force, as dicts.

        Raises:
            ValueError: If the Node or Service is absent, or the daemon
                rejects the reload.
        """
        node, service = ResolverQueryService.find_service(net, node_name)
        previous = list(service.access_control)
        service.set_access_control(rules)
        try:
            cls._apply(net, node, service)
        except ValueError:
            # The rejected rules are already on disk, so restoring only the
            # in-memory list would leave the Lab reporting a policy the next
            # reload or restart would not apply. Rewrite the file too.
            service.set_access_control(previous)
            ResolverProvisioner.write_config(node.name, service, net=net)
            raise
        return [rule.to_dict() for rule in service.access_control]

    @classmethod
    def add_rule(
        cls, net: Any, node_name: str, subnet: str, action: str
    ) -> List[Dict[str, Any]]:
        """Add or replace the rule covering one client subnet.

        A subnet appears at most once: re-declaring it changes its action
        rather than stacking a second rule Unbound would never reach.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.
            subnet: The client subnet in CIDR notation.
            action: ``'allow'``, ``'refuse'``, or ``'deny'``.

        Returns:
            The rules now in force, as dicts.

        Raises:
            ValueError: If the subnet or action is invalid, the Node or
                Service is absent, or the daemon rejects the reload.
        """
        rule = AccessRule(subnet, action)
        _node, service = ResolverQueryService.find_service(net, node_name)
        remaining = _without_subnet(service.access_control, rule.subnet)
        return cls.set_rules(net, node_name, remaining + [rule])

    @classmethod
    def remove_rule(
        cls, net: Any, node_name: str, subnet: str
    ) -> List[Dict[str, Any]]:
        """Remove the rule covering one client subnet.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.
            subnet: The client subnet in CIDR notation.

        Returns:
            The rules now in force, as dicts.

        Raises:
            ValueError: If the subnet is invalid, no rule covers it, the Node
                or Service is absent, or the daemon rejects the reload.
        """
        target = AccessRule(subnet).subnet
        _node, service = ResolverQueryService.find_service(net, node_name)
        remaining = _without_subnet(service.access_control, target)
        if len(remaining) == len(service.access_control):
            raise ValueError(
                f"No Access Control rule for {target} on {node_name}"
            )
        return cls.set_rules(net, node_name, remaining)

    @classmethod
    def _apply(cls, net: Any, node: Any, service: ResolverService) -> None:
        """Rewrite the daemon configuration and reload it without the cache.

        Args:
            net: Network orchestrator instance.
            node: The resolving Node.
            service: The Resolver Service whose rules changed.

        Raises:
            ValueError: If the control socket is absent or the reload fails.
        """
        ResolverProvisioner.write_config(node.name, service, net=net)
        _node, _service, client = ResolverQueryService.get_client(net, node.name)
        try:
            client.reload_keep_cache()
        except ResolverControlError as exc:
            raise ValueError(
                f"Failed to apply Access Control rules on {node.name}: {exc}"
            ) from exc
