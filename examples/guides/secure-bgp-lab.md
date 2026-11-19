## Secure BGP Lab Guide

Use this guide in the web UI while the Lab is running. Click a Node in the
Topology to open its Terminal. The RPKI CA and the Validator also carry a
badge, `RPKI CA` on `ca` and `Validator` on `rv`: click it to open the
Node's panel. Each section first lets something go wrong, then fixes it:
do them in order.

### The Lab at a glance

The Topology view shows how the Nodes are linked. `r1` is in the middle,
linked to `r2`, the Speaker `sp1` and `r4`; `r2` and `r4` are also linked to
each other. `h1`, `rv` and `ca` each hang off `r1`, and `h2` off `r2`.

| Node | AS | Role | Key addresses |
|---|---|---|---|
| `r1` | 65001 | your Router | `10.0.12.1` (to `r2`), `10.0.13.1` (to `sp1`), `10.0.14.1` (to `r4`) |
| `h1` | 65001 | a host in your network | `10.1.0.2` |
| `rv` | 65001 | your Validator | `10.1.0.66` |
| `ca` | the RIR | the RPKI CA every AS registers its ROAs at | `10.99.0.2` |
| `r2` | 65002 | originates `10.2.0.0/24` | `10.0.12.2` (to `r1`), `10.0.24.1` (to `r4`) |
| `h2` | 65002 | the host everyone wants to reach | `10.2.0.2` |
| `sp1` | 65003 | a Speaker playing the hijacker | `10.0.13.2` |
| `r4` | 65004 | a customer of both `r1` and `r2` | `10.0.14.2` (to `r1`), `10.0.24.2` (to `r2`) |

`ca` plays the Regional Internet Registry. Its RPKI CA holds one Hosted CA
per AS, the account each AS's operator signs in to: `AS65001` holds
`10.1.0.0/24`, `AS65002` holds `10.2.0.0/24`, `AS65003` holds
`10.3.0.0/24` and `AS65004` holds `10.4.0.0/24`. Each can sign only for
what it holds. No ROA exists yet. The Validator on `rv` fetches what the
RPKI CA publishes, about every 10 seconds, and serves it to Routers.

Commands starting with `show`, `clear` or `configure` run in `vtysh`. Open
it with `vtysh` in a Router's Terminal and leave it with `exit`. Changes
you make with `vtysh` last until the Lab stops; do not `write memory`, or
you change the Lab for everyone who runs it after you.

### 1. An origin hijack wins

`sp1` announces `10.2.0.0/24` as if AS65003 owned it. On `r1`:

```text
show bgp ipv4 unicast 10.2.0.0/24
```

Two paths are listed: `65003` via `10.0.13.2` and `65002` via `10.0.12.2`.
Both are one AS long, so BGP falls through to its tie-breakers. `r1` is
set to `bgp bestpath compare-routerid`, and `sp1`'s router ID is lower, so
the hijack is marked `best (Router ID)`. There is no `rpki
validation-state` on either path yet: `r1` is not asking a Validator.

Follow the traffic from `h1`'s Terminal:

```text
ping -c 3 10.2.0.2
traceroute -n 10.2.0.2
```

The ping works, so all looks well. It is not: `traceroute` shows two hops,
`10.1.0.1` and then `10.2.0.2` straight away. The real `h2` sits behind
`r2`, one hop further, past `10.0.12.2`. `sp1` answers for `10.2.0.2`
itself. A hijacker that answers is harder to notice than one that drops
the traffic, and it can read everything sent to it.

### 2. Register a ROA at the RIR

A ROA (Route Origin Authorization) says which AS may originate a prefix,
up to which prefix length. AS65002's operator registers one at the RIR.
Click the `RPKI CA` badge on `ca`. The panel shows one Hosted CA at a
time: pick it under **Hosted CA**, as an operator signs in to their own
account. Each shows the prefixes it holds under **Resources**, its
**ROAs** and its **ASPAs**.

Act as AS65002's operator: choose `AS65002` under **Hosted CA**. Under
**Add a ROA at AS65002**, **Prefix** already reads `10.2.0.0/24`, the
prefix AS65002 holds, and **Origin** reads `AS65002`. Leave **maxLength**
empty, so the ROA allows `10.2.0.0/24` itself and nothing longer, and
press **Add ROA**. The ROA appears in AS65002's table: `10.2.0.0/24`,
`24`, `AS65002`.

