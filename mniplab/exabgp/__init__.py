"""ExaBGP integration package for mnIPLab.

Provides models, configuration builder, node wrapper, and utilities for
running ExaBGP speakers within Mininet namespaces.
"""

from .models import BGPRoute, BGPNeighbor
from .config import ExaBGPConfigBuilder
from .node import ExaBGPNode
from .utils import make_v4_routes, make_v4_peer_routes, make_v6_routes, make_v6_peer_routes

__all__ = [
    "BGPRoute",
    "BGPNeighbor",
    "ExaBGPConfigBuilder",
    "ExaBGPNode",
    "make_v4_routes",
    "make_v4_peer_routes",
    "make_v6_routes",
    "make_v6_peer_routes",
]
