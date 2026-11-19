## BGP 2 ISP Lab Guide

Use this guide in the web UI while the lab is running.
Open a Terminal for each Node by clicking it in the Topology.

Two ISPs interconnect and each sells transit to one customer. The
customers reach each other only because the ISPs exchange their customer
networks. Each ISP also has an upstream transit provider announcing
"Internet" prefixes, which it keeps to itself.

### What you will learn

- How eBGP Neighbors join five Autonomous Systems into one routed network
- How to read an AS_PATH, and what it tells you about the way a route came
- How an ISP uses the AS_PATH to export only its customer's networks to
  another ISP
- How a Router uses the AS_PATH to reject a route that has already been
  through its own Autonomous System
- What a BGP connection looks like on the wire as it opens: OPEN, KEEPALIVE,
  and UPDATE, and which path attributes an UPDATE carries

### The Lab at a glance

The Topology view shows how the Nodes are linked. The two ISPs, `isp1` and
`isp2`, are linked to each other. Each has its upstream Speaker on one side
and its customer on the other: an edge Router, two internal Routers and two
Hosts.

| Node | AS | Role |
|------|----|------|
| `tr1`, `tr2` | 4200, 4201 | Upstream transit providers (ExaBGP Speakers); each announces 24 "Internet" prefixes to its ISP |
| `isp1`, `isp2` | 65001, 65002 | ISPs: interconnect, and sell transit to one customer each |
| `r1`, `r2` | 65010, 65020 | Customer edge Routers: eBGP to their ISP, OSPF inside |
| `r11`, `r12`, `r21`, `r22` | 65010, 65020 | Customer internal Routers, OSPF only |
| `h11`, `h12`, `h21`, `h22` | — | Hosts on the customer networks `192.168.11.0/24` … `192.168.22.0/24` |

The eBGP Links, each a `/30`:

| Link | Addresses | Interfaces |
|------|-----------|------------|
| `tr1` ↔ `isp1` | `203.0.113.0` ↔ `.1` | `tr1-eth0` ↔ `isp1-eth0` |
| `isp1` ↔ `isp2` | `203.0.113.8` ↔ `.9` | `isp1-eth1` ↔ `isp2-eth1` |
| `r1` ↔ `isp1` | `203.0.113.12` ↔ `.13` | `r1-eth0` ↔ `isp1-eth2` |
| `tr2` ↔ `isp2` | `203.0.113.4` ↔ `.5` | `tr2-eth0` ↔ `isp2-eth0` |
| `r2` ↔ `isp2` | `203.0.113.16` ↔ `.17` | `r2-eth0` ↔ `isp2-eth2` |

Two Internet Gateways, `nat1` and `nat2`, connect `tr1` and `tr2` to the
network of the machine running the Lab, so the Lab can reach the outside
world. Each is drawn as an "Internet" cloud; it offers no Terminal.

Every Router's configuration is persistent, under
`frr-config/bgp-isp/<router>/frr.conf`.

### Step 1 — Confirm the Neighbors are up

In `isp1`'s Terminal:

```bash
vtysh -c "show ip bgp summary"
```

Three Neighbors, each with a prefix count under `State/PfxRcd` rather than
a state such as `Active`: `203.0.113.0` (`tr1`), `203.0.113.9` (`isp2`), and
`203.0.113.12` (`r1`).

### Step 2 — Read `r1`'s table

In `r1`'s Terminal:

```bash
vtysh -c "show ip bgp"
```

Read the `Path` column right to left: it is the list of Autonomous Systems
the route crossed, ending at the one that originated it.

- `0.0.0.0/0` with path `65001`: `isp1` originates a default route to its
  customer.
- Routes with paths starting `65001 4200`: the "Internet" `tr1` announced
  to `isp1`, passed on to `isp1`'s customer.
- `192.168.21.0/24` and `192.168.22.0/24` with path `65001 65002 65020`: the
  other customer's networks, through both ISPs.
- `192.168.11.0/24` and `192.168.12.0/24` with next hop `0.0.0.0`: `r1`'s
  own networks.

