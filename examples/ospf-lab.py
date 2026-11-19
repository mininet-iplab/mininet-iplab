#!/usr/bin/env python3
"""OSPF routing lab with 3 regions and 9 routers.

Uses OVSBridge switches connecting regional subnets, with fast OSPF timers
(hello-interval 1, dead-interval 3) and deterministic DR priority on ABRs.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mininet.node import OVSBridge
from mniplab import mnIPLab, parse_lab_args, run_lab


def build_network():
    """Build and return the network topology with OSPF."""
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None, switch=OVSBridge)

    info('*** Add Routers\n')
    r1 = net.addRouter('r1', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')
    r11 = net.addRouter('r11', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')
    r12 = net.addRouter('r12', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')
    r2 = net.addRouter('r2', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')
    r21 = net.addRouter('r21', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')
    r22 = net.addRouter('r22', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')
    r3 = net.addRouter('r3', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')
    r31 = net.addRouter('r31', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')
    r32 = net.addRouter('r32', proto='OSPF', frr_dir='./frr-config/ospf-lab/%(name)s')

    info('*** Add hosts\n')
    c11 = net.addHost('c11', defaultRoute='via 172.16.1.1')
    c12 = net.addHost('c12', defaultRoute='via 172.16.2.1')
    c21 = net.addHost('c21', defaultRoute='via 172.17.1.1')
    c22 = net.addHost('c22', defaultRoute='via 172.17.2.1')
    c31 = net.addHost('c31', defaultRoute='via 172.18.1.1')
    c32 = net.addHost('c32', defaultRoute='via 172.18.2.1')

    info('*** Add switches\n')
    s1 = net.addSwitch('s1')
    s2 = net.addSwitch('s2')
    s3 = net.addSwitch('s3')

    info('*** Add links\n')
    # R1 subnet
    net.addLink(s1, r1, params2={'ip': '172.16.0.1/24'})
    net.addLink(s1, r11, params2={'ip': '172.16.0.11/24'})
    net.addLink(s1, r12, params2={'ip': '172.16.0.12/24'})
    net.addLink(c11, r11, params1={'ip': '172.16.1.2/24'}, params2={'ip': '172.16.1.1/24'})
    net.addLink(c12, r12, params1={'ip': '172.16.2.2/24'}, params2={'ip': '172.16.2.1/24'})

    # R2 subnet
    net.addLink(s2, r2, params2={'ip': '172.17.0.1/24'})
    net.addLink(s2, r21, params2={'ip': '172.17.0.21/24'})
    net.addLink(s2, r22, params2={'ip': '172.17.0.22/24'})
    net.addLink(c21, r21, params1={'ip': '172.17.1.2/24'}, params2={'ip': '172.17.1.1/24'})
    net.addLink(c22, r22, params1={'ip': '172.17.2.2/24'}, params2={'ip': '172.17.2.1/24'})

    # R3 subnet
    net.addLink(s3, r3, params2={'ip': '172.18.0.1/24'})
    net.addLink(s3, r31, params2={'ip': '172.18.0.31/24'})
    net.addLink(s3, r32, params2={'ip': '172.18.0.32/24'})
    net.addLink(c31, r31, params1={'ip': '172.18.1.2/24'}, params2={'ip': '172.18.1.1/24'})
    net.addLink(c32, r32, params1={'ip': '172.18.2.2/24'}, params2={'ip': '172.18.2.1/24'})

    # Backbone links
    net.addLink(r1, r2, params1={'ip': '10.10.1.1/30'}, params2={'ip': '10.10.1.2/30'})
    net.addLink(r1, r3, params1={'ip': '10.10.2.1/30'}, params2={'ip': '10.10.2.2/30'})
    net.addLink(r2, r3, params1={'ip': '10.10.3.1/30'}, params2={'ip': '10.10.3.2/30'})

    return net


if __name__ == '__main__':
    args = parse_lab_args('OSPF Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port, log_level=args.log_level)
