#!/usr/bin/env python3
"""Secure BGP Lab.
Teaches Route Origin Validation over a full RPKI chain, "see it break, then
secure it": an origin hijack, a sub-prefix forged-origin hijack through a
loose maxLength, and a route leak stopped with BGP Roles (RFC 9234).

        AS65003                AS65002
          sp1 ----+        +---- r2 ---- h2
                  |        |     |
   h1 ---- r1 ----+--------+     |
          / | \\                  |
        rv ca  +------ r4 -------+
                     AS65004

r1 is AS65001, the Learner's network: h1 and the Validator rv sit inside it,
and the RIR's RPKI CA ca hangs off it too. The CA holds one Hosted CA per
AS, and the Learner registers ROAs at it from its panel.
r2 is AS65002, which originates 10.2.0.0/24 (h2). sp1 is AS65003, a Speaker
that hijacks 10.2.0.0/24 and answers for 10.2.0.2 on its loopback, so a
hijacked ping still gets replies. r4 is AS65004, a customer of r1 and r2.
The Seeds carry no Route Origin Validation: the Guide adds it.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab
from mniplab.exabgp import BGPNeighbor, BGPRoute
from mniplab.rpki import HostedCA, RPKICAService, ValidatorService


def build_network():
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add Routers\n')
    r1 = net.addRouter('r1', proto='BGP', frr_dir='./frr-config/secure-bgp-lab/%(name)s')
    r2 = net.addRouter('r2', proto='BGP', frr_dir='./frr-config/secure-bgp-lab/%(name)s')
    r4 = net.addRouter('r4', proto='BGP', frr_dir='./frr-config/secure-bgp-lab/%(name)s')

    info('*** Add the hijacker\n')
    sp1 = net.addExaBGP('sp1', neighbors=[BGPNeighbor(
        peer='10.0.13.1', local_address='10.0.13.2', local_as=65003, peer_as=65001,
        routes=[BGPRoute('10.2.0.0/24', '10.0.13.2', as_path=[65003])],
    )])
    # The hijacker answers for the address it steals, and routes replies back
    # to h1's network. Not a default route: ExaBGP looks its own name up at
    # start, and a default route sends that DNS query into the Lab to time out.
    net.addStartupCommand(sp1, 'ip addr add 10.2.0.2/32 dev lo', stage='before-daemons')
    net.addStartupCommand(sp1, 'ip route add 10.1.0.0/24 via 10.0.13.1', stage='before-daemons')

    info('*** Add hosts\n')
    h1 = net.addHost('h1', defaultRoute='via 10.1.0.1')
    h2 = net.addHost('h2', defaultRoute='via 10.2.0.1')
    rv = net.addHost('rv', defaultRoute='via 10.1.0.65')
    ca = net.addHost('ca', defaultRoute='via 10.99.0.1')

    info('*** Add RPKI Services\n')
    ca.addService(RPKICAService([
        HostedCA(65001, ['10.1.0.0/24']),
        HostedCA(65002, ['10.2.0.0/24']),
        HostedCA(65003, ['10.3.0.0/24']),
        HostedCA(65004, ['10.4.0.0/24']),
    ]))
    rv.addService(ValidatorService())

    info('*** Add links\n')
    net.addLink(h1, r1, params1={'ip': '10.1.0.2/26'}, params2={'ip': '10.1.0.1/26'})
    net.addLink(rv, r1, params1={'ip': '10.1.0.66/30'}, params2={'ip': '10.1.0.65/30'})
    net.addLink(r1, r2, params1={'ip': '10.0.12.1/30'}, params2={'ip': '10.0.12.2/30'})
    net.addLink(r1, sp1, params1={'ip': '10.0.13.1/30'}, params2={'ip': '10.0.13.2/30'})
    net.addLink(r1, r4, params1={'ip': '10.0.14.1/30'}, params2={'ip': '10.0.14.2/30'})
    net.addLink(r2, r4, params1={'ip': '10.0.24.1/30'}, params2={'ip': '10.0.24.2/30'})
    net.addLink(h2, r2, params1={'ip': '10.2.0.2/24'}, params2={'ip': '10.2.0.1/24'})
    net.addLink(ca, r1, params1={'ip': '10.99.0.2/30'}, params2={'ip': '10.99.0.1/30'})

    return net


if __name__ == '__main__':
    args = parse_lab_args('Secure BGP Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port, log_level=args.log_level)
