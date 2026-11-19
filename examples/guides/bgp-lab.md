## BGP Lab (3 AS) Guide

Use this guide in the web UI while the lab is running.
Open a Terminal for each Node by clicking it in the Topology.

Three Autonomous Systems, each joined to both others by one eBGP Link. Every
network can therefore be reached two ways: directly, or through the third
Autonomous System. Which way a Router picks, and what you can do to change
it, is the lesson.

### What you will learn

- How iBGP carries routes learned over eBGP to every Router inside an
  Autonomous System
- Why an iBGP Router offers its Neighbors only its best path
- What LOCAL_PREF is, and why it is only seen inside an Autonomous System
- How BGP picks between two paths: LOCAL_PREF first, AS_PATH length after
- How to watch a LOCAL_PREF change travel as an UPDATE and move traffic

### The Lab at a glance

The Topology view shows how the Nodes are linked. Three Autonomous Systems
of four Routers each are joined in a triangle, one eBGP Link between every
pair. The view does not mark which Router is in which Autonomous System; the
table does.

| Autonomous System | Routers | Network (originated by) | eBGP exits |
|-------------------|---------|-------------------------|------------|
| 65001 | `r11`–`r14` | `172.16.1.0/24` (`r12`), `c11` on it | `r11` → 65003, `r14` → 65002 |
| 65002 | `r21`–`r24` | `172.17.1.0/24` (`r21`), `c22` on it | `r22` → 65001, `r23` → 65003 |
| 65003 | `r31`–`r34` | `172.18.1.0/24` (`r33`), `c33` on it | `r31` → 65001, `r34` → 65002 |

Inside each Autonomous System, OSPF reaches every Router's loopback
(`172.16.1.11` … `172.16.1.14` in 65001), and every Router is an iBGP
Neighbor of the other three over those loopbacks. The edge Routers rewrite
the next hop to their own loopback (`next-hop-self`) as they pass an eBGP
route inward.

Every Router's configuration is persistent, under
`frr-config/bgp-lab/<router>/frr.conf`. Changes you make with `vtysh` last
until the Lab stops; do not `write memory`, or you change the Lab for
everyone who runs it after you.

### Step 1 — Read `r12`'s table

In `r12`'s Terminal:

```bash
vtysh -c "show ip bgp"
```

All three networks are there: `172.16.1.0/24` from `r12` itself, and the
other two learned over iBGP. Now look at AS 65003's network closely:

```bash
vtysh -c "show ip bgp 172.18.1.0/24"
```

One path, `65003`, learned from `r11` (`172.16.1.11`), with `localpref
100`. There is a second way out, through `r14` and AS 65002, but `r12` is
never offered it. `r14` has both paths too. Both carry LOCAL_PREF 100, so
the tie falls to AS_PATH length, and `r11`'s one-hop path beats `r14`'s own
`65002 65003`. An iBGP Router offers its Neighbors only its best path, so
`r14` keeps its losing one to itself.

If you see `65002 65003` from `172.16.1.14` instead, the Lab is still
converging: `r14`'s route through AS 65002 arrived first. Wait a few
seconds and run the command again.

### Success criteria

- `r12` has routes to `172.16.1.0/24`, `172.17.1.0/24`, and `172.18.1.0/24`
- `r12`'s one path to `172.18.1.0/24` is `65003` from `172.16.1.11`, with
  `localpref 100`

## Packet Analysis

LOCAL_PREF is an Autonomous System's own preference among its exits. It is
set where a route enters, travels only over iBGP, and never leaves. Here
you watch it in the UPDATEs `r14` sends to `r12`, then change it and watch
traffic move.

### Step 1 — Open a Capture on `r12-eth1`

`r12-eth1` is `r12`'s Link to `r14`. Their iBGP connection runs between
loopbacks, and this Link is the route between them.

1. Click the Link between `r12` and `r14` in the Topology and choose
   **Packet Capture**.
