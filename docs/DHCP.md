# DHCP Service

A **Service** is a network service a Node offers to the Lab, alongside whatever
routing Protocol it runs. Unlike a Protocol, a Service attaches to *any* Node,
Host or Router, and a Node may carry several. DHCP is backed by ISC Kea, so
what a Learner sees in a Lease table or a packet capture is what a production
server does.

One Service serves both address families: a `Pool` whose CIDR is an IPv6 prefix
is a DHCPv6 Pool, and `kea-dhcp4` / `kea-dhcp6` are started according to what
the Service actually holds.

## Quick Start

```bash
python3 examples/dhcp-lab.py [--enable-web]
```

Then, from a Terminal on `h1`, take a Lease in the foreground so the exchange is
visible as it happens:

```bash
dhcpcd -1 -B -d -4 h1-eth0
```

## Requirements

All of these are built into the container image; nothing to install for the
Docker Compose or dev container setup.

- **ISC Kea 3.2** (`kea-dhcp4`, `kea-dhcp6`), pinned to ISC's own packages
  because DHCPv6 relaying needs it.
- **dhcpcd** on the client side, so a Learner runs a real client by hand.
- **dnsmasq** in relay-only mode, used for a Relay and nothing else.

## How It Works

- **Service as a role**: `addService(DHCPService(...))` attaches to a Host or
  a Router without changing what kind of Node it is. The Topology still shows
  a Host or a Router, now carrying a `DHCP` badge.
- **Configuration generation**: one Kea configuration per Node is rendered from
  the Pools the Service holds and written into the Node's private directories
  at start; the Kea daemons run inside the Node's namespace.
- **Pools resolve by CIDR**: each `Pool` names its Subnet by CIDR, and at Lab
  start the framework finds the Interface that Subnet sits on, either on the
  Service's own Node or, through a Relay, elsewhere in the Lab.
- **Nothing acquires a Lease on a Learner's behalf**. A client
  Interface comes up bare, and the Learner runs `dhcpcd` themselves.
- **Lifecycle**: Kea and any Relay start with the Lab and stop with it. Pools
  a Learner edits at runtime are Exercise Configuration and are
  discarded on `net.stop()`, so the next run starts from what the Lab Example
  declared.

## Configuration

### Attaching a Service

```python
from mniplab.dhcp import DHCPService, Pool

r1 = net.addRouter('r1')
r1.addService(DHCPService(
    Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
))
```

`addService` works on any Host or Router. A Node may serve several Subnets
from one Service, one `Pool` per Subnet, and a Subnet with no `Pool` simply
gets no answer, which is how you stage a deliberate "no DHCP here" contrast
for a Learner to diagnose.

### Marking a client Interface

Nothing acquires an address on a Learner's behalf. Mark an
Interface as a DHCP client with `dhcp4=True` in its link params, and the
Interface comes up bare until someone runs the client themselves. A static
`ip` on the same side is refused while the Lab is built; a static `ip6`
beside `dhcp4=True` stays valid, so a Lab can serve one family statically
and the other by DHCP:

```python
net.addLink(r1, h1, params1={'ip': '10.50.0.1/24'}, params2={'dhcp4': True})
```

Inside `h1`'s Terminal, a Learner takes the Lease with `dhcpcd`, in the
foreground and verbose so the exchange is visible as it happens:

```bash
dhcpcd -1 -B -d -4 h1-eth0
```

### Declaring a Pool by CIDR

A `Pool` names its Subnet by CIDR rather than by link order, so reordering
`addLink` calls in a Lab Example never breaks the declaration:

```python
from mniplab.dhcp import Pool, Reservation

Pool(
    cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
    lease_time=120,             # seconds; omit to leave Kea's own default
    router='10.50.0.1',         # omit to skip the option entirely
    dns_server='8.8.8.8',       # omit to skip the option entirely
    reservations=(
        Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50', hostname='h1'),
    ),
)
```

`lease_time`, `router`, `dns_server`, and `reservations` are the same four
fields a Learner can edit later from the Web UI's Service panel. Declaring
them here just gives the Lab a starting point instead of an empty one.

### Reservations

