"""DHCPService: a DHCP-serving Service, backed by kea-dhcp4 and/or kea-dhcp6.

One Service per Node, holding one Pool per Subnet — Kea serves many
Subnets from one server natively, so a Service per Subnet would put
several Kea processes on one Node for no reason. Which Subnets get DHCP
is Lab shape: Subnets are declared explicitly rather than discovered, so
an Instructor can leave one Subnet without DHCP on purpose.

The Service is one role serving both address families, family-aware rather
than split in two: each Pool's CIDR infers its family,
and :class:`mniplab.provisioners.DHCPProvisioner` starts ``kea-dhcp4``,
``kea-dhcp6``, or both according to what the declared Pools actually hold —
two processes is the provisioner's business, not a second Service.

All lifecycle behaviour — directory setup, configuration rendering,
CIDR-to-Interface resolution, and starting/stopping the Kea processes —
lives in :class:`mniplab.provisioners.DHCPProvisioner`, so this class
stays a plain data holder plus the state that resolution fills in.

Attributes:
    name: The canonical identifier for this Service (``'dhcp'``).
    pools: The Pools this Service serves, keyed by each Pool's CIDR so a
        Lab Example's declaration does not depend on link order.
    interface_names: The Node's Interface name each Pool's CIDR resolved
        to at Lab start, keyed by CIDR; empty before that.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mniplab.dhcp.pool import Pool
from mniplab.service import Service


class DHCPService(Service):
    """A DHCP Service: Kea serving one Pool per Subnet it serves."""

    name: str = "dhcp"

    def __init__(self, *pools: Pool) -> None:
        """Initialize a DHCP Service around its declared Pools.

        Args:
            *pools: One Pool per Subnet this Service serves. Each Pool's
                CIDR infers its address family — IPv4, IPv6, or both.
        """
        self.pools: Dict[str, Pool] = {pool.cidr: pool for pool in pools}
        # Pool is frozen, so the same instances can be kept as the declared
        # snapshot without copying — reset_pools never risks handing out a
        # mutated object, and every field (including ones added after this
        # was written) survives a reset for free.
        self._declared_pools: Dict[str, Pool] = dict(self.pools)
        self.interface_names: Dict[str, str] = {}

    def reset_pools(self) -> None:
        """Restore pools to what was originally declared by the Lab Example."""
        self.pools = dict(self._declared_pools)

    def pools_of_family(self, family: int) -> Dict[str, Pool]:
        """Return the Pools of one address family, keyed by CIDR.

        Args:
            family: 4 for the IPv4 Pools, 6 for the IPv6 Pools.

        Returns:
            Every declared Pool whose CIDR infers ``family``, in
            declaration order.
        """
        return {cidr: pool for cidr, pool in self.pools.items()
                if pool.family == family}

    @property
    def serves_ipv4(self) -> bool:
        """Whether this Service holds any IPv4 Pools."""
        return any(pool.family == 4 for pool in self.pools.values())

    @property
    def serves_ipv6(self) -> bool:
        """Whether this Service holds any IPv6 Pools."""
        return any(pool.family == 6 for pool in self.pools.values())

    @property
    def badge_text(self) -> str:
        """The badge label derived from the Pools this Service holds.

        Returns:
            "DHCP · DHCPv6" if both IPv4 and IPv6 pools are served,
            "DHCPv6" if only IPv6 pools are served,
            "DHCP" if only IPv4 pools are served, or
            "" if no pools are held.
        """
        if self.serves_ipv4 and self.serves_ipv6:
            return "DHCP · DHCPv6"
        if self.serves_ipv6:
            return "DHCPv6"
        if self.serves_ipv4:
            return "DHCP"
        return ""

    @property
    def families(self) -> List[int]:
        """Address families served by this Service, in family order.

        Returns:
            List of address family integers (e.g. [4], [6], or [4, 6]).
        """
        fams: List[int] = []
        if self.serves_ipv4:
            fams.append(4)
        if self.serves_ipv6:
            fams.append(6)
        return fams

    @classmethod
    def find_on(cls, node: Any) -> DHCPService:
        """Return the DHCP Service attached to ``node``.

        Args:
            node: The Node to search.

        Returns:
            The Node's attached DHCP Service.

        Raises:
            ValueError: The Node has no DHCP Service attached.
        """
        for service in getattr(node, "services", []):
            if isinstance(service, cls):
                return service
        raise ValueError(f"{node.name} has no DHCP Service attached")

