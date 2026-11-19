# DNS Service

DNS in Mininet-IPLab is two Services, both attached to any Node, Host or
Router, via `node.addService(...)`, and both real software:

- An **Authoritative DNS Service**, backed by Knot DNS (`knotd`), hosts Zones.
  A Zone is either a **Primary**, where Records are authored, or a
  **Secondary**, which acquires its copy by Zone Transfer.
- A **Recursive Caching Resolver Service**, backed by Unbound, queries the
  hierarchy on a client's behalf and caches what it learns.

**DNSSEC** spans both: a Primary signs its Zones under Knot's KASP engine, a
Secondary serves the same signatures, and a Resolver validates them against a
Trust Anchor.

## Quick Start

```bash
python3 examples/dns-lab.py [--enable-web]            # Root, TLD, Primary, Secondary, Resolver and client
python3 examples/dnssec-lab.py [--enable-web]         # the signed hierarchy and a validating Resolver
```

Then, from a Terminal on a client Host:

```bash
dig web.example.com                  # resolves through the Host's declared nameservers
dig +dnssec web.example.com @10.0.5.2   # DNSSEC Lab: the answer, its RRSIG, and the ad flag
```

## Requirements

All of these are built into the container image; nothing to install for the
Docker Compose or dev container setup.

- **Knot DNS** (`knotd`, `knotc`, `keymgr`) with the `python3-libknot`
  bindings, chosen over BIND 9 for its transactional control channel.
  Its KASP engine does the DNSSEC signing.
- **Unbound** for the Resolver, chosen over Knot Resolver and BIND 9 for a
  single binary with a direct control socket. Its validator module
  does the DNSSEC validation.
- **`dig`, `delv`** (`dnsutils`) on the client side.

## How It Works

- **Service as a role**: `addService(DNSService(...))` or
  `addService(ResolverService(...))` attaches to a Host or a Router without
  changing what kind of Node it is. The Topology shows a `DNS`, `DNS ·
  SECONDARY`, or `RESOLVER` badge on the Node.
- **Configuration generation**: one Knot or Unbound configuration per Node is
  rendered from the Zones or Resolver settings the Service holds and written
  into the Node's private directories; the daemons run inside the Node's
  namespace and are driven over their own control sockets.
- **Instructor shape, Learner exercise**: a Zone's apex and authority type,
  the Root Hints, the Trust Anchor, and the signing policy are Lab shape an
  Instructor declares. The Records within a Zone, a Resolver's Access
  Control, and a DS in a parent Zone are Exercise Configuration
  that a Learner authors and that `net.stop()` discards.
- **Runtime record changes are transactions**: every add, edit, or delete
  runs `zone-begin`, `zone-set`/`zone-unset`, `zone-diff`, `zone-commit`, so
  a Zone is never served half-edited and a rejected change leaves it intact.
- **Client isolation**: a Host declared with `nameservers=[...]` gets a
  private `/etc/resolv.conf` in its own mount namespace, so `ping`, `curl`,
  and `dig` on that Host resolve against the Lab and the machine running the
  Lab is untouched.
- **Classroom timers**: a Resolver caps positive answers at 300 s and negative
  ones at 60 s, and a Primary's SOA is generated with `retry 60` and
  `expire 600` while `refresh` stays at 3600, so the lessons about
  replication and expiry fit inside a class.

## Configuration

### Authoritative: declaring Zones and Records

```python
from mniplab.dns import DNSService, Zone, ARecord, CNAMERecord, PTRRecord

dns1 = net.addHost('dns1', defaultRoute='via 10.0.0.1')  # 10.0.0.2/24 on its Link end

forward_zone = Zone(
    'example.com.',
    [
        ARecord('ns1', '10.0.0.2'),
        ARecord('web', '10.0.2.2'),
        CNAMERecord('alias', 'web.example.com.'),
    ],
    host_node='dns1',
)

reverse_zone = Zone(
    '0.0.10.in-addr.arpa.',
    [
        PTRRecord('2', 'ns1.example.com.'),
    ],
    host_node='dns1',
)

dns1.addService(DNSService(forward_zone, reverse_zone))
```

Record types: `ARecord`, `AAAARecord`, `CNAMERecord`, `PTRRecord`,
`TXTRecord`, `MXRecord`, `NSRecord`, `SOARecord`, and `DSRecord`. Each takes
an owner name relative to the apex (`'@'` for the apex itself, or a fully
qualified name ending in `.`) and an optional `ttl` (default 300). Standard
SOA and apex NS records are generated automatically when omitted. Two Nodes
claiming Primary authority over one apex is a start-time error, because it
can never work.

### Authoritative: isolating client resolvers

To let client Nodes resolve names transparently without modifying the host
system's DNS, declare `nameservers` on `net.addHost()`:

