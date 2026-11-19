## Static Routing with Internet Access Guide

Use this guide in the web UI while the Lab is running. It follows one static
path end to end: each Host's default route points at the Router `r1`; `r1`
routes between its two connected Host Subnets; and `r1`'s default route points
at the Internet Gateway, drawn as an "Internet" cloud, so everything else
leaves the Lab for the real Internet. For address translation itself, see the
NAT Lab (`nat-lab`).

### The Lab at a glance

The Topology view shows how the Nodes are linked. `h1` and `h2` each have a
Subnet of their own on `r1`, and `r1`'s uplink leads through the Internet
Gateway to the real Internet.

| Node / Interface | IPv4 | Role |
|---|---|---|
| `h1-eth0` | `100.64.1.2/24` | Host on Subnet 1 |
| `r1-eth1` | `100.64.1.1/24` | Subnet 1 gateway |
| `h2-eth0` | `100.64.2.2/24` | Host on Subnet 2 |
| `r1-eth2` | `100.64.2.1/24` | Subnet 2 gateway |
| `r1-eth0` | `100.64.0.2/24` | Uplink to `100.64.0.1` |

### 1. Inspect the static forwarding path

Click `h1`, `h2`, and `r1` in the Topology to open their Terminals.
On `h1`, run `ip route`; its default points to `100.64.1.1`.
On `h2`, run `ip route`; its default points to `100.64.2.1`.
On `r1`:

```bash
ip -br address
ip route
vtysh -c 'show ip route'
```

The two Host Subnets are directly connected. The default route goes via
`100.64.0.1` on `r1-eth0`. There is no routing protocol to converge.
In `show ip route` that default is marked `K` (kernel), not `S` (static): the
Lab installs it in the kernel when it links `r1` to the Internet Gateway, and
FRR shows the kernel's routes alongside its own.

### 2. Check the local routed Subnets

On `h1`:

```bash
ping -c 3 100.64.2.2
```

On `h2`:

```bash
ping -c 3 100.64.1.2
```

Expect three replies in each direction. `r1` routes between its connected
Subnets without rewriting this traffic.

### 3. Send traffic to the Internet

On `h1`:

```bash
curl --noproxy '*' -4 -I --connect-timeout 5 --max-time 10 https://example.com
```

An HTTP response confirms the outbound path. This step needs working
Internet access on the machine running the Lab. If it fails, first check
`r1`'s default and the host's Internet access; local pings in section 2
remain a separate check.

### 4. Finish with a working Lab

Repeat the local pings from section 2. Both Subnets should still be
reachable. Stop the Lab when finished.
