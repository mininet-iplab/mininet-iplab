"""RoutinatorClient: reads a Validator's VRPs, ASPAs and status from Routinator.

Routinator's HTTP endpoint listens inside the Validator Node, so the client does
not dial it. The caller supplies a fetch function that runs one GET inside the
Node and returns the body; the client only parses what comes back. Constructing
a client makes no request.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from mniplab.errors import RoutinatorError
from mniplab.rpki.models import VRP, as_asn

JSON_PATH = "/json"
STATUS_PATH = "/api/v1/status"


class RoutinatorClient:
    """Adapter for one Validator's Routinator HTTP endpoint.

    Attributes:
        fetch: Returns the body of a GET on the given path, as text. An empty
            body means nothing answered.
    """

    def __init__(self, fetch: Callable[[str], str]) -> None:
        """Initialize the client around a fetch function.

        Args:
            fetch: Returns the body of a GET on the given path, as text.
        """
        self.fetch = fetch

    def _get(self, path: str) -> Dict[str, Any]:
        """Fetch one path and parse its JSON object.

        Args:
            path: The path to GET, such as ``/json``.

        Returns:
            The parsed object.

        Raises:
            RoutinatorError: If the answer is empty, not JSON, or not an object,
                which means the Validator is not answering.
        """
        body = self.fetch(path)
        try:
            parsed = json.loads(body)
        except ValueError as exc:
            raise RoutinatorError(
                f"The Validator is not answering: no JSON for {path}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RoutinatorError(
                f"The Validator is not answering: no JSON object for {path}"
            )
        return parsed

    def vrps(self) -> List[VRP]:
        """Return the VRPs the Validator currently serves.

        Returns:
            One VRP per validated ROA, with its origin as an integer.

        Raises:
            RoutinatorError: If the Validator is not answering.
        """
        body = self._get(JSON_PATH)
        return [
            VRP(prefix=roa["prefix"], max_length=int(roa["maxLength"]), origin=as_asn(roa["asn"]))
            for roa in body.get("roas", [])
        ]

    def aspas(self) -> List[Dict[str, Any]]:
        """Return the ASPAs the Validator currently serves.

        Returns:
            One dict per ASPA, with ``customer`` and ``providers`` as integers.

        Raises:
            RoutinatorError: If the Validator is not answering.
        """
        body = self._get(JSON_PATH)
        return [
            {
                "customer": as_asn(aspa["customer"]),
                "providers": [as_asn(provider) for provider in aspa["providers"]],
            }
            for aspa in body.get("aspas", [])
        ]

    def status(self) -> Dict[str, Any]:
        """Return when the Validator last refreshed and which Routers are connected.

        A Router counts as connected while it holds an open RTR session. Routinator
        lists RTR clients only when run with ``--rtr-client-metrics``.

        Returns:
            ``last_refresh``: the ``lastUpdateDone`` text, or None before the
            first run completes; and ``rtr_clients``: the source addresses of the
            connected Routers.

        Raises:
            RoutinatorError: If the Validator is not answering.
        """
        body = self._get(STATUS_PATH)
        clients = (body.get("rtr") or {}).get("clients") or {}
        return {
            "last_refresh": body.get("lastUpdateDone"),
            "rtr_clients": [
                address for address, client in clients.items() if client.get("connections", 0) > 0
            ],
        }
