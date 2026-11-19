"""KnotControlClient: UNIX control-channel adapter for Knot DNS (knotd).

Connects to Knot's UNIX domain control socket from the root namespace across
mount namespaces, wrapping libknot.control.KnotCtl with clean Python exception
handling, transaction lifecycle management, live query stats, and response formatting.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union

import libknot.control
from libknot.control import (
    KnotCtlData,
    KnotCtlDataIdx,
    KnotCtlError,
    KnotCtlErrorConnect,
    KnotCtlErrorReceive,
    KnotCtlErrorRemote,
    KnotCtlErrorSend,
)

from mniplab.errors import DNSControlError, DNSValidationError

if TYPE_CHECKING:
    from mniplab.dns.models import DSRecord


class KnotControlClient:
    """Adapter for one Knot DNS server's UNIX control-channel socket.

    Attributes:
        socket_path: Host-side path of the Knot server's control socket
            (``labs/<key>/nodes/<node>/run/knot/knot.sock`` under the runtime root).
        timeout: Socket operations timeout in seconds.
    """

    _socket_baselines: Dict[str, Dict[str, Any]] = {}

    def __init__(
        self,
        socket_path: str,
        timeout: int = 5,
        ctl_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Initialize the client against one Knot control socket.

        Args:
            socket_path: Host-side path of Knot's control UNIX socket.
            timeout: Control socket timeout in seconds.
            ctl_factory: Optional factory returning a KnotCtl instance
                (defaults to libknot.control.KnotCtl).
        """
        self.socket_path = socket_path
        self.timeout = timeout
        self._ctl_factory = ctl_factory or libknot.control.KnotCtl
        self._ctl: Optional[Any] = None
        self._in_transaction: bool = False
        self._stats_baseline: Dict[str, Any] = copy.deepcopy(
            self._socket_baselines.get(socket_path, {})
        )

    def connect(self) -> None:
        """Connect to the control UNIX socket.

        Raises:
            DNSControlError: If connection to the control socket fails.
        """
        if self._ctl is None:
            self._ctl = self._ctl_factory()
        try:
            self._ctl.set_timeout(self.timeout)
            self._ctl.connect(self.socket_path)
        except Exception as exc:
            self._handle_error(exc)

    def close(self) -> None:
        """Disconnect from the control socket and clean up state."""
        if self._ctl is not None:
            try:
                self._ctl.close()
            except Exception:
                pass
            self._ctl = None
        self._in_transaction = False

    def __enter__(self) -> KnotControlClient:
        """Enter the context manager, establishing a connection."""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        """Exit the context manager, closing the connection."""
        self.close()

    def _ensure_connected(self) -> Any:
        """Ensure the control client is connected and return the underlying KnotCtl."""
        if self._ctl is None:
            self.connect()
        return self._ctl

    def _handle_error(self, exc: Exception) -> None:
        """Map libknot and transport exceptions to clean domain exceptions.

        Args:
            exc: The caught exception.

        Raises:
            DNSValidationError: If the error represents a zone/record semantic or syntax violation.
            DNSControlError: If the error represents a control channel or communication failure.
        """
        if isinstance(exc, KnotCtlErrorRemote):
            command = None
            error_code = None
            zone = None
            details: Dict[str, str] = {}

            if exc.data:
                try:
                    command = exc.data[KnotCtlDataIdx.COMMAND]
                    error_code = exc.data[KnotCtlDataIdx.ERROR]
                    zone = exc.data[KnotCtlDataIdx.ZONE]
                    details = {
                        idx.name.lower(): exc.data[idx]
                        for idx in KnotCtlDataIdx
                        if exc.data[idx]
                    }
                except (AttributeError, KeyError, IndexError):
                    pass

            error_msg = exc.message or error_code or str(exc)
            validation_keywords = (
                "semantic",
                "invalid",
                "not exists",
                "no such",
                "duplicate",
                "malformed",
                "syntax",
                "collision",
            )
            is_validation = (
                any(kw in error_msg.lower() for kw in validation_keywords)
                or (error_code is not None and any(kw in error_code.lower() for kw in validation_keywords))
            )

            if is_validation:
                raise DNSValidationError(
                    str(exc),
                    command=command,
                    error_code=error_code,
                    zone=zone,
                    data=details,
                ) from exc
            raise DNSControlError(
                str(exc),
                command=command,
                error_code=error_code,
                zone=zone,
                data=details,
            ) from exc

        if isinstance(exc, (KnotCtlErrorConnect, KnotCtlErrorSend, KnotCtlErrorReceive, OSError)):
            raise DNSControlError(
                f"Failed to communicate with Knot control socket at {self.socket_path}: {exc}",
                data={"socket_path": self.socket_path},
            ) from exc

        if isinstance(exc, (DNSControlError, DNSValidationError)):
            raise exc

        raise DNSControlError(str(exc)) from exc

    def _send_block(self, cmd: str, **kwargs: Any) -> Dict[str, Any]:
        """Send a control command block and return the received response.

        Args:
            cmd: Knot control command name.
            **kwargs: Command parameters (section, item, zone, owner, ttl, rtype, data).

        Returns:
            Structured dictionary response from Knot.
        """
        ctl = self._ensure_connected()
        try:
            ctl.send_block(cmd=cmd, **kwargs)
            return ctl.receive_block()
        except Exception as exc:
            self._handle_error(exc)
            return {}

    def get_zones(self) -> List[str]:
        """Return the list of hosted zone domain apexes.

        Returns:
            List of hosted zone apex domain names.

        Raises:
            DNSControlError: If communication with the control socket fails.
        """
        res = self._send_block(cmd="zone-status")
        return list(res.keys())

    def get_records(self, zone: str) -> Dict[str, Any]:
        """Return active Resource Records for a hosted zone.

        Args:
            zone: The zone apex domain name.

        Returns:
            Dictionary mapping owner names to record types and TTL/data.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If the zone name is invalid.
        """
        res = self._send_block(cmd="zone-read", zone=zone)
        if zone in res:
            return res[zone]
        return res

    def get_flat_records(self, zone: str) -> List[Dict[str, Any]]:
        """Return a flattened list of active Resource Records for a hosted zone.

        Args:
            zone: The zone apex domain name.

        Returns:
            List of record dicts with 'owner', 'ttl', 'type', and 'data'.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If the zone name is invalid.
        """
        records_map = self.get_records(zone)
        records: List[Dict[str, Any]] = []

        for owner, types in records_map.items():
            if not isinstance(types, dict):
                continue
            for rtype, rinfo in types.items():
                if not isinstance(rinfo, dict):
                    continue
                try:
                    ttl = int(rinfo.get("ttl", 300))
                except (ValueError, TypeError):
                    ttl = 300
                data_items = rinfo.get("data", [])
                if isinstance(data_items, (list, tuple)):
                    for data in data_items:
                        records.append({
                            "owner": owner,
                            "ttl": ttl,
                            "type": rtype,
                            "data": data,
                        })
                elif isinstance(data_items, str):
                    records.append({
                        "owner": owner,
                        "ttl": ttl,
                        "type": rtype,
                        "data": data_items,
                    })

        return records

    def get_zone_status(self, zone: str) -> Dict[str, Any]:
        """Return status information for a hosted zone.

        Args:
            zone: The zone apex domain name.

        Returns:
            Dictionary containing role, serial, and operational status.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If the zone name is invalid.
        """
        res = self._send_block(cmd="zone-status", zone=zone)
        if zone in res:
            return res[zone]
        return res

    def _get_zone_query_type_stats(
        self,
        ctl: Any,
        zones: List[str],
    ) -> Dict[str, int]:
        """Read and aggregate mod-stats query-type counters for Zones.

        Knot exposes query-type counters through ``zone-stats`` rather than
        the global ``stats`` response. Keeping the aggregation here lets the
        Web API expose one stable per-Node stats payload.

        Args:
            ctl: Connected libknot control client.
            zones: Hosted zone apex names.

        Returns:
            Query-type counters aggregated across the supplied Zones.
        """
        query_types: Dict[str, int] = {}
        for zone in zones:
            ctl.send_block(
                cmd="zone-stats",
                zone=zone,
                section="mod-stats",
                item="query-type",
            )
            raw = ctl.receive_stats()
            zone_stats = raw.get("zone", {}).get(zone, {})
            if not zone_stats:
                zone_values = raw.get("zone", {}).values()
                zone_stats = next(iter(zone_values), {})
            counters = zone_stats.get("mod-stats", {}).get("query-type", {})
            for query_type, count in counters.items():
                if isinstance(count, (int, float)):
                    query_types[query_type] = query_types.get(query_type, 0) + int(count)
        return query_types

    def get_stats(self, zones: Optional[List[str]] = None) -> Dict[str, Any]:
        """Return live mod-stats query statistics, adjusted for baseline resets.

        Args:
            zones: Hosted zone apex names whose query-type counters should be
                aggregated into the result.

        Returns:
            Dictionary with parsed request-protocol, server-operation, response-code,
            query-type, and traffic byte counters.

        Raises:
            DNSControlError: If communication with the control socket fails.
        """
        ctl = self._ensure_connected()
        try:
            ctl.send_block(cmd="stats", section="mod-stats")
            raw = ctl.receive_stats()
            mod_stats = copy.deepcopy(raw.get("mod-stats", raw))
            if zones:
                mod_stats["query-type"] = self._get_zone_query_type_stats(ctl, zones)
        except Exception as exc:
            self._handle_error(exc)
            return {}

        return self._apply_stats_baseline(mod_stats)

    def _apply_stats_baseline(self, current: Dict[str, Any]) -> Dict[str, Any]:
        """Subtract baseline counters from current raw statistics.

        Args:
            current: Current raw mod-stats dictionary.

        Returns:
            Adjusted statistics dictionary relative to baseline.
        """
        if not self._stats_baseline:
            return copy.deepcopy(current)

        adjusted: Dict[str, Any] = {}
        for section, values in current.items():
            baseline_section = self._stats_baseline.get(section, {})
            if isinstance(values, dict):
                adj_section: Dict[str, int] = {}
                for key, val in values.items():
                    if isinstance(val, (int, float)):
                        base_val = baseline_section.get(key, 0)
                        adj_section[key] = max(0, int(val - base_val))
                    else:
                        adj_section[key] = val
                adjusted[section] = adj_section
            elif isinstance(values, (int, float)):
                base_val = baseline_section if isinstance(baseline_section, (int, float)) else 0
                adjusted[section] = max(0, int(values - base_val))
            else:
                adjusted[section] = values

        return adjusted

    def reset_stats(self, zones: Optional[List[str]] = None) -> None:
        """Reset live statistics baseline for a Node's DNS Service.

        Args:
            zones: Hosted zone apex names whose query-type counters should be
                included in the baseline.

        Raises:
            DNSControlError: If communication with the control socket fails.
        """
        ctl = self._ensure_connected()
        try:
            ctl.send_block(cmd="stats", section="mod-stats")
            raw = ctl.receive_stats()
            baseline = copy.deepcopy(raw.get("mod-stats", raw))
            if zones:
                baseline["query-type"] = self._get_zone_query_type_stats(ctl, zones)
        except Exception as exc:
            self._handle_error(exc)
            return

        self._stats_baseline = baseline
        self._socket_baselines[self.socket_path] = copy.deepcopy(baseline)

    @classmethod
    def clear_baselines(cls) -> None:
        """Clear all cached socket baselines."""
        cls._socket_baselines.clear()

    def refresh_zone(self, zone: Optional[str] = None) -> None:
        """Ask a Secondary Zone's Primary whether its Serial has moved.

        Knot answers the SOA query and transfers only when the Serial has
        advanced, so this is safe to issue repeatedly.

        Args:
            zone: The zone apex domain name, or None for every hosted Zone.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If the zone name is invalid.
        """
        self._send_block(cmd="zone-refresh", zone=zone)

    def retransfer_zone(self, zone: Optional[str] = None) -> None:
        """Take a full copy of a Secondary Zone regardless of its Serial.

        This is the verb that separates a forced full transfer from a refresh:
        Knot pulls an AXFR even when the Primary's Serial has not moved.

        Args:
            zone: The zone apex domain name, or None for every hosted Zone.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If the zone name is invalid.
        """
        self._send_block(cmd="zone-retransfer", zone=zone)

    def begin_zone(self, zone: str) -> None:
        """Begin a zone transaction for atomic updates.

        Args:
            zone: The zone apex domain name.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If the zone name is invalid.
        """
        self._in_transaction = True
        self._send_block(cmd="zone-begin", zone=zone)

    def set_record(
        self,
        zone: str,
        owner: str,
        rtype: str,
        data: str,
        ttl: Optional[Union[int, str]] = None,
    ) -> None:
        """Add or update a record within the active zone transaction.

        Args:
            zone: The zone apex domain name.
            owner: Record owner name.
            rtype: DNS record type string (e.g. 'A', 'AAAA', 'CNAME').
            data: Record rdata payload.
            ttl: Optional TTL in seconds.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If record parameters or syntax are invalid.
        """
        ttl_str = str(ttl) if ttl is not None else None
        self._send_block(
            cmd="zone-set",
            zone=zone,
            owner=owner,
            ttl=ttl_str,
            rtype=rtype,
            data=data,
        )

    def unset_record(
        self,
        zone: str,
        owner: str,
        rtype: Optional[str] = None,
        data: Optional[str] = None,
    ) -> None:
        """Remove record data within the active zone transaction.

        Args:
            zone: The zone apex domain name.
            owner: Record owner name.
            rtype: Optional record type string.
            data: Optional record rdata payload.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If record parameters or syntax are invalid.
        """
        self._send_block(
            cmd="zone-unset",
            zone=zone,
            owner=owner,
            rtype=rtype,
            data=data,
        )

    def diff_zone(self, zone: str) -> Dict[str, Any]:
        """Return pending changes within the active zone transaction.

        Args:
            zone: The zone apex domain name.

        Returns:
            Dictionary detailing differences staged in the transaction.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If the zone is not in a valid transaction state.
        """
        res = self._send_block(cmd="zone-diff", zone=zone)
        if zone in res:
            return res[zone]
        return res

    def commit_zone(self, zone: str) -> None:
        """Commit staged changes in the active zone transaction atomically.

        Args:
            zone: The zone apex domain name.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If semantic checks fail on the staged zone.
        """
        try:
            self._send_block(cmd="zone-commit", zone=zone)
        finally:
            self._in_transaction = False

    def abort_zone(self, zone: str) -> None:
        """Rollback and discard staged changes in the active zone transaction.

        Args:
            zone: The zone apex domain name.

        Raises:
            DNSControlError: If communication with the control socket fails.
        """
        try:
            self._send_block(cmd="zone-abort", zone=zone)
        finally:
            self._in_transaction = False

    def set_ds_record(self, zone: str, ds_record: DSRecord) -> None:
        """Add or update a DSRecord in the active zone transaction.

        Args:
            zone: Zone apex domain name.
            ds_record: DSRecord instance to stage.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If record parameters or syntax are invalid.
        """
        self.set_record(
            zone=zone,
            owner=ds_record.name,
            rtype="DS",
            data=ds_record.rdata,
            ttl=ds_record.ttl,
        )

    def unset_ds_record(
        self,
        zone: str,
        owner: str,
        ds_record: Optional[DSRecord] = None,
    ) -> None:
        """Remove a DSRecord in the active zone transaction.

        Args:
            zone: Zone apex domain name.
            owner: Record owner name (e.g. child zone name).
            ds_record: Optional DSRecord whose rdata should be removed specifically.

        Raises:
            DNSControlError: If communication with the control socket fails.
            DNSValidationError: If record parameters or syntax are invalid.
        """
        data = ds_record.rdata if ds_record is not None else None
        self.unset_record(zone=zone, owner=owner, rtype="DS", data=data)

    def diff_ds_records(self, zone: str) -> List[Dict[str, Any]]:
        """Return staged transaction diff entries filtered to DS records.

        Args:
            zone: Zone apex domain name.

        Returns:
            List of diff entry dicts or raw diff strings for DS records.
        """
        raw_diff = self.diff_zone(zone)
        ds_diffs = []
        for entry in raw_diff:
            if isinstance(entry, dict) and entry.get("type") == "DS":
                ds_diffs.append(entry)
            elif isinstance(entry, str) and " DS " in entry:
                ds_diffs.append({"raw": entry})
        return ds_diffs

    def zone_key_rollover(self, zone: str, key_type: str = "zsk") -> Dict[str, Any]:
        """Trigger immediate key rollover for a zone.

        Args:
            zone: Zone apex domain name.
            key_type: Key type to roll over ('zsk' or 'ksk').

        Returns:
            Structured dictionary response from Knot.

        Raises:
            DNSValidationError: If key_type is not 'zsk' or 'ksk'.
            DNSControlError: If communication with the control socket fails.
        """
        ktype = key_type.lower().strip()
        if ktype not in ("zsk", "ksk"):
            raise DNSValidationError(
                f"Invalid key_type '{key_type}': expected 'zsk' or 'ksk'",
                command="zone-key-rollover",
                zone=zone,
            )
        return self._send_block(cmd="zone-key-rollover", zone=zone, rtype=ktype)

    def zone_ksk_submitted(self, zone: str) -> Dict[str, Any]:
        """Confirm parent DS presence for a zone after KSK submission.

        Args:
            zone: Zone apex domain name.

        Returns:
            Structured dictionary response from Knot.

        Raises:
            DNSControlError: If communication with the control socket fails.
        """
        return self._send_block(cmd="zone-ksk-submitted", zone=zone)


    def is_running(self) -> bool:
        """Report whether the Knot server answers on its control socket.

        Opens its own connection and closes it again, so it can be polled
        while the server is still coming up without leaving a half-open
        control channel behind.

        Returns:
            True if a ``status`` command round-tripped, False otherwise.
        """
        try:
            self.connect()
            self._send_block(cmd="status")
        except DNSControlError:
            return False
        finally:
            self.close()
        return True
