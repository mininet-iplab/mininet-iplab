## BGP Data Center Lab Guide

Use this guide in the web UI while the Lab is running. Click a Node in the Topology to open its Terminal.
Commands starting with `show` run in `vtysh`; open it with `vtysh` in a
Router's Terminal and leave it with `exit`.

### The Lab at a glance

The Topology view shows how the Nodes are linked. Two spines sit in the
middle: the two ISPs above link to both, and the three leaves below link to
both, each leaf with one server.

| Node | AS | Role | Addresses |
|---|---|---|---|
| `isp1`, `isp2` | 64512, 64513 | Upstream providers, each linked to both spines | announce `1.1.1.0/24` and `8.8.8.0/24` (`isp1`), `2.2.2.0/24` and `9.9.9.0/24` (`isp2`) |
| `rs1`, `rs2` | 65000 | Spines | ISP links in `vrf-ext`, leaf links in the default VRF |
| `rl1`, `rl2`, `rl3` | 65001, 65002, 65003 | Leaves | `172.16.1.1`, `172.16.2.1`, `172.16.3.1` |
| `srv1`, `srv2`, `srv3` | | Servers | `172.16.1.2`, `172.16.2.2`, `172.16.3.2` |

Every link is a point-to-point /30 and every session is eBGP. Each leaf is
in its own AS; both spines share AS65000 and do not peer with each other.

### 1. The fabric sessions

On `rl1`:

```text
show bgp summary
```

Two neighbors, `10.11.1.1` (`rs1`) and `10.12.1.1` (`rs2`), both with a
number under `State/PfxRcd`. On `rs1`, the same command shows the three
leaf sessions. The spine-to-ISP sessions are in a separate VRF:

```text
show bgp vrf vrf-ext ipv4 unicast summary
```

`10.101.1.1` (`isp1`) and `10.102.1.1` (`isp2`) are established.

### 2. Equal-cost paths to every destination

On `rl1`:

```text
show bgp ipv4 unicast
show ip route bgp
```

Each remote prefix, the other servers' `/24`s and the ISP prefixes, has two
paths, one via each spine. The second is marked `=` and the route lists
two next hops, `10.11.1.1` via `rl1-eth0` and `10.12.1.1` via `rl1-eth1`.
`maximum-paths 64` on the leaf is what installs both. From `srv1`:

```bash
traceroute -n 172.16.3.2
traceroute -n 8.8.8.8
```

Hop 2 is a spine, `10.11.1.1` or `10.12.1.1`, and may differ for the two
destinations because the kernel spreads flows over the two next hops.

### 3. Servers reach the outside through the spines

From `srv1`:

```bash
ping -c 3 172.16.3.2
ping -c 3 8.8.8.8
```

Both reply: the first stays in the fabric, the second leaves through a
spine into an ISP. On `rs1` the ISP-facing interfaces live in `vrf-ext`:

```text
show ip route vrf vrf-ext
```

The `1.1.1.0/24`, `2.2.2.0/24`, `8.8.8.0/24` and `9.9.9.0/24` routes point
out of `rs1-eth0` and `rs1-eth1`, and the fabric `172.16.x.0/24` routes
appear with `(vrf default)`. The spine imports routes between the two VRFs
(`import vrf` in the BGP configuration), which keeps provider links apart
from the fabric but lets each side see the other's prefixes.

### 4. The fabric is not a transit network

Compare the tables on `isp1`:

```text
show bgp ipv4 unicast
show bgp ipv4 unicast neighbors 10.101.1.2 advertised-routes
```

`isp1` holds its own `1.1.1.0/24` and `8.8.8.0/24`, and the three
`172.16.x.0/24` fabric prefixes, each with a path via each spine. It does
not hold `2.2.2.0/24` or `9.9.9.0/24` from `isp2`. The spines send
outward only prefixes that match `172.16.0.0/16 ge 24` (`RM-DC-OUT` on
`rs1` and `rs2`), so an ISP learns the servers but cannot use the data
center to reach the other ISP.

### 5. A leaf loses a spine link

On `rl1`, check the route to `srv3`'s network:

```text
show ip route 172.16.3.0/24
```

Two next hops. In `rl1`'s Terminal (bash), cut the link to `rs1`:

```bash
ip link set rl1-eth0 down
```

After a few seconds `show ip route 172.16.3.0/24` marks `10.11.1.1` as
`inactive`, only `10.12.1.1` carries traffic, `show bgp summary` shows the
`rs1` session as `Active`, and `ping -c 3 172.16.3.2` from `srv1` still
succeeds. Restore the link:

```bash
ip link set rl1-eth0 up
```

Within about 15 seconds the session returns and `show ip route
172.16.3.0/24` lists both next hops again.

### 6. Finish with a working Lab

From each server, `ping -c 3` the other two servers and `8.8.8.8`. Stop
the Lab when finished.
