#!/usr/bin/env python3
"""BGP IXP lab: TR / UP / RP / RC topology (IPv4-only)

Topology (logical):
 - TR1, TR2: ExaBGP injectors (transit ASes) with dual peering via per-neighbor routes
 - UP1, UP2: Upstream providers (FRR)
 - RP1, RP2: Route participant routers connected to IXP (FRR)
 - RXP: central IXP route-server (FRR)
 - RC1..RC4: Customer routers (FRR) with hosts hc1..hc4
 - hup1/hup2/hp1/hp2: edge hosts
 - nat1, nat2: TransitNAT gateways (root-ns) for internet masquerade

TransitNAT chain:
 - nat1 (root-ns) → tr1 (ExaBGP) → up1 (FRR); all stages auto-configured
 - nat2 (root-ns) → tr2 (ExaBGP) → up2 (FRR); all stages auto-configured

Notes:
 - FRR routers are created with persistent config directories under
   `frr-config/bgp-ispixp/%(name)s` so the first run will generate configs
   from templates.
 - ExaBGP speakers (TR1, TR2) use per-neighbor route configuration: each
   BGPNeighbor instance carries its own routes.
 - IPv4-only, eBGP everywhere.
 - ASNs: rc1=45705 rc2=46019 rc3=4796 rc4=3382 rp1=7713 rp2=4761 up1=4788 up2=9002 rxp=7717 tr1=3356 tr2=1299
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab
from mniplab.exabgp import BGPNeighbor, make_v4_peer_routes


def build_network() -> mnIPLab:
    """Build and return the BGP IXP topology.

    This example keeps the structure simple (like `examples/bgp-lab.py`) and
    uses Per-Neighbor Routes for its two transit Speakers.
    """
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

    info('*** Add FRR routers (persistent)\n')
    rxp = net.addRouter('rxp', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')
    rp1 = net.addRouter('rp1', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')
    rp2 = net.addRouter('rp2', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')
    up1 = net.addRouter('up1', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')
    up2 = net.addRouter('up2', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')

    rc1 = net.addRouter('rc1', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')
    rc2 = net.addRouter('rc2', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')
    rc3 = net.addRouter('rc3', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')
    rc4 = net.addRouter('rc4', proto='BGP', frr_dir='./frr-config/bgp-ispixp/%(name)s')

    info('*** Add edge hosts\n')
    hup1 = net.addHost('hup1', defaultRoute='via 10.100.1.1')
    hup2 = net.addHost('hup2', defaultRoute='via 10.100.2.1')
    hp1 = net.addHost('hp1', defaultRoute='via 10.200.1.1')
    hp2 = net.addHost('hp2', defaultRoute='via 10.200.2.1')

    hc1 = net.addHost('hc1', defaultRoute='via 10.10.1.1')
    hc2 = net.addHost('hc2', defaultRoute='via 10.10.2.1')
    hc3 = net.addHost('hc3', defaultRoute='via 10.10.3.1')
    hc4 = net.addHost('hc4', defaultRoute='via 10.10.4.1')

    info('*** Add ExaBGP injectors (TR1, TR2)\n')
    # TR1 (AS 3356) peers to UP1 (4788) and UP2 (9002)
    # Each peering has distinct routes via per-neighbor route configuration
    tr1_n1 = BGPNeighbor(
        peer='10.0.5.1', local_address='10.0.5.2',
        local_as=3356, peer_as=4788,
        routes=make_v4_peer_routes(
            num_prefixes=8, num_sub_prefixes=2, local_asn=3356,
            next_hop='10.0.5.2', prefix_seed=42, as_path_seed=88
        )
    )
    tr1_n2 = BGPNeighbor(
        peer='10.0.6.1', local_address='10.0.6.2',
        local_as=3356, peer_as=9002,
        routes=make_v4_peer_routes(
            num_prefixes=8, num_sub_prefixes=2, local_asn=3356,
            next_hop='10.0.6.2', prefix_seed=42, as_path_seed=84
        )
    )
    tr1 = net.addExaBGP('tr1', neighbors=[tr1_n1, tr1_n2], local_as=3356)

    # TR2 (AS 1299) peers to UP1 and UP2
    tr2_n1 = BGPNeighbor(
        peer='10.0.7.1', local_address='10.0.7.2',
        local_as=1299, peer_as=4788,
        routes=make_v4_peer_routes(
            num_prefixes=8, num_sub_prefixes=2, local_asn=1299,
            next_hop='10.0.7.2', prefix_seed=43, as_path_seed=84
        )
    )
    tr2_n2 = BGPNeighbor(
        peer='10.0.8.1', local_address='10.0.8.2',
        local_as=1299, peer_as=9002,
        routes=make_v4_peer_routes(
            num_prefixes=8, num_sub_prefixes=2, local_asn=1299,
            next_hop='10.0.8.2', prefix_seed=43, as_path_seed=88
        )
    )
    tr2 = net.addExaBGP('tr2', neighbors=[tr2_n1, tr2_n2], local_as=1299)

    info('*** Add TransitNAT gateways\n')
    nat_tr1 = net.addTransit('nat1', downstream=[up1, up2], subnet='10.0.50.0/30')
    nat_tr2 = net.addTransit('nat2', downstream=[up1, up2], subnet='10.0.50.4/30')

    info('*** Add links and assign /30 point-to-point IPs\n')
    # rp1 <-> rxp
    net.addLink(rp1, rxp, params1={'ip': '10.0.1.2/30'}, params2={'ip': '10.0.1.1/30'})
    # rp2 <-> rxp
    net.addLink(rp2, rxp, params1={'ip': '10.0.2.2/30'}, params2={'ip': '10.0.2.1/30'})

    # rp1 <-> up1
    net.addLink(rp1, up1, params1={'ip': '10.0.3.1/30'}, params2={'ip': '10.0.3.2/30'})
    # rp2 <-> up2
    net.addLink(rp2, up2, params1={'ip': '10.0.4.2/30'}, params2={'ip': '10.0.4.1/30'})

    # TR1 <-> UP1, TR1 <-> UP2 (single node with dual peering)
    net.addLink(tr1, up1, params1={'ip': '10.0.5.2/30'}, params2={'ip': '10.0.5.1/30'})
    net.addLink(tr1, up2, params1={'ip': '10.0.6.2/30'}, params2={'ip': '10.0.6.1/30'})

    # TR2 <-> UP1, TR2 <-> UP2 (single node with dual peering)
    net.addLink(tr2, up1, params1={'ip': '10.0.7.2/30'}, params2={'ip': '10.0.7.1/30'})
    net.addLink(tr2, up2, params1={'ip': '10.0.8.2/30'}, params2={'ip': '10.0.8.1/30'})

    # TransitNAT <-> TR1, TransitNAT <-> TR2
    net.addLink(nat_tr1, tr1, params1={'ip': '10.0.50.1/30'}, params2={'ip': '10.0.50.2/30'})
    net.addLink(nat_tr2, tr2, params1={'ip': '10.0.50.5/30'}, params2={'ip': '10.0.50.6/30'})

    # UP1 <-> UP2 (peer relationship)
    net.addLink(up1, up2, params1={'ip': '10.0.9.1/30'}, params2={'ip': '10.0.9.2/30'})

    # Customers: rc1,rc2 on rp1; rc3,rc4 on rp2
    net.addLink(rc1, rp1, params1={'ip': '10.0.11.2/30'}, params2={'ip': '10.0.11.1/30'})
    net.addLink(rc2, rp1, params1={'ip': '10.0.12.2/30'}, params2={'ip': '10.0.12.1/30'})
    net.addLink(rc3, rp2, params1={'ip': '10.0.13.2/30'}, params2={'ip': '10.0.13.1/30'})
    net.addLink(rc4, rp2, params1={'ip': '10.0.14.2/30'}, params2={'ip': '10.0.14.1/30'})

    # Attach hosts to UP/ RP and RC routers (hosts already have IPs)
    net.addLink(hup1, up1, params1={'ip': '10.100.1.2/24'}, params2={'ip': '10.100.1.1/24'})
    net.addLink(hup2, up2, params1={'ip': '10.100.2.2/24'}, params2={'ip': '10.100.2.1/24'})
    net.addLink(hp1, rp1, params1={'ip': '10.200.1.2/24'}, params2={'ip': '10.200.1.1/24'})
    net.addLink(hp2, rp2, params1={'ip': '10.200.2.2/24'}, params2={'ip': '10.200.2.1/24'})

    # Attach customer hosts to RC routers
    net.addLink(hc1, rc1, params1={'ip': '10.10.1.2/24'}, params2={'ip': '10.10.1.1/24'})
    net.addLink(hc2, rc2, params1={'ip': '10.10.2.2/24'}, params2={'ip': '10.10.2.1/24'})
    net.addLink(hc3, rc3, params1={'ip': '10.10.3.2/24'}, params2={'ip': '10.10.3.1/24'})
    net.addLink(hc4, rc4, params1={'ip': '10.10.4.2/24'}, params2={'ip': '10.10.4.1/24'})

    info('*** Notes:\n')
    info(' - ASNs: rc1=45705 rc2=46019 rc3=4796 rc4=3382 rp1=7713 rp2=4761 up1=4788 up2=9002 rxp=7717 tr1=3356 tr2=1299\n')

    return net


if __name__ == '__main__':
    args = parse_lab_args('BGP ISP-IXP Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port,
            log_level=args.log_level)