What is *not* there matters too: none of `tr2`'s prefixes (paths starting
`4201`). `isp2` never passes its transit routes to `isp1`. Step 5 of
Packet Analysis below shows why.

### Step 3 — Reach the other customer

In `h11`'s Terminal:

```bash
ping -c 3 192.168.21.100
```

`h21` answers. The path is `h11` → `r11` → `r1` → `isp1` → `isp2` → `r2`
→ `r21` → `h21`. OSPF carries `r1`'s default route to `r11`, and BGP
carries the customer networks between the Autonomous Systems.

### Success criteria

- `show ip bgp summary` on `isp1` shows three Neighbors, each with a
  prefix count
- `r1` has a route to `192.168.21.0/24` with path `65001 65002 65020`
- `r1` has no route whose path contains `4201`
- `h11` pings `192.168.21.100` with no loss

## Packet Analysis

Steps 1 and 2 showed the *outcome* of BGP: Neighbors in a table, routes
with paths. Here you watch the messages that produced it. Every BGP
connection is already up when the Lab starts, so you make one start again
with `clear ip bgp` and capture it re-opening.

### Step 1 — Open a Capture on `r1-eth0`

`r1-eth0` is `r1`'s end of its eBGP connection to `isp1`.

1. Click the Link between `r1` and `isp1` in the Topology and choose
   **Packet Capture**.
2. The dialog names both ends by Interface. Choose **`r1-eth0`**. The
   Packet Analysis pane opens beside your Terminals.
3. Pick the **BGP** preset. It stands for `tcp port 179`: BGP runs over a TCP
   connection to port 179, and the filter keeps everything else out.

Right away, KEEPALIVE rows appear, one each way about every second. They
are how each side knows the other is still there. This Lab sets a 3-second
hold time, so a Neighbor that sends nothing for 3 seconds is declared down.

### Step 2 — Reset the BGP connection from `r1`'s Terminal

With the pane in view, in `r1`'s Terminal:

```bash
vtysh -c "clear ip bgp *"
```

Within a few seconds the pane shows the connection close and re-open. Select
each row to read its fields under **Border Gateway Protocol**:

1. **NOTIFICATION** from `r1` (`203.0.113.12`), with `Major error Code:
   Cease` and `Minor error Code (Cease): Hard Reset`. This is `r1` closing
   the connection because you told it to.
2. **OPEN**, one from each side. `My AS` is `65010` from `r1` and `65001`
   from `isp1`. `Hold Time` is `3`. `BGP Identifier` is each Router's ID.
3. **KEEPALIVE**, one from each side, to accept the other's OPEN.
4. **UPDATE** messages, from both sides, carrying the routes.

Both Routers try to connect as soon as the connection drops, so you may also
see two OPENs each way and a second NOTIFICATION reading `Connection
Collision Resolution`. That is BGP keeping one of the two connections and
closing the other, not an error.

### Step 3 — Read the path attributes in an UPDATE

One row is one packet, and TCP packs several BGP messages into one packet
when it has them ready together. The UPDATEs that follow a reset do
exactly that: expand a row whose source is `isp1` (`203.0.113.13`) and you
find one **Border Gateway Protocol - UPDATE Message** section per message.

In an UPDATE from `isp1`, expand **Path attributes**. Each route is
announced with:

- `Path Attribute - ORIGIN: IGP`: how the route entered BGP.
- `Path Attribute - AS_PATH`: the Autonomous Systems it crossed. Find the
  UPDATE for `192.168.21.0/24` (under **Network Layer Reachability
  Information**): its AS_PATH is `65001 65002 65020`, the same path you
  read in `show ip bgp`, now on the wire.
- `Path Attribute - NEXT_HOP: 203.0.113.13`: where to send traffic for
  these routes. `isp1` rewrites it to its own address before sending over
  eBGP.

The UPDATE carrying `0.0.0.0/0` also has `Path Attribute - MULTI_EXIT_DISC:
0`, which `isp1` adds to the default route it originates.