A Hosted CA cannot sign for a prefix it does not hold. Try it as the
hijacker: choose `AS65003`, change **Prefix** to `10.2.0.0/24`, keep the
origin `AS65003`, and press **Add ROA**. The panel refuses it: `Hosted CA
AS65003 does not hold 10.2.0.0/24, so it cannot sign a ROA for it`. Only
the holder of a prefix can say who may originate it.

Register your own prefix too, as AS65001's operator: choose `AS65001`
and press **Add ROA** as it stands, `10.1.0.0/24` with origin `AS65001`.
Every network that protects itself with ROV should also have its own
ROAs.

Now check what the Validator made of it. Click the `Validator` badge on
`rv`. Within about 20 seconds **Validated ROA payloads (VRPs)** lists
`10.2.0.0/24`, `24`, `AS65002` and your `10.1.0.0/24`. A VRP is a ROA the
Validator fetched and checked against the RIR's signatures. The top of
the panel says when the Validator last fetched from the RPKI CA, and
**Connected Routers** still says `none`: no Router asks this Validator
yet. Leave the panel open; it updates on its own.

### 3. Point r1 at the Validator

On `r1`:

```text
configure terminal
rpki
 rpki polling_period 10
 rpki cache tcp 10.1.0.66 3323 preference 1
exit
end
show rpki cache-connection
show rpki prefix-table
```

Within a few seconds `show rpki cache-connection` ends in `(connected)`,
and the Validator panel lists `r1` under **Connected Routers**. The prefix
table holds `10.2.0.0  24 - 24  65002`, and your own `10.1.0.0` for
`65001`. Look at the prefix again:

```text
show bgp ipv4 unicast 10.2.0.0/24
show bgp ipv4 unicast
```

The hijack now reads `rpki validation-state: invalid`, and AS65002's path
`rpki validation-state: valid`. The table marks them `I` and `V`, and
prefixes no ROA covers `N` (`not found`). But the hijack is still `best`,
and `traceroute -n 10.2.0.2` from `h1` still stops at `sp1` after two
hops: **validation alone rejects nothing**. It only labels routes.

### 4. Deny invalid routes

`r1` already applies inbound route-maps to its neighbors: `FROM-PEER` for
`r2` and `sp1`, and `FROM-CUSTOMER` for `r4`. Put a deny ahead of each:

```text
configure terminal
route-map FROM-PEER deny 5
 match rpki invalid
exit
route-map FROM-CUSTOMER deny 5
 match rpki invalid
exit
end
```

FRR reapplies a changed route-map after about five seconds. Then:

```text
show bgp ipv4 unicast 10.2.0.0/24
show bgp ipv4 unicast neighbors 10.0.13.2 received-routes
```

Only the `65002` path is left, `valid` and `best`. `sp1`'s route still
arrives, but `received-routes` counts it as filtered. From `h1`,
`traceroute -n 10.2.0.2` now takes three hops, through `r2`
(`10.0.12.2`), to the real `h2`.

The other keywords are `match rpki valid` and `match rpki notfound`.
`notfound` is one word in a route-map, although `show bgp` prints `not
found`. Most operators deny only `invalid`: much of the Internet has no
ROA yet, and dropping `notfound` would drop it all.

### 5. A forged origin through a loose maxLength

A smarter hijacker announces a more specific prefix, and puts the real
origin at the end of the AS path so the origin check passes. Play the
hijacker: open `sp1`'s context menu in the Topology and choose **Open
Speaker**. Under **Announce a route**, keep the Neighbor `10.0.13.1` and
the next hop `10.0.13.2`, and fill in:

| Prefix | AS-PATH |
|---|---|
| `10.2.0.0/25` | `65003 65002` |

and press **Announce**. On `r1`:

```text
show bgp ipv4 unicast neighbors 10.0.13.2 received-routes
show bgp ipv4 unicast 10.2.0.0/25
```

The `/25` arrives, with origin `65002`, but it is filtered, so `show bgp
ipv4 unicast 10.2.0.0/25` answers `Network not in table`. The ROA allows
`10.2.0.0/24` only up to length 24, so a `/25` is `invalid` whatever its
origin. From `h1`, `traceroute -n 10.2.0.2` still goes through `r2`.

Now loosen the ROA, as an operator might "to be safe for later". In the
`RPKI CA` panel, choose `AS65002`, press **Edit** on its ROA, change
maxLength to `25` and press **Save ROA**. The Validator panel soon shows `10.2.0.0/24`
with maxLength `25`, and `r1`'s `show rpki prefix-table` shows `24 -  25`.
FRR updates each route's validation state when its VRPs change, but it
reruns inbound route-maps only when told to. So rerun them, on `r1`:

