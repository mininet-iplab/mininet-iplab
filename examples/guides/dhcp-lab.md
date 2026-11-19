## DHCP Lab Guide

Use this guide in the web UI while the lab is running.
Open a Terminal for each Node by clicking it in the Topology.

One Lab holds every DHCP scenario, and each numbered section below is one
of them. Do them in order the first time; later you can jump to any one.
Nothing in this Lab acquires an address through DHCP for you: every
Interface a DHCP client marks comes up without one, and stays that way until
you open its Terminal and run the client yourself.

### What you will learn

- How a DHCP client (`dhcpcd`) requests an address, and how a DHCP Service
  (`kea-dhcp4` and `kea-dhcp6` on `r1`) answers it
- How a Reservation hands a client a predictable address, and how to read
  the live Lease table
- Why a Relay does not change the protocol the client speaks, and what a
  broken one looks like from the client
- How to edit a Pool from the Service panel
- How the M, O and A flags in one IPv6 Router Advertisement select between
  DHCPv6, SLAAC with DHCPv6 options, and SLAAC alone
- What a rogue DHCP server does to a Subnet, and how to tell who answered

### The Lab at a glance

The Topology view shows how the Nodes are linked. `h1` and `rogue` share
switch `sw1` on `r1`. `h3`, `h4`, `h5` and `h6` each have a Link of their
own to `r1`. `h2` sits behind the Relay `r2`, which joins `r1` over a
transit Link.

| Node | Subnet | Role |
|------|--------|------|
| `r1` | all except `h2`'s | Central DHCP Service, IPv4 and IPv6 |
| `h1` | 10.10.0.0/24, 2001:db8:10::/64 | Directly served, dual-stack; a Reservation and a 120-second lease |
| `rogue` | 10.10.0.0/24 | Shares `h1`'s Subnet; runs no DHCP server until section 7 |
| `r2` | 10.20.0.0/24, 2001:db8:20::/64 | Relay for `h2`'s Subnet, forwarding to `r1` over the transit link |
| `h2` | 10.20.0.0/24, 2001:db8:20::/64 | Relayed, dual-stack; MAC Reservations in both families |
| `h3` | 10.51.0.0/24 | IPv4-only; a plain Pool, yours to edit |
| `h4` | 2001:db8:61::/64 | IPv6-only; Router Advertisement sets M |
| `h5` | 2001:db8:62::/64 | IPv6-only; Router Advertisement sets O only |
| `h6` | 2001:db8:63::/64 | IPv6-only; Router Advertisement sets neither |

`r1` carries a DHCP Service badge in the Topology and `r2` a Relay badge.
When the Lab starts, the console prints two warnings about `h5`'s Subnet:
its DHCPv6 Pool has no Router setting the M flag, and its prefix still
advertises autoconfiguration. Both are deliberate; section 5 explains them.

### 1. Confirm the Interfaces start bare

Open a Terminal on `h1`, `h2` and `h3`. In each, run:

```bash
ip -4 addr show dev <node>-eth0
ip -6 addr show dev <node>-eth0
```

No `inet` line, and for IPv6 only a link-local `fe80::` address, which every
Interface forms from its own hardware address and which DHCP does not
deliver. `h4` is bare the same way. `h5` and `h6` are the exception: they
already show a `2001:db8:` address. Nothing leased it. Section 5 explains
where it came from.

### 2. Directly served Subnet: `h1`

In `h1`'s Terminal, ask for an IPv4 and an IPv6 Lease:

```bash
dhcpcd -1 -B -d -4 h1-eth0
dhcpcd -1 -B -d -6 h1-eth0
```

`-1` asks for one address and exits; `-B` and `-d` keep the exchange in the
foreground and verbose, so you see DISCOVER, OFFER, REQUEST, ACK (IPv4) and
SOLICIT, ADVERTISE, REQUEST, REPLY (IPv6) as they happen. Then:

```bash
ip addr show dev h1-eth0
```

The IPv4 address is **10.10.0.50**, outside the Pool's declared 100-200
range: the Reservation pinned to `h1`'s MAC address. The IPv6 address is
`2001:db8:10::100`, from the Pool. The lease is 120 seconds, and `ip route`
and `cat /etc/resolv.conf` show the router and DNS-server options the Pool
declared.

Open `r1`'s Service panel by clicking its DHCP badge. The Lease tables list
what `r1` handed out. That is the Service's own record, not what an
Interface happens to show right now. The IPv6 table is identified by DUID,
not hardware address, and carries preferred and valid lifetimes.

### 3. Relayed Subnet: `h2`