```python
h1 = net.addHost('h1', defaultRoute='via 10.0.1.1', nameservers=['10.0.0.2'])
```

This creates a private `/etc/resolv.conf` mount namespace for `h1`, allowing
standard operating system tools (`ping web.example.com`, `curl`, `dig`) to
resolve against the Lab's DNS server. Declare two nameservers and the Host
has client-side failover to exercise; the framework ships no resolver
options, so the default glibc stall is part of the lesson.

### Secondary: replicating a Zone

A Secondary carries an apex and its Primary and nothing else. Its Records
arrive over the wire, so it has none to seed, and passing any is rejected
rather than silently ignored:

```python
from mniplab.dns import DNSService, SecondaryZone, Zone

ns1.addService(DNSService(Zone('example.com.', records, host_node='ns1')))
ns2.addService(DNSService(SecondaryZone('example.com.', primary=ns1)))
```

`primary` is the Node itself or an IP address string. The Primary's transfer
ACL is derived from the Secondaries declared against it, so nothing else is
needed for the initial AXFR. When the Primary's Serial moves, it sends a
**Notify**, and the Secondary opens an incremental transfer (IXFR) for the
difference; a refresh that finds the Serial unchanged transfers nothing.

To stage a refused transfer, give the Primary an explicit `allow_transfer`,
which replaces the derived ACL outright:

```python
Zone('example.com.', records, host_node='ns1', allow_transfer=['10.0.9.9'])
```

Four failures are reported as **runtime state on the Secondary's panel** and
produce no start-time diagnostic at all, because each is a working
configuration that might be the point: the Primary unreachable, a Zone
Transfer refused by ACL, an expired Zone answering `SERVFAIL`, and a Serial
that failed to advance. The one start-time check is a warning, for a
Secondary whose apex no Node in the Lab serves as Primary.

The generated SOA timers are classroom ones: `retry 60`,
`expire 600`, and `refresh 3600`. Refresh is deliberately long, so that when
a change propagates a Learner can tell it was Notify that did it and not the
timer.

### Resolver: seeding the Iterative Walk from in-lab Root Hints

A Lab has no route to the public root servers, so the Resolver is seeded with
**Root Hints** naming the Lab's own root nameserver. A hint may be a Node or a
bare IP address string:

```python
from mniplab.dns import ARecord, DNSService, NSRecord, Zone
from mniplab.resolver import ResolverService

root1 = net.addHost('root1', defaultRoute='via 10.0.1.1')  # 10.0.1.2/24 on its Link end
root1.addService(DNSService(Zone(
    '.',
    [
        NSRecord('@', 'root1.'),
        ARecord('root1', '10.0.1.2'),
        NSRecord('com', 'ns.com.'),        # delegation to the TLD nameserver
        ARecord('ns.com.', '10.0.2.2'),    # its glue record
    ],
    host_node='root1',
)))

res1 = net.addHost('res1', defaultRoute='via 10.0.4.1')  # 10.0.4.2/24 on its Link end
res1.addService(ResolverService(root_hints=[root1]))
```

With a `.` Zone on `root1`, a `com.` Zone delegating to an authoritative Node,
and `example.com.` on that Node, a query walks Root → TLD → Authoritative
entirely inside the Lab.

### Resolver: forwarding instead

Declare `forwarders` instead, and the Resolver passes every query upstream
rather than walking the hierarchy itself:

```python
res1.addService(ResolverService(forwarders=['10.0.0.1']))
```

Declaring both is legal: forwarding wins, and a start-time warning says so.

### Resolver: TTL caps

Positive answers are capped at `max_ttl` (300 s) and negative ones at
`max_negative_ttl` (60 s), so a classroom exercise never waits out a
production-scale TTL:

```python
res1.addService(ResolverService(root_hints=[root1], max_ttl=120, max_negative_ttl=30))
```

### Resolver: Access Control

The Resolver starts accepting every in-lab client (`0.0.0.0/0 allow`,
`::/0 allow`). An Instructor may narrow that in the Lab Example:

```python
from mniplab.resolver import AccessRule

res1.addService(ResolverService(
    root_hints=[root1],
    access_control=[AccessRule('10.0.0.0/8', 'allow'), AccessRule('10.0.9.0/24', 'refuse')],
))
```

Actions are `allow`, `refuse` (answer `REFUSED`), and `deny` (drop). Rules a
Learner authors later are Exercise Configuration and are discarded on
`net.stop()`, so the next run starts from what the Lab Example declared.

### Resolver: pointing clients at it

`nameservers` on `net.addHost()` works the same way it does for an
Authoritative DNS Service. Give it the Resolver's address and `ping`, `curl`
and `dig` all resolve through it:

```python
h1 = net.addHost('h1', defaultRoute='via 10.0.5.1', nameservers=['10.0.4.2'])
```

