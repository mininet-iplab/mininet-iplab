# API Reference

Complete reference documentation for Mininet-IPLab classes and methods.

## Table of Contents

1. [Network (`mnIPLab`)](#network-mniplab)
2. [Router (`FRRouter`)](#router-frrouter)
3. [Host (`mnIPLabHost`)](#host-mniplabhost)
4. [CLI (`mnIPLabCLI`)](#cli-mniplabcli)
5. [ExaBGP Models](#exabgp-models)
6. [DHCP and Relay (`mniplab.dhcp`)](#dhcp-and-relay-mniplabdhcp)
7. [Labs Web API](#labs-web-api)
8. [DNS Web API](#dns-web-api)
9. [Resolver Web API](#resolver-web-api)
10. [Instructor Lab Authoring](#instructor-lab-authoring)
11. [Web API & WebSockets](#web-api--websockets)
12. [Command Line (`python -m mniplab`)](#command-line-python--m-mniplab)

---

## Network (`mnIPLab`)

### Class: `mnIPLab`

Extended Mininet class for IP routing labs with FRR support.

**Inherits from**: `mininet.net.Mininet`

**Constructor**

```python
mnIPLab(
    *args,
    lab_key: Optional[str] = None,
    **kwargs
) -> mnIPLab
```

**Parameters**:
- `lab_key` (str, optional): The Lab key, a token of 1–6 lowercase letters
  and digits that names this Lab's host-side objects and its Lab directory.
  Default: `MNIPLAB_LAB_KEY` from the environment, else one is allocated.
  The key is printed when the Lab starts (`*** Lab key: <key>`).
- `*args`, `**kwargs`: Passed to `mininet.net.Mininet` (`topo`, `controller`,
  `autoSetMacs`, …). `link` defaults to `TCLink`, so Link Conditions work.
  A `switch=` class here does not change what `addSwitch` builds; see
  [`addSwitch`](#method-addswitch).

**Raises**:
- `FRRError`: If FRR is not installed (its init script is missing)

**Example**:

```python
from mniplab import mnIPLab

net = mnIPLab(topo=None, controller=None)
```

---

### Method: `addRouter`

Add a new FRR-based router node to the network.

```python
def addRouter(
    name: str,
    private_dirs: Optional[List[Union[str, Tuple[str, str]]]] = None,
    frr_dir: Optional[str] = None,
    proto: Optional[Union[str, List[str]]] = None,
    **kwargs
) -> FRRouter
```

**Parameters**:
- `name` (str): Router hostname (must be unique in network)
- `proto` (str or list, optional): Routing Protocol(s), comma- or
  pipe-separated, or a list. Default: None, a static-only Router (zebra and
  staticd always run)
  - Supported: `OSPF`, `OSPF6`, `BGP`, `RIP`, `RIPNG`, `ISIS`, `BFD`, `PIM`, `EIGRP`, `BABEL`, `LDP` and the rest of `PROTO_MAP` in `mniplab.constants`
  - Example: `'OSPF,BGP'` enables both OSPF and BGP
- `private_dirs` (list, optional): Private directories for the Router's
  namespace. Default: the Lab's Router private dirs (`/var/log`, `/etc/frr`, …)
- `frr_dir` (str, optional): FRR config directory template. Default: None (ephemeral under the Lab directory, `<MNIPLAB_TMP_DIR>/labs/<key>/nodes/<name>/etc/frr`, regenerated on each add). A `frr_dir` you supply is persistent: its `frr.conf` is never overwritten. It needs to hold only `frr.conf`; `daemons`, `vtysh.conf` and `support_bundle_commands.conf` are rendered from `proto` on every start, over any copy present, so switch a daemon on by declaring its Protocol rather than by editing `daemons`. When the Lab runs under the web UI's manager for a Group (`MNIPLAB_CONFIG_COPY` set in the environment), `frr_dir` is the seed: the Router uses the Group's copy of it, made from the seed on the Group's first start and resumed after.
  - When specified: Configs saved to disk for version control
  - Example: `'./frr-config/lab-name/%(name)s'` saves to `./frr-config/lab-name/r1/`
- `vrfs` (Dict[str, int], optional): VRFs to create, as name to routing
  table ID. Attach an Interface with `'vrf'` in its Link end's `params`, or
  `r1.attach_interface('r1-eth0', 'blue')`
- `daemons` (List[str], optional): FRR daemon names to run, in place of those
  `proto` implies
- `frr_config` (str, optional): FRR configuration text for `frr.conf`
- `nat` (list, optional): Router NAT entries, each a `PortForward` or a
  `StaticNAT`; see [`PortForward`, `StaticNAT`](#classes-portforward-staticnat).
  Default: none
- `**kwargs`: Other parameters for the Node and the FRR config templates

The Autonomous System of a BGP Router is set in its FRR text
(`router bgp 65001`), not by a parameter.

**Returns**: `FRRouter` instance added to network

**Raises**:
- `ConfigurationError`: If a Protocol name is invalid
- `FRRError`: If the FRR config directory cannot be set up

**Example**:

```python
# Simple OSPF router
r1 = net.addRouter('r1', proto='OSPF')

# BGP + OSPF router; its AS number is in its FRR text
r2 = net.addRouter('r2', proto='BGP,OSPF')
r2.add_frr_config("router bgp 65001\n bgp router-id 10.0.0.2\nexit")

# Persistent config directory (for version control)
r3 = net.addRouter('r3', proto='BGP', frr_dir='./frr-config/my-lab/%(name)s')

# Router with VRFs (name -> routing table ID)
r4 = net.addRouter('r4', proto='BGP', vrfs={'red': 10, 'blue': 20})
```

---

### Classes: `PortForward`, `StaticNAT`

Router NAT is the address translation a Router performs because the Lab
declares it. `addRouter(..., nat=[...])` takes a list of two entry types, both
exported from `mniplab`:

- `PortForward(protocol, outside_port, inside, inside_port, hairpin=False, interface=None)`
  forwards `outside_port` on the outside address to `inside:inside_port`.
  `protocol` is `'tcp'` or `'udp'`, both ports are 1–65535, and `inside` is an
  IPv4 address. `hairpin=True` also lets inside clients reach the forward
  through the outside address; it is off by default.
- `StaticNAT(inside, outside, interface=None)` maps one inside address to one
  outside address, 1:1, in both directions. Both are IPv4. The upstream Router
  must route `outside` toward the outside Interface.

Both take `interface`, the name of the outside Interface the entry applies to,
such as `'r1-eth0'`. Omit it when the Router has exactly one outside Interface.

```python
from mniplab import PortForward, StaticNAT

r1 = net.addRouter('r1', nat=[
    PortForward('tcp', 8080, '192.168.1.10', 80),
    StaticNAT('192.168.1.11', '203.0.113.5'),
])
net.addLink(r1, isp,
    params1={'ip': '203.0.113.2/30', 'nat': 'outside'},
    params2={'ip': '203.0.113.1/30'})
```

The outside Interface is the Link end marked `'nat': 'outside'` (see
[`addLink`](#method-addlink)). Every outside Interface masquerades the traffic
that leaves through it.

Start checks the declaration before any Node starts, then renders it as
iptables rules in the Router's own namespace once the Nodes are built. Nothing
is written on the Host machine, and every start restores the rules from the
declaration. Refusals:

- At declaration (`ValueError`): a protocol other than `tcp` or `udp`, a port
  outside 1–65535, or an address that is not IPv4.
- At start, before any Node starts (`ConfigurationError`, naming the Router and
  the Interface):
  - a `'nat'` mark other than `'outside'`, or `'outside'` on a Node that is not
    a Router
  - an entry that is neither a `PortForward` nor a `StaticNAT`
  - an outside Interface whose far end is an Internet Gateway, or the Speaker in
    front of a Transit Internet Gateway, since that already translates there
  - an entry on a Router with no outside Interface
  - an entry naming an Interface that is not the Router's, or is not marked
    outside
  - an entry that names no Interface while the Router has several outside
    Interfaces
  - a port forward on an outside Interface with no IPv4 address
- After the Nodes are built (`RouterError`): iptables refuses a rule, named with
  the Router and the rule.

---

### Method: `addHost`

Add a host node to the network. **Override** of Mininet method with IPv6 support.

> **Changed: no automatic address.** Mininet numbers every Host from
> `ipBase` (`10.0.0.N/8`) when it is given no `ip`, and applies that at
> `start()` over whatever its Link end declared. An mnIPLab Host, Container
> Host or Node (`addNode`) gets no automatic address: each Interface carries
> exactly what the Lab declares. A script that relied on `10.0.0.N/8`
> appearing by itself should declare the address on the Host's Link end.
> `autoSetMacs` still numbers MAC addresses as before.

> **Changed: no Node-level address.** An address is declared once, on the
> Link end that creates its Interface. `addHost`, `addRouter`, `addNAT`,
> `addTransit`, `addContainer`, `addExaBGP` and `addNode` (and so a Mininet
> `Topo`'s `addHost`) raise `ConfigurationError` for an `ip` or `ip6`,
> naming the Node, the value and the Link-end line to write instead.

```python
def addHost(
    name: str,
    defaultRoute: Optional[str] = None,
    defaultRoute6: Optional[str] = None,
    mac: Optional[str] = None,
    **params
) -> mnIPLabHost
```

**Parameters**:
- `name` (str): Host hostname
- `defaultRoute` (str, optional): IPv4 default route for host
  - Example: `'via 10.0.0.1'`, or `'via 10.0.0.1 dev h1-eth1'` for another Interface
- `defaultRoute6` (str, optional): IPv6 default route for host
    - Example: `'via 2001:db8::1'`, or the link-local `'via fe80::1'`
- `mac` (str, optional): MAC address (auto-generated if not provided)
- `**params`: Additional parameters for node creation

A gateway of the other family (`defaultRoute='via 2001:db8::1'`, or
`defaultRoute6='via 10.0.0.1'`) raises `ConfigurationError` naming the right key.

A gateway is a Host-wide setting: it belongs to the Host's routing table and
names an Interface to leave by, rather than belonging to that Interface. It
leaves by the Interface its `dev` names (for `defaultRoute`, also Mininet's
bare Interface name, such as `'h1-eth1'`), or else by the Host's
lowest-numbered Interface, and is installed naming that device. When the Lab
is built (at `start()`, or at construction when given a `Topo`), every Host's
gateways are checked once all Links exist, before any is installed:
`ConfigurationError` is raised when the Interface is missing or is a DHCP
client of the gateway's family (`dhcp4` for `defaultRoute`, `dhcp6` for
`defaultRoute6`). `addContainer` takes both keys with the same meaning. A Host
given no gateway gets none. The lowest-numbered default and the DHCP refusal
are mnIPLab's teaching conventions, not RFC requirements; see
[DHCP](DHCP.md#a-hosts-gateway-beside-a-dhcp-client).

**Returns**: `mnIPLabHost` instance added to network

**Example**:

```python
# A Host carries Node-wide settings; its address goes on its Link end
h1 = net.addHost('h1', defaultRoute='via 192.168.1.1')
net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})

# IPv6 Host
h2 = net.addHost('h2', defaultRoute6='via 2001:db8::1')
net.addLink(h2, r1, params1={'ip6': '2001:db8::2/64'}, params2={'ip6': '2001:db8::1/64'})

# An exercise: no address on the Host's end, so the Learner assigns it
h3 = net.addHost('h3')
net.addLink(h3, r1, params2={'ip': '192.168.3.1/24'})
```

---

### Method: `addExaBGP`

Add a Speaker, run by ExaBGP, that announces and withdraws routes to its Neighbors on demand.

```python
def addExaBGP(
    name: str,
    neighbors: List[BGPNeighbor],
    local_as: int = 65000,
    routes: Optional[List[BGPRoute]] = None,
    exabgp_dir: Optional[str] = None,
    cls: Optional[type] = None,
    **params
) -> Node
```

**Parameters**:
- `name` (str): ExaBGP node hostname
- `neighbors` (List[BGPNeighbor]): List of BGP neighbor definitions
- `local_as` (int): Local ASN for this ExaBGP instance. Default: 65000
- `routes` (List[BGPRoute], optional): Global route list (used if neighbor routes not specified)
- `exabgp_dir` (str, optional): ExaBGP config directory. Default: None (ephemeral under the Lab directory, `<MNIPLAB_TMP_DIR>/labs/<key>/nodes/<name>/etc/exabgp`)
- `cls` (type, optional): Custom node class (must be compatible with `ExaBGPNode` behavior)
- `**params`: Additional node parameters

**Returns**: Node instance (host-type) with ExaBGP daemon

**Raises**:
- `ValueError`: If neighbor config is invalid
- `RuntimeError`: If ExaBGP is not installed

**Example**:

```python
from mniplab.exabgp import BGPNeighbor, make_v4_peer_routes

# Simple single-neighbor ExaBGP
isp = net.addExaBGP('isp', neighbors=[
    BGPNeighbor(
        peer='10.0.0.1',
        local_address='10.0.0.2',
        local_as=65001,
        peer_as=65000
    )
], local_as=65001)

# Multi-neighbor with distinct routes per neighbor
ex1 = net.addExaBGP('ex1', neighbors=[
    BGPNeighbor(
        peer='10.0.1.1',
        local_address='10.0.1.2',
        local_as=3356,
        peer_as=65000,
        routes=make_v4_peer_routes(num_prefixes=10, local_asn=3356, next_hop='10.0.1.2')
    ),
    BGPNeighbor(
        peer='10.0.2.1',
        local_address='10.0.2.2',
        local_as=3356,
        peer_as=65000,
        routes=make_v4_peer_routes(num_prefixes=8, local_asn=3356, next_hop='10.0.2.2')
    )
], local_as=3356)
```

---

### Method: `addLink`

Add a link between two nodes. **Override** of Mininet with `IPv6Intf` auto-selection.

```python
def addLink(
    node1: Node,
    node2: Node,
    port1: Optional[int] = None,
    port2: Optional[int] = None,
    intfName1: Optional[str] = None,
    intfName2: Optional[str] = None,
    params1: Optional[Dict] = None,
    params2: Optional[Dict] = None,
    **params
) -> Link
```

**Parameters**:
- `node1`, `node2` (Node): Nodes to connect
- `port1`, `port2` (int, optional): Port indices on nodes
- `intfName1`, `intfName2` (str, optional): Interface names (auto-generated if None)
- `params1`, `params2` (Dict, optional): Per-interface parameters
  - `'ip'`: IPv4 address with CIDR (e.g., `'10.0.1.1/30'`)
  - `'ip6'`: IPv6 address with CIDR, configured via `IPv6Intf`
  - A dual-stack end carries both keys; a value of the other family
    (`'ip': '2001:db8::1/64'`) raises `LinkError` naming the right key
  - `'mac'`: MAC address
  - `'nat'`: `'outside'` marks a Router's Interface as outside, for Router NAT
    (see [`PortForward`, `StaticNAT`](#classes-portforward-staticnat))
  - `'bw'`, `'delay'`, `'loss'`: Link characteristics
- `dad_mode` (str, optional): IPv6 DAD mode for this link (`'fast'` or `'strict'`)
- `**params`: Global link parameters

**Returns**: Link instance

**Important**: If either interface has an `ip6`, `mnIPLab` automatically uses `IPv6Intf` for immediate interface configuration.

**Switch ends take no address**: a Switch is layer 2 and runs in the host's own namespace, so an `ip` or `ip6` on its end would land on the host machine. `addLink` raises `LinkError` naming the Link, and `switch.setIP()` raises `ConfigurationError`; put the address on the Host or Internet Gateway end instead. A Switch end takes only VLAN settings (`vlan`, `trunks`, `native`).

**DAD mode**: Prefer per-link setting via `dad_mode='fast|strict'` on `addLink`. If omitted, fallback uses `MNIPLAB_IPV6_DAD_MODE` (default: `fast`).

**Example**:

```python
# Simple link with IPv4
net.addLink(r1, r2, params1={'ip': '10.0.1.1/30'}, params2={'ip': '10.0.1.2/30'})

# Dual-stack link
net.addLink(r1, r2,
    params1={'ip': '10.0.1.1/30', 'ip6': '2001:db8::1/64'},
    params2={'ip': '10.0.1.2/30', 'ip6': '2001:db8::2/64'},
    dad_mode='fast'
)

# Per-link strict DAD (CI/debug)
net.addLink(r1, r2,
    params1={'ip6': '2001:db8:10::1/64'},
    params2={'ip6': '2001:db8:10::2/64'},
    dad_mode='strict'
)

# Link with traffic characteristics
net.addLink(r1, r2,
    params1={'ip': '10.0.1.1/30'},
    params2={'ip': '10.0.1.2/30'},
    bw=10,  # 10 Mbps
    delay='5ms',
    loss=0.1  # 0.1% packet loss
)
```

---

### Method: `addSwitch`

Add a switch. **Override** of Mininet's method.

```python
def addSwitch(name: str, **params) -> Node
```

- Checks Open vSwitch first: without the host's kernel module it raises
  `NetworkError` naming the cause, instead of blocking.
- Builds a `ManagedSwitch` (an Open vSwitch bridge named with the Lab key)
  unless `cls=` says otherwise; a `switch=` class on the constructor does not
  change it. Switch ports take VLAN settings (`show_vlans`, `vlan`).
- A switch end of a Link carries no address.
- `stp=True` enables classic STP; `stp='rstp'` enables RSTP. Both use
  standalone bridge mode. Optional `priority` chooses the bridge priority:
  STP accepts 0–65535, RSTP multiples of 4096 from 0–61440. Lower wins.
  For example: `net.addSwitch('s1', stp='rstp', priority=4096)`.
- `switch.get_spanning_tree()` returns `protocol`, `bridge_id`, `root_bridge`,
  their parsed `priority`, `mac`, `root_priority` and `root_mac`,
  and `ports` (port, role, state). With spanning tree off, `protocol` is
  `None` and `ports` is empty.
- `switch.get_mac_table()` returns learned addresses as rows with `mac`,
  `port`, `vlan` and `age` (seconds). `switch.flush_mac_table()` clears the
  learned table; traffic teaches it again after datapath revalidation.
  The Switch panel offers these views, Refresh and Flush, without a Terminal.
  Authenticated viewers use `GET /api/labs/{key}/switches/{switch}/mac-table`,
  `POST /api/labs/{key}/switches/{switch}/mac-table/flush`, and
  `GET /api/labs/{key}/switches/{switch}/spanning-tree`.

**Raises**: `NetworkError` (Open vSwitch missing or unusable),
`ConfigurationError` (the name is too long to fit a keyed port name)

---

### Methods: `addNAT`, `addTransit`

Add an Internet Gateway. The CLI's node list leaves it out; a Learner's
Topology draws it as the Internet cloud, with its Link and both addresses.

```python
def addNAT(name: str, subnet: Optional[str] = None, **params) -> Node
def addTransit(name: str, downstream=None, subnet: Optional[str] = None, **params) -> Node
```

- `addNAT` adds a plain Internet Gateway: link it to one Router, which then
  translates. Unlike Mininet's `addNAT`, it connects to nothing by itself.
- `addTransit` adds a Transit Internet Gateway behind a Speaker; `downstream` is
  the Router (or list of Routers) one hop beyond the Speaker, each of which
  translates.
- The Gateway's address goes on its Link end, like any other Interface's;
  `subnet` is metadata only.

Unlike Mininet's `addNAT`, ours takes no `connect=`: it does not link itself to
a Node, so you add the Link yourself and give both static addresses, the
Router's end and the Gateway's. It is IPv4 only.

```python
nat = net.addNAT('nat1')
net.addLink(nat, r1, params1={'ip': '192.168.100.1/24'},
            params2={'ip': '192.168.100.2/24'})
```

`addContainer` (Container Hosts) is described in
[CREATE_LAB.md](CREATE_LAB.md#container-hosts).

---

### Method: `start`

Start the network and all FRR daemons.

```python
def start(self) -> None
```

**Behavior**:
1. Configures all hosts and switches (inherited from Mininet)
2. Creates FRR config files in each router's config directory
3. Starts FRR daemons (zebra first, then protocol-specific)
4. Runs link/interface config, including IPv6 via `IPv6Intf` when used
5. Waits for daemons to initialize (~2 seconds by default)

**Raises**:
- `RuntimeError`: If FRR daemon startup fails
- `ConfigurationError`: If a Host's gateway has no Interface to leave by, or
  leaves by a DHCP client Interface of its family (see `addHost`)

**Example**:

```python
net.start()

# Wait for protocol convergence (OSPF/BGP)
import time
time.sleep(3)

# Now routing tables are populated
```

---

### Method: `addStartupCommand`

Declare a shell command a Node runs at one stage of `start()`.

```python
def addStartupCommand(
    node: Node,
    command: str,
    stage: str = 'after-start',
    timeout: int = 5,
    may_fail: bool = False,
) -> None
```

**Stages**:
- `before-daemons`: Interfaces are up; FRR, ExaBGP, NAT and Services have not
  started, so a loopback address added here is present when FRR reads its
  configuration.
- `after-start`: the Lab has started (IPv6 reconciled, Relays up, Hosts ready).

Within a stage every Node runs in parallel and one Node's commands run in
the order declared. Each command runs as `sh -c` in the Node's namespace,
with stdin closed, under its timeout (1–60 s); a whole stage may take at most
120 s. A non-zero exit or timeout stops that Node's remaining commands and
makes `start()` raise `StartupCommandError`, naming the Node, stage, command,
exit status or timeout, and the end of its output. With `may_fail=True` the
failure is recorded in `net.startup_warnings` instead.

**Raises**:
- `ValueError`: If the stage, command or timeout is invalid

**Example**:

```python
r1 = net.addRouter('r1', proto='OSPF')
net.addStartupCommand(r1, 'ip addr add 10.9.9.9/32 dev lo', stage='before-daemons')
net.addStartupCommand(h1, 'ping -c1 -W1 10.9.9.9', may_fail=True)
```

---

### Method: `stop`

Stop the network and clean up FRR daemons.

```python
def stop(self) -> None
```

**Behavior**:
1. Stops all FRR daemons (protocol daemons, then zebra)
2. Removes the Lab directory (`<MNIPLAB_TMP_DIR>/labs/<key>/`) whole
3. Stops all mininet nodes and switches (inherited from Mininet)

**Note**: Persistent configs (with custom `frr_dir`) are **not** deleted.

**Example**:

```python
mnIPLabCLI(net)
net.stop()
```

---

### Functions: `parse_lab_args`, `run_lab`

The two calls a Lab script ends with (`from mniplab import parse_lab_args, run_lab`).

```python
def parse_lab_args(description: str = 'mnIPLab Network') -> argparse.Namespace
def run_lab(net, enable_web: bool = False, web_host: Optional[str] = None,
            web_port: Optional[int] = None, log_level: str = 'info',
            start_network: bool = True) -> None
```

- `parse_lab_args` reads `.env` and parses the script's flags:
  `--enable-web`, `--web-host`, `--web-port` and `--log-level`.
- `run_lab` starts the network, opens the Lab Prompt and, with
  `enable_web`, the web UI; it stops the network when the prompt exits. A
  `web_host` or `web_port` left unset comes from `MNIPLAB_HOST` /
  `MNIPLAB_PORT`, else `0.0.0.0` / `8050`. Pass `start_network=False` to
  configure the network before it is started.

```python
if __name__ == '__main__':
    args = parse_lab_args('My Lab')
    run_lab(build_network(), enable_web=args.enable_web,
            web_host=args.web_host, web_port=args.web_port,
            log_level=args.log_level)
```

---

## Router (`FRRouter`)

### Class: `FRRouter`

Mininet Node subclass running FRR routing daemons.

**Inherits from**: `mininet.node.Node`

**Created by**: `mnIPLab.addRouter()`

---

### Method: `add_frr_config`

Append FRR configuration to the router's config file.

```python
def add_frr_config(self, config: str) -> None
```

**Parameters**:
- `config` (str): FRR configuration block (raw vtysh commands)

**Behavior**:
- Appends to `/etc/frr/frr.conf` inside the router's namespace
- Does **not** reload daemons; manual `write memory` required if applied after startup

**Example**:

```python
r1.add_frr_config("""
router ospf
 ospf router-id 1.1.1.1
 network 192.168.1.0/24 area 0
 network 10.10.1.0/30 area 0
exit
""")

# To apply after network start, use vtysh
r1.cmd('vtysh -c "configure terminal" -c "write memory"')
```

---

### Method: `cmd`

Execute a shell command inside the router's namespace.

```python
def cmd(self, *args, **kwargs) -> str
```

**Parameters**:
- `*args`: Command and arguments (e.g., `'ifconfig'`, `'vtysh -c "show ip route"'`)
- `**kwargs`: Additional parameters (passed to subprocess)

**Returns**: stdout as string

**Inherited from Mininet `Node`**; all standard node commands available.

**Example**:

```python
# Check interfaces
r1.cmd('ifconfig')

# View routing table
r1.cmd('vtysh -c "show ip route"')

# Check OSPF neighbors
r1.cmd('vtysh -c "show ip ospf neighbor"')

# Run ping
r1.cmd('ping -c 1 10.0.0.2')
```

---

### Attribute: `frr_daemons`

List of the FRR daemons enabled on this router, set by `addRouter` from
`proto` (or `daemons`).

```python
frr_daemons: List[str]
```

**Example**:

```python
r1 = net.addRouter('r1', proto='OSPF,BGP')
print(r1.frr_daemons)
```

---

## Host (`mnIPLabHost`)

### Class: `mnIPLabHost`

Mininet Host subclass with shared `/etc/hosts` mounting.

**Inherits from**: `mininet.node.Host`

**Created by**: `mnIPLab.addHost()` or inherited from topology

---

### Shared `/etc/hosts`

All nodes in a Lab mount that Lab's own `/etc/hosts` file. The Lab resets it to the single loopback entry when it is constructed and keeps it in place as nodes are added; nothing has to be called first. Two Labs never share one.

**Behavior**:
- Holds only the loopback entry until an Instructor runs `update_hosts` at the Lab Prompt, which publishes one snapshot of every Node's addresses
- A snapshot never refreshes itself, so names are not available in a Terminal by default
- Network Tools and the Lab Prompt turn a Node's name into its address themselves and do not read this file

**Example**:

```bash
mininet-iplab> h1 ping h2  # The prompt swaps h2 for its address
mininet-iplab> update_hosts  # Publish a snapshot of the Nodes' addresses
h1 $ ping h2               # Now a Terminal on h1 resolves h2 by name
```

---

## CLI (`mnIPLabCLI`)

### Class: `mnIPLabCLI`

Extended Mininet CLI with network utilities.

**Inherits from**: `mininet.cli.CLI`

**Constructor**:

```python
mnIPLabCLI(net: Mininet, *args, **kwargs)
```

---

### Static Method: `init_hosts_file`

Reset a Lab's `/etc/hosts` file to its single loopback entry.

```python
@staticmethod
def init_hosts_file(hosts_file: Optional[Union[str, mnIPLab]] = None) -> None
```

The Lab calls this on itself when constructed, so a Lab Example has nothing
to call before `mnIPLab()`. Pass a Lab to reset its file again mid-run.
Called with **no argument** it does nothing: the hosts file is per Lab, and
there is no global one left to reset. The no-argument form is kept only so
Lab Examples written against earlier docs, which told them to call it first,
still run.

---

### Static Method: `ensure_hosts_file`

Guarantee a regular file at a Lab's hosts path, creating it if absent.

```python
@staticmethod
def ensure_hosts_file(hosts_file: Optional[Union[str, mnIPLab]] = None) -> None
```

Called automatically by `addHost()`/`addRouter()` for every node that binds
`/etc/hosts`. Unlike `init_hosts_file()` it keeps the entries an existing
file already holds. Removes a stale *directory* at that path — Mininet
creates a missing bind source with `mkdir -p`, which no node can read as
`/etc/hosts`. With no argument it does nothing.

---

### Custom Commands

**`show_ips`**

Display all IPv4 and IPv6 addresses in network.

```bash
mininet-iplab> show_ips
```

**Output**:
```
Node    Interface       IP Address
---     ---------       ----------
r1      r1-eth0         10.0.1.1/30
r1      r1-eth1         192.168.1.1/24
r2      r2-eth0         10.0.1.2/30
h1      h1-eth0         192.168.1.2/24
```

---

**`update_hosts`**

Refresh `/etc/hosts` with current node IPs.

```bash
mininet-iplab> update_hosts
```

---

**`show_relay_activity <node>`**

Display the source address and upstream Service address for each DHCP request
the named Node's Relay has forwarded. An empty log is reported as no activity;
if the Relay log cannot be read, the command reports the error.

```bash
mininet-iplab> show_relay_activity r1
```

---

**`show_dns <node>`**

Display status, control socket state, listeners, and hosted zones for a Node's Authoritative DNS Service.

```bash
mininet-iplab> show_dns dns1
```

---

**`show_dns_records <node> <zone>`**

Display all active Resource Records for a hosted zone on the specified node in tabular format.

```bash
mininet-iplab> show_dns_records dns1 example.com.
```

---

**`show_dns_stats <node>`**

Display live `mod-stats` query volume, query type breakdown, and response code statistics for the specified node. Query-type counters are aggregated across the node's hosted zones.

```bash
mininet-iplab> show_dns_stats dns1
```

---

**`show_cache <node>`**

Display a Recursive Caching Resolver's live cache: a metrics banner (queries,
hits, misses, hit ratio, average recursion time) followed by every cached
entry with the TTL it has left. Negative entries are marked `NXDOMAIN` or
`NODATA` and carry the SOA that bounds them (RFC 2308).

```bash
mininet-iplab> show_cache res1
```

---

**`flush_cache <node> [name] [--zone]`**

Evict cached entries so the next query walks the hierarchy again. Omit the
name to clear the whole cache; pass `--zone` to clear the name and everything
beneath it.

```bash
mininet-iplab> flush_cache res1 web.example.com
mininet-iplab> flush_cache res1 example.com --zone
mininet-iplab> flush_cache res1
```

---

**`show_resolver <node>`**

Display a Resolver's operating mode (`iterative`, `forwarding`, or
`default`), TTL caps, Root Hints with their resolved addresses, forwarders,
and the Access Control rules currently in force.

```bash
mininet-iplab> show_resolver res1
```

---

**`resolver_access <node> <add|del> <cidr> [action]`**

Author the client subnet policies a Resolver enforces. Adding a rule rewrites
`unbound.conf` and issues `reload_keep_cache`, so the new policy takes effect
without dropping the cache a Learner is watching. A subnet carries at most one
rule: re-declaring it changes its action. Unbound matches by longest prefix,
so a `/24` refusal overrides the default `0.0.0.0/0 allow`.

```bash
mininet-iplab> resolver_access res1 add 10.0.2.0/24 refuse
mininet-iplab> resolver_access res1 del 10.0.2.0/24
```

Access Control rules are Exercise Configuration: they are
Ephemeral and reset to the Lab Example's own definition when the Lab stops.

The reload keeps every cached entry but restarts Unbound's query counters, so
the hit ratio `show_cache` reports returns to zero after a rule is authored.

---

**More commands**

Each has a `help <command>` entry at the Lab Prompt. The DHCP and DNS ones
are explained in [DHCP.md](DHCP.md) and [DNS.md](DNS.md).

| Command | What it does |
| --- | --- |
| `link_config <node1> <node2> [delay=<ms>] [jitter=<ms>] [loss=<%>] [bw=<Mbps>] [htb\|tbf]` | Set a Link Condition (HFSC scheduling unless `htb` or `tbf`) |
| `show_routes <node> [-4\|-6]` | A Node's kernel routing table, both families by default |
| `iperf3 <server> <client> [seconds=10] [protocol=tcp\|udp] [json]` | An iperf3 test between two Nodes |
| `show_leases <node> [4\|6]` | The Leases a Node's DHCP Service holds |
| `show_dns_transfers <node> [zone]` | A Secondary's Zone Transfers, newest last |
| `dns_refresh <node> <zone>` / `dns_retransfer <node> <zone>` | Ask a Secondary Zone whether its Serial moved, or force a full copy |
| `dns_records <node> <zone> add <owner> <type> <data...> [ttl]` / `… del <owner> [type] [data...]` | Add or remove a Record |
| `dnssec_keys <node> <zone>` / `dnssec_rollover <node> <zone> [zsk\|ksk]` | A signed Zone's keys and DS, and an on-demand rollover |
| `show_vlans [switch]` | 802.1Q VLAN settings of the switches' ports |
| `vlan <switch> <port> <access\|trunk\|none> [vlan_ids] [native_vlan]` | Set a switch port's VLAN mode (`vlan s1 s1-eth2 trunk 10,20`) |
| `loglevel <DEBUG\|INFO\|WARNING\|ERROR\|CRITICAL>` / `showlog` | Set or show the mniplab log level |

---

## Labs Web API

Instructor Session rows include nullable `last_input_at` (Unix seconds), set
only after a Terminal accepts non-empty input. No command contents are stored.
Class Overview Group rows include nullable `last_learner_input`, the latest input
timestamp among that Group's currently signed-in Learner Sessions. It survives
Terminal closure, but disappears when the contributing Sessions end. Output,
keepalives, resizing, HTTP reads and packet-capture traffic do not update it.
The legacy `activity_state` and `last_activity` fields retain their connection
liveness meaning for existing API clients.

Every Web API route that reads or writes a Lab lives under the Lab's key,
`/api/labs/{key}/…`. `GET /api/status` reports the caller's Lab — the one Lab
in Single-Lab Mode, the caller's Group's own in Multi-Lab Mode — and
`GET /api/labs` lists the Labs the caller may see. A key that names a Lab
the caller may not see, another Group's included, is refused with 404 as if
no such Lab existed.

- `GET /api/me` names the login, its role, its Group, the mode (`single` or
  `multi`) and the cap on alive Labs, plus the Session's `nickname` and
  public `connection_code`. The code identifies this connection to the
  Instructor and remains stable through page reloads and nickname changes.
- `PATCH /api/me` with `{"nickname": "Sari"}` renames only the authenticated
  caller's Session. Names are trimmed, limited to 1–40 characters and cannot
  contain control characters; invalid input returns 422. It does not change
  the Session cookie, connection code or open Terminals.
- `GET /api/examples` lists the Lab Examples the caller may start: the whole
  catalog in Single-Lab Mode; a Group's Assignment in Multi-Lab Mode, and
  everything for the Instructor.
- `POST /api/labs` with `{"example_id"}` starts a Lab Example for the
  caller's Group — the Instructor may add `"group"` to start one from another
  Group's Assignment for that Group — and answers with the Lab's `key`. A Group that already runs a Lab is
  refused naming it (409), as is anyone once the cap is reached (409); a
  Group starting outside its Assignment is refused (403).
- `POST /api/labs/{key}/stop` stops a Lab: the Instructor's to call on any,
  a Group's on its own in Multi-Lab Mode.
- `GET /api/admin/dashboard` (Instructor; the Class Overview) lists every Group with its
  Assignment and its Lab — or why its last Lab ended — and every Lab with
  its key, uptime and Terminals.
- `GET /api/admin/assignments` and `PUT /api/admin/assignments/{group}`
  (Instructor) read and set a Group's Assignment, saved to `assignments.json`
  beside `.env`; a running Lab is never touched.

The full request and response shapes are in [web-ui.md](web-ui.md#labs).

## DNS Web API

The authenticated Web UI exposes the same Authoritative DNS state through
these REST endpoints:

- `GET /api/labs/{key}/dns/nodes` lists DNS nodes and their hosted zones.
- `GET /api/labs/{key}/dns/{node}/zones/{zone}/records` returns active records with
    `owner`, `ttl`, `type`, and `data` fields.
- `POST /api/labs/{key}/dns/{node}/zones/{zone}/records` adds or replaces a record. The
    optional `old_owner`, `old_type`, and `old_data` fields make replacement a
    single Knot transaction.
- `DELETE /api/labs/{key}/dns/{node}/zones/{zone}/records` removes a record, optionally
    narrowed by `type` and `data`.
- `GET /api/labs/{key}/dns/{node}/stats` returns `server-operation`, `query-type`, and
    `response-code` counters, plus the other Knot `mod-stats` sections.
- `POST /api/labs/{key}/dns/{node}/stats/reset` records a per-node baseline. The next
    stats response reports counters relative to that baseline.
- `GET /api/labs/{key}/dns/{node}/zones/{zone}/status` returns the SOA serial, primary
    nameserver, role, and Knot transaction/state field.

Record mutations run `zone-begin`, the requested `zone-set`/`zone-unset`,
`zone-diff`, and `zone-commit`. A failed semantic check aborts the transaction
and returns a structured HTTP 400 response. Successful mutations broadcast a
`DNS_RECORD_UPDATED` topology message to connected Web UI sessions.

## Resolver Web API

The authenticated Web UI reaches a Recursive Caching Resolver's live cache and
its Access Control policy through these REST endpoints. They back the Resolver
modal a Learner opens from a Node's `RESOLVER` badge, and expose what
`show_cache`, `flush_cache`, and `resolver_access` do from the CLI. A
Resolver's own shape — its mode, TTL caps, and Root Hints — is read with
`show_resolver` from the CLI; the Web UI has no endpoint for it:

- `GET /api/labs/{key}/nodes/{node}/resolver/cache` returns `entries` (every cached RRset
    and cached denial, in stable order, each with `name`, `type`, `ttl`,
    `data`, `status`, `negative`, and `soa`) alongside `metrics` (`queries`,
    `hits`, `misses`, `hit_ratio`, `recursion_time_ms`). Both come from one
    request, so a hit ratio is never a round trip out of step with the entries
    that produced it.
- `POST /api/labs/{key}/nodes/{node}/resolver/cache/flush` evicts cached entries. Omit
    `target` to clear the whole cache; pass `scope: "zone"` to clear a name
    and everything beneath it.
- `GET /api/labs/{key}/nodes/{node}/resolver/access` returns the rules, in declaration order.
- `PUT /api/labs/{key}/nodes/{node}/resolver/access` replaces the whole policy with the
    submitted `rules` and applies it hot. Adding, editing, and deleting a rule
    each reach the daemon as a single `reload_keep_cache`, so the cache the
    Cache Inspector is showing survives. Successful mutations broadcast a
    `RESOLVER_ACCESS_UPDATED` message to connected Web UI sessions.

A reload keeps every cached entry but restarts Unbound's query counters, so
the metrics reported after a policy change start from zero.

---

**`show_speaker <node>`**

Display a Speaker's Neighbors — address, AS, family, session state and
uptime, a configured Neighbor that never connected listed as down — then,
per Neighbor, what the Speaker has announced to it and what it has sent
back. Routes print in ExaBGP's own words (`next-hop`, `as-path`,
`community`, `large-community`, `med`), so what a Learner reads is what
they would type. The read goes through the Speaker's control pipe; a daemon
that does not answer within five seconds is reported with the command that
went unanswered.

```bash
mininet-iplab> show_speaker isp1
*** Speaker isp1 (AS 65001)
    Neighbors (1):
      - 10.0.1.1             AS 65000  ipv4  established  up 42s
    Announced to 10.0.1.1 (8):
      - 203.0.113.0/24  next-hop 10.0.1.2  as-path 65001 64512
    ...
    Received from 10.0.1.1 (1):
      - 10.20.20.0/24  next-hop 10.0.1.1  as-path 65000
```

---

**`announce <node> [neighbor <ip>] <prefix> next-hop <ip> [as-path …] [community …] [large-community …] [med <n>]`**

Announce a route from a Speaker, in ExaBGP's own words. Without
`neighbor <ip>` the route goes to every Neighbor carrying the prefix's
family, one write per Neighbor. A community is `<asn>:<value>` or one of
`no-export`, `no-advertise`, `blackhole`, `no-peer`; a multi-value
attribute may be bracketed the way ExaBGP writes it (`as-path [ 65001
65002 ]`) or given bare. The line is validated before anything reaches
ExaBGP — the prefix and next-hop must parse and be the same family, and the
target Neighbor must carry that family — and a violation is refused naming
the field and both families, because ExaBGP would accept the cross-family
route and flap the session. A word outside the supported set is refused by
name. An announce ExaBGP rejects is reported with the command that failed.

```bash
mininet-iplab> announce isp1 neighbor 10.0.1.1 198.18.0.0/24 next-hop 10.0.1.2 as-path 65001 65002 med 50
*** isp1: announced 198.18.0.0/24  next-hop 10.0.1.2  as-path 65001 65002  med 50
    to 10.0.1.1
```

**`announce <node> [neighbor <ip>] generate <count> [sub-prefixes N] [prefix-seed S] [as-path-seed S]`**

Announce a burst of `count` random routes of the kind the Lab Examples
ship — public prefixes of the Neighbor's family, its own address as next
hop, an AS-PATH starting at the Speaker's AS — to one Neighbor (named, or
the Speaker's only one). `sub-prefixes N` adds N longer prefixes inside each
base prefix for longest-match lessons; the seeds make a burst repeatable.
Every prefix announced is printed so it can be withdrawn by hand; no burst
is tracked, and `withdraw … all` is the clear. `count` is 1 to 1000.

```bash
mininet-iplab> announce isp1 neighbor 10.0.1.1 generate 3 prefix-seed 42
*** isp1: announced 3 generated prefixes to 10.0.1.1
      - 45.10.0.0/16  next-hop 10.0.1.2  as-path 65001 3356
      - 77.2.0.0/17  next-hop 10.0.1.2  as-path 65001
      - 91.200.4.0/22  next-hop 10.0.1.2  as-path 65001 1299 2914
```

**`withdraw <node> [neighbor <ip>] <prefix>`** / **`withdraw <node> [neighbor <ip>] all`**

Withdraw one prefix from a Neighbor (or from every Neighbor carrying its
family), or with `all` clear everything announced to a Neighbor (or to every
Neighbor). `all` is ExaBGP's `clear adj-rib out`: it withdraws what the Lab
Example shipped along with what was announced since.

```bash
mininet-iplab> withdraw isp1 neighbor 10.0.1.1 198.18.0.0/24
*** isp1: withdrew 198.18.0.0/24 from 10.0.1.1
mininet-iplab> withdraw isp1 neighbor 10.0.1.1 all
*** isp1: withdrew everything announced from 10.0.1.1
```

Nothing a Learner announces is stored by the framework: ExaBGP's memory is
the only record, `show_speaker` reads it back, and a Lab restart is the
reset.

---

**Route Announcements through ExaBGP's FIFO**

A Speaker creates its FIFOs (`/var/run/exabgp/exabgp.in` and `.out`) and
environment file itself before the daemon starts; no CLI command is needed
first. Announce routes by writing to the FIFO, or through `exabgp cli`:

```bash
mininet-iplab> ex1 bash -c "echo 'announce route 10.1.0.0/24 next-hop 10.0.0.2' > /var/run/exabgp/exabgp.in"
mininet-iplab> ex1 exabgp cli show neighbor summary
```

---

### Inherited Mininet CLI Commands

All standard Mininet commands available:

- `nodes` - List all nodes
- `links` - List all links
- `net` - Show network topology
- `dump` - Show node/link summary
- `pingall` - Ping all nodes to all nodes
- `iperf` - Run iperf between two nodes
- `py` - Execute Python code in CLI context
- `sh` - Execute shell command
- `<node> <command>` - Execute command on specific node

### Speaker REST endpoint

`GET /api/labs/{key}/speakers/{node}` returns what `show_speaker` prints, as one
payload: the Speaker's `node` and `local_as`, and `neighbors`, each with
`address`, `peer_as`, `local_address`, `local_as`, `family`, `state`, `up`,
`uptime` (seconds, or `null` when down), `announced` and `received`. A route
carries `prefix`, `next_hop`, `as_path`, `community`, `large_community` and
`med`. Any authenticated Session may read it; a Node that is not a Speaker
answers 404, and a daemon that answers `error` or does not answer within
five seconds answers 502 with the `command` that failed.

```json
{
    "node": "isp1",
    "local_as": 65001,
    "neighbors": [
        {
            "address": "10.0.1.1", "peer_as": 65000,
            "local_address": "10.0.1.2", "local_as": 65001,
            "family": "ipv4", "state": "established", "up": true, "uptime": 42,
            "announced": [
                {"prefix": "203.0.113.0/24", "next_hop": "10.0.1.2",
                 "as_path": [65001, 64512], "community": [],
                 "large_community": [], "med": null}
            ],
            "received": [
                {"prefix": "10.20.20.0/24", "next_hop": "10.0.1.1",
                 "as_path": [65000], "community": [],
                 "large_community": [], "med": 0}
            ]
        }
    ]
}
```

`POST /api/labs/{key}/speakers/{node}/announce` and `POST /api/labs/{key}/speakers/{node}/withdraw`
take the same field set the CLI does, validated the same way. The announce
body carries `prefix`, `next_hop`, optional `as_path` (a list of AS
numbers), `community`, `large_community` (lists of strings) and `med`, and
an optional `neighbor`; without one the route goes to every Neighbor of the
prefix's family. The withdraw body carries `prefix` or `all: true`, and an
optional `neighbor`. Both answer the service's dictionary — `node`, the
`neighbors` written to, and the `route` as announced or the `prefix`
withdrawn — with 400 naming the field for a route that does not validate,
404 for a Node that is not a Speaker, and 502 naming the command when
ExaBGP answers `error` or does not answer.

```json
POST /api/labs/{key}/speakers/isp1/announce
{"neighbor": "10.0.1.1", "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
 "as_path": [65001, 65002], "community": ["65001:100", "no-export"], "med": 50}

→ {"node": "isp1", "neighbors": ["10.0.1.1"],
   "route": {"prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
             "as_path": [65001, 65002], "community": ["65001:100", "no-export"],
             "large_community": [], "med": 50}}

POST /api/labs/{key}/speakers/isp1/withdraw
{"neighbor": "10.0.1.1", "all": true}

→ {"node": "isp1", "all": true, "neighbors": ["10.0.1.1"]}
```

`POST /api/labs/{key}/speakers/{node}/generate` takes the CLI's `generate` options —
`count` (1 to 1000), optional `neighbor` (required when the Speaker has
several), `sub_prefixes`, `prefix_seed`, `as_path_seed` — and answers the
routes it announced, in order, so a burst can be withdrawn by hand:

```json
POST /api/labs/{key}/speakers/isp1/generate
{"neighbor": "10.0.1.1", "count": 3, "prefix_seed": 42}

→ {"node": "isp1", "neighbor": "10.0.1.1",
   "announced": [{"prefix": "45.10.0.0/16", "next_hop": "10.0.1.2",
                  "as_path": [65001, 3356], "community": [], "large_community": [],
                  "med": null}, "…"]}
```

### DHCP Relay Activity REST endpoint

`GET /api/labs/{key}/dhcp/relay/activity/{node}` returns the same activity through the
authenticated Web session facade:

```json
{
    "success": true,
    "activity": [
        {"source": "10.50.0.1", "server": "10.0.0.2"}
    ]
}
```

---

## ExaBGP Models

### Module: `mniplab.exabgp`

Classes for ExaBGP speaker configuration.

---

### Class: `BGPRoute`

Single BGP route announcement.

```python
class BGPRoute:
    prefix: str                   # Route prefix (e.g., '10.0.0.0/24')
    next_hop: str                 # Next-hop IP
    community: List[str]          # BGP communities (default: empty)
    as_path: List[int]            # AS path (default: empty)
    large_community: List[str]    # Large communities (default: empty)
    med: Optional[int]            # Multi-exit discriminator (default: None)
```

Every attribute renders into the Speaker's configuration in ExaBGP's own
words (`as-path`, `community`, `large-community`, `med`), and
`show_speaker` reads the same attributes back, so a route declared in a
Lab and a route observed on the Speaker share one shape.

**Example**:

```python
from mniplab.exabgp import BGPRoute

route = BGPRoute(
    prefix='10.1.0.0/24',
    next_hop='10.0.0.2',
    as_path=[65001, 65002]
)
```

---

### Class: `BGPNeighbor`

BGP neighbor session definition.

```python
class BGPNeighbor:
    peer: str                           # Neighbor IP address
    local_address: str                  # Local IP for session
    local_as: int                       # Local ASN
    peer_as: int                        # Peer ASN
    routes: Optional[List[BGPRoute]]    # Routes specific to this neighbor
    connected: bool                     # Default: True
```

**Parameters**:
- `peer`, `local_address`, `local_as`, `peer_as`: Required
- `routes`: Per-neighbor route list (overrides global routes)
- `connected`: Whether to attempt connection

**Example**:

```python
from mniplab.exabgp import BGPNeighbor, BGPRoute

neighbor = BGPNeighbor(
    peer='10.0.0.1',
    local_address='10.0.0.2',
    local_as=65001,
    peer_as=65000,
    routes=[
        BGPRoute(prefix='10.1.0.0/24', next_hop='10.0.0.2'),
        BGPRoute(prefix='10.2.0.0/24', next_hop='10.0.0.2', as_path=[65001])
    ]
)
```

---

### Function: `make_v4_peer_routes`

Generate randomized IPv4 route set for a neighbor.

```python
def make_v4_peer_routes(
    num_prefixes: int,
    local_asn: int,
    next_hop: str,
    prefix_seed: Optional[int] = None,
    as_path_seed: Optional[int] = None,
    num_sub_prefixes: Optional[int] = None
) -> List[BGPRoute]
```

**Parameters**:
- `num_prefixes` (int): Number of base prefixes: random, public-looking IPv4 prefixes of varied lengths (for example `164.56.0.0/15`)
- `local_asn` (int): The first ASN of every AS path; random ASNs follow it, so paths vary in length
- `next_hop` (str): Next-hop IP for all routes
- `prefix_seed` (int, optional): Seed for the prefixes; the same seed gives the same prefixes. Unset, they differ on every run
- `as_path_seed` (int, optional): Seed for the AS paths, likewise
- `num_sub_prefixes` (int, optional): More-specific prefixes to add under each base prefix (no longer than /24), sharing its AS path; `num_prefixes=3, num_sub_prefixes=2` gives 9 routes

**Returns**: List of `BGPRoute` objects

**Example**:

```python
from mniplab.exabgp import make_v4_peer_routes

# Generate 10 routes whose AS paths start with 65001
routes = make_v4_peer_routes(
    num_prefixes=10,
    local_asn=65001,
    next_hop='10.0.1.2',
    prefix_seed=42,
    as_path_seed=100
)

# routes[0] is BGPRoute(prefix='164.56.0.0/15', next_hop='10.0.1.2',
#                      as_path=[65001, 30116], ...); the same seeds give the same routes
```

---

## Complete Example

```python
#!/usr/bin/env python3
from mniplab import mnIPLab, parse_lab_args, run_lab
from mniplab.exabgp import BGPNeighbor, make_v4_peer_routes

R1_BGP = '''
router bgp 65000
 no bgp ebgp-requires-policy
 neighbor 10.1.0.2 remote-as 65001
 neighbor 10.2.0.2 remote-as 65002
 address-family ipv4 unicast
  network 10.0.1.0/24
 exit-address-family
'''

R2_BGP = '''
router bgp 65001
 no bgp ebgp-requires-policy
 neighbor 10.1.0.1 remote-as 65000
 address-family ipv4 unicast
  network 10.0.2.0/24
 exit-address-family
'''

def build_network():
    # Create network; it starts from a clean hosts file of its own
    net = mnIPLab(topo=None, controller=None)

    # Add routers: r1 in AS 65000, r2 in AS 65001
    r1 = net.addRouter('r1', proto='BGP', frr_config=R1_BGP)
    r2 = net.addRouter('r2', proto='BGP', frr_config=R2_BGP)

    # Add hosts
    h1 = net.addHost('h1', defaultRoute='via 10.0.1.1')
    h2 = net.addHost('h2', defaultRoute='via 10.0.2.1')

    # Add an ExaBGP Speaker in AS 65002. It is passive: r1's
    # `neighbor 10.2.0.2` line is what opens the session.
    isp = net.addExaBGP('isp', neighbors=[
        BGPNeighbor(
            peer='10.2.0.1',
            local_address='10.2.0.2',
            local_as=65002,
            peer_as=65000,
            routes=make_v4_peer_routes(num_prefixes=5, local_asn=65002, next_hop='10.2.0.2')
        )
    ], local_as=65002)

    # Add links; every address sits on a Link end
    net.addLink(h1, r1, params1={'ip': '10.0.1.2/24'}, params2={'ip': '10.0.1.1/24'})
    net.addLink(h2, r2, params1={'ip': '10.0.2.2/24'}, params2={'ip': '10.0.2.1/24'})
    net.addLink(r1, r2, params1={'ip': '10.1.0.1/30'}, params2={'ip': '10.1.0.2/30'})
    net.addLink(isp, r1, params1={'ip': '10.2.0.2/30'}, params2={'ip': '10.2.0.1/30'})

    return net

if __name__ == '__main__':
    args = parse_lab_args('BGP Example Lab')
    run_lab(build_network(),
            enable_web=args.enable_web,
            web_port=args.web_port)
```

---

## DHCP and Relay (`mniplab.dhcp`)

### Class: `Relay`

Declarative offer of a Node to carry DHCP traffic from covered Subnets to an upstream Service.

```python
from mniplab.dhcp import Relay

Relay(
    *subnets: Union[str, Sequence[str]],
    server: Optional[Union[str, Sequence[str]]] = None,
) -> Relay
```

**Parameters**:
- `*subnets` (str or Sequence[str]): One or more Subnet CIDRs (e.g., `'10.20.0.0/24'`, `'2001:db8:20::/64'`).
- `server` (str or Sequence[str]): IP address(es) of the upstream DHCP Service (single IP string, comma-separated string, or list/tuple of IP addresses).

**Attributes & Properties**:
- `subnets` (Tuple[str, ...]): The Subnet CIDRs this Relay covers.
- `server` (Optional[str]): Primary upstream Service IP address.
- `servers` (Dict[int, str]): Mapping of IP version (4 or 6) to upstream Service IP.
- `families` (Tuple[int, ...]): Address families inferred from covered subnets.
- `server_for_family(family: int) -> Optional[str]`: Return the target server address for address family 4 or 6.

**Example**:
```python
relay = r2.addRelay(Relay('10.20.0.0/24', '2001:db8:20::/64', server=['10.0.0.1', '2001:db8:0::1']))
```

---

### Method: `addRelay`

Attach a Relay to a Node. Available on `FRRouter` and `mnIPLabHost` via `ServiceMixin`.

```python
def addRelay(self, relay: Relay) -> Relay
```

**Parameters**:
- `relay` (`Relay`): The Relay instance to attach to this node.

**Returns**: The same `Relay` instance for chaining.

**Raises**:
- `ConfigurationError`: If the node already has a Relay attached (maximum one Relay per node).

**Example**:
```python
r2 = net.addRouter('r2')
r2.addRelay(Relay('10.20.0.0/24', server='10.0.0.1'))
```

---

## Instructor Lab Authoring

Every draft operation and the authoring editor require an authenticated
Instructor session. Opening `/authoring` with a missing or expired session
shows sign-in and returns to the editor after successful authentication.
Learners receive `403` for draft and authoring routes.
Request and response bodies use definition schema version 3. Canvas drafts
contain Nodes and Links, Lab-wide address-family and persistence settings,
Catalog metadata, Guide Markdown, and a Layout keyed by stable draft Node IDs.
Python drafts contain an uploaded script, Seed files, Catalog metadata, Guide
and Layout. Only schema 3 is supported; older drafts are not automatically
migrated. An unsupported stored draft is excluded from draft listings; directly
opening it reports an error without changing the file.
Stale `PUT` and `DELETE` requests return `409`; invalid definitions return
`422`.

Authoring Layout coordinates accept finite numbers on each axis, including
negative values, in virtual units independent of screen pixels. There is no
fixed canvas boundary. Existing draft coordinates are preserved;
Preview, publication and downloaded packages use those positions without
rescaling. Both authoring and running Lab views fit the Nodes to the viewport.
Drafts with negative positions or positions above 2000 require the updated
authoring application.
The running view's **Save layout** for shipped examples continues to normalize
positions into its existing 1000-unit space; it does not rewrite authored drafts.

Config Copy compatibility includes persistent Router identities, enabled
daemons, Interface attachments, VLAN subinterfaces, VRF names and table IDs,
and Interface VRF assignments. Any nonblank Canvas Code section is treated as
opaque: adding, editing or removing it requires **Start clean** or an explicit
Instructor **Config Reset** before an existing copy can be reused. Python script
changes and moved Seed directories follow the same rule. Seed text, addresses,
Guide and Layout edits do not replace an existing Group's Config Copy.
Older compatibility records that lack required VRF or Code-section evidence
also require this explicit reset; copies without these features retain their
existing compatibility. No saved configuration is discarded automatically.

```text
GET    /api/authoring/protocols
GET    /api/authoring/drafts
POST   /api/authoring/drafts
POST   /api/authoring/drafts/upload
GET    /api/authoring/drafts/{draft_id}
PUT    /api/authoring/drafts/{draft_id}
DELETE /api/authoring/drafts/{draft_id}?revision=3
POST   /api/authoring/drafts/{draft_id}/preview
GET    /api/authoring/drafts/{draft_id}/preview-status
GET    /api/authoring/drafts/{draft_id}/keep
POST   /api/authoring/drafts/{draft_id}/keep
POST   /api/authoring/drafts/{draft_id}/publish
GET    /api/authoring/drafts/{draft_id}/python
GET    /api/authoring/drafts/{draft_id}/python.zip
GET    /api/authoring/drafts/{draft_id}/drawing
GET    /api/authoring/examples/{example_id}/removal
DELETE /api/authoring/examples/{example_id}
```

`GET …/python` returns `{"filename": "<slug>.py", "source": "…"}`: the
script generated from the saved draft's definition, where `<slug>` comes from
the Catalog name. A Canvas draft's answer adds `before` and `after`, the
script around the place its Code section goes. Preview and publication run this exact script, so it is the
one executable form of the draft. `GET …/python.zip` downloads `<slug>.zip`
holding a `<slug>/` folder with the script, each persistent Router's Seed
under `frr-config/<router>/`, the Guide at `examples/guides/<slug>.md`, the
Authored Layout at `examples/layouts/<slug>.json`, and a `README.md`. Both
refuse a draft that cannot be built with `422`.

**Code section.** A Canvas draft may carry `"code_section": "…"`, text of at
most 100,000 characters, emitted at the end of `build_network()` indented
under a `# Code section` comment. Absent and empty mean the same; only a
non-empty Code section is part of the fingerprint. A Preview that fails
inside it names `Code section line N`. Its Nodes are placed in the Layout
under `"code:<name>"` keys; the running view sees `<name>`.

**Drawing.** `GET …/drawing` (Instructor only) answers
`{"nodes": [{"name", "kind", "line"}], "links": [{"ends": [{"node", "port"}],
"line"}], "source", "note"}`. The saved script runs in its own process, with
a 10-second limit, against a stand-in network that records and builds
nothing. `kind` is `router`, `host`, `switch`, `speaker`, `container`, `nat`,
`transit` or `other`. A Python draft answers its whole Topology, numbered by
script line; a Canvas draft answers only what its Code section adds,
numbered by Code section line. When the script cannot be recorded, `source`
is `"preview"` with the caller's running Preview's Topology, or `"none"`, and
`note` says why.

**Python drafts.** `POST /api/authoring/drafts/upload` takes one
`multipart/form-data` field, `file`: a `.py` script of UTF-8 text, at most
500 KB. It answers `201` with a new draft whose `kind` is `"python"`, whose
Catalog name is the file name without `.py`, and which holds no Seed files
and no Catalog ID; any other file returns `422` with the reason. A Revise of
a Python Lab Example is a Python draft too; Copy of one returns `422`.
A Python draft replaces `topology` with:

```json
{
  "kind": "python",
  "python": "from mniplab import mnIPLab\n…",
  "files": { "frr-config/lesson/r1/frr.conf": "router ospf\n" },
  "layout": { "version": 1, "nodes": { "r1": { "x": 100, "y": 200 } } }
}
```

`files` holds up to 200 text Seed files by relative path under `frr-config/`,
and the Layout is keyed by runtime Node name. Responses add `python_error`:
`null`, or the `line`, `column` and `message` where the script fails to
compile. Such a script is still created, but Preview and publication
return `422`. A save that changes `python` or `files`, or any draft's
`kind`, returns `422`: a changed script is a new upload.
Preview runs the script with its folder as the working directory. The
fingerprint covers `python` and `files`. Keep offers Router FRR sections
and writes them into the script-named Seed `frr.conf`, including a missing
Seed. Router rows include `seed` and `not_keepable`; inline FRR text, absent
Seed folders and paths outside the package's `frr-config` are refused.
Services, VLANs and Link Conditions carry `not_keepable: "change in your
script"` and cannot be selected. Kept Seeds invalidate Preview evidence. A
published Python example's Catalog row has `"kind": "python"`, and its
`node_count` comes from its last successful Preview. Update existing refuses
to change an example's kind. For Config Copy compatibility, a Python example's
structure is the script's SHA-256 and the sorted directories holding its
Seed files.

**Remove from Catalog.** `GET /api/authoring/examples/{id}/removal` answers
`{"example", "name", "config_copies"}`, the Groups holding a Config Copy of
an authored Lab Example. `DELETE /api/authoring/examples/{id}` removes it
from the Catalog with every published version and discards those copies,
answering `{"example", "discarded"}`. Both return `422` for a shipped example
and `404` for an unknown one; the delete returns `409`, naming the Group, while
any Lab of it runs or a Lab is starting. A draft response's `can_update` says
whether its Catalog ID still names a Lab Example to update; once that is
removed, the draft publishes only as new.

Create a blank draft with `{}`, copy a draft with
`{"source_id":"draft-…"}`, or Copy a Catalog example with
`{"example_id":"static-lab"}`: a draft without a Catalog ID, so it publishes
as a new Lab Example. Add `"revise": true` to Revise an authored example
instead: the draft keeps its Catalog ID for Update existing; a shipped example
returns `422`, and so does Copy of a Python example. Copy and Revise in the
Authoring page's **Lab Catalog** call
this endpoint. Every shipped example opens on the canvas; an authored example
opens as the kind of draft it was published from. Either path creates a
separate unpublished draft. A save submits
the full definition and the
revision it was opened from. For example, save this definition to the draft
whose ID is `draft-0123456789abcdef0123456789abcdef`:

```json
{
  "revision": 3,
  "document": {
    "schema_version": 3,
    "id": "draft-0123456789abcdef0123456789abcdef",
    "revision": 3,
    "created_at": 1700000000.0,
    "updated_at": 1700000000.0,
    "published_revision": null,
    "address_family": "ipv4",
    "persistent_config": true,
    "catalog": {
      "id": null,
      "name": "Basic Host Lab",
      "description": "A one-Host starter lesson.",
      "protocols": []
    },
    "topology": {
      "nodes": [
        { "id": "host-one", "name": "h1", "type": "host",
          "ipv4_gateway": "192.0.2.1", "ipv6_gateway": "", "startup": [] },
        { "id": "router-one", "name": "r1", "type": "router",
          "protocols": ["OSPF"], "fast_timers": true,
          "frr_config": "router ospf\n network 192.0.2.0/24 area 0\n",
          "startup": [{ "stage": "before-daemons",
                        "command": "ip addr add 10.9.9.9/32 dev lo",
                        "timeout": 5, "may_fail": false }] }
      ],
      "links": [{ "id": "link-one", "ends": [
        { "node_id": "host-one", "interface_id": "if-000000000001", "port": 0,
          "ipv4": "192.0.2.2/24", "ipv6": "" },
        { "node_id": "router-one", "interface_id": "if-000000000002", "port": 0,
          "ipv4": "192.0.2.1/24", "ipv6": "" }
      ] }]
    },
    "guide": "",
    "layout": {
      "version": 1,
      "nodes": { "host-one": { "x": 160, "y": 180 }, "router-one": { "x": 500, "y": 180 } }
    }
  }
}
```

Use the `id`, `revision`, and timestamps from the current draft response when
saving another draft. `address_family` is `ipv4`, `ipv6` or `dual`, and no
address of an excluded family may appear. `persistent_config` (boolean) gives
every Router Persistent Config.

Node fields by `type`:

- `host`: `ipv4_gateway` and `ipv6_gateway` are optional default gateways
  (an address of that family, or `""`). Each leaves by the Host's
  lowest-numbered Interface, as the generated `addHost` gateway does.
- `container`: the Host's gateways and Startup commands plus `image`, a
  Docker image reference used by `addContainer`.
- `nat`: `ip` is the Gateway's IPv4 address, written onto its first Link end
  in the generated script; `subnet` is passed to `addNAT` / `addTransit`.
  `transit` adds `downstream_ids`, a nonempty list of distinct Router IDs.
  Both types are drawn as an "Internet" cloud on the Learner's running
  Topology and left out of the CLI's node lists.
- `speaker`: `local_as`, `neighbors`, `routes`, and `startup`. A Neighbor has
  `peer`, `local_address`, `local_as`, `peer_as`, `hold_time` (integer or null)
  and `routes`. Global `routes` apply when that Neighbor's list is empty.
  Each Route needs `prefix` and `next_hop`; optional `community`, `as_path`,
  `large_community`, and `med` are BGPRoute fields. The Speaker is passive:
  configure the peering Router to open the session.
- `router`: `protocols` lists distinct names from `GET /api/authoring/protocols`
  (each enables its FRR Daemon; zebra and staticd always run), and
  `frr_config` is native FRR configuration (up to 100,000 characters) placed
  after the generated `hostname` line. With `persistent_config` it is the
  Seed. `fast_timers` (boolean) adds
  `hello-interval 1` and `dead-interval 3` for OSPF and OSPF6 on every
  Interface of the Router, before its `frr_config`, when the Lab starts.
- `switch`: `id`, `name`, `type`, optional `stp` (`false`, `true` for classic
  STP, or `"rstp"`) and optional `priority` (bridge priority, with the same
  limits as `addSwitch`); a bridge whose Interface ports
  start at 1. Switch ends of Links may carry `vlan`: an access port uses
  `{"mode":"access","vlan":10}`; a trunk uses
  `{"mode":"trunk","trunks":[10,20],"native":null}`. VLAN IDs are
  distinct integers from 1 to 4094; `native` may be a VLAN ID.
- `router` may also carry `subinterfaces`: a list of objects with `id`,
  `parent_interface_id`, `vlan`, `ipv4` and `ipv6`. The parent is a stable
  Interface ID on a Link attached to that Router. Addresses are optional,
  with prefix lengths when present. Each parent/VLAN pair is unique.
- A Link may also carry `condition` with `status` (`up` or `down`), `delay`
  and `jitter` (duration with units), `loss` (percentage), `bandwidth`
  (positive Mbps), and `scheduler` (`hfsc`, `htb`, or `tbf`). All six fields
  are required when `condition` is present; unset impairments are `null`.
  Jitter requires delay, and a nondefault scheduler requires bandwidth.
  Parallel Links between a Node pair cannot declare
  initial conditions because the runtime Link Condition store uses that pair.
- `host` and `router` may also carry `dhcp`, a DHCP Service:
  `{"pools":[…]}`. Each Pool has `cidr`, `start`, `end`, `lease_time` and
  `preferred_lifetime` (seconds or `null`), `router` and `dns_server`
  (an address, or `""` to omit), and `reservations`. The CIDR sets the
  family: `preferred_lifetime` is IPv6 only and `router` IPv4 only. A Node
  declares each Subnet once; two Nodes may serve the same Subnet. The Pool
  is served at Lab start through `addService(DHCPService(Pool(…)))`, which
  also resolves the CIDR to one of the Node's Interfaces.
- `host` and `router` may also carry `relay`: `{"subnets":[…],"servers":[…]}`,
  built as `addRelay(Relay(*subnets, server=servers))`. `subnets` is a
  nonempty list of distinct CIDRs; `servers` names one DHCP Service address
  per address family. Reachability of a server is not checked, so a Relay
  forwarding to an unreachable Service remains publishable.
- A Reservation has `ip`, `mac`, `duid` and `hostname`. An IPv4 Reservation
  names a `mac` only; an IPv6 Reservation names exactly one of `mac` (the
  identity a Relay supplies) or `duid`; the unused identifier is `""`.
- A Host end of a Link may carry `mac`, six colon-separated hex octets
  distinct across the Lab, passed to `addLink` as that end's address. Without
  it the Interface keeps a random MAC, so pin the MAC a Reservation names.
- A Host end of a Link may carry `dhcp4: true` or `dhcp6: true`. The
  Interface then starts without that family's address, which a Learner's DHCP
  client acquires, so its `ipv4` or `ipv6` must be `""`. A gateway is
  refused when the Host's lowest-numbered Interface is a client of its family
  (`ipv4_gateway` beside `dhcp4`, `ipv6_gateway` beside `dhcp6`), since the
  DHCP exchange or the Router Advertisement supplies it there; a client on
  another Interface leaves it valid. Router Advertisements are native FRR
  interface configuration in the Router's `frr_config`; a DHCPv6 Pool never
  implies one.
- Hosts, Container Hosts, Routers, and Speakers: `startup` is up to 50 Startup commands, each with `stage`
  (`before-daemons` or `after-start`), a non-empty `command` (up to 2,000
  characters), `timeout` (whole seconds, 1–60) and `may_fail` (boolean). See
  [`addStartupCommand`](#method-addstartupcommand) for how they run. A Catalog name can remain blank while editing, but is
required before publication. `catalog.area` must also be chosen from the fixed
Catalog Area list before publication. `GET /api/examples` returns that ordered
list as `areas` and each entry's `area`; older authored entries without an Area
read as `Other`, which is never a publishable choice.

`DELETE` removes a draft and its Preview evidence and returns `204` when the
`revision` query parameter matches the current draft. Published Catalog
entries are separate copies and remain available.

Preview and publish requests take `{"revision": 3}`. Publish runs a Preview
startup check itself when current evidence is absent, then publishes once
startup succeeds. A start failure publishes nothing. The same Lab caps,
Single-Lab Mode restrictions and exact `replace_key` confirmation apply. Preview starts from a
fresh configuration directory and counts against the normal Lab cap. Each
persistent Router's Seed is rendered from the draft beside the Preview's
builder and copied into that directory, in both Single-Lab and Multi-Lab
Mode, so Preview never writes a published Seed or any Group's Config Copy. A
Startup command that fails or times out fails the Preview with its report in
`detail`; a successful response lists the failures of commands that may fail
in `warnings`. In
Multi-Lab Mode, if the Instructor's Group already has a Lab, the first request
returns `409` with `detail.code` set to `preview_replacement_required`; after
confirmation, retry with `{"revision":3,"replace_key":"…"}`. This replaces
only that Group's Lab. In Single-Lab Mode, an occupied shared Lab blocks
Preview with `409`; stop the shared Lab before retrying. A successful response
proves startup only, not protocol convergence or connectivity.

In Single-Lab Mode, a Preview request while this draft's own Preview runs
answers `409` with `preview_replacement_required`, like Multi-Lab Mode, and
`replace_key` rebuilds it fresh; any other shared Lab still blocks Preview.

`GET …/keep` reviews each Router's running FRR configuration in the caller's
Preview against its startup baseline (or the last kept sections). Changes
are complete sections named by their top-level command. Duplicate headings,
banners, orphan subcommands or reordered sections fall back to one whole
Router. Switch-port VLANs, unambiguous Link Conditions, DHCP Pools and
Reservations, Primary Zone Records and Resolver Access Control also appear.
Shell-made addresses or gateways, other shell work, Speaker announcements and
observed/generated state are excluded. No running Preview returns `409`.
`has_kept_changes` says whether a successful Keep has saved changes from
this Preview. It survives page reloads and resets to `false` for a new
Preview; older snapshots without that information return `null`.

```json
{ "key": "k1", "has_kept_changes": false,
  "routers": [{ "node_id": "router-one", "name": "r1",
  "config": "router ospf\n ospf router-id 1.1.1.1\nexit\n",
  "digest": "…", "added": 1, "removed": 0,
  "sections": [{ "id": 0, "name": "router ospf", "whole": false,
    "added": 1, "removed": 0,
    "lines": [" router ospf", "+ ospf router-id 1.1.1.1", " exit"] }] }],
  "features": [{"id":"vlan:if-000000000002", "kind":"switch_vlan",
    "node_id":"switch-one", "node_name":"s1", "link_id":"link-one",
    "name":"s1-eth1", "before":{"mode":"access","vlan":10},
    "after":{"mode":"access","vlan":20}, "digest":"…"}] }
```

`POST …/keep` takes
`{"revision": 4, "selections": [{"node_id": "router-one", "digest": "…", "sections": [0]}], "features": [{"id":"vlan:if-000000000002","digest":"…"}]}`
and returns the saved draft. The chosen sections stay intact, and unselected
sections remain at their baseline; selected settings take the reviewed live
values. Network edits make `preview_valid` false. A stale `revision` or
`digest` returns `409`; invalid section ids and empty selections return `422`.
The old authoring `/capture` routes have been removed; Packet Capture's
separate endpoints retain their names.

Publishing requires successful Preview startup for the current network
definition. Guide and Layout edits preserve that startup evidence; network
edits invalidate it. `POST …/publish` defaults to
`{"revision":3,"mode":"save_as_new"}` and creates a new authored Catalog ID.
`{"revision":3,"mode":"update_existing"}` explicitly replaces the selected
version of the authored ID already associated with this draft. A draft cannot
retarget another Catalog ID, and a stale adaptation receives `409` if that
ID was updated since adaptation. The response contains `draft`, `example`,
and `incompatible_groups` (Group names; empty for compatible copies). The
Catalog selects a complete immutable version only after publication succeeds;
old version directories remain available to running Labs. No Group copy is
reset by publication. The Catalog's `persistent_config` flag reflects Canvas
Router persistence or an uploaded Python draft's Seed files. Opaque code may
also create a Config Copy: `resumes` and `incompatible` describe the caller's
actual copy even when this Catalog flag is false. Canvas Router Seeds are
published under the example's versioned `frr-config/<name>/`.
Authored rows appear in `GET /api/examples` and follow
the same Group Assignment rules as shipped rows. The Lab Guide and Layout load
through `GET /api/load_guide?example=<id>` and
`GET /api/load_layout?example=<id>`. Supply `&key=<running-lab-key>` to read
the immutable Guide or Layout selected when that visible Lab started; without
`key`, these endpoints read the current Catalog version.

In Multi-Lab Mode, each authored row in `GET /api/examples` also reports
`resumes` and `incompatible` for the caller's Group.
`GET /api/admin/dashboard` reports each Group's `incompatible_config_copies`.
A successful Group start records the persistent
Router structure in that Group's Config Copy. The comparison uses Router
Node ID and name, type, Interface IDs and local/peer attachment ports,
explicit subinterface IDs/parent Interface IDs/VLAN IDs, VRF names/table IDs,
Interface VRF assignments, selected Daemons, Lab-wide persistence, and Seed
directory mapping. Nonblank Canvas Code sections and Python scripts are
compared by hash because their structure is opaque. Older copies
without a record are incompatible. An incompatible ordinary `POST /api/labs`
returns `409` and preserves the copy. `{"example_id":"…","clean":true}`
performs Start clean and seeds the new structure. Instructors can use the
existing `POST /api/admin/config/reset` for one Group or all Groups; it
retains its running-Lab refusal and all-or-nothing class behavior. FRR text
changes alone are structurally compatible, so ordinary Seed edits still
require explicit reset before existing copies receive them.

See [Instructor Lab Authoring](lab-authoring.md) for the complete workflow and
scope.

---

## Web API & WebSockets

### Lab Prompt WebSocket

Multiplexes the Lab's CLI prompt (`mininet-iplab>`) to attached browser viewers and the launching terminal.

**Endpoint**: `ws://<host>:<port>/ws/labs/{key}/prompt`

**Authentication**: Session cookie from `/api/login`, and the Instructor role.
A Learner receives `{"type": "error", "message": "The Lab Prompt is the
Instructor's; use a Node Terminal"}` and the connection closes with code 1008
(policy violation).

**Message Protocol**:
- **Client to Server**:
  - `{"type": "input", "data": "<keystrokes or command>"}`: Send input string to the CLI prompt.
  - `{"type": "resize", "cols": <int>, "rows": <int>}`: Resize pseudo-terminal dimensions.
- **Server to Client**:
  - `{"type": "output", "data": "<text>"}`: Screen/terminal output from the Lab's CLI.
  - `{"type": "error", "message": "<reason>"}`: Error notification or lab stopped notice.

---

## Command Line (`python -m mniplab`)

| Command | What it does |
| --- | --- |
| `serve [--web-host H] [--web-port P] [--log-level L]` | Start the classroom web server with no Lab running; Labs are started from the browser |
| `cleanup` (or `-c`) `[--no-keep-committed]` | Remove the runtime root and stop **every** Lab on the host, kill FRR processes and remove temp files; `--no-keep-committed` also removes `./frr-config/`. Never run it while `serve` is up |
| `kill-frr` | Kill FRR daemon processes |
| `cleanup-configs` | Remove temporary FRR config files |
| `cleanup-mniplab` | Remove mnIPLab temp files |
| `cleanup-mininet` | Clean up Mininet leftovers |
| `cleanup-python` | Remove Python cache files |
| `-v`, `--version` | Print the version |

A Lab script built on `parse_lab_args` takes `--enable-web`, `--web-host`,
`--web-port` and `--log-level`. For both a Lab script and `serve`, an unset
`--web-host` / `--web-port` comes from `MNIPLAB_HOST` / `MNIPLAB_PORT`, else
`0.0.0.0` / `8050`.

---

## See Also

- [Creating Labs](CREATE_LAB.md) - Tutorial for building topologies
- [Web UI Guide](web-ui.md) - Web interface documentation
- [ExaBGP Documentation](EXABGP.md) - Advanced ExaBGP usage
- [Examples](../examples/) - Sample lab topologies
