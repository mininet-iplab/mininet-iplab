# ExaBGP Integration

This project includes an experimental ExaBGP integration to run ExaBGP speakers
in Mininet namespaces and peer them with FRR routers.

## Quick Start

```bash
python3 examples/bgp-ispixp-lab.py --enable-web
```

## Requirements

- **ExaBGP 5.0.13 or later** must be available in `PATH` (`pip install "exabgp>=5.0.13,<6"`
  or your distribution package manager). The 4.x line is not supported.
- **FRR** (Free Range Routing) installed and running.
- **Root privileges** required (Mininet uses network namespaces).

## How It Works

The ExaBGP integration creates a host node that runs ExaBGP as an external process
in its namespace. The integration handles:

- **Configuration generation**: Uses Mako templates to render `exabgp.conf`.
- **Directory mapping**: Bind-mounts host config/log/run directories into the namespace.
- **FIFO pipes**: Creates `/var/run/exabgp/exabgp.in` and `.out` for CLI access before
  the daemon starts; nothing has to be run by hand.
- **Lifecycle management**: Starts the daemon with `--env-file /etc/exabgp/exabgp.env`
  (so it runs as root with the CLI channel on), stops it, and cleans up resources.

Every neighbor is rendered **passive** with its own `local-address`: the speaker listens
and the FRR router's `neighbor … remote-as` line opens the session. Two neighbors on one
speaker therefore may not share a `local-address`; `addExaBGP` refuses such a
configuration, naming both neighbors.

## Configuration

### Basic Setup

```python
from mniplab.exabgp import (
    BGPRoute,
    BGPNeighbor,
    make_v4_peer_routes,
)

# Define a BGP neighbor (peer)
neighbor = BGPNeighbor(
    peer='10.0.0.1',
    local_address='10.0.0.2',
    local_as=65002,
    peer_as=65001
)

# Generate IPv4 routes for the peer
routes = make_v4_peer_routes(
    num_prefixes=20,
    local_asn=65002,
    next_hop='10.0.0.2'
)

# Create ExaBGP node
isp1 = net.addExaBGP(
    'isp1',
    exabgp_dir='./frr-config/my-speakers/%(name)s',
    neighbors=[neighbor],
    routes=routes,
    local_as=65002
)
```

Alternatively, create routes manually:

```python
from mniplab.exabgp import BGPRoute

route = BGPRoute(
    prefix='192.0.2.0/24',
    next_hop='10.0.0.2',
    as_path=[65002, 65003]  # Optional: specify AS-PATH
)
```

### Configuration Files

ExaBGP configuration is stored in the directory specified by `exabgp_dir`. Files include:

- **exabgp.conf**: Main configuration (auto-generated from template).
- **exabgp.env**: Daemon settings, passed to the daemon with `--env-file`.

Config files are written to `./frr-config/<lab-name>/<node-name>/`.

## Runtime Operations

### Announce Routes via CLI FIFO

The FIFOs exist as soon as the node is created, so a route can be announced
straight away:

```bash
# Send commands through the FIFO pipe
ex1 bash -c "printf 'announce route 10.1.0.0/24 next-hop 10.0.0.2\n' > /var/run/exabgp/exabgp.in"
```

ExaBGP's own client, `exabgp cli`, talks to the same pipes from inside the node:

```bash
ex1 exabgp cli show neighbor summary
```

### Check ExaBGP Status

From the `mininet-iplab>` prompt, `show_speaker <node>` lists a Speaker's
Neighbors with their session state and uptime, then what the Speaker has
announced to each and what each has sent back, in ExaBGP's own words:

```bash
mininet-iplab> show_speaker ex1
```

The Web UI reads the same picture from `GET /api/labs/{key}/speakers/{node}` (see
[api-reference.md](api-reference.md)). Both go through the Speaker's control
pipe, so a daemon that is not running is reported as such rather than shown
empty.

### Announce and Withdraw

The same grammar, from the prompt, the Speaker panel in the Web UI, or
`POST /api/labs/{key}/speakers/{node}/announce` and `…/withdraw`:

```bash
mininet-iplab> announce ex1 neighbor 10.0.0.1 10.1.0.0/24 next-hop 10.0.0.2 as-path 65001 65002 community no-export med 50
mininet-iplab> announce ex1 10.1.1.0/24 next-hop 10.0.0.2        # every Neighbor carrying IPv4
mininet-iplab> withdraw ex1 neighbor 10.0.0.1 10.1.0.0/24
mininet-iplab> withdraw ex1 neighbor 10.0.0.1 all                 # clear adj-rib out: the Lab's routes go too
mininet-iplab> announce ex1 neighbor 10.0.0.1 generate 20         # a burst of the Lab Examples' kind of routes
mininet-iplab> announce ex1 neighbor 10.0.0.1 generate 5 sub-prefixes 3 prefix-seed 42 as-path-seed 7
```

`generate` drives the same random-route generator the Lab Examples use
(`make_v4_peer_routes` / `make_v6_peer_routes`) with the Neighbor's local AS,
local address and family, announces each route, and prints them; seeds make
a burst repeatable, and `withdraw … all` clears it.

A route is validated before it reaches ExaBGP: the next-hop must be the
prefix's family and the Neighbor must carry it, since ExaBGP accepts a
cross-family route and flaps the session on it. Nothing is stored by the
framework — ExaBGP's memory is the only record, and a Lab restart is the
reset. `help announce` and `help withdraw` show both forms.

```bash
# Inside FRR router
r1 vtysh -c 'show ip bgp summary'

# Check ExaBGP logs
tr1 tail -f /var/log/exabgp/tr1.log
```

