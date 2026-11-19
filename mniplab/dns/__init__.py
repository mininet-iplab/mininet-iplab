"""Authoritative DNS Service support: Zone and Record models, DNSService, and lifecycle.

Knot DNS is the authoritative server rather than BIND9: its KASP engine does
the DNSSEC signing, and its control socket is addressable directly.
"""

from mniplab.dns.models import (
    Record,
    ARecord,
    AAAARecord,
    CNAMERecord,
    PTRRecord,
    TXTRecord,
    MXRecord,
    NSRecord,
    SOARecord,
    DSRecord,
    RECORD_TYPES,
    SecondaryZone,
    Zone,
)
from mniplab.dns.service import DNSService
from mniplab.dns.provisioner import DNSProvisioner
from mniplab.dns.diagnostics import DNSDiagnostics
from mniplab.dns.control_client import KnotControlClient
from mniplab.dns.query import DNSQueryService
from mniplab.errors import DNSControlError, DNSValidationError

__all__ = [
    "Record",
    "ARecord",
    "AAAARecord",
    "CNAMERecord",
    "PTRRecord",
    "TXTRecord",
    "MXRecord",
    "NSRecord",
    "SOARecord",
    "DSRecord",
    "RECORD_TYPES",
    "SecondaryZone",
    "Zone",
    "DNSService",
    "DNSProvisioner",
    "DNSDiagnostics",
    "KnotControlClient",
    "DNSQueryService",
    "DNSControlError",
    "DNSValidationError",
]