### Resolver: start-time diagnostics

Startup **fails** when the configuration can never resolve anything: neither
Root Hints nor forwarders and no default route, a hinted Node with no address,
or a hint or forwarder the Resolver has no route to. It **warns and runs** when
the misconfiguration is the lesson: Root Hints aimed at a Node that does not
host the `.` Zone, a forwarder that is nobody's nameserver, or Root Hints
shadowed by forwarders.

### DNSSEC: signing a Zone

Declare `dnssec=True` on a Zone and Knot's KASP engine generates a split
KSK/ZSK pair (ECDSA P-256, algorithm 13) and signs the Zone at Lab build
time, so the keys exist before any daemon starts. Zones prove
non-existence with plaintext NSEC by default; pass `nsec3=True` (and an
optional `nsec3_salt`) to hash the names instead. A salt is hexadecimal
bytes of any length, including none (`''`), and stays as declared; without
one, Knot picks a random 8-byte salt.

```python
example_zone = Zone('example.com.', records, host_node='auth1', dnssec=True)
auth1.addService(DNSService(example_zone))
```

A Secondary of a signed Zone needs nothing extra. It replicates the `RRSIG`,
`DNSKEY`, and `NSEC` Records with everything else and serves signatures it
could never have produced, holding no private key.

### DNSSEC: publishing the delegation

The parent vouches for the child by publishing a **DS** Record, a digest of
the child's KSK. Because the keys exist at build time, a parent Zone can take
it straight from the child:

```python
com_zone = Zone(
    'com.',
    [
        NSRecord('@', 'ns.com.'),
        ARecord('ns', '10.0.2.2'),
        NSRecord('example', 'ns1.example.com.'),
        ARecord('ns1.example.com.', '10.0.3.2'),
        example_zone.ds_record(),          # the delegation the chain of trust hangs on
    ],
    host_node='tld1',
    dnssec=True,
)
```

A DS is Exercise Configuration: a Learner edits it in the parent Zone to
break the chain (a digest of the right shape and the wrong value turns every
answer beneath it into `SERVFAIL`) and again to repair it. A DS that does not
match the child's key at start is a **warning**, not an error,
because the broken delegation is a stageable lesson.

### DNSSEC: validating on the Resolver

Give the Resolver a **Trust Anchor**, the key it is told to trust in advance,
and Unbound's validator checks every answer's chain of signatures back to it:

```python
res1.addService(ResolverService(root_hints=[root1], trust_anchors=[root1]))
```

A trust anchor may be a Node hosting a signed Zone, a `Zone`, a `DSRecord`,
or a raw RR string. A validated answer carries the `ad` flag and is cached as
`SECURE`; an answer whose chain does not hold is answered `SERVFAIL` and not
cached at all, which is the designed failure mode. An unsigned Zone beneath
the anchor is `INSECURE`, not bogus.

### DNSSEC: rolling keys

A ZSK rollover is local to the Zone; a KSK rollover changes the DS the parent
must publish, which is why one is a routine operation and the other is a
conversation with the parent:

```
mininet-iplab> dnssec_rollover auth1 example.com. zsk
mininet-iplab> dnssec_rollover auth1 example.com. ksk
```

## Runtime Operations

### CLI

| Command | What it does |
|---|---|
| `show_dns <node>` | Service status, listeners, and hosted Zones |
| `show_dns_records <node> <zone>` | Active Resource Records |
| `dns_records <node> <zone> add <owner> <type> <data...> [ttl]` | Add or replace a Record |
| `dns_records <node> <zone> del <owner> [type] [data...]` | Remove a Record |
| `show_dns_stats <node> [--reset]` | Live query and response counters |
| `show_dns_transfers <node> [zone]` | The Zone Transfers a Node has made, newest last |
| `dns_refresh <node> <zone>` | Ask a Secondary whether its Primary's Serial has moved |
| `dns_retransfer <node> <zone>` | Ask a Secondary to take a full copy regardless of Serial |
| `dnssec_keys <node> <zone>` | Keys signing a Zone and the DS for its parent |
| `dnssec_rollover <node> <zone> [zsk\|ksk]` | On-demand key rollover |
| `show_resolver <node>` | A Resolver's mode, hierarchy, and Access Control |
| `show_cache <node>` | Query metrics, then every cached entry with its validation state |
| `flush_cache <node> [name] [--zone]` | Evict the cache, or one name, or a name and everything beneath it |
| `resolver_access <node> [add <cidr> <allow\|refuse\|deny> \| del <cidr>]` | Show or author Access Control, applied hot |

### Web UI

A Node with an Authoritative Service carries a `DNS` badge (a Secondary,
`DNS · SECONDARY`); a Resolver, a teal `RESOLVER` badge. Opening the badge
gives:

