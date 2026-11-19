## VLAN & Inter-VLAN Routing Lab Guide

Use this guide in the web UI while the Lab is running. Click a Host or the Router in the Topology to open its
Terminal. A Switch has no Terminal: click it to open its Switch panel,
which is the only place you look inside a Switch. Every section ends with
the Lab as you found it, so you can do the sections in order or come back
to one later.

### What you will learn

Put one VLAN on two Switches, see the 802.1Q tag on a trunk, route between
VLANs with a Router on a stick, watch a Switch learn and forget MAC
addresses, and see Rapid Spanning Tree (RSTP) block one trunk port of a
triangle and bring it back when another trunk fails.

### The Lab at a glance

The Topology view shows how the Nodes are linked. Two Hosts hang off each of
`s2` and `s3`, and `r1` hangs off `s1`.

`s1` is the core, `s2` and `s3` are access Switches. All four trunks
(`s1`-`s2`, `s1`-`s3`, `s2`-`s3` and `s1`-`r1`) carry VLANs 10 and 20 with
802.1Q tags. The three Switches form a triangle, a loop, so RSTP is on,
and `s1` is set to win the election as the root bridge.

| Node | VLAN | IPv4 |
|---|---|---|
| `h1` | 10 (`s2`) | `192.168.10.2/24` |
| `h3` | 10 (`s3`) | `192.168.10.3/24` |
| `h2` | 20 (`s2`) | `192.168.20.2/24` |
| `h4` | 20 (`s3`) | `192.168.20.3/24` |
| `r1` | 10 and 20 | `192.168.10.1/24` on `r1-eth0.10`, `192.168.20.1/24` on `r1-eth0.20` |

| Switch | Ports |
|---|---|
| `s1` (core) | `eth1` to `s2`, `eth2` to `s3`, `eth3` to `r1` |
| `s2` | `eth1` `h1`, `eth2` `h2`, `eth3` to `s1`, `eth4` to `s3` |
| `s3` | `eth1` `h3`, `eth2` `h4`, `eth3` to `s1`, `eth4` to `s2` |

### 1. One VLAN across two Switches

`h1` and `h3` sit on different Switches but in the same VLAN. In `h1`'s
Terminal:

```bash
ping -c 3 192.168.10.3
```

All three replies arrive: the frame left `s2` on a trunk and reached `s3`
tagged with VLAN 10. Now try the other VLAN's Host, `h2`:

```bash
ping -c 3 192.168.20.2
```

It works too, but not at Layer 2: no frame crosses from VLAN 10 into VLAN 20
by itself. The Router carries it, which sections 2 and 3 show. Open `s2`
in the Topology and read the ports table: `s2-eth1` is an **Access** port in
VLAN 10, `s2-eth2` an **Access** port in VLAN 20, and `s2-eth3` and
`s2-eth4` are **Trunk** ports carrying 10 and 20. Close the panel.

### 2. Tagged frames on a trunk

An access port strips the tag; a trunk keeps it. Capture the same ping on
both kinds of port to see it.

1. Click the Link between `h1` and `s2` in the Topology and choose
   **Packet Capture**.
2. Choose **`h1-eth0`**, the Host's end. The Packet Analysis pane opens
   beside your Terminals.
3. Click the Link between `s2` and `s1` and choose **Packet Capture**
   again. Choose **`s2-eth3`**, a trunk port on the Switch. The pane now
   has one tab for each Capture. With no filter you also see a Spanning Tree
   Protocol row about every two seconds on this trunk: those are the Switches'
   own messages, which section 5 returns to.
4. In `h1`'s Terminal:

```bash
ping -c 3 192.168.10.3
```

Both tabs show the same requests and replies. In the `h1-eth0` tab, select
the first row. Its fields go from **Ethernet II** straight to **Internet
Protocol**: the Host sends and receives untagged frames. Now open the
`s2-eth3` tab and select the first row. A new line sits between them,
**802.1Q Virtual LAN**, and it says `ID: 10`. The access port put the frame
into VLAN 10 and the trunk carries that tag to `s1` and on to `s3`. Stop
both Captures when you are done.

The Router can change the tag. Click the Link between `r1` and `s1`, choose
**`r1-eth0`**, and in `h1`'s Terminal run:

```bash
ping -c 3 192.168.20.2
```

Each request appears twice. The first row says `ID: 10`, the frame as it
arrived from `h1`. The second says `ID: 20`, the frame `r1` sent on toward
`h2`, tagged for the other VLAN. The `Ethernet II` line shows the addresses
change too: `h1` sent to `r1`'s MAC (`00:00:00:00:00:01`), and `r1` sent to
`h2`'s. Stop the Capture.

### 3. Inter-VLAN routing

The ping from `h1` to `h2` crossed VLANs only because `r1` routed it. In
`h1`'s Terminal:

```bash
traceroute -n 192.168.20.2
```

Hop 1 is `192.168.10.1`, `r1`'s VLAN 10 address, and hop 2 is `h2`. A
traceroute between `h1` and `h3` has no Router hop at all: same VLAN, same
broadcast domain. Look at `r1`:

