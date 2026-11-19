#!/usr/bin/env python3
"""BGP routing lab with 3 autonomous systems.

Uses OSPF as an internal IGP with fast timers (hello-interval 1,
dead-interval 3) for loopback reachability in each AS, peering via iBGP and eBGP.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab


def build_network() -> mnIPLab:
    """Build and return the network topology with BGP.

    Returns:
        Configured mnIPLab network instance.
    """
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add Routers\n')
    # Region 1 routers (AS 65001)
    r11 = net.addRouter('r11', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r12 = net.addRouter('r12', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r13 = net.addRouter('r13', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r14 = net.addRouter('r14', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')

    # Region 2 routers (AS 65002)
    r21 = net.addRouter('r21', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r22 = net.addRouter('r22', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r23 = net.addRouter('r23', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r24 = net.addRouter('r24', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')

    # Region 3 routers (AS 65003)
    r31 = net.addRouter('r31', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r32 = net.addRouter('r32', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r33 = net.addRouter('r33', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')
    r34 = net.addRouter('r34', proto='BGP,OSPF', frr_dir='./frr-config/bgp-lab/%(name)s')

    info('*** Add hosts\n')
    c11 = net.addHost('c11', defaultRoute='via 172.16.1.1')
    c22 = net.addHost('c22', defaultRoute='via 172.17.1.1')
    c33 = net.addHost('c33', defaultRoute='via 172.18.1.1')

    info('*** Add links\n')
    # Region 1 subnet
    net.addLink(c11, r12, params1={'ip': '172.16.1.2/24'}, params2={'ip': '172.16.1.1/24'})

    # Region 1 internal links
    net.addLink(r12, r14, params1={'ip': '10.1.1.1/30'}, params2={'ip': '10.1.1.2/30'})
    net.addLink(r12, r11, params1={'ip': '10.1.2.1/30'}, params2={'ip': '10.1.2.2/30'})
    net.addLink(r14, r13, params1={'ip': '10.1.3.1/30'}, params2={'ip': '10.1.3.2/30'})
    net.addLink(r13, r11, params1={'ip': '10.1.4.1/30'}, params2={'ip': '10.1.4.2/30'})

    # Region 2 subnet
    net.addLink(c22, r21, params1={'ip': '172.17.1.2/24'}, params2={'ip': '172.17.1.1/24'})

    # Region 2 internal links
    net.addLink(r22, r21, params1={'ip': '10.2.1.1/30'}, params2={'ip': '10.2.1.2/30'})
    net.addLink(r22, r24, params1={'ip': '10.2.2.1/30'}, params2={'ip': '10.2.2.2/30'})
    net.addLink(r24, r23, params1={'ip': '10.2.3.1/30'}, params2={'ip': '10.2.3.2/30'})
    net.addLink(r23, r21, params1={'ip': '10.2.4.1/30'}, params2={'ip': '10.2.4.2/30'})

    # Region 3 subnet
    net.addLink(c33, r33, params1={'ip': '172.18.1.2/24'}, params2={'ip': '172.18.1.1/24'})

    # Region 3 internal links
    net.addLink(r32, r31, params1={'ip': '10.3.1.1/30'}, params2={'ip': '10.3.1.2/30'})
    net.addLink(r32, r34, params1={'ip': '10.3.2.1/30'}, params2={'ip': '10.3.2.2/30'})
    net.addLink(r34, r33, params1={'ip': '10.3.3.1/30'}, params2={'ip': '10.3.3.2/30'})
    net.addLink(r33, r31, params1={'ip': '10.3.4.1/30'}, params2={'ip': '10.3.4.2/30'})

    # Backbone links (eBGP)
    net.addLink(r11, r31, params1={'ip': '10.0.1.1/30'}, params2={'ip': '10.0.1.2/30'})
    net.addLink(r34, r23, params1={'ip': '10.0.2.1/30'}, params2={'ip': '10.0.2.2/30'})
    net.addLink(r14, r22, params1={'ip': '10.0.3.1/30'}, params2={'ip': '10.0.3.2/30'})

    info('*** Configure loopback interfaces for BGP router IDs\n')
    # Region 1 loopbacks (172.16.1.x - matches c11 network)
    r11.cmd('ip addr add 172.16.1.11/32 dev lo')
    r12.cmd('ip addr add 172.16.1.12/32 dev lo')
    r13.cmd('ip addr add 172.16.1.13/32 dev lo')
    r14.cmd('ip addr add 172.16.1.14/32 dev lo')
    
    # Region 2 loopbacks (172.17.1.x - matches c22 network)
    r21.cmd('ip addr add 172.17.1.21/32 dev lo')
    r22.cmd('ip addr add 172.17.1.22/32 dev lo')
    r23.cmd('ip addr add 172.17.1.23/32 dev lo')
    r24.cmd('ip addr add 172.17.1.24/32 dev lo')
    
    # Region 3 loopbacks (172.18.1.x - matches c33 network)
    r31.cmd('ip addr add 172.18.1.31/32 dev lo')
    r32.cmd('ip addr add 172.18.1.32/32 dev lo')
    r33.cmd('ip addr add 172.18.1.33/32 dev lo')
    r34.cmd('ip addr add 172.18.1.34/32 dev lo')

    return net


if __name__ == '__main__':
    args = parse_lab_args('BGP Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port, log_level=args.log_level)
