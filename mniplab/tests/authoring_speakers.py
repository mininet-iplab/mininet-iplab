"""Record Speaker declarations at a Lab script's public network boundary."""

from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch


def announcements(source: str, path: Path) -> dict:
    """Execute a Lab script against the network boundary and keep its announcements.

    Args:
        source: The shipped or generated Python script.
        path: Its path, used to resolve Seed paths without starting namespaces.

    Returns:
        Every Neighbor's ordered BGPRoute values, keyed by Speaker and peer.
    """
    network = MagicMock()
    namespace: dict[str, Any] = {"__name__": "speaker_fidelity", "__file__": str(path)}
    with patch("mniplab.mnIPLab", return_value=network):
        exec(compile(source, str(path), "exec"), namespace)
        namespace["build_network"]()
    result = {}
    for call in network.addExaBGP.call_args_list:
        for neighbor in call.kwargs["neighbors"]:
            rows = neighbor.routes or call.kwargs.get("routes") or []
            result[f"{call.args[0]}->{neighbor.peer}"] = [
                asdict(route) for route in rows
            ]
    return result