2. Choose **`r12-eth1`**. The Packet Analysis pane opens beside your
   Terminals.
3. Pick the **BGP** preset. It stands for `tcp port 179`.

KEEPALIVE rows start arriving at once: the iBGP connections to `r14`, and
any other whose packets take this Link.

### Step 2 — See LOCAL_PREF 100 on the wire

Make `r14` send its routes again. In `r12`'s Terminal:

```bash
vtysh -c "clear ip bgp 172.16.1.14"
```

The connection to `r14` closes and re-opens, and `r14`'s UPDATEs arrive
from `172.16.1.14`. One row can hold several BGP messages: expand it and
read each **Border Gateway Protocol - UPDATE Message** section's **Path
attributes**:

- `Path Attribute - LOCAL_PREF: 100`: the preference `r14` gave the route
  as it came in from AS 65002. An eBGP UPDATE never carries this attribute;
  this one is iBGP.
- `Path Attribute - NEXT_HOP: 172.16.1.14`: `r14`'s loopback, not the
  address of AS 65002's Router, because `r14` rewrites it.
- Under **Network Layer Reachability Information**: `172.17.1.0`, AS
  65002's network. There is no UPDATE for `172.18.1.0/24` from `r14`:
  Step 1 of the walkthrough explained why.

### Step 3 — Raise LOCAL_PREF on `r14`

`r14` sets LOCAL_PREF with a route-map, `IMPORT-FROM-EBGP`, on routes it
learns from AS 65002. Raise it to 200. In `r14`'s Terminal:

```bash
vtysh -c "configure terminal" -c "route-map IMPORT-FROM-EBGP permit 10" -c "set local-preference 200" -c "end" -c "clear ip bgp 10.0.3.2 soft in"
```

The last part, `clear ip bgp 10.0.3.2 soft in`, makes `r14` re-apply the
route-map to what it already learned from AS 65002, without resetting the
connection. `vtysh` may print `No changes found to be committed!`; the
change applies regardless, and the Capture is the proof.

Within a second, a new UPDATE from `172.16.1.14` appears in the pane:

- `Path Attribute - LOCAL_PREF: 200`
- `Path Attribute - AS_PATH: 65002 65003`, announcing `172.18.1.0`

`r14` now prefers its own path to AS 65003 through AS 65002. LOCAL_PREF 200
beats `r11`'s 100 before AS_PATH length is ever compared, so this is now
`r14`'s best path, and it tells its Neighbors.

### Step 4 — Confirm `r12` moved, and so did the traffic

In `r12`'s Terminal:

```bash
vtysh -c "show ip bgp 172.18.1.0/24"
```

One path again, but a different one: `65002 65003`, from `172.16.1.14`,
`localpref 200`. It is the longer path, chosen anyway.

In `c11`'s Terminal:

```bash
traceroute -n -q 1 172.18.1.2
```

The trace now leaves AS 65001 at `r14` and crosses `10.0.3.2`, AS 65002's
Router, on its way to `c33`. Every Router in AS 65001 made the same choice
from one change on one Router: that is what LOCAL_PREF is for.

To undo it, run Step 3 again with `set local-preference 100`, or stop the
Lab.

### Download the Capture

Click **Download pcap** (⤓ in the pane's header) to open the Capture in
Wireshark. The pcap is deleted when the Lab stops, so download it before
you finish.

### Success criteria

- A Capture on `r12-eth1` with the **BGP** preset shows an UPDATE from
  `172.16.1.14` carrying `LOCAL_PREF: 100` after `clear ip bgp 172.16.1.14`
  on `r12`
- After raising LOCAL_PREF on `r14`, an UPDATE from `172.16.1.14` carries
  `LOCAL_PREF: 200` and AS_PATH `65002 65003` for `172.18.1.0/24`
- `r12`'s path to `172.18.1.0/24` becomes `65002 65003` from `172.16.1.14`
- `c11`'s traceroute to `172.18.1.2` crosses `10.0.3.2`
