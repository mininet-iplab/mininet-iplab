#!/usr/bin/env python3
"""Dual-stack static routing lab with 2 routers."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab


def build_network() -> mnIPLab:
    """Build the four-Node dual-stack static routing topology."""
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add Routers\n')
    r1 = net.addRouter('r1', frr_dir='./frr-config/static-lab/%(name)s')
    r2 = net.addRouter('r2', frr_dir='./frr-config/static-lab/%(name)s')

    info('*** Add hosts\n')
    h1 = net.addHost('h1', defaultRoute='via 192.168.1.1',
                     defaultRoute6='via 2001:db8:1::1')
    h2 = net.addHost('h2', defaultRoute='via 192.168.2.1',
                     defaultRoute6='via 2001:db8:2::1')

    info('*** Add links\n')
    net.addLink(h1, r1,
                params1={'ip': '192.168.1.2/24', 'ip6': '2001:db8:1::2/64'},
                params2={'ip': '192.168.1.1/24', 'ip6': '2001:db8:1::1/64'})
    net.addLink(h2, r2,
                params1={'ip': '192.168.2.2/24', 'ip6': '2001:db8:2::2/64'},
                params2={'ip': '192.168.2.1/24', 'ip6': '2001:db8:2::1/64'})
    net.addLink(r1, r2,
                params1={'ip': '10.10.1.1/30', 'ip6': '2001:db8:10::1/64'},
                params2={'ip': '10.10.1.2/30', 'ip6': '2001:db8:10::2/64'})

    return net


if __name__ == '__main__':
    args = parse_lab_args('Static Routing Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port, log_level=args.log_level)
