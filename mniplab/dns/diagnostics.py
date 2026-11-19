"""Start-time configuration diagnostics and teachable warnings for DNS Service.

A diagnostic is a warning when the failure is teachable:
- Fatal start-time errors (ConfigurationError): non-FQDN zone names (missing trailing dot),
  invalid DNS characters, or a duplicate zone apex claimed by multiple nodes.
- Teachable start-time warnings: child delegation NS records without glue, dangling CNAMEs,
  and forward A/AAAA records with no matching reverse PTR records in hosted reverse zones.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Union

from mininet.log import warn

from mniplab.errors import ConfigurationError
from mniplab.dns.models import (
    Record,
    ARecord,
    AAAARecord,
    CNAMERecord,
    DSRecord,
    NSRecord,
    PTRRecord,
    SecondaryZone,
    Zone,
)
from mniplab.dns.service import DNSService


def normalize_nameservers(
    nameservers: Optional[Union[Iterable[str], str]]
) -> List[str]:
    """Normalize and validate nameserver IP addresses.

    Args:
        nameservers: Single IP address string or collection of IP address strings.

    Returns:
        List of validated IP address strings.

    Raises:
        ValueError: If any nameserver is not a valid IPv4 or IPv6 address.
    """
    if nameservers is None:
        return []
    if isinstance(nameservers, str):
        candidates = [nameservers]
    else:
        candidates = list(nameservers)

    result: List[str] = []
    for s in candidates:
        stripped = s.strip()
        if not stripped:
            continue
        try:
            ip = ipaddress.ip_address(stripped)
            result.append(str(ip))
        except ValueError as e:
            raise ValueError(
                f"Invalid nameserver IP address '{stripped}': {e}"
            ) from e
    return result


class DNSDiagnostics:
    """Start-time diagnostic analyzer for Authoritative DNS Service configurations.

    Enforces RFC compliance and identifies configuration errors across all
    nodes and zones before daemons start.
    """

    @staticmethod
    def _to_fqdn(name: str, zone_apex: str) -> str:
        """Normalize a domain name or label to lowercase FQDN ending with dot.

        Args:
            name: Domain name or relative owner label.
            zone_apex: Zone apex domain name ending with dot.

        Returns:
            Normalized lowercase FQDN ending with dot.
        """
        clean = name.strip().lower()
        apex = zone_apex.lower()
        if clean in ("@", ""):
            return apex
        if clean.endswith("."):
            return clean
        if apex == ".":
            return f"{clean}."
        return f"{clean}.{apex}"

    @staticmethod
    def _matches_reverse_zone(ptr_fqdn: str, rz_name: str) -> bool:
        """Check if a PTR FQDN belongs to a reverse zone apex, respecting label boundaries.

        Args:
            ptr_fqdn: Lowercase PTR FQDN (e.g. '1.0.0.10.in-addr.arpa.').
            rz_name: Lowercase reverse zone apex (e.g. '0.0.10.in-addr.arpa.').

        Returns:
            True if ptr_fqdn is exactly rz_name or ends with '.{rz_name}'.
        """
        ptr = ptr_fqdn.lower()
        rz = rz_name.lower()
        return ptr == rz or ptr.endswith(f".{rz}")

    @classmethod
    def check_duplicate_apexes(cls, nodes: Iterable[Any]) -> None:
        """Verify that no two nodes claim Primary authority over one zone apex.

        A Primary and its Secondaries sharing an apex is the replication
        topology, not a conflict, so only two Primaries are fatal.

        Args:
            nodes: Nodes to inspect.

        Raises:
            ConfigurationError: If two nodes claim Primary authority over one apex.
        """
        apex_to_nodes: Dict[str, List[str]] = {}
        for node in nodes:
            for service in getattr(node, "services", []):
                if isinstance(service, DNSService):
                    for apex in service.primary_zones:
                        norm_apex = apex.lower()
                        apex_to_nodes.setdefault(norm_apex, []).append(node.name)

        for apex, node_names in sorted(apex_to_nodes.items()):
            if len(node_names) > 1:
                claimed = ", ".join(sorted(node_names))
                raise ConfigurationError(
                    f"Duplicate zone apex '{apex}' claimed as primary by multiple "
                    f"nodes: {claimed}"
                )

    @classmethod
    def diagnose_orphan_secondaries(cls, nodes: Iterable[Any]) -> List[str]:
        """Diagnose Secondary Zones whose apex no Node in the Lab serves as Primary.

        This is a warning rather than an error because an Instructor may point
        a Secondary at a deliberately unreachable Primary to stage that
        failure; a typo and a staged lesson are indistinguishable here.

        Args:
            nodes: Nodes to inspect.

        Returns:
            List of warning messages, one per orphaned Secondary Zone.
        """
        node_list = list(nodes)
        primary_apexes: Set[str] = {
            apex.lower()
            for node in node_list
            for service in getattr(node, "services", [])
            if isinstance(service, DNSService)
            for apex in service.primary_zones
        }

        warnings: List[str] = []
        for node in node_list:
            for service in getattr(node, "services", []):
                if not isinstance(service, DNSService):
                    continue
                for apex, zone in service.secondary_zones.items():
                    if apex.lower() in primary_apexes:
                        continue
                    warnings.append(
                        f"Node '{node.name}': secondary zone '{apex}' names primary "
                        f"'{zone.primary_label}', but no Node in this Lab serves "
                        f"'{apex}' as a primary. The Zone Transfer will fail unless the "
                        f"primary is reachable outside the Lab."
                    )
        return warnings

    @classmethod
    def check_zone_syntax(
        cls, zone: Union[Zone, SecondaryZone], node_name: str = ""
    ) -> None:
        """Validate zone apex FQDN syntax and record owner characters.

        Args:
            zone: The Zone or SecondaryZone to inspect. A Secondary declares no
                records, so only its apex is checked.
            node_name: Optional serving node name for diagnostic reporting.

        Raises:
            ConfigurationError: If zone apex is non-FQDN, has no trailing dot,
                or contains invalid DNS characters.
        """
        prefix = f"Node '{node_name}': " if node_name else ""
        zone_name = getattr(zone, "name", "")
        if not zone_name or not zone_name.endswith("."):
            raise ConfigurationError(
                f"{prefix}zone apex '{zone_name}' is not a fully-qualified domain name "
                f"(missing trailing dot)."
            )

        # The root zone's apex is the trailing dot alone, so it has no labels
        # to check. An in-lab root nameserver is what makes an Iterative Walk
        # possible without the public root servers.
        labels = zone_name[:-1].split(".") if zone_name != "." else []
        for label in labels:
            if not label or not re.match(r"^[a-zA-Z0-9_-]+$", label):
                raise ConfigurationError(
                    f"{prefix}zone apex '{zone_name}' contains invalid DNS characters."
                )

        # Validate records within zone
        for record in getattr(zone, "records", []):
            rname = getattr(record, "name", "")
            if rname in ("@", "*"):
                continue
            clean_rname = rname[:-1] if rname.endswith(".") else rname
            record_labels = clean_rname.split(".")
            for r_label in record_labels:
                if r_label == "*":
                    continue
                if not r_label or not re.match(r"^[a-zA-Z0-9_-]+$", r_label):
                    rec_prefix = (
                        f"Node '{node_name}', zone '{zone.name}': "
                        if node_name
                        else f"Zone '{zone.name}': "
                    )
                    raise ConfigurationError(
                        f"{rec_prefix}record '{record.name}' ({record.rtype}) "
                        f"contains invalid DNS characters."
                    )

    @classmethod
    def diagnose_delegation_glue(
        cls, zones: Iterable[Zone], node_name: str = ""
    ) -> List[str]:
        """Diagnose delegation NS records pointing into a child zone without glue.

        Args:
            zones: Zones to inspect.
            node_name: Optional serving node name for reporting.

        Returns:
            List of warning messages for missing in-bailiwick glue records.
        """
        warnings: List[str] = []
        for zone in zones:
            for record in getattr(zone, "records", []):
                if not isinstance(record, NSRecord):
                    continue
                if record.name in ("@", "", zone.name):
                    continue

                child_domain = cls._to_fqdn(record.name, zone.name)
                target_fqdn = cls._to_fqdn(record.nameserver, zone.name)

                # In-bailiwick check: target is inside the delegated child domain
                if target_fqdn.endswith(f".{child_domain}") or target_fqdn == child_domain:
                    if target_fqdn.endswith(f".{zone.name.lower()}"):
                        target_rel = target_fqdn[:-len(zone.name)].rstrip(".")
                    else:
                        target_rel = target_fqdn

                    has_glue = any(
                        isinstance(r, (ARecord, AAAARecord)) and (
                            r.name.lower() == target_rel.lower()
                            or cls._to_fqdn(r.name, zone.name) == target_fqdn
                        )
                        for r in zone.records
                    )
                    if not has_glue:
                        node_desc = f" on node '{node_name}'" if node_name else ""
                        warnings.append(
                            f"Zone '{zone.name}'{node_desc}: delegation NS record "
                            f"'{record.name} -> {record.nameserver}' points into child zone '{child_domain}' "
                            f"without matching glue record. Configure "
                            f"'ARecord(\"{target_rel}\", \"<ip>\")' in zone '{zone.name}' to provide glue."
                        )
        return warnings

    @classmethod
    def diagnose_dangling_cnames(
        cls,
        zones: Iterable[Zone],
        all_zones: Optional[Iterable[Zone]] = None,
        node_name: str = "",
    ) -> List[str]:
        """Diagnose CNAME records pointing to targets that do not exist in any zone.

        Args:
            zones: Target zones to inspect for dangling CNAMEs.
            all_zones: Complete universe of configured zones in the lab.
            node_name: Optional serving node name for reporting.

        Returns:
            List of warning messages for dangling CNAME records.
        """
        all_z = list(all_zones) if all_zones is not None else list(zones)
        known_names: Set[str] = set()
        for z in all_z:
            known_names.add(z.name.lower())
            for r in getattr(z, "records", []):
                known_names.add(cls._to_fqdn(r.name, z.name))

        warnings: List[str] = []
        for zone in zones:
            for record in getattr(zone, "records", []):
                if not isinstance(record, CNAMERecord):
                    continue
                target_fqdn = cls._to_fqdn(record.target, zone.name)

                if target_fqdn not in known_names:
                    if target_fqdn.endswith(f".{zone.name.lower()}"):
                        target_rel = target_fqdn[:-len(zone.name)].rstrip(".")
                    else:
                        target_rel = target_fqdn
                    node_desc = f" on node '{node_name}'" if node_name else ""
                    warnings.append(
                        f"Zone '{zone.name}'{node_desc}: CNAME record "
                        f"'{record.name} -> {record.target}' points to '{target_fqdn}' which does "
                        f"not exist in any configured zone. Configure an ARecord or AAAARecord for "
                        f"'{target_fqdn}' (e.g. 'ARecord(\"{target_rel}\", \"<ip>\")') to resolve it."
                    )
        return warnings

    @classmethod
    def diagnose_reverse_ptr_parity(
        cls,
        zones: Iterable[Zone],
        all_zones: Optional[Iterable[Zone]] = None,
        node_name: str = "",
    ) -> List[str]:
        """Diagnose forward A/AAAA records missing reverse PTR in hosted reverse zones.

        Args:
            zones: Target forward zones to inspect.
            all_zones: Complete universe of configured zones in the lab.
            node_name: Optional serving node name for reporting.

        Returns:
            List of warning messages for missing reverse PTR records.
        """
        all_z = list(all_zones) if all_zones is not None else list(zones)
        reverse_zones = [
            z for z in all_z
            if z.name.lower().endswith((".in-addr.arpa.", ".ip6.arpa."))
        ]
        if not reverse_zones:
            return []

        warnings: List[str] = []
        for zone in zones:
            if zone.name.lower().endswith((".in-addr.arpa.", ".ip6.arpa.")):
                continue

            for record in getattr(zone, "records", []):
                if not isinstance(record, (ARecord, AAAARecord)):
                    continue
                address = record.address
                try:
                    ip_obj = ipaddress.ip_address(address)
                except ValueError:
                    continue

                ptr_fqdn = f"{ip_obj.reverse_pointer}.".lower()

                matching_rev = [
                    rz for rz in reverse_zones
                    if cls._matches_reverse_zone(ptr_fqdn, rz.name)
                ]
                for rz in matching_rev:
                    ptr_rel = ptr_fqdn[:-len(rz.name)].rstrip(".")
                    owner_fqdn = cls._to_fqdn(record.name, zone.name)
                    has_ptr = any(
                        isinstance(r, PTRRecord) and (
                            r.name.lower() == ptr_rel.lower()
                            or cls._to_fqdn(r.name, rz.name) == ptr_fqdn
                        )
                        and r.ptrdname.rstrip(".").lower() == owner_fqdn.rstrip(".").lower()
                        for r in getattr(rz, "records", [])
                    )
                    if not has_ptr:
                        node_desc = f" on node '{node_name}'" if node_name else ""
                        warnings.append(
                            f"Forward {record.rtype} record '{owner_fqdn}' ('{address}'){node_desc} "
                            f"has no matching reverse PTR record in hosted reverse zone '{rz.name}'. "
                            f"Configure 'PTRRecord(\"{ptr_rel}\", \"{owner_fqdn}\")' in zone "
                            f"'{rz.name}' to ensure forward-reverse parity."
                        )
        return warnings

    @classmethod
    def diagnose_dnssec_delegations(
        cls,
        zones: Iterable[Zone],
        all_zones: Optional[Iterable[Zone]] = None,
        node_name: str = "",
    ) -> List[str]:
        """Diagnose parent DS records delegating to child zones.

        Identifies:
        1. DS records pointing to unsigned child zones (dnssec=False).
        2. DS records whose key tag, algorithm, digest type, or digest does
           not match the child zone's provisioned KSK.

        These emit start-time warnings rather than fatal errors,
        allowing instructors to deliberately stage broken trust chains.

        Args:
            zones: Target parent zones to inspect.
            all_zones: Complete universe of configured primary zones in the lab.
            node_name: Optional serving node name for reporting.

        Returns:
            List of warning messages for broken or mismatched DNSSEC delegations.
        """
        candidate_zones = list(all_zones) if all_zones is not None else list(zones)
        zone_by_name: Dict[str, Zone] = {z.name.lower(): z for z in candidate_zones}

        warnings: List[str] = []
        for parent_zone in zones:
            for record in getattr(parent_zone, "records", []):
                if not isinstance(record, DSRecord):
                    continue

                child_fqdn = cls._to_fqdn(record.name, parent_zone.name).lower()
                child_zone = zone_by_name.get(child_fqdn)
                if not child_zone:
                    continue

                node_desc = f" on node '{node_name}'" if node_name else ""

                if not child_zone.dnssec:
                    warnings.append(
                        f"Zone '{parent_zone.name}'{node_desc}: DS record for child zone '{child_zone.name}' "
                        f"points to an unsigned zone (dnssec=False). Resolvers enforcing DNSSEC will treat "
                        f"this domain as bogus and fail validation with SERVFAIL."
                    )
                    continue

                try:
                    expected_ds = child_zone.ds_record(digest_type=record.digest_type)
                except (ValueError, RuntimeError) as exc:
                    warnings.append(
                        f"Zone '{parent_zone.name}'{node_desc}: Failed to inspect KSK DS record for child zone '{child_zone.name}': {exc}"
                    )
                    continue

                if (
                    record.key_tag != expected_ds.key_tag
                    or record.algorithm != expected_ds.algorithm
                    or record.digest.strip().lower() != expected_ds.digest.strip().lower()
                ):
                    warnings.append(
                        f"Zone '{parent_zone.name}'{node_desc}: DS record for '{record.name}' "
                        f"(key tag {record.key_tag}, digest {record.digest[:8]}...) does not match "
                        f"child zone '{child_zone.name}' KSK digest (mismatch: expected key tag {expected_ds.key_tag}, "
                        f"digest {expected_ds.digest[:8]}...). Resolvers enforcing DNSSEC will fail "
                        f"validation with SERVFAIL."
                    )
        return warnings

    @classmethod
    def validate_network(
        cls,
        net: Any,
        warn_fn: Optional[Callable[[str], None]] = None,
    ) -> List[str]:
        """Validate DNS configurations across all nodes in the network.

        Runs fatal checks (raising ConfigurationError) and collects teachable
        warnings, outputting warnings via warn_fn (or mininet.log.warn).

        Args:
            net: Mininet network instance holding hosts.
            warn_fn: Optional warning sink callable.

        Returns:
            List of warning message strings.

        Raises:
            ConfigurationError: If any fatal configuration error is detected.
        """
        hosts = getattr(net, "hosts", [])
        dns_nodes = [
            h for h in hosts
            if any(isinstance(s, DNSService) for s in getattr(h, "services", []))
        ]
        if not dns_nodes:
            return []

        # 1. Fatal: Duplicate zone apexes across nodes
        cls.check_duplicate_apexes(dns_nodes)

        # 2. Fatal: Zone FQDN and character syntax
        for node in dns_nodes:
            for srv in getattr(node, "services", []):
                if isinstance(srv, DNSService):
                    for zone in srv.zones.values():
                        cls.check_zone_syntax(zone, node_name=node.name)

        # Collect every Primary Zone across all nodes. A Secondary declares no
        # records, so it has nothing for the record-level diagnostics to read.
        all_zones: List[Zone] = []
        for node in dns_nodes:
            for srv in getattr(node, "services", []):
                if isinstance(srv, DNSService):
                    all_zones.extend(srv.primary_zones.values())

        # 3. Teachable warnings
        warnings: List[str] = cls.diagnose_orphan_secondaries(dns_nodes)
        for node in dns_nodes:
            for srv in getattr(node, "services", []):
                if isinstance(srv, DNSService):
                    node_zones = list(srv.primary_zones.values())
                    warnings.extend(
                        cls.diagnose_delegation_glue(node_zones, node_name=node.name)
                    )
                    warnings.extend(
                        cls.diagnose_dangling_cnames(
                            node_zones, all_zones=all_zones, node_name=node.name
                        )
                    )
                    warnings.extend(
                        cls.diagnose_reverse_ptr_parity(
                            node_zones, all_zones=all_zones, node_name=node.name
                        )
                    )
                    warnings.extend(
                        cls.diagnose_dnssec_delegations(
                            node_zones, all_zones=all_zones, node_name=node.name
                        )
                    )

        # Emit warnings
        for msg in warnings:
            if warn_fn is not None:
                warn_fn(msg)
            else:
                warn(f"*** Warning: {msg}\n")

        return warnings