```bash
ip -br address show
ip route
```

`r1-eth0` has no address; `r1-eth0.10` and `r1-eth0.20` are 802.1Q
sub-interfaces on it, one per VLAN, each the default gateway of its Hosts.
One physical link, two Layer 3 interfaces: that is a router on a stick.

### 4. MAC learning and Flush

A Switch learns which port a source address came from. Open `s2` and read
**MAC address table**: the addresses of the Hosts that have talked,
each on a port and a VLAN. `h1` (`00:00:00:00:00:02`) is on `s2-eth1`, VLAN
10. `h3` (`00:00:00:00:00:04`) is on `s2-eth3`, VLAN 10, the trunk it is
reached through. `r1` (`00:00:00:00:00:01`) appears twice, once for each
VLAN, on the same port: a Switch keeps a table per VLAN.

Click **Flush**: the table empties. In `h1`'s Terminal:

```bash
ping -c 3 192.168.10.3
```

Click **Refresh**: `h1` and `h3` are back, learned again from the frames
that just crossed, with ages of a few seconds. Entries not refreshed are
forgotten after about five minutes, and that is why a Host that moves to
another port works again after the old entry ages out or is flushed.

### 5. The Alternate port, and a trunk that fails

Three Switches in a triangle are a loop. RSTP removes it by blocking one
port. Open `s1`, `s2` and `s3` in turn and read **Spanning tree**. The line
above the table names the root, and it is the same on all three: `s1`, which
this Lab gave the lowest priority (on `s1` itself it reads "This Switch is the
root"). Every port of the root is **Designated**/**Forwarding**.

On each access Switch, the trunk toward `s1` is the **Root** port (the
cheapest way to the root). On exactly one of `s2` and `s3` a trunk port
is **Alternate** and **Discarding**: its `eth4`, on the trunk between the two
access Switches. It carries nothing, but it is a ready backup for the Root
port. Which of the two Switches blocks depends on the bridge identifiers,
which are random at each start, so look rather than assume. Call the Switch
with the Alternate port `X` and the other `Y`.

Now watch the Switches talk. Click the Link between `s2` and `s3` and
choose the end on `X`: **`s3-eth4`** if `X` is `s3`, **`s2-eth4`** if it
is `s2`. In the **Capture filter** box type `stp` and press **Apply**. Every
row is a BPDU, the message a Switch sends its neighbours, about one every two
seconds. Select one and open **Spanning Tree Protocol**. It says `Port Role:
Designated` and a `Root Path Cost` of 2000: the sender is `Y`. `X` sends
nothing here, because its Alternate port only listens.

Start a steady ping between the VLAN 10 Hosts of the two access Switches,
for example from `h3` if `X` is `s3` (from `h1` to `192.168.10.3` if `X` is `s2`):

```bash
ping -O -i 0.2 192.168.10.2
```

The Terminal prints `no answer yet` for each lost reply. Now click the
Link between `X` and `s1`, the one carrying `X`'s Root port, and choose
**Disconnect Link**. Open `X` and read **Spanning tree** again: the former
Alternate port is now **Root** and **Forwarding**, and the pings go on. At
most one or two are lost, and RSTP restores a path in about a second, because
a Switch with an Alternate port does not have to wait: it switches to the
backup as soon as the Root port is down.

Look at the Capture. A row arrives from a second source, `X`, which had
been silent. Select it: it says `Port Role: Root`, `Root Path Cost: 4000`
(two links of 2000 to reach `s1` through `Y`), and `Agreement` and `Topology
Change` in the flags. That is `X` announcing its new path on the old
Alternate port, which you read in the Spanning tree table a moment ago. It
speaks once, at the change. `Y`'s rows keep coming, still `Designated`.

Restore the Lab: click the same Link and choose **Reconnect Link**. After a
moment `X`'s trunk to `s1` is the Root port again and `eth4` is Alternate.
Stop the ping with Ctrl+C and stop the Capture.

### 6. Classic STP, and what RSTP improves

The original Spanning Tree Protocol (802.1D) elects the same root and blocks
the same kind of port, but it is slow because it is passive. A blocked port
learns about a failure only by timers: it waits for the root's messages to
stop (up to 20 seconds, the max age), then passes through two 15-second
states, **Listening** and **Learning**, before it forwards. A failure costs
about 30 to 50 seconds of no traffic. RSTP (802.1w), which this Lab runs,
keeps the election but makes Switches negotiate: an Alternate port is
pre-computed as a backup, a new link agrees with its neighbour by a handshake
instead of a timer, and a port on a link to another Switch can go forward
within a second or two. The role names in the Switch panel, Root, Designated
and Alternate, are RSTP's; classic STP has no Alternate role.

### Things to try next

- In section 5, cut the Link between `s2` and `s3` instead. Which port is
  Alternate afterwards, and why was nothing disturbed?
- Cut the Link between `s1` and `r1`. Which pings still work, and which do
  not? Why does this single Router link make VLAN 10 and VLAN 20 lose each
  other even though every trunk between the Switches is still up?
- Flush a Switch while a ping runs. Does traffic stop?
