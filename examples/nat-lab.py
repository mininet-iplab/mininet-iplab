#!/usr/bin/env python3
"""NAT lab: a home network behind a NAT Router, and an ISP on documentation ranges."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import PortForward, StaticNAT, mnIPLab, parse_lab_args, run_lab

def build_network() -> mnIPLab:
    """Build the home network, its NAT Router, and the ISP."""
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add Routers\n')
    r1 = net.addRouter('r1', frr_dir='./frr-config/nat-lab/%(name)s', nat=[
        PortForward('tcp', 8080, '192.168.1.20', 80),
        StaticNAT('192.168.1.30', '203.0.113.5'),
    ])
    isp = net.addRouter('isp', frr_dir='./frr-config/nat-lab/%(name)s')

    info('*** Add Switches\n')
    s1 = net.addSwitch('s1')
    s2 = net.addSwitch('s2')

    info('*** Add hosts\n')
    home = {'defaultRoute': 'via 192.168.1.1', 'defaultRoute6': 'via 2001:db8:1::1'}
    h1 = net.addHost('h1', **home)
    srv = net.addHost('srv', **home)
    h2 = net.addHost('h2', **home)
    public = {'defaultRoute': 'via 198.51.100.1', 'defaultRoute6': 'via 2001:db8:100::1'}
    web = net.addHost('web', **public)
    remote = net.addHost('remote', **public)

    info('*** Add links\n')
    net.addLink(r1, s1, params1={'ip': '192.168.1.1/24', 'ip6': '2001:db8:1::1/64'})
    net.addLink(h1, s1, params1={'ip': '192.168.1.10/24', 'ip6': '2001:db8:1::10/64'})
    net.addLink(srv, s1, params1={'ip': '192.168.1.20/24', 'ip6': '2001:db8:1::20/64'})
    net.addLink(h2, s1, params1={'ip': '192.168.1.30/24', 'ip6': '2001:db8:1::30/64'})
    net.addLink(r1, isp,
                params1={'ip': '203.0.113.2/30', 'ip6': '2001:db8:ff::2/64', 'nat': 'outside'},
                params2={'ip': '203.0.113.1/30', 'ip6': '2001:db8:ff::1/64'})
    net.addLink(isp, s2, params1={'ip': '198.51.100.1/24', 'ip6': '2001:db8:100::1/64'})
    net.addLink(web, s2, params1={'ip': '198.51.100.10/24', 'ip6': '2001:db8:100::10/64'})
    net.addLink(remote, s2, params1={'ip': '198.51.100.20/24', 'ip6': '2001:db8:100::20/64'})

    return net


if __name__ == '__main__':
    args = parse_lab_args('NAT Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port, log_level=args.log_level)
