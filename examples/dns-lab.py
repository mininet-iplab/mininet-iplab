#!/usr/bin/env python3
"""DNS Lab: the whole resolution chain on one dual-stack Router.

Every server and the client sit on a Subnet of their own off `r1`, so
every hop of a lookup is a routed one and every route is connected: there
is no routing protocol, and a link a Learner drops and restores recovers
by itself.

- `root1` serves `.` and delegates `com.` and the reverse Zone.
- `tld1` serves `com.` and delegates `example.com.` to both nameservers.
- `auth1` is the Primary for `example.com.` and the reverse Zone
  `10.in-addr.arpa.`.
- `auth2` is the Secondary for both and acquires them by Zone Transfer.
- `res1` is a Recursive Caching Resolver that walks the hierarchy from
  `root1`; its TTL caps are short enough to watch a cache expire in class.
- `client1` asks `res1` and nothing else.

The Guide cuts `auth1` off to show the Secondary's SOA expire timer run
out, then reconnects it. The classroom SOA values are the defaults.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab
from mniplab.dns import (
    AAAARecord,
    ARecord,
    CNAMERecord,
    DNSService,
    NSRecord,
    PTRRecord,
    SecondaryZone,
    TXTRecord,
    Zone,
)
from mniplab.resolver import ResolverService

# Short enough that a class watches a cached answer expire rather than
# waiting out a production TTL.
MAX_TTL = 120
MAX_NEGATIVE_TTL = 30


def build_network() -> mnIPLab:
    """Build the DNS Lab topology.

    Returns:
        The Lab, ready to start: a Router, a Root, a TLD, a Primary and a
        Secondary, a Resolver and a client, each on its own Subnet.
    """
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add Router\n')
    r1 = net.addRouter('r1')

    info('*** Add Nameserver Nodes\n')
    names = ('root1', 'tld1', 'auth1', 'auth2', 'res1')
    nodes = [
        net.addHost(
            name,
            defaultRoute=f'via 10.0.{index}.1',
            defaultRoute6=f'via 2001:db8:{index}::1',
        )
        for index, name in enumerate(names, start=1)
    ]
    root1, tld1, auth1, auth2, res1 = nodes

    info('*** Add Client Host\n')
    client1 = net.addHost(
        'client1',
        defaultRoute='via 10.0.6.1',
        defaultRoute6='via 2001:db8:6::1',
        nameservers=['10.0.5.2'],
    )

    info('*** Add Links\n')
    for index, node in enumerate(nodes + [client1], start=1):
        net.addLink(
            node,
            r1,
            params1={'ip': f'10.0.{index}.2/24', 'ip6': f'2001:db8:{index}::2/64'},
            params2={'ip': f'10.0.{index}.1/24', 'ip6': f'2001:db8:{index}::1/64'},
        )

    info('*** Add Root Zone\n')
    # The root delegates com. and the reverse Zone, each with the glue a
    # Resolver needs to follow the referral.
    root1.addService(DNSService(Zone(
        '.',
        [
            NSRecord('@', 'root1.'),
            ARecord('root1', '10.0.1.2'),
            AAAARecord('root1', '2001:db8:1::2'),
            NSRecord('com', 'ns.com.'),
            ARecord('ns.com.', '10.0.2.2'),
            AAAARecord('ns.com.', '2001:db8:2::2'),
            NSRecord('10.in-addr.arpa.', 'ns1.example.com.'),
            NSRecord('10.in-addr.arpa.', 'ns2.example.com.'),
            ARecord('ns1.example.com.', '10.0.3.2'),
            ARecord('ns2.example.com.', '10.0.4.2'),
        ],
        host_node='root1',
    )))

    info('*** Add TLD Zone\n')
    tld1.addService(DNSService(Zone(
        'com.',
        [
            NSRecord('@', 'ns.com.'),
            ARecord('ns', '10.0.2.2'),
            AAAARecord('ns', '2001:db8:2::2'),
            NSRecord('example', 'ns1.example.com.'),
            NSRecord('example', 'ns2.example.com.'),
            ARecord('ns1.example.com.', '10.0.3.2'),
            AAAARecord('ns1.example.com.', '2001:db8:3::2'),
            ARecord('ns2.example.com.', '10.0.4.2'),
            AAAARecord('ns2.example.com.', '2001:db8:4::2'),
        ],
        host_node='tld1',
    )))

    info('*** Add Primary and Secondary Zones\n')
    auth1.addService(DNSService(
        Zone(
            'example.com.',
            [
                NSRecord('@', 'ns1.example.com.'),
                NSRecord('@', 'ns2.example.com.'),
                ARecord('ns1', '10.0.3.2'),
                AAAARecord('ns1', '2001:db8:3::2'),
                ARecord('ns2', '10.0.4.2'),
                AAAARecord('ns2', '2001:db8:4::2'),
                ARecord('res1', '10.0.5.2'),
                ARecord('client1', '10.0.6.2'),
                ARecord('web', '10.0.3.2'),
                AAAARecord('web', '2001:db8:3::2'),
                ARecord('mail', '10.0.3.2'),
                CNAMERecord('alias', 'web.example.com.'),
                TXTRecord('info', 'Mininet-IPLab DNS Lab'),
            ],
            host_node='auth1',
        ),
        Zone(
            '10.in-addr.arpa.',
            [
                NSRecord('@', 'ns1.example.com.'),
                NSRecord('@', 'ns2.example.com.'),
                PTRRecord('2.1.0', 'root1.'),
                PTRRecord('2.2.0', 'ns.com.'),
                PTRRecord('2.3.0', 'ns1.example.com.'),
                PTRRecord('2.3.0', 'web.example.com.'),
                PTRRecord('2.3.0', 'mail.example.com.'),
                PTRRecord('2.4.0', 'ns2.example.com.'),
                PTRRecord('2.5.0', 'res1.example.com.'),
                PTRRecord('2.6.0', 'client1.example.com.'),
            ],
            host_node='auth1',
        ),
    ))
    auth2.addService(DNSService(
        SecondaryZone('example.com.', primary=auth1, host_node='auth2'),
        SecondaryZone('10.in-addr.arpa.', primary=auth1, host_node='auth2'),
    ))

    info('*** Add Recursive Caching Resolver Service\n')
    res1.addService(ResolverService(
        root_hints=[root1],
        max_ttl=MAX_TTL,
        max_negative_ttl=MAX_NEGATIVE_TTL,
    ))

    return net


if __name__ == '__main__':
    args = parse_lab_args('DNS Lab')
    run_lab(
        build_network(),
        enable_web=args.enable_web,
        web_host=args.web_host,
        web_port=args.web_port,
        log_level=args.log_level,
    )
