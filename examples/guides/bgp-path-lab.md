## BGP Path Selection Lab Guide

Use this guide in the web UI while the Lab is running. Click a Node in the Topology to open its
Terminal. Every section below ends by undoing what it changed, so you can
do the sections in order, or return to one later.

### The Lab at a glance

The Topology view shows how the Nodes are linked. Three Autonomous Systems
sit in a row: `r1` (AS65001) links to both `r2` and `r3` (AS65002), which
are linked to each other and both link to `r4` (AS65003). Two Hosts sit
behind `r1`, and two behind `r4`.

| Node | AS | Key addresses |
|---|---|---|
| `h1`, `h2` | 65001 | `192.168.0.2/24`, `192.168.1.2/24` |
| `r1` | 65001 | `10.10.0.1` (to `r2`), `10.10.0.5` (to `r3`) |
| `r2` | 65002 | `10.10.0.2` (to `r1`), `10.10.0.13` (to `r4`), `10.10.0.9` (to `r3`) |
| `r3` | 65002 | `10.10.0.6` (to `r1`), `10.10.0.17` (to `r4`), `10.10.0.10` (to `r2`) |
| `r4` | 65003 | `10.10.0.14` (to `r2`), `10.10.0.18` (to `r3`) |
| `h3`, `h4` | 65003 | `192.168.2.2/24`, `192.168.3.2/24` |

`r1` reaches `192.168.2.0/24` and `192.168.3.0/24` through two equally
long paths, one via `r2` and one via `r3`. `r4` has the same choice toward
`192.168.0.0/24` and `192.168.1.0/24`. Each section changes the policy on
one router and watches which path wins.

Commands starting with `show` or `clear` run in `vtysh`. Open it with
`vtysh` in a Router's Terminal and leave it with `exit`. Policy changes
are runtime only: do not run `write memory`.

### 1. The best path by default

Confirm the sessions are up on `r1`:

```text
show bgp summary
```

Every neighbor shows a number under `State/PfxRcd`, not `Active` or
`Idle`. Then look at one prefix:

```text
show bgp ipv4 unicast 192.168.2.0/24
```

Two paths are listed, via `10.10.0.2` (`r2`) and `10.10.0.6` (`r3`). Both
are `valid, external`, with AS path `65002 65003`, the same LOCAL_PREF and
the same MED, so none of BGP's earlier rules can separate them. The
winner is marked `best`, with the rule that picked it in brackets. On a
fresh start that is usually `(Older Path)`: for external paths FRR keeps
the one it heard first, so which of `r2` and `r3` wins can differ from one
start to the next.

Make the tie-break deterministic. On `r1`:

```text
configure terminal
router bgp 65001
bgp bestpath compare-routerid
end
clear bgp ipv4 unicast * soft in
show bgp ipv4 unicast 192.168.2.0/24
```

The best path is now the one from `r2`, marked `(Router ID)`: its router
ID, `10.10.0.2`, is lower than `r3`'s `10.10.0.6`. Check the path in the
`h1` Terminal:

```bash
traceroute -n 192.168.2.2
```

Hop 2 is `10.10.0.2`, `r2`. Undo on `r1`:

```text
configure terminal
router bgp 65001
no bgp bestpath compare-routerid
end
```

### 2. LOCAL_PREF: choose the way out

LOCAL_PREF is compared before AS path length, so a higher value wins even
over a shorter path. It stays inside one AS, so it only controls how that
AS sends traffic out. On `r1`, prefer everything heard from `r3`:

```text
configure terminal
route-map PREFER_R3 permit 10
 match ip address prefix-list PREFIX_FROM_EBGP
 set local-preference 200
exit
router bgp 65001
address-family ipv4 unicast
neighbor 10.10.0.6 route-map PREFER_R3 in
end
clear bgp ipv4 unicast 10.10.0.6 soft in
show bgp ipv4 unicast
```

The `LocPrf` column shows `200` on the `10.10.0.6` paths and `>` moves to
them for `192.168.2.0/24` and `192.168.3.0/24`. In the `h1` Terminal,
`traceroute -n 192.168.2.2` now shows `r3` at hop 2.

The setting is local to `r1`. In the `h3` Terminal, `traceroute -n
192.168.0.2` still takes whichever transit router `r4` chose; the way
back is `r4`'s decision, so the two directions can differ.

Undo on `r1`:

```text
configure terminal
router bgp 65001
address-family ipv4 unicast
neighbor 10.10.0.6 route-map IMPORT_EBGP in
end
configure terminal
no route-map PREFER_R3
end
clear bgp ipv4 unicast 10.10.0.6 soft in
```

`LocPrf` is empty again in `show bgp ipv4 unicast`.

### 3. MED: ask a neighbor to choose the way in

MED is a hint sent to the neighboring AS about which of several entry
points to use; the lowest MED wins, and only when both paths come from
the same neighbor AS. LOCAL_PREF is compared first, so MED only matters
once the previous section has been undone.

`r2` and `r3` ask `r1` to enter AS65002 for `192.168.2.0/24` through `r2`
and for `192.168.3.0/24` through `r3`. On `r2`:

```text
configure terminal
ip prefix-list H3_NET seq 5 permit 192.168.2.0/24
ip prefix-list H4_NET seq 5 permit 192.168.3.0/24
route-map MED_TO_R1 permit 10
 match ip address prefix-list H3_NET
 set metric 50
exit
route-map MED_TO_R1 permit 20
 match ip address prefix-list H4_NET
 set metric 100
exit
route-map MED_TO_R1 permit 30
exit
router bgp 65002
address-family ipv4 unicast
neighbor 10.10.0.1 route-map MED_TO_R1 out
end
clear bgp ipv4 unicast 10.10.0.1 soft out
```

On `r3`, enter the same commands with the metrics swapped (`set metric 100`
in sequence 10, `set metric 50` in sequence 20), `neighbor 10.10.0.5` in
place of `10.10.0.1`, and `clear bgp ipv4 unicast 10.10.0.5 soft out`.

On `r1`, `show bgp ipv4 unicast` has a `Metric` column of `50` and `100`.
The `>` path to `192.168.2.0/24` is via `10.10.0.2` and the `>` path to
`192.168.3.0/24` is via `10.10.0.6`. In the `h1` Terminal:

```bash
traceroute -n 192.168.2.2
traceroute -n 192.168.3.2
```

Hop 2 is `r2` for the first and `r3` for the second: AS65002 has split the
traffic entering it by destination.

Undo on `r2`, and on `r3` with its own neighbor address (`10.10.0.5`):

```text
configure terminal
router bgp 65002
address-family ipv4 unicast
neighbor 10.10.0.1 route-map EXPORT_EBGP out
end
configure terminal
no route-map MED_TO_R1
no ip prefix-list H3_NET
no ip prefix-list H4_NET
end
clear bgp ipv4 unicast 10.10.0.1 soft out
```

`Metric` is empty again on `r1`.

### 4. Multipath: use both paths

`r4` has two equally good paths to `192.168.0.0/24` but installs one,
because `maximum-paths 1` is set. In vtysh on `r4`:

```text
show running-config
show ip route 192.168.0.0/24
```

`maximum-paths 1` is under `address-family ipv4 unicast`, and the route
has one next hop. Allow two:

```text
configure terminal
router bgp 65003
address-family ipv4 unicast
maximum-paths 2
end
show bgp ipv4 unicast
show ip route 192.168.0.0/24
```

The second path is now marked `=` (multipath) and the route lists two
next hops, `10.10.0.13` via `r4-eth2` and `10.10.0.17` via `r4-eth3`.
Multipath only joins paths that tie on every earlier rule, so it would
not apply with the section 2 or 3 policy in place.

The kernel spreads flows over the two next hops by hashing source and
destination. In the `h3` Terminal:

```bash
traceroute -n 192.168.0.2
traceroute -n 192.168.1.2
```

Hop 2 is `10.10.0.13` (`r2`) for one destination and `10.10.0.17` (`r3`)
for the other; the split can differ between runs.

Cut one path. In `r4`'s Terminal (bash, not vtysh):

```bash
ip link set r4-eth2 down
vtysh -c 'show ip route 192.168.0.0/24'
```

The `10.10.0.13` next hop is `inactive`, only `10.10.0.17` carries
traffic, and `ping -c 3 192.168.0.2` from `h3` still succeeds. Restore it:

```bash
ip link set r4-eth2 up
vtysh -c 'show ip route 192.168.0.0/24'
```

Repeat the second command until both next hops are active again. Then
return to one path. In vtysh on `r4`:

```text
configure terminal
router bgp 65003
address-family ipv4 unicast
maximum-paths 1
end
```

### 5. Finish with a working Lab

From `h3` and `h4`, `ping -c 3` each of `192.168.0.2` and `192.168.1.2`;
all four should reply. Stop the Lab when finished.