- **DNS modal**: the hosted Zones, a Record editor with server-side
  validation shown in the form, live query statistics, and Zone status. A
  Secondary's Records are read-only, with a link to its Primary's editor, and
  its panel carries the replication header (Primary, Serial, last transfer),
  the Transfer Log, **Refresh** and **Full Transfer** buttons, and the runtime
  state banner. A signed Zone shows a **Signed** badge and a DNSSEC Details
  section listing its keys and DS, with a **Copy DS** button.
- **Resolver modal**: a Cache Inspector (metrics, every cached entry with
  `HIT`/`NXDOMAIN`/`NODATA` status and `SECURE`/`BOGUS`/`INSECURE` validation,
  TTLs counting down, per-row and whole-cache flush) and an Access Control
  editor applied in one hot reload that keeps the cache.

See [web-ui.md](web-ui.md#dns-service-modal) for the modals in detail.

### REST

All endpoints require the authenticated session cookie:

```text
GET    /api/labs/{key}/dns/nodes
GET    /api/labs/{key}/dns/{node}/zones/{zone}/records
POST   /api/labs/{key}/dns/{node}/zones/{zone}/records
DELETE /api/labs/{key}/dns/{node}/zones/{zone}/records
GET    /api/labs/{key}/dns/{node}/stats
POST   /api/labs/{key}/dns/{node}/stats/reset
GET    /api/labs/{key}/dns/{node}/zones/{zone}/status
GET    /api/labs/{key}/dns/{node}/transfers
POST   /api/labs/{key}/dns/{node}/zones/{zone}/refresh
POST   /api/labs/{key}/dns/{node}/zones/{zone}/retransfer
GET    /api/labs/{key}/dns/{node}/zones/{zone}/dnssec
POST   /api/labs/{key}/dns/{node}/zones/{zone}/dnssec/rollover
GET    /api/labs/{key}/nodes/{node}/resolver/cache
POST   /api/labs/{key}/nodes/{node}/resolver/cache/flush
GET    /api/labs/{key}/nodes/{node}/resolver/access
PUT    /api/labs/{key}/nodes/{node}/resolver/access
```

`{key}` is the running Lab's key, which `GET /api/status` reports (see
[web-ui.md](web-ui.md#api-endpoints)).

Record and Access Control mutations broadcast `DNS_RECORD_UPDATED` and
`RESOLVER_ACCESS_UPDATED` to every open session. See
[api-reference.md](api-reference.md#dns-web-api) for payloads.

## Troubleshooting

### A client on the Lab resolves nothing

- Check the Host was declared with `nameservers=[...]`; without it the Host
  has the machine's own `/etc/resolv.conf`, which knows nothing of the Lab.
- Query the server directly to separate the client from the Service:
  `dig @10.0.0.2 web.example.com`.
- `dig` bypasses local caches; `ping` and `curl` go through glibc. If the two
  disagree, it is the client's resolver configuration, not the Service.

### The Secondary never replicates

- Its panel (or `show_dns_transfers ns2`) names the cause: Primary
  unreachable, transfer refused, or Serial not advanced. Each is the designed
  runtime state for a staged lesson, not a start-time error.
- At Lab boot the first transfer may run before routing has converged; Knot
  retries on its own a few seconds later.
- Never take a client Host's Link down to stage a break; Mininet does not
  restore its routes on `link up`. Break the routed core Link instead.

### The Resolver answers `SERVFAIL`

With DNSSEC, `SERVFAIL` is the validator refusing to hand over data it cannot
prove. `show_cache res1` shows which links of the chain are still `SECURE`
and which entry is missing; `delv @<resolver> -a <anchor-file> <name>` says
which link broke. Without DNSSEC, check the Resolver's Root Hints or
forwarders with `show_resolver res1`.

### Knot and Unbound logs

Both run inside the Node's namespace:

```bash
./util/connect_node.sh dns1 bash
knotc status
knotc zone-status example.com.

./util/connect_node.sh res1 bash
unbound-control status
```

## Example Labs

| Lab Example | Guide | What it stages |
|---|---|---|
| `examples/dns-lab.py` | `examples/guides/dns-lab.md` | The whole resolution chain on one Router: forward and reverse Zones, Zone Transfer to a Secondary, a caching Resolver, SOA expiry |
| `examples/dnssec-lab.py` | `examples/guides/dnssec-lab.md` | A signed hierarchy and a validating Resolver; break and repair the chain of trust, roll keys |

## See Also

- [Creating Labs](CREATE_LAB.md) for the topology the Service attaches to
- [DHCP Service](DHCP.md), the other Service family
- [Web UI Guide](web-ui.md) for the DNS and Resolver modals
- [API Reference](api-reference.md) for the REST payloads