In `h2`'s Terminal, run the same two commands against `h2-eth0`:

```bash
dhcpcd -1 -B -d -4 h2-eth0
dhcpcd -1 -B -d -6 h2-eth0
```

The exchange is **identical**: a Relay does not change the protocol the
client speaks. `h2` receives `10.20.0.50` and `2001:db8:20::50`: Reservations
pinned to its MAC address. An IPv6 Reservation by MAC works here because
`r2` inserts the client's link-layer address into the forwarded request (RFC
6939); on `h1`'s directly attached Subnet there is no Relay to add it.

Click `r2`'s Relay badge. The panel shows the Relay's own address on the
client side (`10.20.0.1`, `2001:db8:20::1`) forwarding to `r1` (`10.0.0.1`,
`2001:db8::1`). That client-side address is how `r1` knows which Subnet's Pool
to answer from. In `r1`'s Service panel, `h2`'s Leases are labelled
**relayed**; `h1`'s are not.

#### Stage a relay failure, then undo it

Click the Link between `r1` and `r2` in the Topology and choose
**Disconnect Link**. In `h2`'s Terminal, drop the Lease and try again:

```bash
dhcpcd -4 -k h2-eth0
rm -f /var/lib/dhcpcd/h2-eth0.lease
ip addr flush dev h2-eth0 scope global
timeout 8 dhcpcd -1 -B -d -4 h2-eth0
```

`DISCOVER` goes out and nothing answers: to the client a broken Relay looks
exactly like a Subnet with no server. Nothing about `h2` changed; the path
behind its Relay did.

**Undo:** click the same Link and choose **Reconnect Link**, then repeat the
last command. `h2` takes `10.20.0.50` again. The static routes on `r1` and
`r2` came back with the Interfaces.

### 4. IPv4-only Subnet: `h3` and the Pool editor

In `h3`'s Terminal:

```bash
dhcpcd -1 -B -d -4 h3-eth0
```

`h3` takes an address from `10.51.0.100`-`.200`; this Subnet has no
IPv6 at all. Now edit its Pool. First, find `h3`'s MAC address in its
Terminal:

```bash
ip link show h3-eth0
```

In `r1`'s Service panel, in the Pool editor, find `10.51.0.0/24` and:

1. Lower its **Lease time** to something short, e.g. `120`.
2. Set **Router** to `10.51.0.1` and **DNS server** to `8.8.8.8`.
3. Click **Add reservation**, and enter `h3`'s MAC address with a Reserved
   address outside the Pool's range, e.g. `10.51.0.50`.
4. Click **Apply**.

Back in `h3`'s Terminal, drop the cached Lease, so the client sends a fresh
DISCOVER instead of asking to keep its old address:

```bash
dhcpcd -4 -k h3-eth0
rm -f /var/lib/dhcpcd/h3-eth0.lease
dhcpcd -1 -B -d -4 h3-eth0
```

`h3` now takes `10.51.0.50`, and the router and DNS-server options arrived
too (`ip route show default`, `cat /etc/resolv.conf`). A form that is
rejected explains why, and leaves the running Service exactly as it was.

### 5. IPv6-only Subnets: the Router Advertisement decides

An IPv6 host learns how to configure itself from one message: the Router
Advertisement its Router sends. Three flags in it select the outcome:

- **M, Managed:** "ask a DHCPv6 server for an address."
- **O, Other:** "ask a DHCPv6 server for everything except an address", DNS
  servers in this Lab.
- **A, Autoconfiguration:** "build yourself an address from the advertised
  prefix" (SLAAC).

