"""Path constants, protocol mappings, and default values for mnIPLab.

Supports environment variable overrides: FRR_INIT_SCRIPT, MNIPLAB_TMP_DIR, MNIPLAB_ENV_FILE, MNIPLAB_LAB_KEY.
"""

import os
from typing import List

# FRR-related path constants
# FRR_INIT_SCRIPT can be overridden via environment variable for custom installations
FRR_INIT_SCRIPT = os.getenv('FRR_INIT_SCRIPT', '/usr/lib/frr/frrinit.sh')
FRR_VAR_LOG_DIR = '/var/log'
FRR_VAR_RUN_DIR = '/var/run'
FRR_ETC_DIR = '/etc/frr'

# mnIPLab's runtime root: the one host-side tree the package writes under,
# and the one path an environment variable moves. Every Lab's ephemeral
# files live in its own Lab directory under LABS_DIR (see mniplab.lab_path);
# host-wide state such as IP_FORWARD_LEASE_DIR sits beside labs/, never
# inside a Lab directory. Cleanup removes the whole tree.
MNIPLAB_TMP_DIR = os.getenv('MNIPLAB_TMP_DIR', '/tmp/mniplab')

# The web server's log file, ephemeral to one run.
MNIPLAB_WEB_LOG_FILE = os.getenv('MNIPLAB_WEB_LOG', '/tmp/mniplab-web.log')

# The env file a lab run and the web server read their settings from.
ENV_FILE_VAR = 'MNIPLAB_ENV_FILE'
DEFAULT_ENV_FILE = '.env'


def env_file_path() -> str:
    """The env file this process reads its settings from.

    ``MNIPLAB_ENV_FILE`` names it; unset, it is ``.env`` in the working
    directory. One knob for both readers, so an operator can point a web
    server at another file and the test suite can point everything at
    ``os.devnull``. Resolved on each call rather than at import: the lab
    entry points' ``load_lab_environment`` reads it when a lab starts, the
    web server's ``Settings`` once, when its class is defined.

    Returns:
        The path, relative to the working directory unless made absolute.
    """
    return os.environ.get(ENV_FILE_VAR, DEFAULT_ENV_FILE)

# Every object a Lab creates in the host's one namespace carries the Lab
# key in its name so two Labs never meet there: a Docker container is
# ``mn.<key>.<node>``, its namespace link under NETNS_DIR has the same
# name, an Open vSwitch bridge is ``<key>-<switch>`` and a root-namespace
# veth is ``<key>-<node>-ethN``. The kernel caps an interface name at
# IFNAMSIZ - 1 characters, so a root-namespace Node name is refused at
# declaration when its keyed veth could not fit with a one-digit port; a
# Link whose port number then overflows is refused when it is added.
CONTAINER_NAME_PREFIX = 'mn.'
NETNS_DIR = '/var/run/netns'
MAX_INTF_NAME_LEN = 15
ROOT_INTF_PORT_DIGITS = 1

# Root ``net.ipv4.ip_forward`` is one host-wide flag every NAT Gateway on
# the host needs on. Each holder leaves a marker file here while it runs,
# beside the value the first holder found; the last holder to leave
# restores it. One directory for every process on the host, because each
# Lab may be its own process; its lock file stays once created.
IP_FORWARD_LEASE_DIR = MNIPLAB_TMP_DIR + '/ip-forward'

# Every alive Lab has one directory here, named by its key; the Lab's
# handle socket sits at its top.
LABS_DIR = MNIPLAB_TMP_DIR + '/labs'
LAB_SOCKET_TEMPLATE = LABS_DIR + '/%(key)s/lab.sock'

# The environment variable through which a manager hands a Lab process its
# key; mnIPLab reads it when no explicit lab_key is passed.
LAB_KEY_ENV = 'MNIPLAB_LAB_KEY'

# Mapping of protocol names to FRR daemon names
# Used for convenient protocol specification like proto='OSPF,BGP'
PROTO_MAP = {
    'OSPF': 'ospfd',
    'OSPF6': 'ospf6d',
    'OSPF6D': 'ospf6d',
    'ISIS': 'isisd',
    'IS-IS': 'isisd',
    'BGP': 'bgpd',
    'PIM': 'pimd',
    'PIMD': 'pimd',
    'PIM6': 'pim6d',
    'PIM6D': 'pim6d',
    'RIP': 'ripd',
    'RIPNG': 'ripngd',
    'EIGRP': 'eigrpd',
    'BABEL': 'babeld',
    'LDP': 'ldpd',
    'NHRP': 'nhrpd',
    'SHARP': 'sharpd',
    'PBR': 'pbrd',
    'BFD': 'bfdd',
    'FABRIC': 'fabricd',
    'VRRP': 'vrrpd',
    'PATH': 'pathd',
}

# Alternative protocol names that map to the same daemon
# For backwards compatibility and convenience
PROTO_ALIASES = {
    'OSPFV3': 'ospf6d',
    'RIPv2': 'ripd',
    'RIPv6': 'ripngd',
}

def get_valid_protocols() -> List[str]:
    """Return a sorted list of all valid protocol names.
    
    Returns:
        List of protocol names that can be used in proto parameter
        
    Example:
        >>> from mniplab.constants import get_valid_protocols
        >>> protocols = get_valid_protocols()
        >>> 'OSPF' in protocols
        True
    """
    return sorted(list(PROTO_MAP.keys()) + list(PROTO_ALIASES.keys()))


def get_daemon_for_protocol(protocol: str) -> str:
    """Get the FRR daemon name for a protocol.
    
    Args:
        protocol: Protocol name (e.g., 'OSPF', 'BGP')
        
    Returns:
        FRR daemon name (e.g., 'ospfd', 'bgpd')
        
    Raises:
        KeyError: If protocol is not recognized
        
    Example:
        >>> from mniplab.constants import get_daemon_for_protocol
        >>> get_daemon_for_protocol('OSPF')
        'ospfd'
    """
    protocol_upper = protocol.upper()
    if protocol_upper in PROTO_MAP:
        return PROTO_MAP[protocol_upper]
    elif protocol_upper in PROTO_ALIASES:
        return PROTO_ALIASES[protocol_upper]
    else:
        raise KeyError(f"Unknown protocol: {protocol}")


# How long a Service Provisioner waits, after launching a server, for its
# control socket to answer a command before failing the Lab start. One
# budget for Kea, Knot and Unbound alike; ``start_node_services`` returns
# only once the server is ready to be queried.
SERVICE_READY_TIMEOUT = 10.0

# 802.1Q VLAN constants
VLAN_MIN = 1
VLAN_MAX = 4094
