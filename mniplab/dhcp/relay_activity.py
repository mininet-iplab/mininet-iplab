"""RelayActivityQueryService: reading what a DHCP Relay forwarded.

The relay's dnsmasq log is the authoritative runtime record for this view.
Keeping its file I/O and parsing here gives the CLI, REST API, and later Web UI
one stable query seam, just as :class:`mniplab.dhcp.leases.LeaseQueryService`
gives them one seam for Kea and
:class:`mniplab.dhcp.router_advertisement.RouterAdvertisementAdapter` for
Router Advertisements.

This adapter owns reading and parsing the relay's log and nothing else: it
never starts, stops, or diagnoses the relay. Start-time failure detection
belongs to the lifecycle owner,
:class:`mniplab.provisioners.DHCPRelayProvisioner`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from mniplab.dhcp.relay import Relay


class RelayActivityQueryService:
    """Reads forwarded DHCP requests from one Node's Relay log."""

    _FORWARDED_LINE = re.compile(
        r"^dnsmasq-dhcp: DHCP relay at (?P<source>\S+) -> (?P<server>\S+)$"
    )

    @staticmethod
    def read_log(log_path: str) -> str:
        """Read one Relay log from the host-side path."""
        with open(log_path, "r", encoding="utf-8") as log_file:
            return log_file.read()

    @classmethod
    def parse_log(cls, log_text: str) -> List[Dict[str, str]]:
        """Parse forwarded-request lines from dnsmasq log text.

        Args:
            log_text: Raw text from a Relay's dnsmasq log.

        Returns:
            One source/server mapping per recognised forwarded request.
            Startup, error, and otherwise unrecognised lines are ignored.
        """
        activity: List[Dict[str, str]] = []
        for line in log_text.splitlines():
            match = cls._FORWARDED_LINE.match(line)
            if match is not None:
                activity.append(
                    {
                        "source": match.group("source"),
                        "server": match.group("server"),
                    }
                )
        return activity

    @staticmethod
    def get_activity(net: Any, node_name: str) -> Dict[str, Any]:
        """Return the forwarded requests recorded by a Node's Relay.

        Args:
            net: The running Lab.
            node_name: The relaying Node's name.

        Returns:
            ``{"success": True, "activity": [...]}``, with one entry for
            each recognised forwarded request. An empty log is successful and
            returns an empty activity list.

        Raises:
            ValueError: The Node does not exist, has no Relay, or its log
                cannot be read because the Relay is not running.
        """
        node = net.get(node_name)
        if not node:
            raise ValueError(f"Node not found: {node_name}")

        Relay.find_on(node)

        # Imported locally: mniplab.provisioners imports this module, so a
        # top-level import here would be circular, the same way
        # mniplab.dhcp.leases keeps its DHCPProvisioner import local.
        from mniplab.provisioners import DHCPRelayProvisioner

        log_path = DHCPRelayProvisioner.log_path(node_name, net=net)
        try:
            log_text = RelayActivityQueryService.read_log(log_path)
        except OSError as exc:
            raise ValueError(
                f"Could not read relay log at {log_path}. Is the DHCP Relay running?"
            ) from exc

        return {
            "success": True,
            "activity": RelayActivityQueryService.parse_log(log_text),
        }
