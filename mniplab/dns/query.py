"""DNSQueryService: query methods for reading DNS state, records, and statistics.

Shared by the CLI (mniplab.cli.mnIPLabCLI.do_show_dns*) and the Web API.

Replication is read here too. The Transfer Log is parsed out of the log Knot
already writes to each Node's run directory, which is host-visible, so watching
a Zone replicate needs no new log target, no new configuration and no daemon of
our own: every read happens on request.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, Iterator, List, Optional, Tuple

from mniplab.dns.control_client import KnotControlClient
from mniplab.dns.models import SecondaryZone
from mniplab.dns.provisioner import DNSProvisioner
from mniplab.dns.service import DNSService
from mniplab.errors import DNSControlError, DNSValidationError

#: One Knot log line: a timestamp, a severity, an optional Zone, and the message.
_LINE_RE = re.compile(
    r"^(?P<timestamp>\S+)\s+(?P<level>\w+):\s+(?:\[(?P<zone>[^\]]+)\]\s+)?(?P<message>.*)$"
)

#: A Zone Transfer line: 'AXFR, incoming, remote 10.0.1.2@53, finished, ...'.
_TRANSFER_RE = re.compile(
    r"^(?P<transfer_type>AXFR|IXFR), (?P<direction>incoming|outgoing), "
    r"remote (?P<remote>[^,]+), (?P<rest>.*)$"
)

#: 'started', optionally naming the Serial served or the increment carried.
_STARTED_RE = re.compile(
    r"^started(?:, serial (?P<first>\d+)(?: -> (?P<second>\d+))?)?$"
)

#: A refresh that moved the Zone: 'zone updated, 0.00 seconds, serial 1 -> 2'.
_UPDATED_RE = re.compile(
    r"^refresh, remote (?P<remote>[^,]+), zone updated, [\d.]+ seconds, "
    r"serial (?P<serial_from>none|\d+) -> (?P<serial_to>\d+)"
)

#: The metrics Knot puts on a finished transfer line.
_MESSAGES_RE = re.compile(r"(\d+) messages")
_BYTES_RE = re.compile(r"(\d+) bytes")
_DURATION_RE = re.compile(r"([\d.]+) seconds")

#: A refresh that found nothing to do: the Serial has not advanced.
_UP_TO_DATE_RE = re.compile(
    r"^refresh, remote [^,]+, remote serial (?P<serial>\d+), zone is up-to-date"
)

#: A single attempt against one Primary address, and why it failed.
_ATTEMPT_FAILED_RE = re.compile(
    r"^refresh, remote [^,]+, address (?P<address>[^,]+), failed \((?P<cause>[^)]*)\)$"
)

#: A Notify event: 'notify, incoming, remote 10.0.1.2@43120, serial 2'.
_NOTIFY_RE = re.compile(
    r"^notify, (?P<direction>incoming|outgoing), remote (?P<remote>[^,]+)(?:, serial (?P<serial>\d+))?$"
)

#: The refresh event giving up on every Primary, and when it tries again.
_REFRESH_FAILED_RE = re.compile(
    r"^refresh, failed \([^)]*\), next retry at (?P<next_retry>\S+)$"
)

#: Causes that mean the Primary answered and said no, rather than not answering.
_REFUSAL_CAUSES = ("not allowed", "not authorized", "refused")


@dataclass(frozen=True)
class TransferEvent:
    """One Zone Transfer as Knot logged it.

    Attributes:
        timestamp: When the transfer ended, in Knot's own log format.
        zone: The Zone's apex FQDN.
        direction: 'incoming' on a Secondary, 'outgoing' on a Primary.
        transfer_type: 'AXFR' for a full copy, 'IXFR' for an incremental one.
        remote: The other nameserver, as address@port.
        outcome: 'finished' or 'failed'.
        serial_from: The Serial replicated from, or None for a full copy of a
            Zone the receiver did not hold.
        serial_to: The Serial replicated to.
        messages: DNS messages the transfer took.
        bytes: Bytes that crossed the wire, which is what separates a full copy
            from an incremental one.
        duration: Seconds the transfer took.
        detail: Knot's own words when the transfer failed.
    """

    timestamp: str
    zone: str
    direction: str
    transfer_type: str
    remote: str
    outcome: str
    serial_from: Optional[int] = None
    serial_to: Optional[int] = None
    messages: Optional[int] = None
    bytes: Optional[int] = None
    duration: Optional[float] = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return this event as a JSON-ready dict.

        Returns:
            The event's fields under the same names both surfaces use.
        """
        return asdict(self)


