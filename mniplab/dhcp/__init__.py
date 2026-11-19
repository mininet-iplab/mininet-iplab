"""DHCP Service support: Pool declaration, Kea configuration, and lifecycle.

Kea is the server rather than dnsmasq because the lessons here need a real
DHCPv6 server with its own lease database, and nothing acquires a Lease on a
Learner's behalf: declaring ``dhcp4=True`` marks an Interface, it does not
address it. :class:`mniplab.provisioners.DHCPProvisioner` owns the lifecycle
behaviour built on top of the types here.

``mniplab.dhcp.leases.LeaseQueryService`` is deliberately not re-exported
here: it depends on ``mniplab.provisioners.DHCPProvisioner``, which itself
imports from this package, so importing it eagerly at package init time
would be circular. Import it directly (``from mniplab.dhcp.leases import
LeaseQueryService``) instead.
"""

from mniplab.dhcp.config import KeaConfigBuilder
from mniplab.dhcp.control_client import KeaControlClient
from mniplab.dhcp.pool import Pool, Reservation, validate_pool_range
from mniplab.dhcp.router_advertisement import (
    InterfaceRouterAdvertisement,
    RouterAdvertisementAdapter,
    RouterAdvertisementDiagnostics,
)
from mniplab.dhcp.relay import Relay, DHCPRelayDiagnostics
from mniplab.dhcp.service import DHCPService

__all__ = [
    'Pool', 'Reservation', 'validate_pool_range', 'DHCPService', 'Relay',
    'DHCPRelayDiagnostics',
    'KeaConfigBuilder', 'KeaControlClient',
    'InterfaceRouterAdvertisement', 'RouterAdvertisementAdapter',
    'RouterAdvertisementDiagnostics',
]

