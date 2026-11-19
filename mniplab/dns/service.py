"""DNSService: an Authoritative DNS Service, backed by Knot DNS (knotd).

One Service per Node, holding one or more Zones — Knot DNS serves multiple
forward and reverse zones from a single daemon natively.

Authority is a property of each Zone rather than of the Service, so one Node is
free to be Primary for one apex and Secondary for another, which is how real
nameservers are deployed.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Union

from mniplab.dns.models import SecondaryZone, Zone
from mniplab.service import Service

AnyZone = Union[Zone, SecondaryZone]


class DNSService(Service):
    """An Authoritative DNS Service: Knot DNS serving one or more hosted Zones.

    Attributes:
        name: The canonical identifier for this Service ('dns').
        zones: The Zones this Service hosts, keyed by each Zone's apex FQDN.
            Both Primary Zones and Secondary Zones live here.
        node: The Node this Service is attached to.
    """

    name: str = "dns"

    def __init__(self, *zones: Union[AnyZone, Iterable[AnyZone]]) -> None:
        """Initialize a DNS Service around its declared Zones.

        Args:
            *zones: One or more Zone or SecondaryZone instances (or an iterable
                of them) hosted by this Service.
        """
        zone_list: List[AnyZone] = []
        for item in zones:
            if isinstance(item, (Zone, SecondaryZone)):
                zone_list.append(item)
            elif isinstance(item, Iterable):
                zone_list.extend(
                    [z for z in item if isinstance(z, (Zone, SecondaryZone))]
                )

        self.zones: Dict[str, AnyZone] = {zone.name: zone for zone in zone_list}
        self._declared_zones: Dict[str, AnyZone] = {
            name: zone.clone() for name, zone in self.zones.items()
        }

    @property
    def primary_zones(self) -> Dict[str, Zone]:
        """The Zones this Service is authoritative for from its own zone files.

        Returns:
            Primary Zones keyed by apex FQDN.
        """
        return {
            name: zone
            for name, zone in self.zones.items()
            if isinstance(zone, Zone)
        }

    @property
    def secondary_zones(self) -> Dict[str, SecondaryZone]:
        """The Zones this Service replicates from a Primary.

        Returns:
            Secondary Zones keyed by apex FQDN.
        """
        return {
            name: zone
            for name, zone in self.zones.items()
            if isinstance(zone, SecondaryZone)
        }

    def bind_node(self, node_name: str) -> None:
        """Bind this Service and its hosted zones to a serving Node.

        Updates default SOA and NS records on auto-generated zones and refreshes
        the declared state snapshot so reset_zones() preserves node binding.

        Args:
            node_name: The name of the serving Node.
        """
        for zone in self.zones.values():
            zone.bind_host_node(node_name)
        self._declared_zones = {
            name: zone.clone() for name, zone in self.zones.items()
        }

    def reset_zones(self) -> None:
        """Restore hosted zones to what was originally declared by the Lab Example."""
        self.zones = {
            name: zone.clone() for name, zone in self._declared_zones.items()
        }

    @property
    def badge_text(self) -> str:
        """The badge label for this Service.

        Returns:
            "DNS · SECONDARY" when all hosted zones are secondary,
            "DNS · PRIMARY · SECONDARY" when hosting both primary and secondary,
            otherwise "DNS".
        """
        has_primary = bool(self.primary_zones)
        has_secondary = bool(self.secondary_zones)
        if has_secondary and not has_primary:
            return "DNS · SECONDARY"
        if has_primary and has_secondary:
            return "DNS · PRIMARY · SECONDARY"
        return "DNS"

    @property
    def families(self) -> List[int]:
        """Address families served by this Service.

        Returns:
            [4, 6] (Dual-stack DNS queries over IPv4 and IPv6).
        """
        return [4, 6]

    def add_zone(self, zone: AnyZone) -> None:
        """Add a Zone to this Service.

        Args:
            zone: The Zone or SecondaryZone instance to add.
        """
        self.zones[zone.name] = zone

    def get_zone(self, name: str) -> Optional[AnyZone]:
        """Get a hosted Zone by apex FQDN.

        Args:
            name: The zone apex FQDN.

        Returns:
            The matching Zone or SecondaryZone instance, or None.
        """
        return self.zones.get(name)

    @classmethod
    def find_on(cls, node: Any) -> DNSService:
        """Return the DNS Service attached to node.

        Args:
            node: The Node to search.

        Returns:
            The Node's attached DNS Service.

        Raises:
            ValueError: If the Node has no DNS Service attached.
        """
        for service in getattr(node, "services", []):
            if isinstance(service, cls):
                return service
        node_name = getattr(node, "name", "Node")
        raise ValueError(f"{node_name} has no DNS Service attached")