def _normalize_apex(zone_name: str) -> str:
    """Return a Zone apex with its trailing dot.

    Args:
        zone_name: A Zone apex, with or without its trailing dot.

    Returns:
        The apex FQDN.
    """
    return zone_name if zone_name.endswith(".") else f"{zone_name}."


def _parse_number(pattern: "re.Pattern[str]", text: str, cast: Any) -> Any:
    """Return the number a pattern captures in text, or None.

    Args:
        pattern: A compiled regex with one capturing group.
        text: The text to search.
        cast: int or float, the type to read the capture as.

    Returns:
        The captured number, or None when absent or unparsable.
    """
    match = pattern.search(text)
    if not match:
        return None
    try:
        return cast(match.group(1))
    except ValueError:
        return None


def _iter_lines(
    text: str, zone: Optional[str] = None
) -> Iterator[Tuple[str, str, str]]:
    """Yield the parsed Knot log lines of one Zone, skipping the rest.

    Args:
        text: Raw Knot log text.
        zone: Optional Zone apex to narrow to; every Zone when omitted.

    Yields:
        (timestamp, zone, message) for each line that parses and matches.
    """
    wanted = _normalize_apex(zone) if zone else None
    for line in text.splitlines():
        match = _LINE_RE.match(line.strip())
        if not match:
            continue
        line_zone = match.group("zone")
        if not line_zone:
            continue
        if wanted and line_zone != wanted:
            continue
        yield match.group("timestamp"), line_zone, match.group("message")


def parse_transfer_log(
    text: str, zone: Optional[str] = None, include_notify: bool = False
) -> List[TransferEvent]:
    """Parse Knot log text into the Transfer Log.

    Lines that do not parse are skipped rather than raising, so an unfamiliar
    log format degrades to an empty Transfer Log instead of a broken command.

    Args:
        text: Raw Knot log text.
        zone: Optional Zone apex to narrow to; every Zone when omitted.
        include_notify: Whether to include Notify prompt events in the log.

    Returns:
        The completed and failed Zone Transfers, oldest first.
    """
    events: List[TransferEvent] = []
    # A transfer spans two lines. The started line carries the Serials a
    # Primary serves; a Secondary learns its own from the refresh line that
    # follows the transfer, so both are stitched onto the finished event.
    started: Dict[Tuple[str, str, str, str], Tuple[Optional[int], Optional[int]]] = {}

    for timestamp, line_zone, message in _iter_lines(text, zone):
        if include_notify:
            notify = _NOTIFY_RE.match(message)
            if notify:
                serial_str = notify.group("serial")
                events.append(
                    TransferEvent(
                        timestamp=timestamp,
                        zone=line_zone,
                        direction=notify.group("direction"),
                        transfer_type="NOTIFY",
                        remote=notify.group("remote"),
                        outcome="finished",
                        serial_to=int(serial_str) if serial_str is not None else None,
                    )
                )
                continue

        updated = _UPDATED_RE.match(message)
        if updated:
            # The Serials belong to the transfer that just landed from this
            # same remote: matching on the Zone alone would hand them to a
            # different Primary's event on a Node replicating from two.
            remote = updated.group("remote")
            landed = next(
                (
                    index
                    for index in range(len(events) - 1, -1, -1)
                    if events[index].zone == line_zone
                    and events[index].direction == "incoming"
                    and events[index].outcome == "finished"
                    and events[index].remote == remote
                    and events[index].serial_to is None
                ),
                None,
            )
            if landed is not None:
                serial_from = updated.group("serial_from")
                events[landed] = replace(
                    events[landed],
                    serial_from=None if serial_from == "none" else int(serial_from),
                    serial_to=int(updated.group("serial_to")),
                )
            continue

        transfer = _TRANSFER_RE.match(message)
        if not transfer:
            continue

        key = (
            line_zone,
            transfer.group("transfer_type"),
            transfer.group("direction"),
            transfer.group("remote"),
        )
        rest = transfer.group("rest")

        start = _STARTED_RE.match(rest)
        if start:
            first, second = start.group("first"), start.group("second")
            if second is not None:
                started[key] = (int(first), int(second))
            elif first is not None:
                started[key] = (None, int(first))
            else:
                started[key] = (None, None)
            continue

        serial_from, serial_to = started.pop(key, (None, None))
        finished = rest.startswith("finished")
        events.append(
            TransferEvent(
                timestamp=timestamp,
                zone=line_zone,
                direction=transfer.group("direction"),
                transfer_type=transfer.group("transfer_type"),
                remote=transfer.group("remote"),
                outcome="finished" if finished else "failed",
                serial_from=serial_from,
                serial_to=serial_to,
                messages=_parse_number(_MESSAGES_RE, rest, int),
                bytes=_parse_number(_BYTES_RE, rest, int),
                duration=_parse_number(_DURATION_RE, rest, float),
                detail="" if finished else rest,
            )
        )

    return events


