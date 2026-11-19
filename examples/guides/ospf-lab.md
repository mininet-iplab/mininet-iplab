## OSPF Routing Lab Guide

Use this guide in the web UI while the Lab is running. Click a Node in the Topology to open its Terminal.
Commands starting with `show` run in `vtysh`; open it with `vtysh` in a
Router's Terminal and leave it with `exit`. This Lab uses short OSPF
timers (hello 1 s, dead 3 s), so changes settle in seconds.

### The Lab at a glance

The Topology view shows how the Nodes are linked. `r1`, `r2` and `r3` form a
backbone triangle. Each of them also sits on its own Area's switch with two
more Routers, and each of those Routers has one Host.

| Area | Routers | Subnet on the switch | Hosts |
|---|---|---|---|
| 0 | `r1`, `r2`, `r3` | `10.10.1.0/30` (`r1`-`r2`), `10.10.2.0/30` (`r1`-`r3`), `10.10.3.0/30` (`r2`-`r3`) | none |
| 1 | `r1` (ABR), `r11`, `r12` | `172.16.0.0/24`, switch `s1` | `c11` `172.16.1.2`, `c12` `172.16.2.2` |
| 2 | `r2` (ABR), `r21`, `r22` | `172.17.0.0/24`, switch `s2` | `c21` `172.17.1.2`, `c22` `172.17.2.2` |
| 3 | `r3` (ABR), `r31`, `r32` | `172.18.0.0/24`, switch `s3` | `c31` `172.18.1.2`, `c32` `172.18.2.2` |

`r1`, `r2` and `r3` each touch the backbone and one region, which makes
them area border routers (ABRs). Router IDs are `1.1.1.1`, `2.2.2.2`,
`3.3.3.3`, `1.1.1.11` and so on.

### 1. Neighbors and the designated router

On `r1`:

```text
show ip ospf neighbor
```

`r2` and `r3` are `Full/-`: on a point-to-point link there is no election.
`r11` and `r12` are `Full/DROther`. On the shared switch `s1`, `r1` has
priority 255 and `r11` and `r12` have priority 0, so `r1` is always the
designated router (DR). The two non-DR routers only reach `2-Way` with
each other. Check on `r11`:

```text
show ip ospf neighbor
show ip ospf interface r11-eth0
```

`r1` is `Full/DR`, `r12` is `2-Way/DROther`, and the interface lists
`Priority 0` and a `Designated Router (ID) 1.1.1.1`.

### 2. Areas and area border routers

On `r1`:

```text
show ip ospf
```

It reports `This router is an ABR` and two areas: `0.0.0.0 (Backbone)` and
`0.0.0.1`. On `r11`, the same command lists one area, `0.0.0.1`, and
`show ip ospf border-routers` names `1.1.1.1` as the ABR it reaches. An
internal router only knows the ABRs of its own area.

### 3. What each area knows

Look at the link-state database on `r1`:

```text
show ip ospf database
```

Area 0 holds one Router LSA for each of `r1`, `r2` and `r3`, plus
Summary LSAs: the regional prefixes the ABRs inject. Area 1 holds the
Router LSAs of `r1`, `r11` and `r12`, one Network LSA for `s1` (advertised
by the DR, `172.16.0.1`), and Summary LSAs for the backbone and the other
regions. The ABR keeps a separate database for every area it touches.

On `r11`:

```text
show ip ospf route
```

Routes in its own area are `N` (intra-area). Routes learned through the
ABR are `N IA` (inter-area): the backbone links and the regions of `r2`
and `r3`. `r11` has no Router LSAs for those regions at all.

### 4. Summarization at the ABR

Each ABR carries an `area ... range` line covering its region's four
`/24`s (`172.16.0.0/22` on `r1`, `172.17.0.0/22` on `r2`, `172.18.0.0/22`
on `r3`), so each region is announced to the backbone as one route. On `r2`:

```text
show ip route ospf
```

`172.16.0.0/22` and `172.18.0.0/22` appear as single routes. The individual
`172.16.1.0/24` and `172.16.2.0/24` do not. For its own region `r2` shows
the `/24` routes plus `172.17.0.0/22 ... unreachable (blackhole)`, the
discard route the ABR installs for its own summary so it never loops
packets for unused addresses back out.

Trace across areas from `c11`:

```bash
traceroute -n 172.18.2.2
```

The hops are `r11`, `r1`, `r3` (over `10.10.2.2`), `r32`, then `c32`:
the path leaves Area 1 at its ABR, crosses the backbone and enters Area 3.

### 5. A backbone link fails

On `r1`, note the route to Area 2:

```text
show ip route 172.17.0.0/22
```

The next hop is `10.10.1.2` on `r1-eth1`, directly to `r2`. In `r1`'s
Terminal (bash), cut that link:

```bash
ip link set r1-eth1 down
```

After a few seconds `show ip route 172.17.0.0/22` shows `10.10.2.2` on
`r1-eth2`, and `traceroute -n 172.17.1.2` from `c11` goes through `r3` and
`10.10.3.1` before reaching `r21`. The area summary stays the same; only
the way across the backbone changed. Restore the link:

```bash
ip link set r1-eth1 up
```

Wait about 15 seconds for the adjacency and the routes to come back, then
check that the next hop is `10.10.1.2` again.

### 6. Finish with a working Lab

From `c11`, `ping -c 3` `172.17.1.2` and `172.18.2.2`; both reply and
`show ip ospf neighbor` on `r1` lists four neighbors in `Full`. Stop the
Lab when finished.
