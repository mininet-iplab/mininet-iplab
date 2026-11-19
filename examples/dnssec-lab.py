#!/usr/bin/env python3
"""DNSSEC Flagship Lab: Authoritative Signing and Recursive Validation.

Demonstrates end-to-end DNSSEC cryptographic chain of trust in Mininet-IPLab:
- `root1` hosts the signed root zone (`.`) using Knot KASP (ECDSA P-256).
- `tld1` hosts the signed `com.` zone, delegated from root with a DS record.
- `auth1` hosts the signed `example.com.` primary zone, delegated from `com.` with a DS record.
- `auth2` hosts the secondary `example.com.` zone, replicating pre-signed records over AXFR.
- `res1` runs Unbound with cryptographic validation enabled (`validator iterator`)
  and anchored at `root1`'s KSK trust anchor.
- `client1` queries `res1` and directly observes the `ad` (Authenticated Data) flag,
  traces the validated trust chain with `delv`, and tests secondary origin authentication.
- Router `r1` routes traffic across all separate subnets, IPv4 and IPv6, with no
  routing protocol: every route is connected.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab
from mniplab.dns import ARecord, DNSService, NSRecord, SecondaryZone, TXTRecord, Zone
from mniplab.resolver import ResolverService

MAX_TTL = 120
MAX_NEGATIVE_TTL = 30


def build_network() -> mnIPLab:
    """Build the DNSSEC Flagship Lab topology.

    Returns:
        The Lab network ready to start.
    """
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add Router\n')
    r1 = net.addRouter('r1')

    info('*** Add Authoritative Nameservers\n')
    root1 = net.addHost(
        'root1', defaultRoute='via 10.0.1.1', defaultRoute6='via 2001:db8:1::1',
    )
    tld1 = net.addHost(
        'tld1', defaultRoute='via 10.0.2.1', defaultRoute6='via 2001:db8:2::1',
    )
    auth1 = net.addHost(
        'auth1', defaultRoute='via 10.0.3.1', defaultRoute6='via 2001:db8:3::1',
    )
    auth2 = net.addHost(
        'auth2', defaultRoute='via 10.0.4.1', defaultRoute6='via 2001:db8:4::1',
    )

    info('*** Add Validating Resolver Node\n')
    res1 = net.addHost(
        'res1', defaultRoute='via 10.0.5.1', defaultRoute6='via 2001:db8:5::1',
    )

    info('*** Add Client Host\n')
    client1 = net.addHost(
        'client1',
        defaultRoute='via 10.0.6.1',
        defaultRoute6='via 2001:db8:6::1',
        nameservers=['10.0.5.2'],
    )

    info('*** Add Links\n')
    nodes = (root1, tld1, auth1, auth2, res1, client1)
    for index, node in enumerate(nodes, start=1):
        net.addLink(
            node,
            r1,
            params1={'ip': f'10.0.{index}.2/24', 'ip6': f'2001:db8:{index}::2/64'},
            params2={'ip': f'10.0.{index}.1/24', 'ip6': f'2001:db8:{index}::1/64'},
        )

    info('*** Configure Signed Authoritative Zones\n')
    # Primary zone on auth1
    example_zone = Zone(
        'example.com.',
        [
            NSRecord('@', 'ns1.example.com.'),
            NSRecord('@', 'ns2.example.com.'),
            ARecord('ns1', '10.0.3.2'),
            ARecord('ns2', '10.0.4.2'),
            ARecord('web', '10.0.6.100'),
            ARecord('mail', '10.0.3.2'),
            TXTRecord('info', 'DNSSEC Validated Lab'),
        ],
        host_node='auth1',
        dnssec=True,
    )
    auth1.addService(DNSService(example_zone))

    # Secondary zone on auth2 replicating from auth1
    auth2.addService(DNSService(
        SecondaryZone('example.com.', primary=auth1, host_node='auth2')
    ))

    # TLD zone on tld1 delegating to example.com with DS record
    com_zone = Zone(
        'com.',
        [
            NSRecord('@', 'ns.com.'),
            ARecord('ns', '10.0.2.2'),
            NSRecord('example', 'ns1.example.com.'),
            ARecord('ns1.example.com.', '10.0.3.2'),
            NSRecord('example', 'ns2.example.com.'),
            ARecord('ns2.example.com.', '10.0.4.2'),
            example_zone.ds_record(),
        ],
        host_node='tld1',
        dnssec=True,
    )
    tld1.addService(DNSService(com_zone))

    # Root zone on root1 delegating to com with DS record
    root_zone = Zone(
        '.',
        [
            NSRecord('@', 'root1.'),
            ARecord('root1', '10.0.1.2'),
            NSRecord('com', 'ns.com.'),
            ARecord('ns.com.', '10.0.2.2'),
            com_zone.ds_record(),
        ],
        host_node='root1',
        dnssec=True,
    )
    root1.addService(DNSService(root_zone))

    info('*** Configure Validating Recursive Resolver\n')
    res1.addService(ResolverService(
        root_hints=[root1],
        trust_anchors=[root1],
        max_ttl=MAX_TTL,
        max_negative_ttl=MAX_NEGATIVE_TTL,
    ))

    return net


if __name__ == '__main__':
    args = parse_lab_args('DNSSEC Flagship Lab')
    run_lab(
        build_network(),
        enable_web=args.enable_web,
        web_host=args.web_host,
        web_port=args.web_port,
        log_level=args.log_level,
    )
