#!/usr/bin/env python3
"""Static routing lab with one Router and Internet access."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info, setLogLevel
from mniplab import mnIPLab, parse_lab_args, run_lab

def build_network():
    """Build static routing topology."""
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add NAT Node\n')
    nat1 = net.addNAT('nat1', subnet='100.64.0.0/23')

    info('*** Add Routers\n')
    r1 = net.addRouter('r1')
    net.addLink(nat1, r1, params1={'ip': '100.64.0.1/24'}, params2={'ip': '100.64.0.2/24'})

    info('*** Add hosts\n')
    h1 = net.addHost('h1', defaultRoute='via 100.64.1.1')
    h2 = net.addHost('h2', defaultRoute='via 100.64.2.1')

    info('*** Add links\n')
    net.addLink(h1, r1, params1={'ip': '100.64.1.2/24'}, params2={'ip': '100.64.1.1/24'})
    net.addLink(h2, r1, params1={'ip': '100.64.2.2/24'}, params2={'ip': '100.64.2.1/24'})

    return net

if __name__ == '__main__':
    args = parse_lab_args('Static Routing with Internet Access Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port,
            log_level=args.log_level,
            )
