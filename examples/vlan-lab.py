#!/usr/bin/env python3
"""802.1Q VLANs, Router-on-a-Stick routing and Rapid Spanning Tree across three Switches."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab


def build_network():
    """Build two VLANs across three Switches joined in a triangle.

    Topology:
      - s1: the core Switch, pinned as the RSTP root by its priority.
      - s2, s3: access Switches, each with a Host in VLAN 10 and one in VLAN 20.
      - Trunks (802.1Q, VLANs 10 and 20): s1-s2, s1-s3 and s2-s3, a
        triangle that RSTP breaks by blocking one port.
      - h1, h3: VLAN 10 (192.168.10.0/24); h2, h4: VLAN 20 (192.168.20.0/24).
      - r1: Router-on-a-Stick on the core s1, routing between the VLANs.
    """
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add switches\n')
    s1 = net.addSwitch('s1', stp='rstp', priority=4096)
    s2 = net.addSwitch('s2', stp='rstp')
    s3 = net.addSwitch('s3', stp='rstp')

    info('*** Add router\n')
    r1 = net.addRouter('r1')

    info('*** Add hosts\n')
    h1 = net.addHost('h1', defaultRoute='via 192.168.10.1')
    h2 = net.addHost('h2', defaultRoute='via 192.168.20.1')
    h3 = net.addHost('h3', defaultRoute='via 192.168.10.1')
    h4 = net.addHost('h4', defaultRoute='via 192.168.20.1')

    info('*** Add links\n')
    # Access ports for hosts
    net.addLink(h1, s2, params1={'ip': '192.168.10.2/24'}, params2={'vlan': 10})
    net.addLink(h2, s2, params1={'ip': '192.168.20.2/24'}, params2={'vlan': 20})
    net.addLink(h3, s3, params1={'ip': '192.168.10.3/24'}, params2={'vlan': 10})
    net.addLink(h4, s3, params1={'ip': '192.168.20.3/24'}, params2={'vlan': 20})

    # 802.1Q trunks carrying VLANs 10 and 20 between the Switches
    trunk = {'trunks': [10, 20]}
    net.addLink(s1, s2, params1=trunk, params2=trunk)
    net.addLink(s1, s3, params1=trunk, params2=trunk)
    net.addLink(s2, s3, params1=trunk, params2=trunk)

    # Trunk from the core to the router, which routes between the VLANs
    r1_link = net.addLink(r1, s1, params2=trunk)

    info('*** Configure 802.1Q sub-interfaces on router\n')
    r1.add_subinterface(r1_link.intf1, 10, ip='192.168.10.1/24')
    r1.add_subinterface(r1_link.intf1, 20, ip='192.168.20.1/24')

    return net


if __name__ == '__main__':
    args = parse_lab_args('VLAN & Inter-VLAN Routing Lab')
    run_lab(
        build_network(),
        enable_web=args.enable_web,
        web_host=args.web_host,
        web_port=args.web_port,
        log_level=args.log_level,
    )
