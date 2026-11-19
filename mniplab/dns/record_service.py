"""DNSRecordService: atomic zone-record mutations for the Web API.

Wraps Knot zone transactions (zone-begin -> zone-set/zone-unset ->
zone-commit) so Learner edits from the Web UI apply atomically to the
running knotd server without dropping traffic or restarting the daemon.
Shared by the REST API layer (mniplab.webserver.api).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from mniplab.dns.models import SecondaryZone
from mniplab.dns.query import DNSQueryService
from mniplab.dns.service import DNSService
from mniplab.errors import DNSValidationError


def _normalize_zone(zone_name: str) -> str:
    """Return the zone apex FQDN, appending the trailing dot when missing."""
    return zone_name if zone_name.endswith(".") else f"{zone_name}."


class DNSRecordService:
    """Mutation service for zone Resource Records on running DNS nodes."""

    @staticmethod
    def _reject_secondary(service: DNSService, node_name: str, zone: str) -> None:
        """Refuse a Record write against a replicated Zone, naming its Primary.

        Knot answers a write to a Secondary with a bare transaction error that
        says nothing about where the Zone is authored, which is exactly the
        thing the Learner needs to know.

        Args:
            service: The Node's DNS Service.
            node_name: Serving Node's name.
            zone: The normalized zone apex FQDN.

        Raises:
            ValueError: If the Zone is a Secondary on this Node.
        """
        hosted = service.get_zone(zone)
        if isinstance(hosted, SecondaryZone):
            raise ValueError(
                f"{node_name} is secondary for {zone}: its Records are replicated "
                f"from {hosted.primary_label} and cannot be edited here. "
                f"Edit them on {hosted.primary_label} instead."
            )

    @staticmethod
    def _validate_common(zone_name: str, owner: str, rtype: Optional[str], data: Optional[str]) -> str:
        """Validate shared record parameters and return the normalized zone apex.

        Args:
            zone_name: The zone apex domain name.
            owner: Record owner name.
            rtype: DNS record type string (e.g. 'A', 'AAAA', 'CNAME').
            data: Record rdata payload.

        Returns:
            The normalized zone apex FQDN.

        Raises:
            ValueError: If any required field is empty.
        """
        if not owner or not owner.strip():
            raise ValueError("Record owner must not be empty")
        if not rtype or not rtype.strip():
            raise ValueError("Record type must not be empty")
        if not data or not data.strip():
            raise ValueError("Record data must not be empty")
        return _normalize_zone(zone_name)

    @classmethod
    def set_record(
        cls,
        net: Any,
        node_name: str,
        zone_name: str,
        owner: str,
        rtype: str,
        data: str,
        ttl: Optional[int] = None,
        old_owner: Optional[str] = None,
        old_type: Optional[str] = None,
        old_data: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add or update a Resource Record in an atomic Knot zone transaction.

        If ``old_owner`` is provided, the previous record values are unset
        within the same Knot zone transaction, guaranteeing atomic replacement
        without intermediate inconsistent states or data loss on validation failure.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.
            owner: Record owner name.
            rtype: DNS record type string (e.g. 'A', 'AAAA', 'CNAME').
            data: Record rdata payload.
            ttl: Optional TTL in seconds; must be positive when given.
            old_owner: Optional previous owner name for atomic replacement.
            old_type: Optional previous type for atomic replacement.
            old_data: Optional previous data for atomic replacement.

        Returns:
            Dict describing the committed record (node, zone, owner, type,
            data, ttl, and old_owner if replaced).

        Raises:
            ValueError: If any field is invalid or the socket is unreachable.
            DNSValidationError: If Knot's semantic checks reject the record.
            DNSControlError: If communication with the control socket fails.
        """
        zone = cls._validate_common(zone_name, owner, rtype, data)
        old_record = (old_owner, old_type, old_data)
        if any(value is not None for value in old_record) and not all(
            value and value.strip() for value in old_record
        ):
            raise ValueError(
                "old_owner, old_type, and old_data must be provided together "
                "for record replacement"
            )
        if ttl is None:
            # Knot's zone-set requires a TTL; default to the same 300 s the
            # record model and CLI use when a Lab Example omits one.
            ttl = 300
        if ttl <= 0:
            raise ValueError("TTL must be a positive integer")

        _node, service, client = DNSQueryService.get_client(net, node_name)
        cls._reject_secondary(service, node_name, zone)

        with client:
            client.begin_zone(zone)
            try:
                if old_owner is not None and old_type is not None and old_data is not None:
                    client.unset_record(
                        zone,
                        old_owner.strip(),
                        rtype=old_type.strip().upper(),
                        data=old_data.strip(),
                    )
                client.set_record(zone, owner.strip(), rtype.strip().upper(), data.strip(), ttl=ttl)
                client.diff_zone(zone)
                client.commit_zone(zone)
            except Exception:
                client.abort_zone(zone)
                raise

        result: Dict[str, Any] = {
            "node": node_name,
            "zone": zone,
            "owner": owner.strip(),
            "type": rtype.strip().upper(),
            "data": data.strip(),
            "ttl": ttl,
        }
        if old_owner and old_owner.strip():
            result["old_owner"] = old_owner.strip()
        return result

    @classmethod
    def unset_record(
        cls,
        net: Any,
        node_name: str,
        zone_name: str,
        owner: str,
        rtype: Optional[str] = None,
        data: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove a Resource Record in an atomic Knot zone transaction.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.
            owner: Record owner name.
            rtype: Optional record type string; omit to remove the whole owner.
            data: Optional record rdata payload; omit to remove all data of
                the given type at the owner.

        Returns:
            Dict describing the removed record (node, zone, owner, type, data).

        Raises:
            ValueError: If the owner is empty or the socket is unreachable.
            DNSValidationError: If Knot's semantic checks reject the removal.
            DNSControlError: If communication with the control socket fails.
        """
        if not owner or not owner.strip():
            raise ValueError("Record owner must not be empty")
        zone = _normalize_zone(zone_name)

        _node, service, client = DNSQueryService.get_client(net, node_name)
        cls._reject_secondary(service, node_name, zone)

        with client:
            client.begin_zone(zone)
            try:
                client.unset_record(
                    zone,
                    owner.strip(),
                    rtype=rtype.strip().upper() if rtype else None,
                    data=data.strip() if data else None,
                )
                client.diff_zone(zone)
                client.commit_zone(zone)
            except Exception:
                client.abort_zone(zone)
                raise

        return {
            "node": node_name,
            "zone": zone,
            "owner": owner.strip(),
            "type": rtype.strip().upper() if rtype else None,
            "data": data.strip() if data else None,
        }

    @classmethod
    def trigger_dnssec_rollover(
        cls,
        net: Any,
        node_name: str,
        zone_name: str,
        key_type: str = "zsk",
    ) -> Dict[str, Any]:
        """Trigger immediate DNSSEC key rollover for a hosted zone.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.
            key_type: Key type to roll over ('zsk' or 'ksk').

        Returns:
            Dict confirming the rollover action.

        Raises:
            ValueError: If node/zone not found or if the zone is a secondary.
            DNSValidationError: If key_type is invalid.
            DNSControlError: If communication with Knot fails.
        """
        zone = _normalize_zone(zone_name)
        _node, service, client = DNSQueryService.get_client(net, node_name)
        cls._reject_secondary(service, node_name, zone)

        with client:
            resp = client.zone_key_rollover(zone, key_type)

        return {
            "node": node_name,
            "zone": zone,
            "key_type": key_type.lower().strip(),
            "status": "success",
            "response": resp,
        }