`r1`'s configuration writes these flags explicitly, so they are visible:
`vtysh -c "show running-config"` in `r1`'s Terminal lists `ipv6 nd
managed-config-flag` and `other-config-flag` on the interfaces that set
them, and `ipv6 nd prefix ... no-autoconfig` where A is turned off. The
three Subnets differ only in those flags. Run the same command in each
Terminal:

```bash
dhcpcd -1 -B -d -6 h4-eth0
dhcpcd -1 -B -d -6 h5-eth0
dhcpcd -1 -B -d -6 h6-eth0
```

| Node | Router Advertisement | What happens |
|------|----------------------|--------------|
| `h4` | M set, A off | SOLICIT, ADVERTISE, REQUEST, REPLY: an address arrives from the Pool (`2001:db8:61::100`) |
| `h5` | O set, A on | An address built by SLAAC, plus an INFORM and its REPLY fetching `r1`'s DNS server; no Lease |
| `h6` | neither, A on | An address built by SLAAC; dhcpcd reports "No DHCPv6 instruction in RA" and never contacts a server |

Check each with `ip -6 addr show dev <node>-eth0`. `h4` shows
`2001:db8:61::100` and no SLAAC address, because A is off. `h5` and `h6`
show SLAAC addresses built from the advertised prefix: one embeds the
Interface's own hardware address (`...:200:ff:fe00:8` on `h5`), and the one
`dhcpcd` adds is opaque. On
`h5`, `cat /etc/resolv.conf` shows `nameserver 2001:db8:62::1`, the one
thing DHCPv6 delivered; on `h6` nothing did. Compare with `r1`'s Lease
table: it holds `h4`'s Lease and none for `h5` or `h6`, because only an
address is a Lease.

The two console warnings from Lab start named `h5`'s Subnet for exactly
this reason: its Pool exists, but no M flag asks for an address, and its
prefix still lets hosts autoconfigure. That is what O-without-M is.

Every Subnet's default route comes from the Router Advertisement
(`ip -6 route show default` shows a `fe80::` gateway), in all three cases:
DHCPv6 has no router option to give.

### 6. Watch an address go deprecated

`h4`'s Pool declared a 600-second valid lifetime with a 120-second
preferred lifetime. Wait about two minutes after section 5, then in `h4`'s
Terminal:

```bash
ip -6 addr show dev h4-eth0
```

The leased address is still there, valid for another eight minutes, but is
flagged `deprecated`: do not use it for new connections. The Lease table's
preferred-lifetime column counted down to zero over the same period.

### 7. A rogue DHCP server, then removing it

`rogue` shares `h1`'s Subnet and runs nothing. In `rogue`'s Terminal, start
a second DHCP server that hands out different addresses, a different router
and a different DNS server:

```bash
dnsmasq --conf-file=/dev/null --port=0 --interface=rogue-eth0 \
  --bind-interfaces --dhcp-range=10.10.0.201,10.10.0.250,255.255.255.0,120 \
  --dhcp-option=option:router,10.10.0.254 \
  --dhcp-option=option:dns-server,6.6.6.6 \
  --dhcp-leasefile=/tmp/rogue.leases --pid-file=/tmp/rogue.pid
