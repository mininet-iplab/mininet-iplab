#!/usr/bin/env python3
"""BGP Path Selection Lab.
Teaches how BGP chooses among several paths to one prefix: the default best
path, LOCAL_PREF, MED and multipath, on one diamond of three ASes.
  AS65001    AS65002     AS65003
h1 ---+  +----- r2 -----+  +--- h3
      |  |      |       |  |
       r1       |        r4
      |  |      |       |  |
h2 ---+  +----- r3 -----+  +--- h4

h1 and h2 are in AS65001, connected to router r1.
h3 and h4 are in AS65003, connected to router r4.
r2 and r3 are AS65002, the transit between them, and peer over iBGP.
Both ends see two paths to every remote prefix, one via r2 and one via r3.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab


def build_network():
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add Routers\n')
    r1 = net.addRouter('r1', proto='BGP', frr_dir='./frr-config/bgp-path-lab/%(name)s')
    r2 = net.addRouter('r2', proto='BGP', frr_dir='./frr-config/bgp-path-lab/%(name)s')
    r3 = net.addRouter('r3', proto='BGP', frr_dir='./frr-config/bgp-path-lab/%(name)s')
    r4 = net.addRouter('r4', proto='BGP', frr_dir='./frr-config/bgp-path-lab/%(name)s')

    info('*** Add hosts\n')
    # AS 65001 hosts (connected to r1)
    h1 = net.addHost('h1', defaultRoute='via 192.168.0.1')
    h2 = net.addHost('h2', defaultRoute='via 192.168.1.1')

    # AS 65003 hosts (connected to r4)
    h3 = net.addHost('h3', defaultRoute='via 192.168.2.1')
    h4 = net.addHost('h4', defaultRoute='via 192.168.3.1')

    info('*** Add links\n')
    # Hosts to routers
    net.addLink(h1, r1, params1={'ip': '192.168.0.2/24'}, params2={'ip': '192.168.0.1/24'})
    net.addLink(h2, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
    net.addLink(h3, r4, params1={'ip': '192.168.2.2/24'}, params2={'ip': '192.168.2.1/24'})
    net.addLink(h4, r4, params1={'ip': '192.168.3.2/24'}, params2={'ip': '192.168.3.1/24'})

    # Router interconnects
    net.addLink(r1, r2, params1={'ip': '10.10.0.1/30'}, params2={'ip': '10.10.0.2/30'})
    net.addLink(r1, r3, params1={'ip': '10.10.0.5/30'}, params2={'ip': '10.10.0.6/30'})
    net.addLink(r2, r3, params1={'ip': '10.10.0.9/30'}, params2={'ip': '10.10.0.10/30'})
    net.addLink(r2, r4, params1={'ip': '10.10.0.13/30'}, params2={'ip': '10.10.0.14/30'})
    net.addLink(r3, r4, params1={'ip': '10.10.0.17/30'}, params2={'ip': '10.10.0.18/30'})

    return net


if __name__ == '__main__':
    args = parse_lab_args('BGP Path Selection Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port, log_level=args.log_level)
