"""ResolverQueryService: read and invalidate a running Resolver's cache.

Shared by the CLI (``show_cache``, ``flush_cache``, ``show_resolver``) and the
Web API, mirroring :class:`mniplab.dns.query.DNSQueryService`, which does the
same job for the Authoritative DNS Service.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from mniplab.errors import ResolverControlError
from mniplab.resolver.cache_dump import parse_cache_dump
from mniplab.resolver.control_client import UnboundControlClient
from mniplab.resolver.models import CacheEntry, ResolverMetrics
from mniplab.resolver.provisioner import ResolverProvisioner
from mniplab.resolver.service import ResolverService

ROOT_ZONE = "."

FLUSH_SCOPE_NAME = "name"
FLUSH_SCOPE_ZONE = "zone"
FLUSH_SCOPES = (FLUSH_SCOPE_NAME, FLUSH_SCOPE_ZONE)

STAT_QUERIES = "total.num.queries"
STAT_HITS = "total.num.cachehits"
STAT_MISSES = "total.num.cachemiss"
STAT_RECURSION_TIME = "total.recursion.time.avg"

SECONDS_TO_MS = 1000.0


class ResolverQueryService:
    """Query service for inspecting Recursive Caching Resolvers on Nodes."""

    @staticmethod
    def find_service(net: Any, node_name: str) -> Tuple[Any, ResolverService]:
        """Find a Node and the Resolver Service attached to it.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.

        Returns:
            Tuple of (node, ResolverService).

        Raises:
            ValueError: If the Node is absent or offers no Resolver Service.
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

        if node is None:
            raise ValueError(f"Node not found: {node_name}")

        return node, ResolverService.find_on(node)

    @classmethod
    def get_client(
        cls, net: Any, node_name: str
    ) -> Tuple[Any, ResolverService, UnboundControlClient]:
        """Resolve a Node, its Service, and a client on its control socket.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.

        Returns:
            Tuple of (node, service, client).

        Raises:
            ValueError: If the Node or Service is absent, or the control
                socket does not exist — which means Unbound is not running.
        """
        node, service = cls.find_service(net, node_name)
        socket_path = ResolverProvisioner.control_socket_host_path(node_name, net=net)
        if not os.path.exists(socket_path):
            raise ValueError(
                f"Unbound control socket not found at {socket_path}. "
                f"Is the Resolver running on {node_name}?"
            )
        return node, service, UnboundControlClient(socket_path)

    @classmethod
    def get_cache(cls, net: Any, node_name: str) -> List[CacheEntry]:
        """Return everything a Resolver is currently holding in cache.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.

        Returns:
            Positive RRsets and cached negative answers, in stable order,
            each with the TTL it has left.

        Raises:
            ValueError: If the Node, Service, or control socket is absent, or
                the control exchange fails.
        """
        _node, _service, client = cls.get_client(net, node_name)
        try:
            return parse_cache_dump(client.dump_cache())
        except ResolverControlError as exc:
            raise ValueError(f"Failed to read the cache on {node_name}: {exc}") from exc

    @classmethod
    def get_metrics(cls, net: Any, node_name: str) -> ResolverMetrics:
        """Return a Resolver's live query counters without resetting them.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.

        Returns:
            The counters behind the cold-versus-warm contrast: queries, hits,
            misses, hit ratio, and mean recursion time.

        Raises:
            ValueError: If the Node, Service, or control socket is absent, or
                the control exchange fails.
        """
        _node, _service, client = cls.get_client(net, node_name)
        try:
            stats = client.stats_noreset()
        except ResolverControlError as exc:
            raise ValueError(f"Failed to read stats on {node_name}: {exc}") from exc

        sec_queries = int(
            stats.get("num.query.dnssec.secure")
            or stats.get("total.num.query.dnssec.secure")
            or stats.get("num.answer.secure")
            or stats.get("total.num.answer.secure")
            or 0
        )
        bogus_queries = int(
            stats.get("num.query.dnssec.bogus")
            or stats.get("total.num.query.dnssec.bogus")
            or stats.get("num.answer.bogus")
            or stats.get("total.num.answer.bogus")
            or 0
        )
        insec_queries = int(
            stats.get("num.query.dnssec.insecure")
            or stats.get("total.num.query.dnssec.insecure")
            or 0
        )

        return ResolverMetrics(
            queries=int(stats.get(STAT_QUERIES, 0)),
            hits=int(stats.get(STAT_HITS, 0)),
            misses=int(stats.get(STAT_MISSES, 0)),
            # Unbound reports recursion time in seconds; a sub-millisecond
            # cache hit is the whole point, so milliseconds is the unit that
            # keeps the contrast readable.
            recursion_time_ms=float(stats.get(STAT_RECURSION_TIME, 0.0)) * SECONDS_TO_MS,
            dnssec_secure=sec_queries,
            dnssec_bogus=bogus_queries,
            dnssec_insecure=insec_queries,
        )

    @classmethod
    def flush_cache(
        cls,
        net: Any,
        node_name: str,
        target: Optional[str] = None,
        scope: str = FLUSH_SCOPE_NAME,
    ) -> Dict[str, Any]:
        """Evict cached entries so the next query walks the hierarchy again.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.
            target: The name or zone to evict; omit to clear everything.
            scope: ``'name'`` to evict exactly one name, ``'zone'`` to evict
                a name and everything beneath it.

        Returns:
            Dict describing what was flushed (node, scope, target) and the
            confirmation line Unbound answered with.

        Raises:
            ValueError: If the scope is unknown, the Node, Service, or
                control socket is absent, or the control exchange fails.
        """
        if scope not in FLUSH_SCOPES:
            raise ValueError(
                f"Invalid flush scope '{scope}': expected one of "
                f"{', '.join(FLUSH_SCOPES)}."
            )

        _node, _service, client = cls.get_client(net, node_name)
        name = (target or "").strip()

        try:
            if not name:
                # Unbound has no "flush everything" command; the root zone is
                # an ancestor of every cached name, so flushing it is one.
                response = client.flush_zone(ROOT_ZONE)
                flushed_scope, flushed_target = FLUSH_SCOPE_ZONE, ROOT_ZONE
            elif scope == FLUSH_SCOPE_ZONE:
                response = client.flush_zone(name)
                flushed_scope, flushed_target = FLUSH_SCOPE_ZONE, name
            else:
                response = client.flush_name(name)
                flushed_scope, flushed_target = FLUSH_SCOPE_NAME, name
        except ResolverControlError as exc:
            raise ValueError(f"Failed to flush the cache on {node_name}: {exc}") from exc

        return {
            "node": node_name,
            "scope": flushed_scope,
            "target": flushed_target,
            "response": response.strip(),
        }

    @classmethod
    def get_resolver_info(cls, net: Any, node_name: str) -> Dict[str, Any]:
        """Return a Resolver's operating mode, hierarchy, and active policy.

        Args:
            net: Network orchestrator instance.
            node_name: The resolving Node's name.

        Returns:
            Dict with the Node's name, running state, control socket path and
            state, mode, TTL caps, Root Hints, forwarders, and Access Control
            rules.

        Raises:
            ValueError: If the Node is absent or offers no Resolver Service.
        """
        _node, service = cls.find_service(net, node_name)
        socket_path = ResolverProvisioner.control_socket_host_path(node_name, net=net)

        running = False
        socket_state = "inactive"
        if os.path.exists(socket_path):
            running = UnboundControlClient(socket_path).is_running()
            socket_state = "active" if running else "stale"

        return {
            "node": node_name,
            "running": running,
            "socket_path": socket_path,
            "socket_state": socket_state,
            "mode": service.mode,
            "listeners": ["0.0.0.0@53", "::@53"],
            "max_ttl": service.max_ttl,
            "max_negative_ttl": service.max_negative_ttl,
            "root_hints": [hint.to_dict() for hint in service.root_hints],
            "forwarders": list(service.forwarders),
            "access_control": [rule.to_dict() for rule in service.access_control],
        }
