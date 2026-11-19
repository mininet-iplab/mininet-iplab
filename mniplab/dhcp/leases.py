"""LeaseQueryService: reading what a DHCP Service's Kea currently holds.

Shared by the CLI (``mniplab.cli.mnIPLabCLI.do_show_leases``) and the Web
API (``mniplab.webserver.session.SessionRegistry.get_node_leases``), the
same way :class:`mniplab.netutils.RoutingQueryService` is shared by both
surfaces for routing state.

Leases are reported separately from configured Interface addresses, because
Lease display is not address display: a Lease is the
server's own record, which exists before a client applies it and outlives
the moment it stops being used, so folding it into address inspection would
misrepresent both.

A Lease carries no record of the Relay that forwarded it — Kea's lease
document has no such field — so "relayed" is derived here by joining a
Lease's subnet identifier back to the Pool it came from, which the
framework already knows the relay status of. The join is reliable only
because the Kea upgrade forces explicit subnet identifiers, enumerated the
same way :class:`mniplab.dhcp.config.KeaConfigBuilder` renders them; with
auto-assigned ones it would have been guesswork.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mniplab.dhcp.query import DHCPQueryService
from mniplab.dhcp.service import DHCPService
from mniplab.errors import ConfigurationError
from mniplab.provisioners import DHCPProvisioner


class LeaseQueryService:
    """Lists the Leases a Node's DHCP Service currently holds."""

    @staticmethod
    def get_leases(net: Any, node_name: str) -> Dict[str, Any]:
        """Return every Lease the named Node's DHCP Service currently holds.

        Leases are reported split by family, never merged into one table.
        A family with no Pool is absent rather than empty.

        Args:
            net: The running Lab.
            node_name: The DHCP Service's Node.

        Returns:
            ``{"success": True, "v4": [...], "v6": [...]}`` — Leases split by
            family. A family with no Pool is absent rather than empty. A
            Lease recorded by the server but not yet applied by any
            Interface still appears here, because this reads the server's
            own record, not Interface state. When the Lab stages a Relay for
            a family, every Lease of that family carries a ``"relayed"``
            boolean derived from its Pool's relay status; a Lab with no
            Relay adds no such label at all.

        Raises:
            ValueError: The Node does not exist, has no DHCP Service
                attached, or a Kea server is not reachable over its control
                socket (not running, or its socket does not exist yet).
        """
        _, service = DHCPQueryService.find_service(net, node_name)

        result: Dict[str, Any] = {"success": True}

        for family in service.families:
            _, _, client = DHCPQueryService.get_client(net, node_name, family)
            try:
                leases = client.get_all_leases()
            except ConfigurationError as exc:
                raise ValueError(str(exc)) from exc
            LeaseQueryService._annotate_relay_labels(leases, service, family, net)
            result[f"v{family}"] = leases

        return result

    @staticmethod
    def _annotate_relay_labels(
        leases: List[Dict[str, Any]], service: DHCPService, family: int, net: Any
    ) -> None:
        """Label each Lease ``relayed`` or ``direct``, in place, when the Lab
        stages a Relay for this family.

        A Lease carries no record of the Relay that forwarded it, so the
        label is derived: the Lease's ``subnet-id`` resolves to the Pool it
        came from — the identifiers the Kea upgrade made explicit, enumerated
        here exactly as :meth:`mniplab.dhcp.config.KeaConfigBuilder.build`
        renders them, so the join cannot drift from the served configuration
        — and the Pool's relay status is what the framework already knows:
        a Relay covers its Subnet or it does not. Runtime Pool edits replace
        a Pool in place without changing the per-family enumeration
        (:meth:`mniplab.dhcp.pool_service.PoolService.set_pool`), so a Lease
        granted before an edit still joins to the Pool that served it.

        A relayed Lease is otherwise the same record as a direct one, so the
        label is one added boolean and nothing else: no fields are removed or
        reordered, and a Lease whose ``subnet-id`` matches no Pool the
        framework knows about keeps the whole record unlabelled — absent
        data, not an error.

        Args:
            leases: The family's Leases, as ``leaseX-get-all`` reported them.
            service: The DHCP Service holding the Pools of this family.
            family: The address family these Leases belong to (4 or 6).
            net: The running Lab, searched for a Relay covering each Pool.
        """
        pools = service.pools_of_family(family)
        subnet_ids = {
            subnet_id: pool for subnet_id, pool in enumerate(pools.values(), start=1)
        }
        relayed_ids = {
            subnet_id
            for subnet_id, pool in subnet_ids.items()
            if DHCPProvisioner._find_relay_covering_subnet(net, pool.cidr) is not None
        }

        if not relayed_ids:
            # A Lab with no Relay shows no relay labelling at all, rather
            # than a "direct" label on every row.
            return

        for lease in leases:
            subnet_id = lease.get("subnet-id")
            if subnet_id in subnet_ids:
                lease["relayed"] = subnet_id in relayed_ids
