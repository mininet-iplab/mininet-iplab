# Mininet-IPLab Quickstart Guide

Welcome to Mininet-IPLab! This guide will help you get started with running and interacting with network labs in just a few minutes.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Interaction Modes Overview](#interaction-modes-overview)
- [Quick Start: CLI Mode](#quick-start-cli-mode)
- [Quick Start: Web UI Mode](#quick-start-web-ui-mode)
- [Quick Start: Classroom Server](#quick-start-classroom-server)
- [Common Commands](#common-commands)
- [Example Workflows](#example-workflows)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

---

## Prerequisites

Everything in this guide runs **inside the container** — either the Docker Compose
container (users) or the dev container (contributors). Both already provide Mininet, FRR
and root, so no command here needs `sudo`.

Set one up first if you have not: **[install.md](install.md)**. Pay particular attention
to the Open vSwitch kernel modules, which must be loaded on the **host** — without them,
labs that use switches stop at startup with a `NetworkError`.

Running labs directly on your own machine is unsupported unless Mininet, FRR and Open
vSwitch are installed there; see
[install.md](install.md#running-on-a-host-without-the-container).

---

## Interaction Modes Overview

Mininet-IPLab can be used in four stages, each building on the one before.
Stages 1 and 2 are for one person, a Learner or an Instructor working alone,
and work the way Mininet does; stages 3 and 4 are classroom deployments.

### 1. Build and run a Lab
- `python3 examples/static-lab.py` (or your own `my-lab.py`) builds the Lab and
  opens the `mininet-iplab>` **Lab Prompt**: **CLI Mode**, the default
- The Mininet workflow, extended with FRR routing and Services (DHCP, DNS)
- Use an external terminal with `./util/connect_node.sh` to reach individual Nodes
- See [Quick Start: CLI Mode](#quick-start-cli-mode)

### 2. See it in the browser
- Add `--enable-web`: **Web UI Mode** for one person. The Lab Prompt stays on
  your terminal; the browser adds the Topology and a Terminal per Node
- One shared login, `admin` with a password printed at startup (generated afresh
  each time unless you set `MNIPLAB_PASSWORD` in `.env`)
  (see [Web UI Configuration](#web-ui-configuration))
- See [Quick Start: Web UI Mode](#quick-start-web-ui-mode)

### 3. Run a class on one shared Lab
- `python -m mniplab serve` starts the web server with no Lab running
- `MNIPLAB_USERS` gives the Instructor and the Learners their own logins
- **Single-Lab Mode** (the default): the Instructor picks a Lab Example from the
  **Lab Catalog**, starts and stops it, and everyone works on that one Lab
- See [Quick Start: Classroom Server](#quick-start-classroom-server)

### 4. Run simultaneous Group Labs
- `serve` with `MNIPLAB_LAB_MODE=multi` in `.env`: **Multi-Lab Mode**
- Each Learner login is a **Group** with a Lab of its own, isolated from the
  others by network namespaces, started from what the Instructor put in its
  **Assignment**
- The Instructor follows every Group from the **Class Overview**
- See [Multi-Lab Mode](web-ui.md#multi-lab-mode)

On `serve`, an Instructor can also create Labs beyond the shipped Lab Examples
with **Lab Authoring**: draw a Lab draft on a canvas, try it in a Preview Lab,
and Publish it into the Lab Catalog. See [lab-authoring.md](lab-authoring.md).

---

## Quick Start: CLI Mode

### 1. Launch a Lab

```bash
# Start the static routing lab
python3 examples/static-lab.py
```

You'll see output like:
```
*** Add Routers
*** Add hosts
*** Add links
*** Starting network
*** Lab key: b8f8
*** Configuring hosts
r1 r2 h1 h2
*** Starting controller
*** Starting 0 switches
*** Starting FRR routers
r1 r2
*** Updated /tmp/mniplab/labs/b8f8/etc/hosts with 14 entries (shared by all nodes)
*** Starting CLI:
mininet-iplab>
```

### 2. Explore the Network

At the `mininet-iplab>` prompt, try these commands:

```bash
# List all nodes
mininet-iplab> nodes

# Show network topology
mininet-iplab> net

# Display IP addresses
mininet-iplab> show_ips

# Test connectivity
mininet-iplab> pingall

# Get detailed information
mininet-iplab> dump
```

### 3. Interact with Individual Nodes

A node's terminal is its own machine, not a window onto the Lab VM: `ps`
lists only the processes running inside that node, and `hostname` returns
the node's own name — identically whether you open it here or from the Web
UI. That list includes anything started on your behalf, such as a
`network-tools` `iperf3` server, not only commands you typed yourself.

**Open a new terminal** and use the helper script. It attaches to the one
running Lab; with several running, name yours with `--lab <key>` (the key
each Lab prints as `*** Lab key: <key>` when it starts) or set
`MNIPLAB_LAB_KEY`:

```bash
# Access a host
./util/connect_node.sh h1 bash

# Inside the host, you can run:
# - ip addr show
# - ping 192.168.2.2
# - traceroute 192.168.2.2
# - exit (to close)

# Or run commands directly (a Node's name works as a target only after
# `update_hosts` at the Lab Prompt):
./util/connect_node.sh h1 ping -c 3 192.168.2.2

# Access a router's FRR CLI
./util/connect_node.sh r1 vtysh

# Inside vtysh:
# - show ip route
# - show running-config
# - exit

# Or run FRR commands directly:
./util/connect_node.sh r1 vtysh -c 'show ip route'
./util/connect_node.sh r1 vtysh -c 'show ip route static'
```

### 4. Stop the Lab

Back at the `mininet-iplab>` prompt:

```bash
mininet-iplab> exit
```

Or press `Ctrl+D`.

---

## Quick Start: Web UI Mode

### 1. Launch a Lab with Web UI

```bash
# Start any lab with the --enable-web flag
python3 examples/static-lab.py --enable-web

# Or specify a custom port:
python3 examples/ospf-lab.py --enable-web --web-port 8080
```

Without `--web-port`, the port comes from `MNIPLAB_PORT` in `.env`, else 8050
(`--web-host` and `MNIPLAB_HOST` work the same way for the bind address). With
Docker Compose only port 8050 is published, so a custom port also needs the
`ports:` mapping in `docker-compose.yml` changed.

You'll see, among the startup lines:
```
*** Starting web server on http://0.0.0.0:8050
*** Web server logs: /tmp/mniplab-web.log
*** Login username: admin  password: 7xKq2mRvTa9cWpEz
***   (password generated for this run; set MNIPLAB_PASSWORD to choose one)
*** Lab mode: single
mininet-iplab>
```

### 2. Access the Web Interface

1. **Open your browser** and go to: http://localhost:8050
2. **Login** with the shared login:
   - Username: `admin`
   - Password: the one printed on the `*** Login username:` line

### 3. Explore the Topology

- **View the topology**: You'll see a visual representation of your network
- **Node colors**: Routers blue, Hosts green, switches orange, Speakers purple,
  Container Hosts Docker blue (see [web-ui.md](web-ui.md#2-topology-view))
- **Lines** show network connections between nodes

### 4. Open Node Terminals

1. **Click on any node** (host or router) in the topology
2. A terminal tab for that node opens beneath the topology
3. The terminal is fully interactive - you can:
   - Run commands
   - Use command history (up/down arrows)
   - Copy/paste
   - Resize the window

**Example sessions:**

**Host terminal** (after clicking h1):
```bash
# Check network configuration
ip addr show

# Test connectivity
ping 192.168.2.2

# Trace route
traceroute 192.168.2.2

# Run network tools
netstat -rn
```

**Router terminal** (after clicking r1):
```bash
# Access FRR CLI
vtysh

# Inside vtysh:
show ip route
show ip ospf neighbor
show running-config
show interface brief

# Or run commands directly:
vtysh -c 'show ip route'
```

### 5. Use the CLI and Lab Prompt

The `mininet-iplab>` prompt works in the terminal where you launched the lab, and is also reachable directly in the browser:

- Click the **Lab Prompt** button in the topology toolbar (beside Tools) to open `mininet-iplab>` in a browser terminal tab.
- Input and output are multiplexed: typing in either the launching terminal or the browser appears in both.
- The Lab Prompt is the Instructor's alone. The shared `admin` login is an Instructor; a Learner login drives the Lab through Node Terminals instead.
- Run network-level commands (`pingall`, `show_ips`, `link_config`, `show_routes`, `py net.hosts`) directly from the browser prompt.

```bash
mininet-iplab> nodes
mininet-iplab> show_ips
mininet-iplab> pingall
```

### 6. Stop the Lab

At the `mininet-iplab>` prompt (in terminal or browser):

```bash
mininet-iplab> exit
```

The web server will automatically shut down.

---

## Quick Start: Classroom Server

`python -m mniplab serve` runs the web server on its own, with no Lab chosen
up front: the Instructor starts Labs from the browser, and every Lab runs in a
process of its own. It is what a classroom uses.

### 1. Create the logins

```bash
cp .env.example .env
```

In `.env`, give the Instructor a login and each Learner (or each table of
Learners sharing one) a login of their own:

```bash
MNIPLAB_USERS=teacher:Instructor-Pass1:instructor,table1:Table-Pass1:learner,table2:Table-Pass2:learner
```

`.env` needs only the Instructor's login: once the server runs, the
Instructor can add Learner logins (Groups, in Multi-Lab Mode) from
**Server settings**, each with a generated password. See
[Accounts](web-ui.md#accounts).

Keep `accounts.json`, where those logins and their passwords are kept, out of
anything shared with Learners.

### 2. Start the server

```bash
python -m mniplab serve
```

The console lists each login with its role, and the Lab mode. No Lab is
running yet. The bind address follows the same rules as `--enable-web`:
`--web-host` / `--web-port`, else `MNIPLAB_HOST` / `MNIPLAB_PORT`, else
`0.0.0.0:8050`.

### 3. Run one shared Lab (Single-Lab Mode)

Sign in as the Instructor, choose a Lab Example from the Lab Catalog in the
**Labs** sidebar, and start it. Learners who sign in see that same Lab: its
Topology, its Guide and Terminals of their own. While it runs, the status
line shows how many Learner Sessions are signed in. The Lab Prompt stays the
Instructor's. **Stop Lab**, on the Topology's toolbar, ends it after a
confirmation.

### 4. Run simultaneous Group Labs (Multi-Lab Mode)

Add `MNIPLAB_LAB_MODE=multi` to `.env` and restart `serve`. Each Learner login
is now a **Group** with a Lab of its own: the Instructor sets each Group's
**Assignment** from the **Class Overview** (**Assign…**), and the Group starts
and stops its own Lab from it. See [Multi-Lab Mode](web-ui.md#multi-lab-mode).

The Instructor can also create Labs of their own here with **Lab Authoring**;
see [lab-authoring.md](lab-authoring.md).

### 5. Tune the Limits while the class runs

**Server settings**, in the Instructor's account menu at the right of the
header, opens a dialog for the
Terminal caps, the idle timeouts, Sessions per login and, in Multi-Lab Mode,
Labs alive at once. A saved value applies at once, with no restart. `.env`
stays the baseline: the dialog only overrides it, and emptying a field returns
that Limit to what `.env` (else the default) says. See
[Server settings](web-ui.md#server-settings).

---

## Web UI Configuration

> ⚠️ Without `MNIPLAB_PASSWORD`, the password is generated afresh at every start and printed on the console. Set your own before sharing access or running a classroom.

Copy `.env.example` to `.env` and update the values, then restart the lab:

```bash
cp .env.example .env
# Set: MNIPLAB_USERNAME, MNIPLAB_PASSWORD
```

For a class, run `python -m mniplab serve` with Instructor and Learner logins
in `MNIPLAB_USERS`; see [Quick Start: Classroom Server](#quick-start-classroom-server).
For a class where groups work at their own pace, add `MNIPLAB_LAB_MODE=multi`;
each group then starts, stops and sees only its own Lab, and the Instructor's
Class Overview shows them all. See [Multi-Lab Mode](web-ui.md#multi-lab-mode).

For the full list of configuration options and Web UI API reference, see [web-ui.md](web-ui.md).

---

## Common Commands

### Network-Level Commands (at `mininet-iplab>` prompt)

| Command | Description | Example Output |
|---------|-------------|----------------|
| `nodes` | List all nodes | `Available nodes: h1 h2 r1 r2` |
| `net` | Show network topology | `h1 h1-eth0:r1-eth0` |
| `links` | Show all links | `h1-eth0<->r1-eth0 (OK OK)` |
| `dump` | Show node details | IP addresses, PIDs, interfaces |
| `show_ips` | Display all IP addresses | IPv4 and IPv6 addresses |
| `update_hosts` | Publish a snapshot of the Nodes' addresses into `/etc/hosts` | Terminals can then use Node names; it does not refresh itself |
| `pingall` | Test connectivity | Ping between all hosts |
| `iperf` | Run bandwidth test (iperf2) | `h1 h2` |
| `iperf3` | Run bandwidth test (iperf3) | `h1 h2 [seconds=10] [protocol=tcp\|udp] [json]` |
| `link_config` | Set a Link Condition | `r1 r2 delay=10ms loss=5 bw=100` |
| `show_routes` | A Node's kernel routing table | `r1 [-4\|-6]` |
| `help` | Show all commands | Full command list |
| `exit` or `quit` | Stop the lab | Cleanup and exit |

### Service Commands (at `mininet-iplab>` prompt)

Labs that attach a DHCP or DNS Service to a Node, or add a Speaker, add
inspection and authoring commands. Each has a `help <command>` entry; the
full reference is in [DHCP.md](DHCP.md), [DNS.md](DNS.md) and
[EXABGP.md](EXABGP.md).

| Command | Description |
|---------|-------------|
| `show_speaker <node>` | A Speaker's Neighbors with session state, and the routes announced to and received from each |
| `announce <node> [neighbor <ip>] <prefix> next-hop <ip> [attrs…]` / `withdraw <node> [neighbor <ip>] <prefix>\|all` | Announce or withdraw routes from a Speaker, in ExaBGP's own words |
| `announce <node> [neighbor <ip>] generate <count> [sub-prefixes N] [prefix-seed S] [as-path-seed S]` | Announce a burst of random routes of the kind the Lab Examples ship |
| `show_leases <node> [4\|6]` | The Leases a Node's DHCP Service holds, per address family |
| `show_relay_activity <node>` | The DHCP requests a Node's Relay has forwarded |
| `show_dns <node>` | DNS Service status, listeners, and hosted Zones |
| `show_dns_records <node> <zone>` / `dns_records <node> <zone> add\|del ...` | Inspect or author a Zone's Records |
| `show_dns_stats <node> [--reset]` | Live query and response counters |
| `show_dns_transfers <node> [zone]` / `dns_refresh` / `dns_retransfer` | A Secondary's Transfer Log and its two replication levers |
| `dnssec_keys <node> <zone>` / `dnssec_rollover <node> <zone> [zsk\|ksk]` | A signed Zone's keys and DS, and an on-demand rollover |
| `show_resolver <node>` / `show_cache <node>` / `flush_cache <node>` | A Resolver's shape, its live cache, and eviction |
| `resolver_access <node> [add\|del ...]` | Show or author a Resolver's Access Control |

### Node-Level Commands (via `connect_node.sh` or Web UI terminal)

#### For Hosts (h1, h2, etc.):

```bash
# Network information
ip addr show          # Show IP addresses
ip route show         # Show routing table
ip -6 route show      # Show IPv6 routes
netstat -rn           # Show routes (alternative)

# Connectivity testing
ping <ip>             # Test connectivity
ping6 <ipv6>          # Test IPv6 connectivity
traceroute <ip>       # Trace route to destination
mtr <ip>              # Real-time traceroute

# Network diagnostics
netstat -tulpn        # Show listening ports
ss -tulpn             # Socket statistics
tcpdump -i eth0       # Packet capture
```

#### For Routers (r1, r2, etc.):

```bash
# Access FRR CLI
vtysh                 # Interactive mode

# FRR show commands (use inside vtysh or with -c flag)
show ip route         # Display routing table
show ip route ospf    # OSPF routes only
show ip route bgp     # BGP routes only
show ip ospf neighbor # OSPF neighbors
show ip bgp summary   # BGP summary
show running-config   # Current configuration
show interface brief  # Interface summary
show ipv6 route       # IPv6 routing table

# System-level
ip addr show          # Show IP addresses
ip link show          # Show interfaces
```

---

## Example Workflows

### Workflow 1: Testing Static Routing (CLI Mode)

```bash
# 1. Start the lab
python3 examples/static-lab.py

# 2. In another terminal, check routes:
./util/connect_node.sh r1 ip route show
./util/connect_node.sh r2 ip route show

# 3. Test from hosts:
./util/connect_node.sh h1 ping -c 3 192.168.2.2
./util/connect_node.sh h2 traceroute 192.168.1.2

# 6. Clean up:
mininet-iplab> exit
```

### Workflow 2: Exploring OSPF (Web UI Mode)

```bash
# 1. Start the lab with Web UI
python3 examples/ospf-lab.py --enable-web

# 2. Open browser to http://localhost:8050 and login

# 3. Click on router r1 to open its terminal

# 4. In the r1 terminal window:
vtysh
show ip ospf neighbor
show ip route ospf
show ip ospf interface

# 5. Click on host c11 to open its terminal

# 6. In the c11 terminal window (c32, in another Area):
ping 172.18.2.2
traceroute 172.18.2.2

# 7. Back at mininet-iplab> prompt:
mininet-iplab> pingall

# 8. Clean up:
mininet-iplab> exit
```

### Workflow 3: Debugging BGP Issues

```bash
# 1. Start BGP lab
python3 examples/bgp-lab.py --enable-web

# 2. Access router via web UI or CLI:
./util/connect_node.sh r11 vtysh -c 'show ip bgp summary'
./util/connect_node.sh r11 vtysh -c 'show ip bgp neighbors'

# 3. Check if BGP sessions are established:
./util/connect_node.sh r11 vtysh -c 'show ip bgp'

# 4. Test connectivity:
mininet-iplab> pingall

# 5. If issues, check configuration:
./util/connect_node.sh r11 cat /etc/frr/frr.conf
```

### Workflow 4: Taking a DHCP Lease (CLI Mode)

A DHCP client Interface starts with no address; nothing takes a Lease on
your behalf, so the exchange is yours to run and watch.

```bash
# 1. Start the DHCP lab
python3 examples/dhcp-lab.py

# 2. In another terminal, confirm h1 starts bare (no inet line):
./util/connect_node.sh h1 ip -4 addr show dev h1-eth0

# 3. Take a Lease, in the foreground and verbose, and watch
#    DISCOVER, OFFER, REQUEST, and ACK go by:
./util/connect_node.sh h1 dhcpcd -1 -B -d -4 h1-eth0

# 4. h1 now holds 10.10.0.50, the Reservation the lab declares for it:
./util/connect_node.sh h1 ip -4 addr show dev h1-eth0

# 5. See the same Lease from the server's side:
mininet-iplab> show_leases r1
```

### Workflow 5: Resolving a Name Through the Lab (Web UI Mode)

```bash
# 1. Start the DNS lab with the Web UI
python3 examples/dns-lab.py --enable-web

# 2. Open http://localhost:8050 and click client1 to open its terminal.
#    The first query walks Root -> TLD -> Authoritative inside the lab:
dig web.example.com A

# 3. Ask again: the answer now comes from res1's cache.
#    Select the teal RESOLVER badge above res1 to open the Cache Inspector
#    and watch the rows, their HIT badges, and their TTLs counting down.

# 4. Evict the cache and watch the next query walk the hierarchy again:
mininet-iplab> flush_cache res1
```

Every DHCP and DNS lab ships a Guide (`examples/guides/<lab>.md`) that walks
these steps and the failure modes the lab stages; in Web UI Mode it opens in
the **Guide** pane, at the Left of the Topology.

---

## Troubleshooting

### Lab won't start or shows errors

**Problem**: Network resources from previous lab still exist

**Solution**: Clean up before starting:
```bash
# Full cleanup (like "mn -c" in Mininet): stops every Lab on the host
python -m mniplab -c

# Or explicit cleanup:
python -m mniplab cleanup
```

> ⚠️ Cleanup stops **every** Lab on the host, not only the one that failed.
> Never run it while a classroom server (`python -m mniplab serve`) is up.

### Can't connect to nodes with `connect_node.sh`

**Problem**: Node name doesn't exist or lab isn't running

**Solution**:
1. Check if lab is running (you should see `mininet-iplab>` prompt)
2. List nodes: `mininet-iplab> nodes`
3. Use exact node name: `./util/connect_node.sh h1 bash` (not H1 or host1)
4. If multiple Labs are running, specify the Lab key: `./util/connect_node.sh --lab <key> h1 bash` (or set `MNIPLAB_LAB_KEY=<key>`)

### Web UI won't load or shows "Connection refused"

**Problem 1**: Port is already in use

**Solution**: Use a different port:
```bash
python3 examples/static-lab.py --enable-web --web-port 8888
```

With Docker Compose, also change the `ports:` mapping in `docker-compose.yml`:
only port 8050 is published to the host.

**Problem 2**: Web server didn't start properly

**Solution**: Check logs:
```bash
cat /tmp/mniplab-web.log
```

> **Note**: The web log contains only web server events (HTTP, WebSocket, Terminals, authentication). Lab operations, FRR daemons, and Mininet events appear on stdout when running labs in CLI Mode.

### Router daemons not starting

**Problem**: FRR configuration errors or daemons not enabled

**Solution**: Check configuration and logs:
```bash
# Check if daemons file exists:
./util/connect_node.sh r1 cat /etc/frr/daemons

# Check FRR configuration:
./util/connect_node.sh r1 cat /etc/frr/frr.conf

# Check daemon status:
./util/connect_node.sh r1 ps aux | grep frr
```

### IPv6 not working

**Problem**: IPv6 forwarding not enabled or interfaces not configured

**Solution**: Check IPv6 settings:
```bash
# Check if IPv6 forwarding is enabled:
./util/connect_node.sh r1 sysctl net.ipv6.conf.all.forwarding

# Check IPv6 addresses:
./util/connect_node.sh r1 ip -6 addr show

# Check IPv6 routes:
./util/connect_node.sh r1 ip -6 route show
```

### "Permission denied" errors

**Problem**: Labs need root, and you are not running as root.

**Solution**: Run inside the container, which already runs as root:

```bash
docker compose exec mniplab python3 examples/static-lab.py
```

In the dev container you are already root — no `sudo`, no prefix. If you see this error
*inside* a container, the container is likely missing `--privileged` / `NET_ADMIN`; see
[install.md](install.md#host-requirements).

### A lab stops with a `NetworkError` about Open vSwitch

**Problem**: Open vSwitch kernel modules are not loaded on the host. A Lab
with switches stops at `addSwitch` naming the cause (an older version hung
with no output instead).

**Solution**: On the **host** (not in the container):

```bash
lsmod | grep openvswitch      # empty means not loaded
sudo modprobe openvswitch
```

This is the one place `sudo` is genuinely needed — loading a kernel module is a host
operation. See [install.md](install.md#2-open-vswitch-kernel-modules).

---

## Next Steps

### Learn More

- **[CREATE_LAB.md](CREATE_LAB.md)**: Complete guide to building custom labs in Python
- **[lab-authoring.md](lab-authoring.md)**: Building a Lab on the browser canvas instead, as an Instructor on `serve`
- **[DHCP.md](DHCP.md)** and **[DNS.md](DNS.md)**: The DHCP and DNS Services, from declaration to the CLI and Web UI surfaces
- **[web-ui.md](web-ui.md)**: Detailed Web UI documentation and API reference
- **[README.md](../README.md)**: Full project documentation

### Explore Examples

```bash
# Static routing (IPv4 and IPv6)
python3 examples/static-lab.py

# OSPF routing (dynamic)
python3 examples/ospf-lab.py

# BGP routing (multi-AS)
python3 examples/bgp-lab.py

# DHCP: a central Service with direct, relayed and IPv6-only Subnets
python3 examples/dhcp-lab.py

# DNS: the whole resolution chain, then the same chain signed
python3 examples/dns-lab.py
python3 examples/dnssec-lab.py
```

### Try Different Configurations

1. Modify router configurations in `frr-config/<lab>/<router>/frr.conf`
2. Restart the lab
3. Observe changes in routing tables

### Build Your Own Lab

1. Start with a simple topology (2 routers, 2 hosts)
2. Follow patterns from `examples/static-lab.py`
3. Add routing protocols as needed (OSPF, BGP)
4. Test thoroughly with `pingall` and connectivity checks

Prefer a canvas to Python? An Instructor on `serve` can draw the Lab in the
browser with Lab Authoring instead; see [lab-authoring.md](lab-authoring.md).

### Community & Support

- Report issues: [GitHub Issues](https://github.com/mininet-iplab/mininet-iplab/issues)
- Ask questions: Check the documentation or open an issue
- Contribute: Pull requests welcome!

---

## Quick Reference Card

### Starting Labs

| Command | Mode | Description |
|---------|------|-------------|
| `python3 examples/static-lab.py` | CLI | Terminal-only interaction |
| `python3 examples/static-lab.py --enable-web` | Web UI | Browser + terminal interaction |
| `python -m mniplab serve` | Web UI | Classroom server: Labs started from the browser |

### Interacting with Nodes

| Method | Command | When to Use |
|--------|---------|-------------|
| CLI Mode | `./util/connect_node.sh <node> <cmd>` | External terminal access |
| Web UI Mode | Click node in topology | In-browser terminals |

### Essential Commands

| Location | Command | Purpose |
|----------|---------|---------|
| `mininet-iplab>` | `nodes` | List all nodes |
| `mininet-iplab>` | `show_ips` | Show IP addresses |
| `mininet-iplab>` | `pingall` | Test connectivity |
| `mininet-iplab>` | `show_leases <node>` | Show a DHCP Service's Leases |
| `mininet-iplab>` | `show_cache <node>` | Show a Resolver's cache |
| Node terminal | `ip addr show` | Show interfaces |
| Host terminal | `dhcpcd -1 -B -d -4 <intf>` | Take a DHCP Lease, verbose |
| Host terminal | `dig <name>` | Resolve through the lab's nameservers |
| Router terminal | `vtysh -c 'show ip route'` | Show routes |

## Bandwidth Testing: Iperf2 vs Iperf3

Mininet-IPLab supports both iperf (version 2) and iperf3 for bandwidth measurement. Here's how to use each:

### Iperf2 (Classic Iperf)

```bash
# CLI Mode: Fixed 5-second TCP test
mininet-iplab> iperf h1 h2
*** Iperf: testing TCP bandwidth between h1 and h2
*** Results: 1.23 Gbits/sec

# UDP test (inherited from Mininet)
mininet-iplab> iperfudp 1M h1 h2
```

**Web UI:** None; the Network Tools dialog offers iperf3 only. Run `iperf` and `iperfudp` from the Lab Prompt.

### Iperf3 (Modern Iperf)

Iperf3 is a complete rewrite with modern features (incompatible binary format with iperf2):

```bash
# CLI Mode: Configurable duration, protocol, and output format
mininet-iplab> iperf3 h1 h2                          # 10s TCP (default)
mininet-iplab> iperf3 h1 h2 seconds=20               # 20-second test
mininet-iplab> iperf3 h1 h2 protocol=udp             # UDP test
mininet-iplab> iperf3 h1 h2 seconds=5 protocol=udp   # 5s UDP
mininet-iplab> iperf3 h1 h2 json                     # JSON output
mininet-iplab> iperf3 h1 h2 seconds=30 json          # 30s with JSON
```

**Web UI:** Select server/client in "Iperf3" tab, adjust duration/protocol/format, set a Bandwidth (a number with K, M or G, at most 1G) or leave it empty for iperf3's default (1 Mbit/s for UDP, unlimited for TCP), click "Run Iperf3". The output streams as iperf3 prints it; while the test runs the button reads "Stop Iperf3", which ends it early and still shows the summary.

A test runs for at most 60 seconds, from the Web UI and the `iperf3` command alike. For a longer one, run `iperf3` yourself in the Nodes' Terminals.

### Key Differences

| Feature | Iperf2 | Iperf3 | Mininet-IPLab Support |
|---------|--------|--------|----------------------|
| **Protocol** | TCP, UDP | TCP, UDP, SCTP | TCP, UDP |
| **Duration** | Default ~10s | Default 10s | CLI: configurable; Web: 10s default (iperf3 only) |
| **JSON Output** | ❌ No | ✅ Yes | CLI: `json` flag; Web: checkbox |
| **Bidirectional** | Limited | ✅ Full support | Not exposed in CLI/Web UI yet |
| **Development** | Legacy | Active (ESnet) | Both available |
| **Binary Name** | `iperf` | `iperf3` | Both installed in devcontainer |

### Choosing Between Them

- **Use Iperf2** for quick tests with Mininet's built-in helpers and legacy compatibility
- **Use Iperf3** for modern features: configurable duration, JSON parsing, UDP jitter/loss stats
- Both binaries coexist; no conflicts

### Example: Testing with JSON Output

```bash
mininet-iplab> iperf3 h1 h2 seconds=5 json
*** Iperf3: testing TCP bandwidth between h1 and h2
*** Duration: 5s
*** Bandwidth: 5.23 Gbps
*** Received: 5.21 Gbps
```

### Cleanup

| Command | Purpose |
|---------|---------|
| `mininet-iplab> exit` | Normal shutdown |
| `python -m mniplab -c` | Force cleanup (stops every Lab on the host) |

---

**Happy networking!** 🚀

For more advanced topics, refer to the [main documentation](../README.md) and [CREATE_LAB.md](CREATE_LAB.md).
