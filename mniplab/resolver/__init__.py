"""Recursive Caching Resolver Service: Unbound running inside a Node's namespaces.

The Resolver answers client queries by walking the in-lab hierarchy from Root
Hints (an Iterative Walk) or by forwarding to declared upstream addresses, and
caches what it learns. Unbound is the daemon: one binary, a direct control
socket, and a validator module for the DNSSEC lessons.
"""

from mniplab.errors import ResolverControlError
from mniplab.resolver.access import ResolverAccessService
from mniplab.resolver.cache_dump import parse_cache_dump
from mniplab.resolver.control_client import UnboundControlClient
from mniplab.resolver.diagnostics import ResolverDiagnostics
from mniplab.resolver.models import AccessRule, CacheEntry, ResolverMetrics
from mniplab.resolver.provisioner import ResolverProvisioner
from mniplab.resolver.query import ResolverQueryService
from mniplab.resolver.service import ResolverService

__all__ = [
    "AccessRule",
    "CacheEntry",
    "ResolverAccessService",
    "ResolverControlError",
    "ResolverDiagnostics",
    "ResolverMetrics",
    "ResolverProvisioner",
    "ResolverQueryService",
    "ResolverService",
    "UnboundControlClient",
    "parse_cache_dump",
]