```

In `h1`'s Terminal, hand back the current address and ask again, several
times. Each repetition is a new race between `r1` and `rogue`:

```bash
dhcpcd -4 -k h1-eth0
rm -f /var/lib/dhcpcd/h1-eth0.lease
ip addr flush dev h1-eth0 scope global
dhcpcd -1 -B -d -4 h1-eth0
```

Read the `offered ... from` line: `10.10.0.1` is `r1`; `10.10.0.254` is
the rogue. Then `cat /etc/resolv.conf` in `h1`'s Terminal: `6.6.6.6` and a
default route via `10.10.0.254` mean the rogue won. The address alone also
tells you: `10.10.0.50` or `.100`-`.200` came from `r1`, `.201`-`.250` from
`rogue`. In `rogue`'s Terminal, `cat /tmp/rogue.leases` lists the Leases it
handed out; they are absent from `r1`'s Lease table. Which server answers
first is a race, not something the Lab controls, so run it until you see
both outcomes. Nothing on the client says which server is genuine.

**Undo:** in `rogue`'s Terminal, stop the server and delete its files:

```bash
kill $(cat /tmp/rogue.pid)
rm -f /tmp/rogue.pid /tmp/rogue.leases
```

Repeat the four commands on `h1`. Now only `r1` answers: `h1` takes
`10.10.0.50` and `nameserver 8.8.8.8` again.

### 8. Where the Lease lives on the client

`dhcpcd` keeps the Lease inside the Node, in a directory each Node has its
own copy of:

```bash
ls -l /var/lib/dhcpcd/
```

**Do not `cat` the `.lease` file**: it is the raw DHCP reply packet, not
text. Ask a *running* client instead. Section 2 used `-1`, which exits as
soon as the address arrives, so start one that stays, then ask it:

```bash
dhcpcd -4 h1-eth0      # no -1: stays running in the background
dhcpcd -4 -U h1-eth0
```

After a short block describing the link, the Lease itself:

```text
reason=REBOOT
interface=h1-eth0
ip_address=10.10.0.50
subnet_mask=255.255.255.0
routers=10.10.0.1
domain_name_servers=8.8.8.8
host_name=h1
dhcp_lease_time=120
dhcp_server_identifier=10.10.0.1
```

`dhcp_lease_time` counts down from 120, so it shows the seconds left. Every
field the Service sent: the Reservation, the short lease time, and
both options the Pool declared. `REBOOT` rather than `BOUND` because
section 2 already cached this Lease, so the new client confirmed it with a
single REQUEST. A client started with `-4` needs `-4 -U` and `-4 -k`: its
socket is named for the family it started with. To hand the address back:

```bash
dhcpcd -4 -k h1-eth0
```

That sends a RELEASE, drops the address, and removes the cached Lease. The
same works for IPv6 with `-6`, and its `.lease6` file is a packet too.

### Troubleshooting

**`h1`'s client takes `10.10.0.50` and ignores the rogue**

`r1` answered first. Hand the address back and ask again: it is a race.

**`dhcpcd -U` or `-k` says "dhcpcd is not running"**

You took the Lease with `-1`, which exits once the address arrives, so there
is no client left to ask. Start one without `-1`.

**An address from a previous section is still on the Interface**

Run `dhcpcd -4 -k` or `-6 -k` for that Interface, remove its cached Lease
under `/var/lib/dhcpcd/`, and flush the address with `ip addr flush dev
<node>-eth0 scope global`.

**An edit in the Pool editor is rejected**

The form explains why (an address outside the Subnet, a malformed MAC
address, a negative lease time). The running Service is left exactly as it
was; nothing is half-applied.

### Success criteria

- Before running any client, `h1`, `h2`, `h3` and `h4` show no global address
- `h1` takes `10.10.0.50`, its Reservation, and `2001:db8:10::100`
- `h2` takes `10.20.0.50` and `2001:db8:20::50`, and `r1` labels the Leases
  relayed
- With the Link `r1`-`r2` disconnected, `h2`'s DISCOVER goes unanswered; after
  Reconnect Link it is answered again
- `h3` takes `10.51.0.50` after you add its Reservation
- `h4` leases `2001:db8:61::100`; `h5` gains a SLAAC address and a DNS
  server but no Lease; `h6` gains a SLAAC address and nothing else
- While `rogue`'s server runs, some of `h1`'s Leases come from it; after you
  stop it, `h1` takes `10.10.0.50` from `r1` again

## Packet Analysis

In section 2, `dhcpcd` reported the exchange as it went: `sending DISCOVER`,
`offered`, `sending REQUEST`, `acknowledged`. That is the client's account
of it. Here you watch the four messages themselves cross `h1`'s Interface,
decoded field by field, while you cause them.

A Packet Capture happens at one point. Capture **`h1-eth0`**, the client's
own Interface: every message of the exchange passes through it, in both
directions.

### Open a Capture on `h1-eth0`

1. Right-click `h1` in the Topology and choose **Packet Capture**.
2. The dialog lists `h1`'s Interfaces. Choose **Capture on h1-eth0 (to
   sw1)**. The Packet Analysis pane opens beside your Terminals and starts
   capturing straight away.
3. In the pane, pick the **DHCP** preset. The pane shows the capture filter
   it stands for, `udp port 67 or udp port 68 or udp port 546 or udp port 547`,
   and restarts the Capture with it. DHCP servers listen on port 67 and
   clients on port 68; 546 and 547 are the DHCPv6 equivalents.

Keep the pane and `h1`'s Terminal both in view.

### Cause the exchange from `h1`'s Terminal

If you already took a Lease in section 2, `dhcpcd` has cached it and will only
ask to confirm it. Drop the cached Lease first, so the client starts from
nothing, then run the client. (If a client is still running from "Where the
Lease lives on the client", hand its address back first with
`dhcpcd -4 -k h1-eth0`.)

```bash
rm -f /var/lib/dhcpcd/h1-eth0.lease
dhcpcd -1 -B -d -4 h1-eth0
```

Four rows appear in the pane, in this order: Discover, Offer, Request, ACK.

### Read the four messages

Select a row to expand it into its fields. Under **Dynamic Host
Configuration Protocol**, find these fields:

| Row | Source → Destination | `Option: (53) DHCP Message Type` | `Your (client) IP address` |
|-----|----------------------|----------------------------------|----------------------------|
| 1 | `0.0.0.0` → `255.255.255.255` | `Discover` | `0.0.0.0` |
| 2 | `10.10.0.1` → `10.10.0.50` | `Offer` | `10.10.0.50` |
| 3 | `0.0.0.0` → `255.255.255.255` | `Request` | `0.0.0.0` |
| 4 | `10.10.0.1` → `10.10.0.50` | `ACK` | `10.10.0.50` |

What to notice:

- **Option 53 is what makes it a Discover.** All four messages are the
  same BOOTP packet format. The message type is not a header field: it is
  carried as option 53, **DHCP Message Type**. Expand **Option: (53)** in
  each row to see it.
- **The client broadcasts because it has no address.** Rows 1 and 3 come
  from `0.0.0.0` and go to `255.255.255.255`. Under **Ethernet II**, their
  **Destination** is `ff:ff:ff:ff:ff:ff`, the broadcast hardware address.
  The client cannot address anything else yet.
- **The Service answers unicast, to the address it is proposing.** Rows 2
  and 4 go from `r1` (`10.10.0.1`) to `10.10.0.50`, and their Ethernet
  **Destination** is `h1`'s own MAC address (`ip link show h1-eth0` shows
  it), not broadcast. The client's
  **Bootp flags** say `Broadcast flag: Unicast`, meaning it can accept a
  reply sent to an address it does not hold yet. `r1` takes it at its word.
- **`yiaddr` is the proposal.** `Your (client) IP address` is the `yiaddr`
  field. It is `0.0.0.0` in what the client sends. In the Offer and the ACK
  it is `10.10.0.50`: the Reservation, arriving on the wire before
  `h1-eth0` holds it.
- **The Request names what it accepts.** Row 3 still has no source address,
  but its **Requested IP Address** option reads `10.10.0.50`: the client
  repeats the Offer back to the Service, still broadcast so that any other
  server that offered can see its Offer was declined.

### Run it again without dropping the Lease

Now run only the client, leaving the cached Lease in place:

```bash
dhcpcd -1 -B -d -4 h1-eth0
```

Only two rows appear: Request and ACK. With a cached Lease, the client
skips discovery and asks to keep the address it already has. This is the
`REBOOT` you read in "Where the Lease lives on the client" above.

### Download the Capture

Click **Download pcap** (⤓ in the pane's header) to keep the exchange
and open it in Wireshark. The pcap is deleted when the Lab stops, so
download it before you finish.

### Success criteria

- A Capture on `h1-eth0` with the **DHCP** preset shows Discover, Offer,
  Request, and ACK after a fresh `dhcpcd` run
- You can find option 53 in each message and name its type
- Discover and Request are broadcast from `0.0.0.0`; Offer and ACK are
  unicast from `10.10.0.1` to `10.10.0.50`
- The Offer's `Your (client) IP address` is `10.10.0.50`
- A second run with the Lease cached shows only Request and ACK

## IPv6 Reservations

Everything above is IPv4, where a Reservation names a hardware address and
an Instructor can pin one in advance. IPv6 is genuinely different, and the
difference is the lesson.

**On a directly attached Subnet, an IPv6 Reservation is authored by the
Learner, not the Instructor.** DHCPv4 identifies a client by the hardware
address in its `chaddr` field — a value a Lab Example can fix in advance
with `addLink`'s `mac=`. DHCPv6 has no `chaddr`. The protocol replaced it
with the Client Identifier option carrying a **DUID**, and a DUID does not
exist until the client first runs. There is nothing for an Instructor to
predeclare: the first Lease has to happen first.

That is not as limiting as it sounds, because it is also the exercise:

1. Run the client yourself: `dhcpcd -1 -B -d -6 <node>-eth0`.
2. Open the DHCP Service panel. The IPv6 Lease table shows the Lease with
   its DUID — the whole identifier, presented to be copied (there is a
   **Copy DUID** button on each row).
3. In the Pool editor, click **Add reservation**. An IPv6 row asks how to
   identify the client: pick **DUID** (the default) and paste it, give a
   Reserved address outside the Pool's range, and click **Apply**.
4. Drop the client's cached Lease (`rm -f /var/lib/dhcpcd/<node>-eth0.lease6`)
   and run the client again. The reserved address arrives instead of a Pool
   address.

A Reservation carries **exactly one** identifier — a hardware address or a
DUID, never both and never neither. The form enforces it, and so does the
Python `Reservation` type.

**The hardware-address branch is a convenience, not the mechanism.** This
Lab's client (`dhcpcd`) happens to generate a DUID of type DUID-LLT, which
embeds its MAC address, and Kea can match a hardware-address Reservation
against that. So picking **Hardware address** on an IPv6 Reservation will
usually work for a directly attached client too. "Usually" is doing real
work there: a client that generates a different DUID type, or forms its
link-local address independently of its MAC, will not match. Do not build
an exercise on it.

**A Relay changes this.** `h2`'s Reservations in section 3 are exactly this
case. When a DHCP Relay stands between the client and the Service, the relay
inserts the client's link-layer address into the exchange (RFC 6939), and Kea
consumes it with no configuration, so behind a Relay the hardware address is
as reliable an identity as it is in IPv4, and an Instructor can predeclare
IPv6 Reservations by MAC again. On the directly attached Subnet, the DUID is
the identity that always works.
