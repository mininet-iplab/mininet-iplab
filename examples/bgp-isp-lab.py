#!/usr/bin/env python3
"""BGP Multi-Provider Lab with Dual NAT to emulate Internet connection transit

Topology:
  tr1 (ExaBGP, AS 4200) -- isp1 (AS 65001) -- r1 (AS 65010)
                                |
                                |
  tr2 (ExaBGP, AS 4201) -- isp2 (AS 65002) -- r2 (AS 65020)

  Transit NAT gateways (addTransit) — 3-stage masquerade chain, fully managed:
  - nat1: nat1 (root-ns) → tr1 (ExaBGP) → isp1 (FRR); all stages auto-configured
  - nat2: nat2 (root-ns) → tr2 (ExaBGP) → isp2 (FRR); all stages auto-configured

  Customer networks:
  - AS 65010: r1 (edge with BGP+OSPF, default-originate to internal OSPF), r11, r12 (internal OSPF)
  - AS 65020: r2 (edge with BGP+OSPF, default-originate to internal OSPF), r21, r22 (internal OSPF)

  Hosts:
  - h11, h12 behind AS 65010
  - h21, h22 behind AS 65020
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab
from mniplab.exabgp import BGPNeighbor, make_v4_peer_routes


def build_network() -> mnIPLab:
    """Build BGP Multi-Provider topology with dual NAT.

    Returns:
        Configured mnIPLab network instance.
    """
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    # ISP Layer: isp1, isp2
    info('*** Add ISP routers\n')
    isp1 = net.addRouter('isp1', proto='BGP', frr_dir='./frr-config/bgp-isp/%(name)s')
    isp2 = net.addRouter('isp2', proto='BGP', frr_dir='./frr-config/bgp-isp/%(name)s')

    info('*** Add TransitNAT gateways\n')
    nat_tr1 = net.addTransit('nat1', downstream=isp1, subnet='192.168.0.0/30')
    nat_tr2 = net.addTransit('nat2', downstream=isp2, subnet='192.168.0.4/30')

    # Customer Layer: r1, r11, r12 (AS 65010) and r2, r21, r22 (AS 65020)
    info('*** Add customer edge routers (r1, r2)\n')
    r1 = net.addRouter('r1', proto='BGP,OSPF,BFD', frr_dir='./frr-config/bgp-isp/%(name)s')
    r2 = net.addRouter('r2', proto='BGP,OSPF,BFD', frr_dir='./frr-config/bgp-isp/%(name)s')

    info('*** Add customer internal routers (r11, r12, r21, r22)\n')
    r11 = net.addRouter('r11', proto='OSPF,BFD', frr_dir='./frr-config/bgp-isp/%(name)s')
    r12 = net.addRouter('r12', proto='OSPF,BFD', frr_dir='./frr-config/bgp-isp/%(name)s')
    r21 = net.addRouter('r21', proto='OSPF,BFD', frr_dir='./frr-config/bgp-isp/%(name)s')
    r22 = net.addRouter('r22', proto='OSPF,BFD', frr_dir='./frr-config/bgp-isp/%(name)s')

    # ExaBGP speakers: tr1 (AS 4200), tr2 (AS 4201)
    info('*** Add ExaBGP route injectors (tr1, tr2)\n')
    tr1_neighbor = BGPNeighbor(
        peer='203.0.113.1', local_address='203.0.113.0',
        local_as=4200, peer_as=65001,
        routes=make_v4_peer_routes(
            num_prefixes=8, num_sub_prefixes=2, local_asn=4200,
            next_hop='203.0.113.0', prefix_seed=42, as_path_seed=100,
        ),
    )
    tr1 = net.addExaBGP('tr1', neighbors=[tr1_neighbor])

    tr2_neighbor = BGPNeighbor(
        peer='203.0.113.5', local_address='203.0.113.4',
        local_as=4201, peer_as=65002,
        routes=make_v4_peer_routes(
            num_prefixes=8, num_sub_prefixes=2, local_asn=4201,
            next_hop='203.0.113.4', prefix_seed=43, as_path_seed=101,
        ),
    )
    tr2 = net.addExaBGP('tr2', neighbors=[tr2_neighbor])

    # Add hosts
    info('*** Add hosts\n')
    h11 = net.addHost('h11', defaultRoute='via 192.168.11.1')
    h12 = net.addHost('h12', defaultRoute='via 192.168.12.1')
    h21 = net.addHost('h21', defaultRoute='via 192.168.21.1')
    h22 = net.addHost('h22', defaultRoute='via 192.168.22.1')

    # Links: ISP tier (eBGP backbone)
    info('*** Add ISP backbone links\n')
    net.addLink(tr1, isp1, params1={'ip': '203.0.113.0/30'}, params2={'ip': '203.0.113.1/30'})
    net.addLink(tr2, isp2, params1={'ip': '203.0.113.4/30'}, params2={'ip': '203.0.113.5/30'})
    net.addLink(isp1, isp2, params1={'ip': '203.0.113.8/30'}, params2={'ip': '203.0.113.9/30'})
    net.addLink(nat_tr1, tr1, params1={'ip': '192.168.0.1/30'}, params2={'ip': '192.168.0.2/30'})
    net.addLink(nat_tr2, tr2, params1={'ip': '192.168.0.5/30'}, params2={'ip': '192.168.0.6/30'})

    # Links: ISP ↔ Customer (eBGP)
    info('*** Add customer edge links (eBGP)\n')
    net.addLink(r1, isp1, params1={'ip': '203.0.113.12/30'}, params2={'ip': '203.0.113.13/30'})
    net.addLink(r2, isp2, params1={'ip': '203.0.113.16/30'}, params2={'ip': '203.0.113.17/30'})

    # Links: Customer AS 65010 (r1, r11, r12) - iBGP + OSPF
    info('*** Add AS 65010 internal links\n')
    net.addLink(r1, r11, params1={'ip': '10.10.0.1/30'}, params2={'ip': '10.10.0.2/30'})
    net.addLink(r1, r12, params1={'ip': '10.10.1.1/30'}, params2={'ip': '10.10.1.2/30'})
    net.addLink(r11, r12, params1={'ip': '10.10.2.1/30'}, params2={'ip': '10.10.2.2/30'})

    # Links: Customer AS 65020 (r2, r21, r22) - iBGP + OSPF
    info('*** Add AS 65020 internal links\n')
    net.addLink(r2, r21, params1={'ip': '10.20.0.1/30'}, params2={'ip': '10.20.0.2/30'})
    net.addLink(r2, r22, params1={'ip': '10.20.1.1/30'}, params2={'ip': '10.20.1.2/30'})
    net.addLink(r21, r22, params1={'ip': '10.20.2.1/30'}, params2={'ip': '10.20.2.2/30'})

    # Links: Hosts
    info('*** Add host links\n')
    net.addLink(h11, r11, params1={'ip': '192.168.11.100/24'}, params2={'ip': '192.168.11.1/24'})
    net.addLink(h12, r12, params1={'ip': '192.168.12.100/24'}, params2={'ip': '192.168.12.1/24'})
    net.addLink(h21, r21, params1={'ip': '192.168.21.100/24'}, params2={'ip': '192.168.21.1/24'})
    net.addLink(h22, r22, params1={'ip': '192.168.22.100/24'}, params2={'ip': '192.168.22.1/24'})

    info('*** Configuring loopback addresses\n')
    r1.cmd('ip addr add 192.0.2.1/32 dev lo')
    r11.cmd('ip addr add 192.0.2.11/32 dev lo')
    r12.cmd('ip addr add 192.0.2.12/32 dev lo')
    r2.cmd('ip addr add 192.0.2.2/32 dev lo')
    r21.cmd('ip addr add 192.0.2.21/32 dev lo')
    r22.cmd('ip addr add 192.0.2.22/32 dev lo')

    return net

if __name__ == '__main__':
    args = parse_lab_args('BGP Multi-Provider with NAT Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port,
            log_level=args.log_level
            )
