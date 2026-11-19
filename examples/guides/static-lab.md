## Static Routing Lab Guide

Use this guide in the web UI while the Lab is running. In the Topology, click a Host or Router to open
its Terminal. Commands below run in that Node's Terminal.

### What you will learn

Read IPv4 and IPv6 static routes side by side, follow a packet through two
Routers, and restore connectivity after a link failure. This Lab has no
routing protocol: the two Routers' Seeds supply the remote routes.

### The Lab at a glance

The Topology view shows how the Nodes are linked. They form one line, `h1` –
`r1` – `r2` – `h2`. Their addresses:

| Node / Interface | IPv4 | IPv6 |
|---|---|---|
| `h1-eth0` | `192.168.1.2/24` | `2001:db8:1::2/64` |
| `r1-eth0` | `192.168.1.1/24` | `2001:db8:1::1/64` |
| `r1-eth1` | `10.10.1.1/30` | `2001:db8:10::1/64` |
| `r2-eth1` | `10.10.1.2/30` | `2001:db8:10::2/64` |
| `r2-eth0` | `192.168.2.1/24` | `2001:db8:2::1/64` |
| `h2-eth0` | `192.168.2.2/24` | `2001:db8:2::2/64` |

### 1. Inspect addresses and Host default routes

Open Terminals on `h1`, `r1`, `r2`, and `h2`. On `h1`, run:

```bash
ip -br address
ip route
ip -6 route
```

Its defaults point to `192.168.1.1` and `2001:db8:1::1`, respectively.
Run the same commands on `h2`: its gateways end in `2.1` and `2::1`.

### 2. Read both Routers' static routes

On `r1`:

```bash
vtysh -c 'show ip route'
vtysh -c 'show ipv6 route'
```

Look for `S>*` entries: `192.168.2.0/24` via `10.10.1.2` and
`2001:db8:2::/64` via `2001:db8:10::2`, both on `r1-eth1`.
On `r2`, run the same commands. Its return routes are `192.168.1.0/24`
via `10.10.1.1` and `2001:db8:1::/64` via `2001:db8:10::1`, on `r2-eth1`.
A static route needs the next hop in the destination's address family.
Both directions need a route, because replies also cross both Routers.

### 3. Send IPv4 and IPv6 traffic

On `h1`:

```bash
ping -c 3 192.168.2.2
ping -6 -c 3 2001:db8:2::2
```

On `h2`:

```bash
ping -c 3 192.168.1.2
ping -6 -c 3 2001:db8:1::2
```

Each should receive all three replies. The packet path in either family is
`h1 → r1 → r2 → h2`; the reply follows it backwards.

### 4. Trace both paths

On `h1`:

```bash
traceroute -n -w 1 -q 1 192.168.2.2
traceroute -6 -n -w 1 -q 1 2001:db8:2::2
```

Expect three hops: the first Router's Host-facing address, the second
Router's transit address, and the destination Host. IPv4 uses
`192.168.1.1 → 10.10.1.2 → 192.168.2.2`; IPv6 uses
`2001:db8:1::1 → 2001:db8:10::2 → 2001:db8:2::2`.

### 5. Cut the transit Link, then restore it

Click the Link between `r1` and `r2` in the Topology and choose
**Disconnect Link**. On `h1`, repeat both pings from section 3;
they fail because neither family has a transit path.

Before continuing, click the same Link and choose **Reconnect Link**. Repeat both pings;
after the Interfaces return, all replies should arrive. Read both route
tables again to see the static routes installed. No manual route repair
is needed when reconnecting this Link through the Topology.

### 6. Check forwarding and finish

On each Router:

```bash
vtysh -c 'show interface'
vtysh -c 'show running-config'
```

The connected Interfaces are up and each remote Subnet has its static
route. If a ping fails, compare the addresses with the table above, check
both Host defaults, and inspect both Routers' return routes. The Web UI's
**Network Tools → Ping** and **Traceroute** offer the same checks.

Leave the transit Link up. Stop the Lab when finished.
