## BGP ISP / IXP Lab Guide

Use this guide in the web UI while the Lab is running. Click a Router or Host in the Topology to open
its Terminal; a Speaker (`tr1`, `tr2`) opens through its context menu, as
section 5 shows. Commands starting with `show` run in `vtysh`; open it
with `vtysh` in a Router's Terminal and leave it with `exit`. The Lab has
19 Nodes and takes about a minute to settle.

### The Lab at a glance

The Topology view shows how the Nodes are linked. It has three tiers. The
two Speakers link to both upstream providers. Each upstream provider sells
transit to one ISP, the two ISPs meet at the IXP route server `rxp`, and
each ISP has two customers below it.

| Node | AS | Role |
|---|---|---|
| `tr1`, `tr2` | 3356, 1299 | Speakers (ExaBGP) standing in for the Internet; each peers with `up1` and `up2` and announces 24 prefixes to each |
| `up1`, `up2` | 4788, 9002 | Upstream providers; they peer with each other and sell transit to `rp1` and `rp2` |
| `rp1`, `rp2` | 7713, 4761 | ISPs (IXP participants): customers below, one upstream, and the IXP |
| `rxp` | 7717 | IXP route server; only `rp1` and `rp2` peer with it |
| `rc1`..`rc4` | 45705, 46019, 4796, 3382 | Customer Routers, announcing `10.10.1.0/24` .. `10.10.4.0/24` |
| `hc1`..`hc4`, `hp1`, `hp2`, `hup1`, `hup2` | | Hosts on the customer, ISP and upstream networks |

The IXP here is a single route server in the middle: `rp1` and `rp2` each
have one link to `rxp` and no link to each other, so traffic between them
also crosses `rxp`.

### 1. Sessions at every level

On `up1`, `rp1` and `rxp`:

```text
show bgp summary
```

Every neighbor shows a number under `State/PfxRcd`. `up1` has four
neighbors: `rp1` (customer), `tr1` and `tr2` (its transit), and `up2`
(peer). `rp1` has four: `rxp`, `up1`, `rc1` and `rc2`. `rxp` has two, the
participants. Each Router in the Lab tags routes by where they came from
with communities such as `4788:2000` (customer) or `7713:3000` (IXP), and
the sections below read those tags.

### 2. Speakers inject the Internet

`tr1` and `tr2` are not Routers: they run ExaBGP and only announce what
they were given. Open **Open Speaker** from `tr1`'s context menu in the
Topology (the Node itself opens a Terminal). The panel lists two
Neighbors, `10.0.5.1` (`up1`) and `10.0.6.1` (`up2`), both established, and
24 routes announced to each, such as `24.108.0.0/14` with AS path `3356`.
Each Neighbor gets a different set of routes: the two sessions of one
Speaker do not carry the same prefixes.

On `up1`:

```text
show bgp ipv4 unicast neighbors 10.0.5.2 routes
```

shows what `tr1` sent. On `rp1`, `show bgp ipv4 unicast` lists those
prefixes with AS paths beginning `4788 ...`, and `0.0.0.0/0`, which `up1`
sends its customer by default.

### 3. The IXP beats transit

On `rp1`:

```text
show bgp ipv4 unicast 10.200.2.0/24
```

`10.200.2.0/24` is `rp2`'s own network, reachable two ways: through the
IXP (AS path `7717 4761`, `localpref 200`, community `7713:3000`) and
through the upstream (`4788 9002 4761`, `localpref 100`). The IXP path
wins because `rp1` gives IXP routes a higher LOCAL_PREF than its
upstream's. From `hp1`:

```bash
traceroute -n 10.200.2.2
```

Hop 2 is `10.0.1.1`, `rxp`, then `10.0.2.2` (`rp2`). On `rxp`,
`show bgp ipv4 unicast` lists six routes: the participants' own networks
and their customers' `10.10.x.0/24`. Unlike a transparent route server,
`rxp` puts its own AS, 7717, in the path.

### 4. Who may transit whom

`rxp` holds no Internet prefixes, though `rp1` and `rp2` each have dozens
of paths to them. Each ISP sends only its own and its customers' routes
to the IXP and to its upstream (the `RM_IXP_OUT` and `RM_UPSTREAM_OUT`
policies on `rp1` and `rp2`). Likewise `up1` does not send `tr1`'s
prefixes to its peer `up2`. Customers get everything.

Compare `up2` and `rc1` for one Internet prefix, for example
`show bgp ipv4 unicast 24.108.0.0/14`. `rc1` has it with AS path
`7713 4788 3356` and communities `4788:1000 7713:1000` stacked on the way
down; `up2` has its own copy straight from `tr1`, with a different AS path
(`3356 43346 ...`), not the one `up1` learned.

### 5. Announce and withdraw a route from a Speaker

In `tr1`'s Speaker panel, use **Announce a route**: Neighbor `10.0.5.1`,
prefix `203.0.113.0/24`, next hop `10.0.5.2` (pre-filled). From the Lab
Prompt the same announcement is
`announce tr1 neighbor 10.0.5.1 203.0.113.0/24 next-hop 10.0.5.2`. The
route appears in the panel with a **Withdraw** button.

Within seconds `up1` has it (`show bgp ipv4 unicast 203.0.113.0/24`,
community `4788:1000`, `localpref 150`), and so do `rp1` and then `rc1`
(communities `4788:1000 7713:1000`, AS path `7713 4788 3356`). `up2`, `rxp`
and `rp2` do not: a route learned from an upstream goes to customers only.

Withdraw it with the row's **Withdraw** button. `show bgp ipv4 unicast
203.0.113.0/24` on `rc1` answers `Network not in table` again. Leave
**Withdraw all** alone: it also removes the 24 routes the Lab shipped.

### 6. Lose the IXP

On `rp1`, in a bash Terminal, cut its IXP link:

```bash
ip link set rp1-eth0 down
```

After about 10 seconds `show bgp summary` shows `rxp` as `Active`, and
`show ip route 10.200.2.0/24` has one next hop, `10.0.3.2` on `rp1-eth1`
(`up1`). From `hp1`, `traceroute -n 10.200.2.2` now goes through `up1`,
`up2` (`10.0.9.2`) and `rp2`: the same traffic, now paid-for transit.
Restore the link:

```bash
ip link set rp1-eth0 up
```

About 15 seconds later `rxp` is established again and the route to
`10.200.2.0/24` points at `10.0.1.1`.

### 7. Finish with a working Lab

From `hc1`, `ping -c 3 10.10.3.2`, and from `hp1`, `ping -c 3 10.200.2.2`;
both reply. Stop the Lab when finished.