**Look for LOCAL_PREF: it is not there.** LOCAL_PREF is an Autonomous System's
own preference among its exit points, so it is only sent to iBGP Neighbors
(Routers inside the same Autonomous System), never across the boundary
between two. Every Neighbor in this Lab is eBGP, so no UPDATE you capture
here carries it. The BGP Lab (3 AS) has iBGP inside each Autonomous System, and its
Guide shows LOCAL_PREF on the wire, and changing it moving traffic.

### Step 4 — Watch `r1` reject its own networks

Among `isp1`'s UPDATEs, find the one for `192.168.11.0/24` and
`192.168.12.0/24`. Its AS_PATH is `65001 65010`. `isp1` is announcing
`r1`'s own networks back to `r1`.

`r1` sees its own Autonomous System, `65010`, in the path, and drops the
route. That is BGP's loop prevention. Confirm it in `r1`'s Terminal:

```bash
vtysh -c "show ip bgp 192.168.11.0/24"
```

One path, `r1`'s own. The one `isp1` just sent was never accepted.

### Step 5 — Watch the ISPs exchange only customer networks

Stop the first Capture, or keep it: two can run at once.

1. Click the Link between `isp1` and `isp2` and choose **Packet Capture**.
2. Choose **`isp1-eth1`** and pick the **BGP** preset.
3. In `isp1`'s Terminal, reset only its connection to `isp2`:

```bash
vtysh -c "clear ip bgp 203.0.113.9"
```

Read the AS_PATH in each UPDATE:

- From `isp1` (`203.0.113.8`): only `65001 65010`, `r1`'s networks.
- From `isp2` (`203.0.113.9`): only `65002 65020`, `r2`'s networks.

Neither ISP sends the 24 prefixes it learned from its transit provider.
That is the policy in each ISP's `frr.conf`: a route-map, `PEER-ISP-OUT`,
applied outbound to the other ISP, that matches the AS_PATH against
`_65010_` (on `isp1`) or `_65020_` (on `isp2`) and exports only what
matches. Interconnected ISPs exchange their customers' routes for free;
they do not give each other free transit. That is why `r1` has no route
with `4201` in its path.

Each ISP applies the same route-map outbound to its own transit provider
too, so `tr1` and `tr2` are offered only customer routes as well.

### Download the Capture

Click **Download pcap** (⤓ in a pane's header) to open the Capture in
Wireshark. The pcap is deleted when the Lab stops, so download it before
you finish.

### Success criteria

- A Capture on `r1-eth0` with the **BGP** preset shows KEEPALIVEs before
  you do anything
- After `clear ip bgp *` on `r1`, it shows a NOTIFICATION (`Hard Reset`),
  an OPEN from each side naming `My AS` `65010` and `65001`, and then UPDATEs
- An UPDATE from `isp1` carries ORIGIN, AS_PATH, and NEXT_HOP, and none
  carries LOCAL_PREF
- `isp1` sends `r1` a path of `65001 65010`, and `r1` does not install it
- On `isp1-eth1`, after `clear ip bgp 203.0.113.9` on `isp1`, `isp1` sends
  only paths `65001 65010` and `isp2` only `65002 65020`

## NAT

`isp1` and `isp2` also rewrite their customers' source addresses toward their
transit providers. That plumbing carries the Lab's traffic to the outside
world; this Lab does not teach it. To read a NAT Table, open the **NAT Lab**
(`nat-lab`) from the NAT Area of the Labs list.

## Troubleshooting

**`show ip bgp summary` shows `Active` or `Connect` instead of a count**

The BGP connection is not up yet. The Lab's BGP timers are short, so wait a few
seconds and run it again.

**The pane shows only KEEPALIVEs after `clear ip bgp`**

You cleared a different Router from the one you are capturing, or a
different Neighbor. `clear ip bgp *` must run on `r1`
for a Capture on `r1-eth0`; `clear ip bgp 203.0.113.9` must run on `isp1`
for a Capture on `isp1-eth1`.

**The pane shows nothing at all**

Check the filter under the presets reads `tcp port 179`. If you typed your
own expression and it was invalid, the pane says so and the Capture ends;
pick the **BGP** preset again.
