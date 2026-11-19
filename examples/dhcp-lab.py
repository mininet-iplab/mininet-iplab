#!/usr/bin/env python3
"""DHCP Lab: every DHCP scenario on one dual-stack topology.

One central Service, one Relay and six client Hosts, worked through one
Guide section at a time:

- `r1` runs the central DHCP Service (IPv4 and IPv6). It directly serves
  `h1` on `10.10.0.0/24` and `2001:db8:10::/64`; `h1`'s IPv4 Pool carries a
  Reservation pinned to its MAC and a short lease time.
- A rogue DHCP Host sits beside `h1` on the same Subnet. Nothing runs on it
  until a Guide step starts a second DHCP server there, and a later step
  stops it again.
- `r2` is a Relay Router: it forwards `10.20.0.0/24` and `2001:db8:20::/64`
  to `r1` over a transit link. `h2` sits behind it with MAC Reservations in
  both families.
- `h3` sits on an IPv4-only Subnet and `h4`, `h5` and `h6` on IPv6-only
  Subnets, each advertising a different Router Advertisement outcome: M set
  (an address arrives), O only (SLAAC address, DNS from DHCPv6), and neither
  (SLAAC alone).

The Router Advertisements are written as explicit FRR configuration below
so that the M, O and A flags stay visible as the thing being taught.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mininet.node import OVSBridge
from mniplab import mnIPLab, parse_lab_args, run_lab
from mniplab.dhcp import DHCPService, Pool, Relay, Reservation


def build_network() -> mnIPLab:
    """Build the central Service, a Relay, a rogue Host and six clients."""
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None, switch=OVSBridge)

    info('*** Add Routers and Switch\n')
    r1 = net.addRouter('r1')  # central DHCP Service
    r2 = net.addRouter('r2')  # Relay Router for h2
    sw1 = net.addSwitch('sw1')  # the directly served Subnet's shared segment

    info('*** Add Hosts\n')
    h1 = net.addHost('h1')  # direct, dual-stack, Reservation and short lease
    rogue = net.addHost('rogue')  # no server running until its Guide step
    h2 = net.addHost('h2')  # relayed, dual-stack, MAC Reservations
    h3 = net.addHost('h3')  # IPv4-only, plain Pool for a Learner to edit
    h4 = net.addHost('h4')  # IPv6-only, Router Advertisement M set
    h5 = net.addHost('h5')  # IPv6-only, Router Advertisement O only
    h6 = net.addHost('h6')  # IPv6-only, Router Advertisement with neither

    # autoSetMacs gave these Hosts deterministic MACs at addHost() time; the
    # Reservations pin to them.
    h1_mac = h1.params['mac']
    h2_mac = h2.params['mac']

    info('*** Add Links\n')
    # Directly served Subnet: r1 and the clients share sw1 (r1-eth0).
    net.addLink(sw1, r1, params2={'ip': '10.10.0.1/24', 'ip6': '2001:db8:10::1/64'})
    net.addLink(sw1, h1, params2={'dhcp4': True, 'dhcp6': True})
    net.addLink(sw1, rogue, params2={'ip': '10.10.0.254/24'})
    # Transit link: r1 <-> r2 (r1-eth1)
    net.addLink(
        r1, r2,
        params1={'ip': '10.0.0.1/30', 'ip6': '2001:db8:0::1/64'},
        params2={'ip': '10.0.0.2/30', 'ip6': '2001:db8:0::2/64'},
    )
    # Relayed Subnet: r2 <-> h2 (r2-eth1)
    net.addLink(
        r2, h2,
        params1={'ip': '10.20.0.1/24', 'ip6': '2001:db8:20::1/64'},
        params2={'dhcp4': True, 'dhcp6': True},
    )
    # IPv4-only Subnet (r1-eth2)
    net.addLink(r1, h3, params1={'ip': '10.51.0.1/24'}, params2={'dhcp4': True})
    # IPv6-only Subnets, one per Router Advertisement outcome (r1-eth3..5)
    net.addLink(r1, h4, params1={'ip6': '2001:db8:61::1/64'}, params2={'dhcp6': True})
    net.addLink(r1, h5, params1={'ip6': '2001:db8:62::1/64'}, params2={'dhcp6': True})
    net.addLink(r1, h6, params1={'ip6': '2001:db8:63::1/64'}, params2={'dhcp6': True})

    info('*** Write Router Advertisements and static routes\n')
    # Where a Pool leases the address, the prefix is advertised with A=0
    # (no-autoconfig) so nothing hands an Interface an address before DHCPv6
    # can. The prefix must be marked no-autoconfig BEFORE Router
    # Advertisements are un-suppressed, or FRR emits an autoconfig-enabled
    # one first and the host builds an address by SLAAC.
    r1.add_frr_config(
        'interface r1-eth0\n'
        ' ipv6 nd prefix 2001:db8:10::/64 no-autoconfig\n'
        ' ipv6 nd managed-config-flag\n'
        ' no ipv6 nd suppress-ra\n'
        'exit\n'
        # h4: M set, A off -- the address comes from DHCPv6
        'interface r1-eth3\n'
        ' ipv6 nd prefix 2001:db8:61::/64 no-autoconfig\n'
        ' ipv6 nd managed-config-flag\n'
        ' no ipv6 nd suppress-ra\n'
        'exit\n'
        # h5: O set, A on -- SLAAC builds the address, DHCPv6 adds DNS
        'interface r1-eth4\n'
        ' ipv6 nd other-config-flag\n'
        ' no ipv6 nd suppress-ra\n'
        'exit\n'
        # h6: neither flag, A on -- SLAAC alone
        'interface r1-eth5\n'
        ' no ipv6 nd suppress-ra\n'
        'exit\n'
        'ip route 10.20.0.0/24 10.0.0.2\n'
        'ipv6 route 2001:db8:20::/64 2001:db8:0::2'
    )
    r2.add_frr_config(
        'interface r2-eth1\n'
        ' ipv6 nd prefix 2001:db8:20::/64 no-autoconfig\n'
        ' ipv6 nd managed-config-flag\n'
        ' no ipv6 nd suppress-ra'
    )

    info('*** Add DHCP Service\n')
    r1.addService(DHCPService(
        # Directly served Subnet: h1's Reservation and a short lease.
        Pool(
            cidr='10.10.0.0/24', start='10.10.0.100', end='10.10.0.200',
            lease_time=120, router='10.10.0.1', dns_server='8.8.8.8',
            reservations=(Reservation(mac=h1_mac, ip='10.10.0.50', hostname='h1'),),
        ),
        Pool(
            cidr='2001:db8:10::/64', start='2001:db8:10::100', end='2001:db8:10::200',
            lease_time=600, preferred_lifetime=300, dns_server='2001:db8:10::1',
        ),
        # Relayed Subnet: Reservations by MAC, which a Relay makes possible
        # in IPv6 too.
        Pool(
            cidr='10.20.0.0/24', start='10.20.0.100', end='10.20.0.200',
            lease_time=600, router='10.20.0.1', dns_server='8.8.8.8',
            reservations=(Reservation(mac=h2_mac, ip='10.20.0.50', hostname='h2'),),
        ),
        Pool(
            cidr='2001:db8:20::/64', start='2001:db8:20::100', end='2001:db8:20::200',
            lease_time=600, preferred_lifetime=300, dns_server='2001:db8:20::1',
            reservations=(Reservation(mac=h2_mac, ip='2001:db8:20::50', hostname='h2'),),
        ),
        # IPv4-only Subnet: plain, left for a Learner to edit.
        Pool(cidr='10.51.0.0/24', start='10.51.0.100', end='10.51.0.200'),
        # M set: preferred lifetime well under the valid one, so a Learner
        # can watch the leased address go deprecated before it expires.
        Pool(
            cidr='2001:db8:61::/64', start='2001:db8:61::100', end='2001:db8:61::200',
            lease_time=600, preferred_lifetime=120,
        ),
        # O only: the DNS-server option is what the O flag fetches.
        Pool(
            cidr='2001:db8:62::/64', start='2001:db8:62::100', end='2001:db8:62::200',
            dns_server='2001:db8:62::1',
        ),
        # 2001:db8:63::/64 (h6) has no Pool: SLAAC alone needs no server.
    ))

    info('*** Add DHCP Relay\n')
    r2.addRelay(Relay('10.20.0.0/24', '2001:db8:20::/64', server=['10.0.0.1', '2001:db8:0::1']))

    return net


if __name__ == '__main__':
    args = parse_lab_args('DHCP Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port, log_level=args.log_level)