```text
clear bgp ipv4 unicast * soft in
show bgp ipv4 unicast 10.2.0.0/25
```

The `/25` is now `rpki validation-state: valid` and `best`. A `/25` beats
the `/24` on every Router, since the longest match wins. From `h1`,
`traceroute -n 10.2.0.2` stops at `sp1` after two hops again: the hijack
is back, and validation calls it `valid`. A ROA's maxLength should be
exactly the lengths its AS announces.

Tighten it again: **Edit** the ROA back to maxLength `24` and **Save
ROA**. Wait for `24 -  24` in `r1`'s prefix table, which takes up to
about 20 seconds. Then, on `r1`:

```text
show bgp ipv4 unicast 10.2.0.0/25
```

The `/25` reads `rpki validation-state: invalid`, but it is still `best`.
FRR relabelled the route when the VRP changed, but it did not run
`FROM-PEER` again: a route-map is applied when a route arrives, not when
its label changes. Run it again:

```text
clear bgp ipv4 unicast * soft in
show bgp ipv4 unicast 10.2.0.0/25
```

Now the `/25` is filtered (`Network not in table`). If it is still there,
the clear ran before the new VRP reached `r1`: check the prefix table and
run the clear once more. In `sp1`'s Speaker panel, expand **Announced** under the Neighbor and
press **Withdraw** on `10.2.0.0/25`.

### 6. A route leak

`r4` buys transit from both `r1` and `r2`. It should announce only its own
`10.4.0.0/24` to them, and the `TO-UPSTREAM` route-map does that. Make a
classic mistake on `r4`, dropping the filter toward `r1`:

```text
configure terminal
router bgp 65004
 address-family ipv4 unicast
  no neighbor 10.0.14.1 route-map TO-UPSTREAM out
end
```

`r4` now passes everything it learns from `r2` up to `r1`. On `r1`:

```text
show bgp ipv4 unicast 10.2.0.0/24
```

The new path `65004 65002` is `best (Local Pref)`: `r1` prefers routes
from its customers (`localpref 200`, set by `FROM-CUSTOMER`), as
providers usually do. It is also `rpki validation-state: valid`, because
the origin is right. Route Origin Validation cannot see a leak. From
`h1`, `traceroute -n 10.2.0.2` now goes through `r4` (`10.0.14.2`).

BGP Roles (RFC 9234) can. Each side of a session declares its
relationship, and a provider marks every route it sends to a customer
with the Only to Customer (OTC) attribute. A provider that receives a
route marked OTC from a customer knows the route has leaked, and drops
it. `r4` is the one that leaked, so leave it as it is. Declare the roles
on the two providers, and reset each session, since Roles take effect
when a session opens. On `r2`:

```text
configure terminal
router bgp 65002
 neighbor 10.0.24.2 local-role provider
end
clear bgp 10.0.24.2
```

On `r1`:

```text
configure terminal
router bgp 65001
 neighbor 10.0.14.2 local-role provider
end
clear bgp 10.0.14.2
```

After a few seconds, on `r4`:

```text
show bgp ipv4 unicast 10.2.0.0/24
```

The path from `r2` now carries `otc 65002`. `r4` still leaks it, but on
`r1` the leaked path is gone:

```text
show bgp ipv4 unicast 10.2.0.0/24
show bgp neighbors 10.0.14.2
```

Only `65002` via `10.0.12.2` remains, and the neighbor shows `Local Role:
provider`. `Remote Role: undefined` means `r4` declared nothing: the leak
was stopped without the leaker's help. Adding `strict-mode` to
`local-role` would refuse a session whose neighbor declares no role.

Put `r4`'s filter back:

```text
configure terminal
router bgp 65004
 address-family ipv4 unicast
  neighbor 10.0.14.1 route-map TO-UPSTREAM out
end
```

### 7. Reading: what if the Validator dies?

Nothing to type here, just numbers worth knowing. If `rv` stopped, `r1`
would lose `(connected)` on its cache connection, but it would keep every
VRP, and every route its `valid` or `invalid` state, for about **ten
minutes**. The RTR protocol (RFC 8210) gives cached VRPs an expiry, and
the Validator tells the Router what it is. This Lab's Validator says 600
seconds, and FRR will not go lower: `rpki expire_interval` accepts
600 to 172800 seconds. Only when that time runs out do the routes fall to
`not found`, and with `match rpki invalid` alone they are then accepted.

So a Validator outage does not open the door at once. It holds the last
known state, "fail-static", long enough to restart the Validator or fail
over to a second one. That is why operators run two Validators, with two
`rpki cache` lines at different preferences.
