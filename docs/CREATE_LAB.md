# Creating a Lab with Mininet-IPLab

This guide walks you through creating your own IP routing lab using Mininet-IPLab. Whether you're building a simple static routing topology or a complex BGP network, this guide covers all the essential steps.

This is the Python path: you write the Lab script yourself. Prefer a canvas?
An Instructor on the classroom server (`python -m mniplab serve`) can draw the
Lab in the browser with Lab Authoring, try it in a Preview Lab and Publish it
into the Lab Catalog. **Download .zip** exports the complete native package
and runnable folder, including the original source mode and supporting files;
**Import package…** restores it as a new unpublished draft. A Python draft can
edit its script and FRR Seeds in the Script pane, or replace the script while
keeping its draft identity. External Python imports still need their runtime
dependencies installed separately. See [lab-authoring.md](lab-authoring.md)
for the browser workflow, source-mode choices and package limits.

## Table of Contents

1. [Lab Structure](#lab-structure)
2. [Basic Anatomy](#basic-anatomy)
3. [Step-by-Step Tutorial](#step-by-step-tutorial)
4. [Static Routing Labs](#static-routing-labs)
5. [Dynamic Routing Labs](#dynamic-routing-labs)
6. [IPv6 Support](#ipv6-support)
7. [Services](#services)
8. [Container Hosts](#container-hosts)
9. [Running Your Lab](#running-your-lab)
10. [Debugging Tips](#debugging-tips)

## Lab Structure

A Mininet-IPLab lab is a Python script that:
1. Builds a network topology (routers, hosts, links)
2. Configures routing protocols and addresses
3. Starts and runs an interactive CLI or web UI

For labs shown in the Web UI, you can also provide companion files in the
`examples/` directory:

- `layouts/<lab-id>.json` for saved node positions
- `guides/<lab-id>.md` for the in-app Lab Guide pane

### File Organization

```
my-lab/
├── my-lab.py                 # Main lab script
└── frr-config/my-lab/        # (Optional) Persistent FRR configs
    ├── r1/
    │   └── frr.conf
    └── r2/
        └── frr.conf
```

A Router's Seed folder holds only `frr.conf`. `daemons`, `vtysh.conf` and
`support_bundle_commands.conf` are rendered from its `proto` every time it
starts — in CLI Mode into the Seed folder itself, where the repository's
`.gitignore` keeps them out of version control.

    When adding a lab under `examples/`, use this naming convention for companion
    Web UI assets:

    ```text
    examples/
    ├── my-lab.py
    ├── layouts/
    │   └── my-lab.json       # Optional node layout for topology view
    └── guides/
        └── my-lab.md         # Optional markdown guide for Guide tab
    ```

    The Guide tab automatically looks up `guides/<lab-id>.md` while the lab is
    running. If not present, the UI shows that no guide is available.

## Basic Anatomy

Every lab has this structure:

```python
#!/usr/bin/env python3
"""Description of your lab."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.log import info
from mniplab import mnIPLab, parse_lab_args, run_lab

def build_network():
    """Build your network topology."""
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)
    
    info('*** Add Routers\n')
    # Add routers here
    
    info('*** Add Hosts\n')
    # Add hosts here
    
    info('*** Add Links\n')
    # Connect nodes with links
    
    return net

if __name__ == '__main__':
    args = parse_lab_args('Lab Title')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_host=args.web_host,
            web_port=args.web_port)
```

## Step-by-Step Tutorial

### 1. Create Your Lab File

Create a new Python file in the `examples/` directory:

```bash
touch examples/my-lab.py
```

### 2. Add Routers

Use `net.addRouter()` to add routers to your topology:

```python
info('*** Add Routers\n')
r1 = net.addRouter('r1')
r2 = net.addRouter('r2')
r3 = net.addRouter('r3')
```

**Router Parameters:**

- `name` (required): Router identifier (e.g., `'r1'`, `'r2'`). Must be unique within the network.
- `proto`: Protocol(s) to enable. Multiple formats accepted:
  - Single: `'OSPF'`
  - Comma-delimited: `'OSPF,BGP'`
  - Pipe-delimited: `'OSPF|BGP'`
  - List: `['OSPF', 'BGP']`
  - Available protocols: `OSPF`, `OSPF6`, `BGP`, `ISIS`, `RIP`, `RIPNG`, `EIGRP`, `BABEL`, `LDP`, `PIM`, `BFD`
  - Default: none (L2/L3 forwarding only, no routing daemon)
- `frr_dir`: Directory path for FRR config files. Supports `%(name)s` placeholder.
  - Default (`None`): ephemeral under the Lab directory (`<MNIPLAB_TMP_DIR>/labs/<key>/nodes/<name>/etc/frr`) — regenerated on each `addRouter()`
    and deleted when `net.stop()` is called, so a lab never inherits a previous lab's daemons.
  - Persistent: `'./frr-config/my-lab/%(name)s'` — its `frr.conf` is never overwritten, suitable for version control. The folder holds only `frr.conf`; the other FRR files are rendered from `proto` on every start.
    Under the web UI's Multi-Lab Mode this is the seed each Group's own copy starts from;
    see [Persistent Configurations](#persistent-configurations).
- `frr_config` (kwargs): Custom FRR configuration string appended to `frr.conf`. Use for router-specific settings (e.g., OSPF area, BGP peer definitions).
- `private_dirs` (kwargs): Override the list of private (bind-mounted) directories for the router namespace. Defaults to the Lab's router bindings (`/var/log`, `/etc/frr`, `/var/run`, `/etc/hosts`, `/etc/hostname`, `/etc/resolv.conf`, …), each backed from the Lab directory.

**Example with protocols:**

```python
r1 = net.addRouter('r1', proto='OSPF')
r2 = net.addRouter('r2', proto='BGP')
r3 = net.addRouter('r3', proto='OSPF,BGP')
```

### 3. Add Hosts

Use `net.addHost()` to add end nodes (servers, clients):

```python
info('*** Add Hosts\n')
h1 = net.addHost('h1', defaultRoute='via 192.168.1.1')
h2 = net.addHost('h2', defaultRoute='via 192.168.2.1')
h3 = net.addHost('h3', defaultRoute='via 10.0.0.1')
```

**Host Parameters:**

- `name` (required): Host identifier (e.g., 'h1', 'h2')
- `defaultRoute`: IPv4 default gateway (e.g., `'via 192.168.1.1'`)
- `defaultRoute6`: IPv6 default gateway (e.g., `'via 2001:db8:1::1'`)

A gateway is a Host-wide setting: it sits in the Host's routing table and
leaves by one of its Interfaces. Without `dev` that is the Host's
lowest-numbered Interface, usually `h1-eth0`; name another with
`dev`, as in `defaultRoute='via 192.168.2.1 dev h1-eth1'`, or give Mininet's
bare Interface name, `defaultRoute='h1-eth1'`. The route is installed naming
its device, so an IPv6 link-local gateway such as `defaultRoute6='via fe80::1'`
works too. The Interface is chosen at `start()`, once every Link exists, so the
order of `addLink` calls never changes it.

`start()` refuses, before any Host's route is installed, a gateway whose
Interface does not exist and one that leaves by a DHCP client Interface of its
own family: `defaultRoute` by a `dhcp4` one, `defaultRoute6` by a `dhcp6` one.
A DHCP client on another Interface, or of the other family, leaves the gateway
as declared. A Host that declares no gateway gets none: mnIPLab does not pick a
Router from the topology, so the Learner gets the route from DHCP, a Router
Advertisement or their own `ip route add`. The lowest-numbered default and the
DHCP refusal are mnIPLab's teaching conventions, not protocol rules; see
[DHCP](DHCP.md#a-hosts-gateway-beside-a-dhcp-client).

A Host carries only Node-wide settings. It gets no address of its own — not
even Mininet's automatic `10.0.0.N/8` — and `ip=` or `ip6=` on `addHost` is
refused: see [Where addresses go](#4-add-links) at the top of the next step.
Leave a Link end without addresses when the Learner is to assign them.

### 4. Add Links

> **Where addresses go.** An address is declared once, where its Interface is
> declared. A Node declares only what applies to the whole Node.
>
> | On the Node (`addHost`, `addRouter`, …) | On the Interface |
> |---|---|
> | `defaultRoute` (IPv4), `defaultRoute6` (IPv6) | A Link end: `params1`/`params2` with `ip` (IPv4) and `ip6` (IPv6) |
> | `nameservers` | A VLAN sub-interface: `add_subinterface(…, ip=…, ip6=…)` |
> | `proto` | A Router's loopback: `interface lo` in its Seed |
> | An Internet Gateway's `subnet=` (the inside prefixes it serves) | |
>
> A Node-level address would not say which Interface it is for, and it could
> silently disagree with the Link end's, so every `add*` call refuses `ip=` and
> `ip6=` with the line to write instead. Each family has one key: an IPv6 value
> under `ip`, or an IPv4 gateway under `defaultRoute6`, is refused too. A
> dual-stack end carries both `ip` and `ip6`.
>
> Moving a Mininet script over:
>
> ```python
> # Mininet
> h1 = net.addHost('h1', ip='192.168.1.2/24', defaultRoute='via 192.168.1.1')
> net.addLink(h1, r1)
>
> # mnIPLab
> h1 = net.addHost('h1', defaultRoute='via 192.168.1.1')
> net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
> ```

Connect nodes using `net.addLink()`:

```python
info('*** Add Links\n')
# Host h1 to Router r1: each end's address, declared once
net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})

# Host h2 to Router r2
net.addLink(h2, r2, params1={'ip': '192.168.2.2/24'}, params2={'ip': '192.168.2.1/24'})

# Router r1 to Router r2 (inter-router link)
net.addLink(r1, r2, 
            params1={'ip': '10.10.1.1/30'}, 
            params2={'ip': '10.10.1.2/30'})

# Router r2 to Router r3
net.addLink(r2, r3,
            params1={'ip': '10.10.2.1/30'},
            params2={'ip': '10.10.2.2/30'})
```

**Link Parameters:**

- `params1`: the first Node's Interface — `ip`, `ip6`, `dhcp4`, `dhcp6`, `mac`
- `params2`: the second Node's Interface, with the same keys
- This is the one place an Interface's addresses are declared. A Switch end
  takes none (only `vlan`, `trunks`, `native`); an address there is refused.

**Address Planning Tips:**

- Use private IP ranges:
  - `10.0.0.0/8`
  - `172.16.0.0/12`
  - `192.168.0.0/16`
- Use `/24` for host networks
- Use `/30` for router-to-router links (4 addresses, 2 usable)
- Reserve higher addresses for loopbacks and management

## Static Routing Labs

For labs without dynamic routing protocols, give each Router its static routes
in FRR's syntax with `frr_config`, and each Host its gateway:

```python
def build_network():
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)
    
    info('*** Add Routers with static routes\n')
    r1 = net.addRouter('r1', frr_config='ip route 192.168.2.0/24 10.10.1.2\n')
    r2 = net.addRouter('r2', frr_config='ip route 192.168.1.0/24 10.10.1.1\n')
    
    info('*** Add Hosts\n')
    h1 = net.addHost('h1', defaultRoute='via 192.168.1.1')
    h2 = net.addHost('h2', defaultRoute='via 192.168.2.1')
    
    info('*** Add Links\n')
    net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
    net.addLink(h2, r2, params1={'ip': '192.168.2.2/24'}, params2={'ip': '192.168.2.1/24'})
    net.addLink(r1, r2, params1={'ip': '10.10.1.1/30'}, params2={'ip': '10.10.1.2/30'})
    
    return net
```

## Dynamic Routing Labs

### OSPF Example

```python
def build_network():
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)
    
    info('*** Add Routers with OSPF\n')
    r1 = net.addRouter('r1', proto='OSPF', frr_dir='./frr-config/my-lab/%(name)s')
    r2 = net.addRouter('r2', proto='OSPF', frr_dir='./frr-config/my-lab/%(name)s')
    r3 = net.addRouter('r3', proto='OSPF', frr_dir='./frr-config/my-lab/%(name)s')
    
    info('*** Add Hosts\n')
    h1 = net.addHost('h1', defaultRoute='via 192.168.1.1')
    h2 = net.addHost('h2', defaultRoute='via 192.168.2.1')
    h3 = net.addHost('h3', defaultRoute='via 192.168.3.1')
    
    info('*** Add Links\n')
    net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
    net.addLink(h2, r2, params1={'ip': '192.168.2.2/24'}, params2={'ip': '192.168.2.1/24'})
    net.addLink(h3, r3, params1={'ip': '192.168.3.2/24'}, params2={'ip': '192.168.3.1/24'})
    net.addLink(r1, r2, params1={'ip': '10.10.1.1/30'}, params2={'ip': '10.10.1.2/30'})
    net.addLink(r2, r3, params1={'ip': '10.10.2.1/30'}, params2={'ip': '10.10.2.2/30'})
    
    info('*** Configure OSPF\n')
    r1.add_frr_config("""
router ospf
 ospf router-id 1.1.1.1
 network 192.168.1.0/24 area 0
 network 10.10.1.0/30 area 0
exit
""")
    
    r2.add_frr_config("""
router ospf
 ospf router-id 2.2.2.2
 network 192.168.2.0/24 area 0
 network 10.10.1.0/30 area 0
 network 10.10.2.0/30 area 0
exit
""")
    
    r3.add_frr_config("""
router ospf
 ospf router-id 3.3.3.3
 network 192.168.3.0/24 area 0
 network 10.10.2.0/30 area 0
exit
""")
    
    return net
```

With FRR's default timers, OSPF needs up to about 50 seconds after the Lab
starts before routes appear: on a broadcast Link each Router waits 40 seconds
before electing a Designated Router. Give it that long before testing; the Lab
Examples shorten the timers (`ip ospf hello-interval` and `dead-interval` on
each Interface) to settle in seconds.

### BGP Example

```python
def build_network():
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)
    
    info('*** Add Routers with BGP\n')
    r1 = net.addRouter('r1', proto='BGP', frr_dir='./frr-config/my-lab/%(name)s')
    r2 = net.addRouter('r2', proto='BGP', frr_dir='./frr-config/my-lab/%(name)s')
    
    info('*** Add Hosts\n')
    h1 = net.addHost('h1', defaultRoute='via 192.168.1.1')
    h2 = net.addHost('h2', defaultRoute='via 192.168.2.1')
    
    info('*** Add Links\n')
    net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
    net.addLink(h2, r2, params1={'ip': '192.168.2.2/24'}, params2={'ip': '192.168.2.1/24'})
    net.addLink(r1, r2, params1={'ip': '10.10.1.1/30'}, params2={'ip': '10.10.1.2/30'})
    
    info('*** Configure BGP\n')
    r1.add_frr_config("""
router bgp 65001
 bgp router-id 1.1.1.1
 no bgp ebgp-requires-policy
 neighbor 10.10.1.2 remote-as 65002
 address-family ipv4 unicast
  network 192.168.1.0/24
 exit-address-family
exit
""")
    
    r2.add_frr_config("""
router bgp 65002
 bgp router-id 2.2.2.2
 no bgp ebgp-requires-policy
 neighbor 10.10.1.1 remote-as 65001
 address-family ipv4 unicast
  network 192.168.2.0/24
 exit-address-family
exit
""")
    
    return net
```

`no bgp ebgp-requires-policy` lets the two eBGP peers exchange routes without
route-maps: FRR otherwise follows RFC 8212 and neither sends nor accepts any.

## IPv6 Support

Add IPv6 addresses to your lab:

```python
def build_network():
    net = mnIPLab(topo=None, autoSetMacs=True, controller=None)
    
    info('*** Add Routers\n')
    r1 = net.addRouter('r1', proto='OSPF6')
    r2 = net.addRouter('r2', proto='OSPF6')
    
    info('*** Add Hosts with IPv6\n')
    h1 = net.addHost('h1', defaultRoute6='via 2001:db8:1::1')
    h2 = net.addHost('h2', defaultRoute6='via 2001:db8:2::1')
    
    info('*** Add Links with IPv6\n')
    net.addLink(h1, r1,
                params1={'ip6': '2001:db8:1::2/64'},
                params2={'ip6': '2001:db8:1::1/64'})
    net.addLink(h2, r2,
                params1={'ip6': '2001:db8:2::2/64'},
                params2={'ip6': '2001:db8:2::1/64'})
    net.addLink(r1, r2,
                params1={'ip6': '2001:db8:ff::1/64'},
                params2={'ip6': '2001:db8:ff::2/64'})
    
    info('*** Configure OSPFv3\n')
    r1.add_frr_config("""
interface r1-eth0
 ipv6 ospf6 area 0
exit
interface r1-eth1
 ipv6 ospf6 area 0
exit
router ospf6
 ospf6 router-id 1.1.1.1
exit
""")
    r2.add_frr_config("""
interface r2-eth0
 ipv6 ospf6 area 0
exit
interface r2-eth1
 ipv6 ospf6 area 0
exit
router ospf6
 ospf6 router-id 2.2.2.2
exit
""")
    
    return net
```

OSPFv3 waits for its Designated Router the same way: allow about 50 seconds
before testing with `h1 ping -6 2001:db8:2::2`.

**IPv6 Address Allocation:**

- ULA (Unique Local): `fd00::/8`
- Documentation: `2001:db8::/32`
- Link-local: `fe80::/10` (automatic)

**Where IPv6 goes:**

- Every IPv6 Interface address is an `ip6` in `params1`/`params2`, a Host's
  included; a Host's gateway is `defaultRoute6` on `addHost`, and a
  link-local one (`'via fe80::1'`) leaves by the Interface chosen as in
  [Add Hosts](#3-add-hosts).
- `ip` and `defaultRoute` take IPv4 only: an IPv6 value there is refused with
  the right key named.

**DAD mode note (startup speed):**

- Preferred per-link option: pass `dad_mode` directly on IPv6 links (e.g., `net.addLink(..., dad_mode='strict')`).
- Default lab mode is fast: IPv6 addresses are applied with `nodad` to reduce startup delay.
- Global fallback (if `dad_mode` omitted):
    - `MNIPLAB_IPV6_DAD_MODE=strict python3 examples/my-lab.py`

## Services

A **Service** is a network service a Node offers to the Lab, alongside whatever
routing Protocol it runs. Unlike a Protocol, a Service attaches to *any* Node,
Host or Router, via `node.addService(...)`, and a Node may carry several. Each
Service family has its own reference document; this section only shows the
attachment point.

### DHCP

Backed by ISC Kea. A `Pool` per served Subnet, named by CIDR; a `Relay` on
another Node carries a remote Subnet's conversation to the Service:

```python
from mniplab.dhcp import DHCPService, Pool

r1.addService(DHCPService(
    Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
))
net.addLink(r1, h1, params1={'ip': '10.50.0.1/24'}, params2={'dhcp4': True})
```

Pools, Reservations, DHCPv6 and the Router Advertisement, Relay, the
rogue server, and the CLI and Web UI surfaces are in
**[DHCP.md](DHCP.md)**.

### DNS

Backed by Knot DNS for Authoritative Zones (Primary or Secondary) and Unbound
for a Recursive Caching Resolver; DNSSEC spans both. Point client Hosts at a
nameserver with `nameservers=[...]` on `addHost()`:

```python
from mniplab.dns import ARecord, DNSService, Zone
from mniplab.resolver import ResolverService

dns1.addService(DNSService(Zone('example.com.', [ARecord('web', '10.0.2.2')], host_node='dns1')))
res1.addService(ResolverService(root_hints=[root1]))
h1 = net.addHost('h1', defaultRoute='via 10.0.1.1', nameservers=['10.0.0.2'])
```

Zones and Records, Secondary replication, the Resolver's hierarchy, cache
and Access Control, DNSSEC signing and validation, and the CLI and Web UI
surfaces are in **[DNS.md](DNS.md)**.

### RPKI

Backed by Krill for the RPKI CA, which holds one Hosted CA per Autonomous
System, and Routinator for the Validator, which serves Routers over RTR. A
Lab holds at most one RPKI CA:

```python
from mniplab.rpki import HostedCA, RPKICAService, ValidatorService

ca.addService(RPKICAService([
    HostedCA(65001, ['10.1.0.0/24']),
    HostedCA(65002, ['10.2.0.0/24'], roas=['10.2.0.0/24 => 65002']),
]))
rv.addService(ValidatorService())
```

Hosted CAs, ROAs and ASPAs, pointing a Router at the Validator, Route Origin
Validation in FRR, and the CLI and Web UI surfaces are in
**[RPKI.md](RPKI.md)**.

## Container Hosts

A **Container Host** is a Node whose processes come from a Docker image
rather than a bare network namespace, so a Lab can include real application
software (a web server, a database, a client stack) on the emulated network.
Add one with `addContainer`; `dimage` is required:

```python
net = mnIPLab(topo=None, autoSetMacs=True, controller=None)

h1 = net.addHost('h1')
d1 = net.addContainer('d1', dimage='nginx:1.27', dcmd='nginx -g "daemon off;"')
s1 = net.addSwitch('s1')
net.addLink(h1, s1, params1={'ip': '10.0.0.1/24'})
net.addLink(s1, d1, params2={'ip': '10.0.0.2/24'})
```

- The image's own command is not run. Pass `dcmd='...'` to start the
  software you want on the network; without it the container runs a sleep
  loop, kept alive so a Terminal can be opened on it.
- `environment`, `volumes`, `ports`, `port_bindings`, and `cap_add`
  (default `NET_ADMIN`) pass through to `docker run`. Mininet `privateDirs`
  become Docker volume mounts; the shared `/etc/hosts` is mounted by default
  so the container resolves the Lab's Node names.
- Links to a Container Host are built from the host side, so a switch-based
  topology works the same as with any Host. Like every switch, `s1` needs the
  Open vSwitch kernel module on the host (see [install.md](install.md#2-open-vswitch-kernel-modules)).
- A Docker daemon must be reachable from wherever the Lab runs. The dev
  container ships docker-in-docker for this.
- `defaultRoute` and `defaultRoute6` work as on `addHost`, choosing their
  Interface the same way.

In the Web UI a Container Host is drawn distinctly from a Host, and its
Terminal runs `docker exec` into the container. Use the declaration above
in your Lab builder with `run_lab` to try it.

## Running Your Lab

### CLI Mode

```bash
# Basic run (no web UI)
python3 examples/my-lab.py

# Then at the Lab Prompt:
mininet-iplab> r1 vtysh -c "show ip route"
mininet-iplab> h1 ping h2
mininet-iplab> quit
```

### Web UI Mode

```bash
# Start with web UI
python3 examples/my-lab.py --enable-web

# Access at http://localhost:8050
# Log in as admin with the password printed at startup
```

To use the Lab with a class, register it (below) and start it from the
browser under `python -m mniplab serve`; see
[QUICKSTART.md](QUICKSTART.md#quick-start-classroom-server).

### External Node Access

Execute commands without entering the CLI:

```bash
# Show all running nodes
./util/connect_node.sh --list

# Run a command on a router
./util/connect_node.sh r1 vtysh -c 'show ip route'

# Test connectivity from a host
./util/connect_node.sh h1 ping -c 3 192.168.2.2

# Access node's shell
./util/connect_node.sh r1 bash
```

## Registering Your Lab in the Web UI

After creating your lab script in `examples/`, register it in the Web UI's lab list by running:

```bash
python3 util/gen_labs_json.py examples/your-lab.py
```

The script auto-detects protocols, node count, IPv6 usage, and whether a layout file exists.
It then prompts you for a name and description, and updates `examples/labs.json`.

To check which lab scripts are not yet registered:

```bash
python3 util/gen_labs_json.py --check
```

To see all registered labs and re-sort the manifest:

```bash
python3 util/gen_labs_json.py --all
```

## Debugging Tips

### View Network State

```bash
# At the Lab Prompt
mininet-iplab> show_ips           # Show all IPs
mininet-iplab> update_hosts       # Sync /etc/hosts

# Via connect_node.sh
./util/connect_node.sh r1 ip addr show
./util/connect_node.sh r1 ip route show
```

### Check Routing Protocols

```bash
# View OSPF neighbors
./util/connect_node.sh r1 vtysh -c 'show ip ospf neighbor'

# View BGP status
./util/connect_node.sh r1 vtysh -c 'show ip bgp summary'

# View all routes
./util/connect_node.sh r1 vtysh -c 'show ip route'
```

### Check FRR Status

```bash
# Check running config
./util/connect_node.sh r1 vtysh -c 'show running-config'

# Check daemon processes
./util/connect_node.sh r1 ps aux | grep frr
```

### Test Connectivity

```bash
# Ping between hosts
./util/connect_node.sh h1 ping -c 3 192.168.2.2

# Traceroute
./util/connect_node.sh h1 traceroute 192.168.2.2

# Check interface state
./util/connect_node.sh r1 ip link show
```

### Cleanup on Error

If your lab doesn't start properly:

```bash
# Quick cleanup: removes the runtime root and stops every Lab on the host,
# kills FRR processes, and removes temp files.
python -m mniplab -c

# Kill remaining processes
killall -9 zebra ospfd bgpd
```

Never run the cleanup while a classroom server (`python -m mniplab serve`) is
up: it stops every Group's Lab too.

## Common Patterns

### Adding Configuration After Startup

```python
# Inside build_network(), after creating routers:
r1.add_frr_config("""
router bgp 65001
 neighbor 10.0.0.2 remote-as 65002
exit
""")

# Enable a daemon at runtime
r1.enable_daemon('bgpd')
```

### Persistent Configurations

Create persistent configs (checked into git):

```bash
mkdir -p ./frr-config/my-lab/r1
mkdir -p ./frr-config/my-lab/r2
```

Then use in your lab:

```python
r1 = net.addRouter('r1', frr_dir='./frr-config/my-lab/%(name)s')
```

Run from a terminal (`python3 examples/my-lab.py`), the lab reads and writes
that directory directly: `write memory` in `vtysh` lands there, which is how
you author it. That directory is the **seed**. Under the web UI in Multi-Lab
Mode each Group gets its own copy of it — `group-config/<group>/my-lab/r1/`
beside `frr-config/`, copied from the seed on the Group's first start and
resumed after — so a Group's `write memory` never reaches the seed and two
Groups never share a file. Groups can start clean, and the Instructor can
reset a Group's copy (or every Group's) to the seed from the Class Overview; see
[web-ui.md](web-ui.md#multi-lab-mode). Register the lab with
`util/gen_labs_json.py`, which records `persistent_config` in the catalog so
the web UI knows to offer those controls.

### Multiple Network Segments

```python
# Segment 1
s1_r1 = net.addRouter('s1_r1')
s1_h1 = net.addHost('s1_h1')
net.addLink(s1_h1, s1_r1, params1={'ip': '10.1.0.2/24'}, params2={'ip': '10.1.0.1/24'})

# Segment 2
s2_r1 = net.addRouter('s2_r1')
s2_h1 = net.addHost('s2_h1')
net.addLink(s2_h1, s2_r1, params1={'ip': '10.2.0.2/24'}, params2={'ip': '10.2.0.1/24'})

# Inter-segment link
net.addLink(s1_r1, s2_r1,
            params1={'ip': '10.255.0.1/30'},
            params2={'ip': '10.255.0.2/30'})
```

## Next Steps

- Review [examples/](../examples/) for more patterns
- Check [docs/web-ui.md](web-ui.md) for advanced UI features
- Explore FRR documentation: https://frrouting.org/user-guide/
- Join the Mininet community: http://mininet.org/

---

Happy lab building! 🧪
