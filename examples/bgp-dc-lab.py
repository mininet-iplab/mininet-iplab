#!/usr/bin/env python3
r"""BGP Data Center Lab: Leaf-Spine with redundant ISP connectivity.

Topology:
  ISP1  ISP2
   | \  / |
   |  \/  |
   |  /\  |
  RS1----RS2 (Spines)
   |\  /\  /|
   | \/  \/ |
   | /\ / \ |
  RL1 RL2  RL3 (Leaves)
   |   |    |
  SRV1 SRV2 SRV3 (Servers)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab


def build_network() -> mnIPLab:
    """Build the Redundant BGP Data Center lab topology."""
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    # FRR configuration directory template
    frr_base = './frr-config/bgp-dc-lab/%(name)s'

    info('*** Add ISP Routers\n')
    isp1 = net.addRouter('isp1', proto='BGP', frr_dir=frr_base)
    isp2 = net.addRouter('isp2', proto='BGP', frr_dir=frr_base)

    info('*** Add Spine Routers\n')
    rs1 = net.addRouter('rs1', proto='BGP', frr_dir=frr_base, vrfs={'vrf-ext': 10})
    rs2 = net.addRouter('rs2', proto='BGP', frr_dir=frr_base, vrfs={'vrf-ext': 10})

    info('*** Add Leaf Routers\n')
    rl1 = net.addRouter('rl1', proto='BGP', frr_dir=frr_base)
    rl2 = net.addRouter('rl2', proto='BGP', frr_dir=frr_base)
    rl3 = net.addRouter('rl3', proto='BGP', frr_dir=frr_base)

    info('*** Add Servers\n')
    srv1 = net.addHost('srv1', defaultRoute='via 172.16.1.1')
    srv2 = net.addHost('srv2', defaultRoute='via 172.16.2.1')
    srv3 = net.addHost('srv3', defaultRoute='via 172.16.3.1')

    info('*** Link ISPs to Spines (Redundant)\n')
    # ISP1 links
    net.addLink(isp1, rs1, params1={'ip': '10.101.1.1/30'}, params2={'ip': '10.101.1.2/30', 'vrf': 'vrf-ext'})
    net.addLink(isp1, rs2, params1={'ip': '10.101.2.1/30'}, params2={'ip': '10.101.2.2/30', 'vrf': 'vrf-ext'})
    # ISP2 links
    net.addLink(isp2, rs1, params1={'ip': '10.102.1.1/30'}, params2={'ip': '10.102.1.2/30', 'vrf': 'vrf-ext'})
    net.addLink(isp2, rs2, params1={'ip': '10.102.2.1/30'}, params2={'ip': '10.102.2.2/30', 'vrf': 'vrf-ext'})

    info('*** Link Leaves to Spines (Full Mesh/ECMP)\n')
    # RL1 links
    net.addLink(rl1, rs1, params1={'ip': '10.11.1.2/30'}, params2={'ip': '10.11.1.1/30'})
    net.addLink(rl1, rs2, params1={'ip': '10.12.1.2/30'}, params2={'ip': '10.12.1.1/30'})
    # RL2 links
    net.addLink(rl2, rs1, params1={'ip': '10.11.2.2/30'}, params2={'ip': '10.11.2.1/30'})
    net.addLink(rl2, rs2, params1={'ip': '10.12.2.2/30'}, params2={'ip': '10.12.2.1/30'})
    # RL3 links
    net.addLink(rl3, rs1, params1={'ip': '10.11.3.2/30'}, params2={'ip': '10.11.3.1/30'})
    net.addLink(rl3, rs2, params1={'ip': '10.12.3.2/30'}, params2={'ip': '10.12.3.1/30'})

    info('*** Link Servers to Leaves\n')
    net.addLink(srv1, rl1, params1={'ip': '172.16.1.2/24'}, params2={'ip': '172.16.1.1/24'})
    net.addLink(srv2, rl2, params1={'ip': '172.16.2.2/24'}, params2={'ip': '172.16.2.1/24'})
    net.addLink(srv3, rl3, params1={'ip': '172.16.3.2/24'}, params2={'ip': '172.16.3.1/24'})

    return net


if __name__ == '__main__':
    args = parse_lab_args('BGP Data Center Lab')
    run_lab(build_network(),
        enable_web=args.enable_web,
        web_host=args.web_host,
        web_port=args.web_port,
        log_level=args.log_level
    )