A `Reservation` is a fixed address declared in advance for one named client,
handed out in place of one from the Pool. It is identified by a hardware
address or a DUID, exactly one, and never by hostname (Kea requires a hardware
identifier or a DUID, and DHCPv4 has no DUID). An IPv4 Reservation names the
hardware address; an IPv6 one names either, the DUID being the baseline (see
the DHCPv6 Guide's "IPv6 Reservations" section).

Pin a Host's MAC via `addLink`'s **top-level** `addr1`/`addr2` argument, or
read back the one `autoSetMacs` already assigned via `host.params['mac']`
right after `addHost()`. Do not put `'mac'` inside `params1`/`params2`: that
collides with Mininet's own `Link.__init__`, which already passes
`mac=addr1`/`addr2` to the Interface constructor.

### Serving IPv6 (DHCPv6)

A `Pool` whose CIDR is an IPv6 prefix is a DHCPv6 Pool. The family is inferred
from the CIDR, with no separate declaration syntax. Mark an Interface as a
DHCPv6 client with `dhcp6=True`; a static `ip6` on the same side is refused,
as a static `ip` is beside `dhcp4=True`, and a `defaultRoute6` leaving by
that Interface is refused at start, since the default route comes from the
Router Advertisement ([below](#a-hosts-gateway-beside-a-dhcp-client)). A
Learner runs the client with `-6`:

```python
net.addLink(r1, h1, params1={'ip6': '2001:db8:1::1/64'}, params2={'dhcp6': True})
```

```bash
dhcpcd -1 -B -d -6 h1-eth0
```

Two fields are family-specific. A DHCPv6 Pool has **no router option**, since
the default gateway comes from the Router Advertisement and not from DHCPv6,
so declaring `router=` on an IPv6 Pool is a validation error saying exactly
that. And `preferred_lifetime` (IPv6 only) is how long an address stays
preferred before becoming deprecated; it must not exceed `lease_time` (the
valid lifetime). Both lifetimes appear on each v6 Lease in the Lease table.

The part DHCPv6 genuinely adds is the **Router Advertisement**, and it stays
in the Instructor's hands as raw FRR configuration. There is no typed
parameter and no generation from the Service, because the M, O and A flags
are the subject matter:

```
interface r1-eth0
 ipv6 nd prefix 2001:db8:1::/64 no-autoconfig   ← A=0: no SLAAC from this prefix
 ipv6 nd managed-config-flag                    ← M=1: ask DHCPv6 for an address
 no ipv6 nd suppress-ra                         ← Router Advertisements on (FRR suppresses by default)
```

Stateful DHCPv6 depends on the Router Advertisement rather than replacing it:
the client will not start DHCPv6 until a Router Advertisement sets the M (or
O) flag, and a host's default route comes from the Router Advertisement in
every case. Mark the prefix `no-autoconfig` *before* un-suppressing the Router
Advertisement, or the host autoconfigures an address first. The DHCPv6 Lab
Example (`examples/dhcp-lab.py`) stages three IPv6-only Subnets on one Router:
M set, O without M, and neither flag (SLAAC alone).

Two start-time diagnostics check that the Router Advertisements and the Pools
agree, and both are **warnings, not errors**, because both
conditions are stageable lessons: one fires when a served Subnet has no Router
Advertisement setting the M flag, the other when a served prefix still
advertises autoconfiguration. Each names the FRR line that would change it.
Fatal stays reserved for what can never work: a CIDR matching no Interface,
and the missing-Relay case below.

### A Host's gateway beside a DHCP client

A Host's `defaultRoute` and `defaultRoute6` leave by its lowest-numbered
Interface, or by the one their `dev` names. When that Interface is a DHCP
client of the gateway's family, `start()` refuses the Lab, naming the Host,
the gateway, the Interface and the flag: remove the static default or the
flag. A DHCP client on another Interface, or of the other family, leaves the
gateway as declared:

```python
h1 = net.addHost('h1', defaultRoute='via 192.168.1.1')   # leaves by h1-eth0
net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
net.addLink(h1, r2, params1={'dhcp4': True}, params2={'ip': '10.50.0.1/24'})
```

The refusal is mnIPLab's teaching policy, not a protocol rule. DHCPv4 may
hand out a gateway in its optional Router option
([RFC 2132 §3.5](https://www.rfc-editor.org/rfc/rfc2132.html#section-3.5)),
which a Pool sets with `router=`. DHCPv6 has no such option: a host learns its
default router from a Router Advertisement's Router Lifetime
([RFC 4861 §4.2](https://www.rfc-editor.org/rfc/rfc4861.html#section-4.2),
[RFC 8504 §8.2](https://www.rfc-editor.org/rfc/rfc8504.html#section-8.2)).
Neither protocol forbids a static default beside a dynamic address; mnIPLab
refuses it on the client Interface so that the route stays something the
Learner obtains and can watch arrive.

### Serving Remote Subnets with DHCP Relay

In real networks, a central DHCP server typically serves multiple subnets
reachable through intermediate routers. In Mininet-IPLab, an author models
this by attaching a **Relay** (`mniplab.dhcp.Relay`) to a Node fronting remote
clients:

```python
from mniplab.dhcp import Relay

# On relay router r2, cover one or more Subnets and name the central Service's address
r2.addRelay(Relay('10.20.0.0/24', '2001:db8:20::/64', server=['10.0.0.1', '2001:db8:0::1']))
```

A Relay runs `dnsmasq` in relay-only mode (`--port=0 --log-dhcp`). The central
server (`r1.addService(DHCPService(...))`) declares Pools for both direct and
relayed Subnets:

```python
# On central router r1:
r1.addService(DHCPService(
    Pool(cidr='10.10.0.0/24', start='10.10.0.100', end='10.10.0.200'),
    Pool(cidr='10.20.0.0/24', start='10.20.0.100', end='10.20.0.200'),  # Relayed subnet
))
```

A Relay is not a Service: it holds no Pool and answers no client. It belongs
to the Lab's shape, so an Instructor declares it and a Learner can see it but
not change it. In the Web UI it is drawn as a **Relay Path**, a logical
overlay from the client Subnet through the relaying Router to the upstream
Service.

#### Resolution and Start-Time Validation

At Lab start, Pool resolution evaluates four branches:

1. **Direct Subnet**: The Pool matches an Interface on the Service's own Node.
2. **Relayed Subnet**: The Pool matches an Interface elsewhere in the Lab, and a
   `Relay` covering that Subnet exists. The server listens on the transit interface
   facing the Relay.
3. **Missing Relay (Fatal)**: The Pool matches an Interface elsewhere in the Lab,
   but **no** Relay covers it. The Lab refuses to start and names the missing
   declaration:
   ```text
   r1: Pool CIDR 10.50.0.0/24 is not attached to this Node, and no Relay covers it.
   To serve this remote Subnet, attach a Relay to the Node hosting it:
     r2.addRelay(Relay('10.50.0.0/24', server='<r1-ip>'))
   ```
4. **Unknown Subnet (Fatal)**: The Pool CIDR matches no Interface anywhere in the
   Lab. This fails as an invalid CIDR / typo.

#### The Two Boundaries of a Relay

An author must be aware of two fundamental constraints:

1. **A Node cannot be both relay and server on one Interface**:
   Declaring a Relay and a Service on the same Node covering the same Interface/Subnet
   is rejected at start time with a `ConfigurationError`. Under `dnsmasq`, relaying
   silently takes precedence over serving, and `dnsmasq` and Kea would collide on
   wildcard ports. Across different Nodes (which have separate network namespaces),
   coexistence is completely safe.

2. **A Relay always names its Service's address**:
   A Relay requires explicit unicast server address(es) via the `server=` argument
   (e.g., `server='10.0.0.1'` or `server=['10.0.0.1', '2001:db8:0::1']`).
   Server-less relaying (broadcast or multicast) is not permitted.
   If a Relay names a Service address that is not assigned to any Node in the Lab,
   the framework emits a start-time **warning** rather than failing,
   allowing instructors to deliberately stage unreachable-service scenarios.

#### Relayed IPv6 Reservations by Hardware Address

A major advantage of a Relay for IPv6 is hardware-address identification (RFC 6939):

- On a **relayed IPv6 Subnet**, the Relay inserts Option 79 (client link-layer
  address). Kea consumes this and matches reservations by MAC address:
  ```python
  Reservation(mac=h2_mac, ip='2001:db8:20::50', hostname='h2')
  ```
- On a **directly attached IPv6 Subnet**, no relay is in the path. Because host
  DUIDs are not generated until the client runs, an instructor cannot pre-declare
  MAC reservations on directly attached IPv6 subnets. Learners must author them
  by DUID in the Web UI.

### A rogue server

Two Services on one Subnet is a misconfiguration in production and a
deliberate lesson here. Nothing validates against it, because a Learner
watching a client take the wrong Lease is the point:

```python
s1.addService(DHCPService(Pool(cidr='10.60.0.0/24', start='10.60.0.100', end='10.60.0.150')))
s2.addService(DHCPService(Pool(cidr='10.60.0.0/24', start='10.60.0.151', end='10.60.0.200')))
```

`examples/dhcp-lab.py` stages it without a second Service: its `rogue` Host
sits on the directly served Subnet running nothing, and the Guide has the
Learner start a `dnsmasq` DHCP server there, then stop it. Either way it needs
a switch (`net.addSwitch(...)`, which needs Open vSwitch), since more than two
Nodes sharing one Subnet needs a shared L2 segment, not a point-to-point link.

## Runtime Operations

### CLI

```
mininet-iplab> show_leases r1            # the Leases r1's Service holds
mininet-iplab> show_leases r1 6          # one family at a time
mininet-iplab> show_relay_activity r2    # the requests r2's Relay has forwarded
```

A Lease is the server's own record, reported separately from configured
Interface addresses (`show_ips`): it exists before a client applies it and
outlives the moment it stops being used, which is what makes DHCP's failure
modes observable.

### Web UI

The Topology badge names the families a Service serves (`DHCP`, `DHCPv6`, or
`DHCP · DHCPv6`). Opening the Service panel from the badge shows:

- One **Lease table** per family. A v6 Lease is identified by DUID where a v4
  one shows a hardware address, and a relayed Lease is labelled as such.
- The **Pool editor**: `lease_time`, `router`, `dns_server`, and
  `reservations`, applied to the running Kea without a restart and preserved
  across existing Leases.
- A **Relay panel** on a relaying Node, listing the requests it has forwarded,
  and the Relay Path drawn on the canvas.

### REST

All endpoints require the authenticated session cookie:

```text
GET  /api/labs/{key}/dhcp/leases/{node}
GET  /api/labs/{key}/dhcp/pools/{node}
POST /api/labs/{key}/dhcp/pools/{node}
GET  /api/labs/{key}/dhcp/relay/activity/{node}
```

`{key}` is the running Lab's key, which `GET /api/status` reports (see
[web-ui.md](web-ui.md#api-endpoints)). `POST /api/labs/{key}/dhcp/pools/{node}` takes the Pool's `cidr`, `start`, and `end` and
the four editable fields above.

## Troubleshooting

### The Lab refuses to start naming a Pool CIDR

The CIDR matches no Interface in the Lab (a typo), or it matches an Interface
on another Node and no Relay covers it. The message names the `addRelay` call
that would fix the second case.

### The Lab refuses to start naming a default route

A Host's `defaultRoute` or `defaultRoute6` leaves by a DHCP client Interface of
its family, or names an Interface the Host lacks. Remove the static default or
the client flag, or name another Interface with `dev`; see
[A Host's gateway beside a DHCP client](#a-hosts-gateway-beside-a-dhcp-client).

### The client never gets an address

- Check the Interface was marked: `dhcp4=True` / `dhcp6=True` in the link
  params starts it bare and refuses a static address of that family, so an
  Interface that still shows one was never marked.
- Run the client verbose in the foreground (`dhcpcd -1 -B -d -4 <intf>`) and
  read the exchange.
- For DHCPv6, the client does not start until a Router Advertisement sets the
  M flag. The start-time warnings name the FRR line to change.

### Two Nodes answer on one Subnet

Intended when staged (the `rogue` Host in `examples/dhcp-lab.py`); otherwise
check for two Services declaring overlapping Pools on the same CIDR.

### Kea logs

Kea runs inside the Node's namespace:

```bash
./util/connect_node.sh r1 bash
cat /var/run/kea/kea-dhcp4.log      # kea-dhcp6.log for the v6 server
```

## Example Labs

| Lab Example | Guide | What it stages |
|---|---|---|
| `examples/dhcp-lab.py` | `examples/guides/dhcp-lab.md` | A central Service with direct, relayed, IPv4-only and IPv6-only Subnets, the three Router Advertisement outcomes, and a rogue server to start and stop |

## See Also

- [Creating Labs](CREATE_LAB.md) for the topology the Service attaches to
- [DNS Service](DNS.md), the other Service family
- [Web UI Guide](web-ui.md) for the Service panels
- [API Reference](api-reference.md) for `Relay` and `addRelay`