## Troubleshooting

### ExaBGP binary not found

Install ExaBGP 5:
```bash
pip install "exabgp>=5.0.13,<6"
```

### BGP session not established

- Check that FRR daemons are running: `r1 vtysh -c 'show ip bgp summary'`
- Verify IP connectivity: `r1 ping 10.0.0.2`
- The speaker is passive: the FRR side must carry `neighbor <local-address> remote-as <as>`.
- Check logs: `tr1 tail -f /var/log/exabgp/tr1.log`
- FRR daemons sometimes need a few seconds to start inside namespaces.

### ExaBGP process crashes

Check the ExaBGP log file for errors:
```bash
tr1 tail -f /var/log/exabgp/tr1.log
```

Ensure `/var/run/exabgp` exists and has writable permissions.

## Example Lab

See [examples/bgp-ispixp-lab.py](../examples/bgp-ispixp-lab.py) for a
complete working Lab Example with transit Speakers `tr1` and `tr2`:

- Each Speaker has a Neighbor on each upstream Router, `up1` and `up2`.
- Per-Neighbor Routes give each upstream a reproducible set of prefixes.
- `make_v4_peer_routes()` declares the Route Announcements.

### Running the Example

```bash
python3 examples/bgp-ispixp-lab.py
```

Then inside the Lab Prompt:

```text
show_speaker tr1
up1 vtysh -c 'show ip bgp summary'
up1 vtysh -c 'show ip bgp'
up1 vtysh -c 'show ip bgp neighbors 10.0.5.2 routes'
```

`tr1`'s local address toward `up1` is `10.0.5.2`, and its Neighbor is
`10.0.5.1`. Use those addresses when adapting the generic FIFO and
announcement commands above. From a Node Terminal, omit the Node-name prefix.

## Route Generation Utilities

The `mniplab.exabgp` module provides utilities for generating test routes:

### IPv4 Route Generators

#### make_v4_routes(num_prefixes=20)

Creates random public IPv4 prefixes with randomized prefix lengths (8-24 bits) and
diverse AS-PATH attributes. Useful for general-purpose BGP testing.

```python
from mniplab.exabgp import make_v4_routes

routes = make_v4_routes(10)
```

#### make_v4_peer_routes(num_prefixes, local_asn, next_hop, seed=None)

Creates random IPv4 routes for a specific AS number with optional seeding for reproducible output.
Ideal for multi-peer scenarios where different speakers announce different prefixes.

```python
from mniplab.exabgp import make_v4_peer_routes

peer1_routes = make_v4_peer_routes(15, local_asn=65001, next_hop='10.0.1.2', prefix_seed=42)
peer2_routes = make_v4_peer_routes(15, local_asn=65002, next_hop='10.0.2.2', prefix_seed=43)
```

### IPv6 Route Generators

#### make_v6_routes(num_prefixes=20)

Creates random IPv6 prefixes from the documentation range (2001:db8::/32) with prefix
lengths between /32 and /48. Includes diverse AS-PATH attributes for BGP testing.

```python
from mniplab.exabgp import make_v6_routes

routes = make_v6_routes(10)
```

#### make_v6_peer_routes(num_prefixes, local_asn, next_hop, seed=None)

Creates random IPv6 routes for a specific AS number with optional seeding for reproducible output.
Ideal for IPv6 BGP testing scenarios.

```python
from mniplab.exabgp import make_v6_peer_routes

peer1_routes = make_v6_peer_routes(
    num_prefixes=10,
    local_asn=65001,
    next_hop='2001:db8:1::2',
    prefix_seed=42
)
```

## IPv6 BGP Peering

ExaBGP supports IPv6 BGP peering via the `address_family` attribute in `BGPNeighbor`. The address
family is auto-detected from the peer address (IPv4 vs IPv6).

### IPv6 Example

```python
from mniplab.exabgp import BGPNeighbor, make_v6_peer_routes

# Generate IPv6 routes
routes = make_v6_peer_routes(
    num_prefixes=5,
    local_asn=65001,
    next_hop='2001:db8:1::2',
    prefix_seed=42
)

# Create IPv6 BGP neighbor (address_family auto-detected from peer address)
neighbor = BGPNeighbor(
    peer='2001:db8:1::1',           # IPv6 peer
    local_address='2001:db8:1::2',  # IPv6 local address
    local_as=65001,
    peer_as=65000,
    routes=routes
)

# Add ExaBGP node
isp1 = net.addExaBGP('isp1', neighbors=[neighbor], local_as=65001)
```

**FRR Router Configuration for IPv6:**

```bash
./util/connect_node.sh r1 vtysh -c "configure terminal" \
  -c "router bgp 65000" \
  -c " bgp router-id 10.0.1.1" \
  -c " neighbor 2001:db8:1::2 remote-as 65001" \
  -c " address-family ipv6 unicast" \
  -c "  neighbor 2001:db8:1::2 activate" \
  -c "  network 2001:db8:2::/64" \
  -c "  exit-address-family" \
  -c "write memory"
```

**Verify IPv6 BGP:**

```bash
# Check IPv6 BGP summary
./util/connect_node.sh r1 vtysh -c "show bgp ipv6 summary"

# View IPv6 routes
./util/connect_node.sh r1 vtysh -c "show bgp ipv6 unicast"
```

The IPv6 declaration above can be used in an authored IPv6 or dual-stack Lab.
The transit Speakers in [bgp-ispixp-lab.py](../examples/bgp-ispixp-lab.py)
show the same Neighbor and Per-Neighbor Routes interfaces using IPv4.

