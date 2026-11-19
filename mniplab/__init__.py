"""Mininet-IPLab network emulation framework.

Extends Mininet with FRR (Free Range Routing) routers, ExaBGP speakers, and an
optional FastAPI/WebSocket web UI for topology visualization and interactive terminals.

See ``docs/`` for full documentation and ``examples/`` for reference topologies.
"""

__version__: str = "0.1.0"

from mniplab.net import mnIPLab
from mniplab.router import FRRouter
from mniplab.host import mnIPLabHost
from mniplab.switch import ManagedSwitch
from mniplab.container import ContainerHost, ContainerIntf, ContainerLink
from mniplab.link import IPv6Intf
from mniplab.nodelib import MasqueradeNAT, TransitNAT
from mniplab.routernat import PortForward, StaticNAT
from mniplab.service import Service, ServiceMixin
from mniplab.dhcp import Pool, DHCPService, Relay
from mniplab.dns import (
    Zone,
    SecondaryZone,
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
    DNSService,
    DNSDiagnostics,
    KnotControlClient,
    DNSControlError,
    DNSValidationError,
)
from mniplab.cli import mnIPLabCLI
from mniplab.runner import parse_lab_args, run_lab
# Optional web server components
try:
    from mniplab.webserver import start_server, stop_server, registry
except ImportError:
    pass

from mniplab.cleanup import (
    full_cleanup,
    kill_frr_processes,
    cleanup_frr_configs,
    cleanup_python_cache,
    cleanup_mniplab_files,
    cleanup_mininet_leftovers,
    emergency_cleanup
)
# Export constants for users who need them
from mniplab.constants import (
    PROTO_MAP,
    FRR_INIT_SCRIPT,
    get_valid_protocols,
    get_daemon_for_protocol,
)
# Export error classes for exception handling
from mniplab.errors import (
    mnIPLabError,
    ConfigurationError,
    RouterError,
    FRRError,
    NetworkError,
    HostError,
    LinkError,
    CLIError,
    AuthenticationError,
    WebServerError,
    ValidationError,
)
# Export logging functions for advanced usage
from mniplab.logging import (
    setup_logging,
    get_logger,
    set_log_level,
    disable_mininet_logging,
)

__all__ = [
    'mnIPLab',
    'FRRouter',
    'mnIPLabHost',
    'ManagedSwitch',
    'ContainerHost',
    'ContainerIntf',
    'ContainerLink',
    'IPv6Intf',
    'MasqueradeNAT',
    'TransitNAT',
    'PortForward',
    'StaticNAT',
    'Service',
    'ServiceMixin',
    'Pool',
    'DHCPService',
    'Relay',
    'Zone',
    'SecondaryZone',
    'Record',
    'ARecord',
    'AAAARecord',
    'CNAMERecord',
    'PTRRecord',
    'TXTRecord',
    'MXRecord',
    'NSRecord',
    'SOARecord',
    'DNSService',
    'DNSDiagnostics',
    'KnotControlClient',
    'DNSControlError',
    'DNSValidationError',
    'PROTO_MAP',
    'FRR_INIT_SCRIPT',
    'get_valid_protocols',
    'get_daemon_for_protocol',
    'mnIPLabCLI',
    'parse_lab_args',
    'run_lab',
    'start_server',
    'stop_server',
    'registry',
    'full_cleanup',
    'kill_frr_processes',
    'cleanup_frr_configs',
    'cleanup_python_cache',
    'cleanup_mniplab_files',
    'cleanup_mininet_leftovers',
    'emergency_cleanup',
    'mnIPLabError',
    'ConfigurationError',
    'RouterError',
    'FRRError',
    'NetworkError',
    'HostError',
    'LinkError',
    'CLIError',
    'AuthenticationError',
    'WebServerError',
    'ValidationError',
    'setup_logging',
    'get_logger',
    'set_log_level',
    'disable_mininet_logging',
    '__version__'
]