def parse_replication_state(text: str, zone: Optional[str] = None) -> Dict[str, Any]:
    """Parse a Zone's current replication state out of Knot log text.

    The last thing that happened wins, so a transfer clears an earlier failure
    and a failure supersedes an earlier transfer. A refusal is reported as its
    own state: an ACL that says no is a reachable Primary, and calling that
    unreachable hides the cause a Learner is meant to find.

    Args:
        text: Raw Knot log text.
        zone: Optional Zone apex to narrow to.

    Returns:
        Dict with 'state' (one of 'replicated', 'up-to-date',
        'primary-unreachable', 'transfer-refused', 'expired', 'unknown'),
        'detail', 'since', 'next_retry' and 'serial'.
    """
    state = "unknown"
    detail = ""
    since = ""
    next_retry: Optional[str] = None
    serial: Optional[int] = None
    attempt_cause = ""

    for timestamp, _line_zone, message in _iter_lines(text, zone):
        updated = _UPDATED_RE.match(message)
        if updated:
            state, detail, since = "replicated", message, timestamp
            next_retry, attempt_cause = None, ""
            serial = int(updated.group("serial_to"))
            continue

        up_to_date = _UP_TO_DATE_RE.match(message)
        if up_to_date:
            state, detail, since = "up-to-date", message, timestamp
            next_retry, attempt_cause = None, ""
            serial = int(up_to_date.group("serial"))
            continue

        if message == "zone expired":
            state, detail, since = "expired", message, timestamp
            continue

        attempt = _ATTEMPT_FAILED_RE.match(message)
        if attempt:
            attempt_cause = attempt.group("cause")
            detail = message
            continue

        refused = _TRANSFER_RE.match(message)
        if refused and "server responded with error" in refused.group("rest"):
            attempt_cause = refused.group("rest")
            detail = message
            continue

        failed = _REFRESH_FAILED_RE.match(message)
        if failed:
            refusal = any(cause in attempt_cause.lower() for cause in _REFUSAL_CAUSES)
            # Expiry outlives the retries that follow it: a Zone that has
            # stopped answering is the state worth reporting.
            if state != "expired":
                state = "transfer-refused" if refusal else "primary-unreachable"
                since = timestamp
            next_retry = failed.group("next_retry")

    return {
        "state": state,
        "detail": detail,
        "since": since,
        "next_retry": next_retry,
        "serial": serial,
    }


class DNSQueryService:
    """Query service for inspecting running DNS services on Mininet nodes."""

    @staticmethod
    def find_service(net: Any, node_name: str) -> Tuple[Any, DNSService]:
        """Find the node and its attached DNSService.

        Args:
            net: Mininet network instance.
            node_name: Node name to find.

        Returns:
            Tuple of (node, DNSService).

        Raises:
            ValueError: If node is not found or has no DNSService.
        """
        node = None
        if hasattr(net, "get"):
            # Mininet's get raises KeyError for a name the Lab lacks; the
            # caller is promised a ValueError either way.
            try:
                node = net.get(node_name)
            except KeyError:
                node = None
        elif hasattr(net, "__getitem__"):
            try:
                node = net[node_name]
            except KeyError:
                node = None
        elif hasattr(net, "name") and net.name == node_name:
            node = net

        if node is None:
            raise ValueError(f"Node not found: {node_name}")

        try:
            service = DNSService.find_on(node)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        return node, service

    @classmethod
    def get_client(
        cls, net: Any, node_name: str
    ) -> Tuple[Any, DNSService, KnotControlClient]:
        """Resolve a Node, its Service, and a client on its control socket.

        The client is constructed, not connected: callers open it with
        ``with``, so this lookup has no side effect to undo.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.

        Returns:
            Tuple of (node, service, client).

        Raises:
            ValueError: If the Node or Service is absent, or the control
                socket does not exist — which means knotd is not running.
        """
        node, service = cls.find_service(net, node_name)
        socket_path = DNSProvisioner.control_socket_host_path(node_name, net=net)
        if not os.path.exists(socket_path):
            raise ValueError(
                f"Knot control socket not found at {socket_path}. Is knotd running on {node_name}?"
            )
        return node, service, KnotControlClient(socket_path)

    @classmethod
    def get_dns_info(cls, net: Any, node_name: str) -> Dict[str, Any]:
        """Return information about a Node's Authoritative DNS Service.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.

        Returns:
            Dict containing node status, socket path and state, listeners, and hosted zones.

        Raises:
            ValueError: If node is not found or has no DNSService attached.
        """
        node, service = cls.find_service(net, node_name)
        socket_path = DNSProvisioner.control_socket_host_path(node_name, net=net)
        socket_exists = os.path.exists(socket_path)
        socket_state = "inactive"
        running = False
        zones = list(service.zones.keys())

        if socket_exists:
            try:
                with KnotControlClient(socket_path) as client:
                    live_zones = client.get_zones()
                    if live_zones:
                        zones = live_zones
                    socket_state = "active"
                    running = True
            except Exception:
                socket_state = "stale"

        return {
            "node": node_name,
            "running": running,
            "socket_path": socket_path,
            "socket_state": socket_state,
            "listeners": ["0.0.0.0@53", "::@53"],
            "zones": zones,
            "zone_count": len(zones),
            "roles": {zone: cls.zone_role(service, zone) for zone in zones},
        }

    @classmethod
    def list_dns_nodes(cls, net: Any) -> List[Dict[str, Any]]:
        """Return information about every Node offering an Authoritative DNS Service.

        Args:
            net: Mininet network instance.

        Returns:
            List of per-node info dicts (as returned by :meth:`get_dns_info`),
            one per Node with a DNSService attached.
        """
        nodes: List[Dict[str, Any]] = []
        for host in getattr(net, "hosts", []) or []:
            try:
                DNSService.find_on(host)
            except ValueError:
                continue
            nodes.append(cls.get_dns_info(net, host.name))
        return nodes

    @classmethod
    def get_dns_zone_status(cls, net: Any, node_name: str, zone_name: str) -> Dict[str, Any]:
        """Return status (SOA serial, role, transaction state) for a hosted zone.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.

        Returns:
            Dict containing the zone's status fields from Knot (role, serial,
            transaction state).

        Raises:
            ValueError: If node/zone not found or socket is unreachable.
        """
        node, service, client = cls.get_client(net, node_name)
        normalized_zone = zone_name if zone_name.endswith(".") else f"{zone_name}."

        try:
            with client:
                status = client.get_zone_status(normalized_zone)
                try:
                    live_records = client.get_flat_records(normalized_zone)
                except DNSControlError:
                    live_records = []
        except DNSControlError as exc:
            raise ValueError(f"Failed to read status for zone {normalized_zone} on {node_name}: {exc}") from exc

        zone_obj = service.get_zone(normalized_zone)
        serial = status.get("serial")
        primary_ns = ""
        live_soa = next(
            (record for record in live_records if record.get("type") == "SOA"),
            None,
        )
        if live_soa:
            soa_fields = str(live_soa.get("data", "")).split()
            if soa_fields:
                primary_ns = soa_fields[0]
            if serial is None and len(soa_fields) > 2:
                try:
                    serial = int(soa_fields[2])
                except ValueError:
                    pass
        if not primary_ns:
            primary_ns = status.get("primary_ns", "")
        if not primary_ns and zone_obj and zone_obj.soa_record:
            primary_ns = zone_obj.soa_record.mname
        if serial is None and zone_obj and zone_obj.soa_record:
            serial = zone_obj.soa_record.serial

        status["primary_ns"] = primary_ns
        if serial is not None:
            status["serial"] = serial

        role = cls.zone_role(service, normalized_zone)
        # The Transfer Log is read from the same log the transfers panel uses;
        # only its last entry belongs on a status payload.
        transfers = cls.get_dns_transfers(net, node_name, normalized_zone)

        return {
            "node": node_name,
            "zone": normalized_zone,
            "status": status,
            "serial": serial,
            "primary_ns": primary_ns,
            "role": role["role"],
            "primary": role["primary"],
            "last_transfer": transfers["transfers"][-1] if transfers["transfers"] else None,
        }

    @classmethod
    def get_dns_records(cls, net: Any, node_name: str, zone_name: str) -> List[Dict[str, Any]]:
        """Return the active Resource Records for a hosted zone on node.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.

        Returns:
            List of record dicts with 'owner', 'ttl', 'type', and 'data'.

        Raises:
            ValueError: If node/zone not found or socket is unreachable.
        """
        node, service, client = cls.get_client(net, node_name)
        normalized_zone = zone_name if zone_name.endswith(".") else f"{zone_name}."

        try:
            with client:
                return client.get_flat_records(normalized_zone)
        except DNSControlError as exc:
            raise ValueError(f"Failed to read records for zone {normalized_zone} on {node_name}: {exc}") from exc

    @classmethod
    def get_dns_stats(cls, net: Any, node_name: str) -> Dict[str, Any]:
        """Return live query statistics for a Node's DNS Service.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.

        Returns:
            Dict of mod-stats counters.

        Raises:
            ValueError: If node not found or socket is unreachable.
        """
        node, service, client = cls.get_client(net, node_name)
        try:
            with client:
                return client.get_stats(list(service.zones))
        except DNSControlError as exc:
            raise ValueError(f"Failed to read stats on {node_name}: {exc}") from exc

    @classmethod
    def reset_dns_stats(cls, net: Any, node_name: str) -> None:
        """Reset query statistics baseline for a Node's DNS Service.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.

        Raises:
            ValueError: If node not found or socket is unreachable.
        """
        node, service, client = cls.get_client(net, node_name)
        try:
            with client:
                client.reset_stats(list(service.zones))
        except DNSControlError as exc:
            raise ValueError(f"Failed to reset stats on {node_name}: {exc}") from exc

    @staticmethod
    def zone_role(service: DNSService, zone_name: str) -> Dict[str, str]:
        """Return a hosted Zone's authority role and its Primary.

        Args:
            service: The Node's DNS Service.
            zone_name: The zone apex domain name.

        Returns:
            Dict with 'role' ('primary', 'secondary' or '' when the Zone was
            not declared by the Lab) and 'primary' naming the Node or address a
            Secondary replicates from.
        """
        zone = service.get_zone(_normalize_apex(zone_name))
        if isinstance(zone, SecondaryZone):
            return {"role": "secondary", "primary": zone.primary_label}
        if zone is not None:
            return {"role": "primary", "primary": ""}
        return {"role": "", "primary": ""}

    @classmethod
    def get_dns_transfers(
        cls,
        net: Any,
        node_name: str,
        zone_name: Optional[str] = None,
        include_notify: bool = False,
    ) -> Dict[str, Any]:
        """Return the Transfer Log and replication state of a Node's Zones.

        Read from the log Knot already writes, on request: there is no watcher
        and no cached history, so what is returned is what the daemon has
        logged by now.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: Optional zone apex to narrow the log to.
            include_notify: Whether to include Notify prompt events in the log.

        Returns:
            Dict with node, zone, role, primary, the parsed 'transfers', and
            the current replication 'state'.

        Raises:
            ValueError: If the node is not found or has no DNSService attached.
        """
        _node, service = cls.find_service(net, node_name)
        apex = _normalize_apex(zone_name) if zone_name else None
        if apex and apex not in service.zones:
            raise ValueError(f"Zone {apex} is not hosted on {node_name}")

        log_path = DNSProvisioner.log_host_path(node_name, net=net)
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
                text = log_file.read()
        except OSError:
            # A Node whose log is missing has simply transferred nothing yet.
            text = ""

        role = cls.zone_role(service, apex) if apex else {"role": "", "primary": ""}
        return {
            "node": node_name,
            "zone": apex or "",
            "role": role["role"],
            "primary": role["primary"],
            "transfers": [
                event.to_dict()
                for event in parse_transfer_log(
                    text, apex, include_notify=include_notify
                )
            ],
            "state": parse_replication_state(text, apex),
        }

    @classmethod
    def _transfer_command(
        cls, net: Any, node_name: str, zone_name: str, retransfer: bool
    ) -> Dict[str, Any]:
        """Ask a Secondary Zone to refresh or to take a full copy.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.
            retransfer: True to force a full copy, False to check the Serial.

        Returns:
            Dict naming the node, zone and the action taken.

        Raises:
            ValueError: If the Zone is not a Secondary on this Node, or the
                control socket is unreachable.
        """
        _node, service, client = cls.get_client(net, node_name)
        apex = _normalize_apex(zone_name)
        zone = service.get_zone(apex)
        if zone is None:
            raise ValueError(f"Zone {apex} is not hosted on {node_name}")
        if not isinstance(zone, SecondaryZone):
            raise ValueError(
                f"{node_name} is primary for {apex}: only a secondary transfers a Zone"
            )

        action = "retransfer" if retransfer else "refresh"
        try:
            with client:
                if retransfer:
                    client.retransfer_zone(apex)
                else:
                    client.refresh_zone(apex)
        except (DNSControlError, DNSValidationError) as exc:
            raise ValueError(
                f"Failed to {action} zone {apex} on {node_name}: {exc}"
            ) from exc

        return {
            "node": node_name,
            "zone": apex,
            "action": action,
            "primary": zone.primary_label,
        }

    @classmethod
    def refresh_zone(cls, net: Any, node_name: str, zone_name: str) -> Dict[str, Any]:
        """Ask a Secondary whether its Primary's Serial has moved.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.

        Returns:
            Dict naming the node, zone, action and Primary.

        Raises:
            ValueError: If the Zone is not a Secondary or the socket is unreachable.
        """
        return cls._transfer_command(net, node_name, zone_name, retransfer=False)

    @classmethod
    def retransfer_zone(cls, net: Any, node_name: str, zone_name: str) -> Dict[str, Any]:
        """Ask a Secondary to take a full copy regardless of the Serial.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.

        Returns:
            Dict naming the node, zone, action and Primary.

        Raises:
            ValueError: If the Zone is not a Secondary or the socket is unreachable.
        """
        return cls._transfer_command(net, node_name, zone_name, retransfer=True)

    @classmethod
    def get_zone_dnssec(cls, net: Any, node_name: str, zone_name: str) -> Dict[str, Any]:
        """Return the DNSSEC state, keys, and DS records for a hosted zone.

        Args:
            net: Mininet network instance.
            node_name: Serving Node's name.
            zone_name: The zone apex domain name.

        Returns:
            Dict containing DNSSEC status, NSEC3 settings, key list, and DS records.

        Raises:
            ValueError: If node/zone not found or zone is not hosted.
        """
        node, service, client = cls.get_client(net, node_name)
        normalized_zone = _normalize_apex(zone_name)
        zone = service.get_zone(normalized_zone)
        if zone is None:
            raise ValueError(f"Zone {normalized_zone} is not hosted on {node_name}")

        if isinstance(zone, SecondaryZone):
            return {
                "node": node_name,
                "zone": normalized_zone,
                "dnssec": False,
                "nsec3": False,
                "nsec3_salt": None,
                "is_secondary": True,
                "keys": [],
                "ds_records": [],
            }

        dnssec_enabled = bool(zone.dnssec)
        nsec3_enabled = bool(zone.nsec3)
        nsec3_salt = zone.nsec3_salt

        keys: List[Dict[str, Any]] = []
        ds_records: List[Dict[str, Any]] = []

        if dnssec_enabled:
            keys_dir = getattr(zone, "keys_dir", None)
            if not keys_dir or not os.path.exists(keys_dir):
                keys_dir = os.path.join(DNSProvisioner.host_dir(node_name, "lib", net=net), "keys")

            if os.path.exists(keys_dir):
                cmd_keys = ["keymgr", "-D", keys_dir, "-j", normalized_zone, "list"]
                res_keys = subprocess.run(cmd_keys, capture_output=True, text=True)
                if res_keys.returncode == 0 and res_keys.stdout.strip().startswith("["):
                    try:
                        raw_keys = json.loads(res_keys.stdout)
                        for k in raw_keys:
                            key_entry = {
                                "tag": k.get("tag") or k.get("keytag", 0),
                                "ksk": bool(k.get("ksk")),
                                "zsk": bool(k.get("zsk")),
                                "algorithm": k.get("algorithm", 13),
                                "size": k.get("size", 256),
                                "active": k.get("active"),
                                "id": k.get("id", ""),
                            }
                            for attr in ("created", "published", "ready", "retire", "remove"):
                                if attr in k:
                                    key_entry[attr] = k[attr]
                            keys.append(key_entry)
                    except json.JSONDecodeError:
                        pass

                if nsec3_enabled:
                    # The salt the Zone is signed with, which Knot picks itself
                    # when the Zone declares none.
                    res_salt = subprocess.run(
                        ["keymgr", "-D", keys_dir, normalized_zone, "nsec3-salt"],
                        capture_output=True, text=True,
                    )
                    live_salt = res_salt.stdout.partition("Current salt:")[2].strip()
                    if res_salt.returncode == 0 and live_salt:
                        nsec3_salt = None if live_salt == "-" else live_salt.lower()

                cmd_ds = ["keymgr", "-D", keys_dir, normalized_zone, "ds"]
                res_ds = subprocess.run(cmd_ds, capture_output=True, text=True)
                if res_ds.returncode == 0:
                    for line in res_ds.stdout.strip().splitlines():
                        parts = line.strip().split()
                        if len(parts) >= 6 and parts[1].upper() == "DS":
                            ds_records.append({
                                "owner": parts[0],
                                "key_tag": int(parts[2]),
                                "algorithm": int(parts[3]),
                                "digest_type": int(parts[4]),
                                "digest": parts[5],
                            })

        return {
            "node": node_name,
            "zone": normalized_zone,
            "dnssec": dnssec_enabled,
            "nsec3": nsec3_enabled,
            "nsec3_salt": nsec3_salt,
            "is_secondary": False,
            "keys": keys,
            "ds_records": ds_records,
        }
