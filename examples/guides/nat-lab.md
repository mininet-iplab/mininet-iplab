## NAT Lab Guide

Use this guide in the web UI while the Lab is running. It is a home network
behind a home router, `r1`, and that router's provider, `isp`. Every
translation in this Lab happens on `r1`, a Router you can open, capture and
read.

### What you will learn

- How port address translation (PAT) lets many Hosts share one outside address
- How long a NAT entry lasts, and why UDP and TCP differ
- How a port forward lets the outside reach a server inside
- How a static 1:1 mapping gives one inside Host its own outside address
- Why a port forward fails from inside, and how hairpinning fixes it
- Why IPv6 needs none of this

### The Lab at a glance

The Topology view shows how the Nodes are linked. The home network is on one
side: `h1`, `srv` and `h2` on switch `s1`, behind `r1`. The Internet is on
the other: `web` and `remote` on switch `s2`, behind `isp`. The one Link
between `r1` and `isp` is where the translation happens.

| Node / Interface | IPv4 | IPv6 | Role |
|---|---|---|---|
| `h1-eth0` | `192.168.1.10/24` | `2001:db8:1::10/64` | Home Host |
| `srv-eth0` | `192.168.1.20/24` | `2001:db8:1::20/64` | Home server |
| `h2-eth0` | `192.168.1.30/24` | `2001:db8:1::30/64` | Home Host with its own outside address |
| `r1-eth0` | `192.168.1.1/24` | `2001:db8:1::1/64` | `r1`'s inside Interface |
| `r1-eth1` | `203.0.113.2/30` | `2001:db8:ff::2/64` | `r1`'s outside Interface |
| `isp-eth0` | `203.0.113.1/30` | `2001:db8:ff::1/64` | Provider's end of the Link |
| `isp-eth1` | `198.51.100.1/24` | `2001:db8:100::1/64` | Provider's network |
| `web-eth0` | `198.51.100.10/24` | `2001:db8:100::10/64` | A web server on the Internet |
| `remote-eth0` | `198.51.100.20/24` | `2001:db8:100::20/64` | A client on the Internet |

`203.0.113.0/24` and `198.51.100.0/24` are documentation ranges (RFC 5737).
In this Lab they stand in for public addresses. `192.168.1.0/24` is a private
range, and `isp` has no route to it. A reply can only reach a home Host
because `r1` translated it.

`r1` declares three translations:

- **Masquerade** on its outside Interface, `r1-eth1`.
- A **port forward** from `203.0.113.2:8080` to `srv` port 80.
- A **static mapping** of `h2` to `203.0.113.5`.

### 0. Start the two web servers

Click `srv` in the Topology to open its Terminal, and run:

```bash
python3 -m http.server --bind :: 80
```

Do the same on `web`. Leave both running. `--bind ::` makes each server
answer on IPv4 and IPv6. A forward or a mapping only works when something is
listening behind it.

### 1. PAT: many Hosts, one outside address

On `r1`, open two Packet Captures:

1. Click the Link between `r1` and `s1`, choose **Packet Capture**, and
   choose **`r1-eth0`**. This is the inside of `r1`. In the **Capture filter**
   box type `tcp port 80 or tcp port 8080` and press **Apply**.
2. Click the Link between `r1` and `isp`, choose **Packet Capture**, and
   choose **`r1-eth1`**. This is the outside. Apply the same filter.

In `h1`'s Terminal:

```bash
curl -I http://198.51.100.10/
```

Compare the two tabs. On `r1-eth0` the request comes from `192.168.1.10`.
On `r1-eth1` the same request comes from `203.0.113.2`, `r1`'s own outside
address. `web` replies to `203.0.113.2`, and `r1` rewrites the reply back to
`192.168.1.10`.

Now right-click `r1` and choose **NAT Table**. Above the entries, **Rules
running now** shows the translations `r1` is running: masquerade on `r1-eth1`, the
port forward and the static mapping. Below it, find the TCP entry: Inside
`192.168.1.10` and a port, Outside `203.0.113.2` and a port, destination
`198.51.100.10` port 80. That entry is what lets `r1` send the reply back.
Run the same `curl` from `srv`. A second entry appears with the same outside
address. If two Hosts pick the same source port, `r1` changes one of the
ports. This is why the technique is called port address translation.

### 2. Entry timeouts

An entry lasts only while its connection is in use. In `h1`'s Terminal,
send one UDP datagram to `remote`:

```bash
echo hi | nc -u -w1 198.51.100.20 5000
```

A UDP entry appears in `r1`'s NAT Table, reading `UNREPLIED`, with **Expires
in** at about 30 seconds. Watch it count down and disappear. 30 seconds is
the kernel's default for UDP traffic with no reply.

Now hold a TCP connection open. In `remote`'s Terminal run `nc -l -p 5001`, and
in `h1`'s Terminal run:

```bash
nc 198.51.100.20 5001
```

The TCP entry reads `ESTABLISHED` and starts at five days. A TCP connection
says when it ends, so `r1` can keep its entry long. A UDP flow never says, so
`r1` forgets it quickly. Press **Ctrl+C** in both Terminals. The TCP entry
moves to a closing state and expires within two minutes.

### 3. Port forward: the outside reaches in

`remote` cannot reach `srv` at `192.168.1.20`, because `isp` has no route
there. It can reach `r1`'s outside address. In `remote`'s Terminal:

```bash
curl -I http://203.0.113.2:8080/
```

`srv` answers. On the `r1-eth1` tab the request goes to `203.0.113.2` port
8080. On the `r1-eth0` tab it goes to `192.168.1.20` port 80. `r1` rewrote
the destination, not the source, so `srv` sees `remote`'s own address,
`198.51.100.20`, in its log. In the NAT Table, the row reads **TCP,
inbound**: Inside is the server it reached, `192.168.1.20:80`; Outside is the
address it targeted, `203.0.113.2:8080`; Destination is `remote`'s address,
marked **(client)**.

### 4. Static 1:1: one Host, its own outside address

From `remote`:

```bash
curl -I http://203.0.113.5/
```

There is no port number here. Every connection to `203.0.113.5` goes to
`h2`. You started no server on `h2`, so start one first, as in section 0, in
`h2`'s Terminal. Then run the `curl` again and `h2` answers.

The mapping works both ways. In `h2`'s Terminal:

```bash
ping -c 3 198.51.100.20
```

On the `r1-eth1` tab (pick the **ICMP** preset) the pings leave as
`203.0.113.5`, not `203.0.113.2`. `h1`'s traffic still leaves as
`203.0.113.2`. In the NAT Table, `h2`'s entry has the same outside address
every time, and its ports are kept. Compare that with PAT in section 1.

### 5. Hairpinning: the forward, from inside

From `h1`, try the port forward the way `remote` did:

```bash
curl -I --max-time 5 http://203.0.113.2:8080/
```

It times out. `r1` only forwards traffic that arrives on `r1-eth1`, and this
request arrived on `r1-eth0`. Even if `r1` forwarded it, `srv` would answer
`h1` directly across `s1`, from an address `h1` never contacted.

Fix both problems in `r1`'s Terminal:

```bash
iptables -t nat -I PREROUTING -i r1-eth0 -d 203.0.113.2 -p tcp --dport 8080 -j DNAT --to-destination 192.168.1.20:80
iptables -t nat -I POSTROUTING -s 192.168.1.0/24 -d 192.168.1.20 -p tcp --dport 80 -m conntrack --ctstate DNAT -j MASQUERADE
```

The first rule forwards inside traffic as well. The second rewrites the
source to `r1`'s address, so `srv` replies through `r1`. Run the `curl` from
`h1` again, and `srv` answers. The two new rules appear in the NAT Table's
rules list, beside the Lab's own. Run `iptables -t nat -S` in `r1`'s Terminal
to see the same rules as text.

A rule you type lasts only while the Lab runs. When the Lab stops, it is
gone. The Lab's own translations come back on every start because the Lab
declares them.

### 6. IPv6: no NAT needed

From `remote`:

```bash
curl -I 'http://[2001:db8:1::20]/'
```

`srv` answers at its own address, with no forward. `isp` has a route to the
home network's IPv6 prefix, `2001:db8:1::/64`. On both `r1` tabs, set the
**Capture filter** to `ip6 and tcp port 80`. The addresses are the same on both sides,
and the NAT Table gains no entry. There are enough IPv6 addresses for every
Host to have a public one. Whether `remote` may reach `srv` is a firewall's
decision, not a side effect of translation.

### Success criteria

- `h1`'s request leaves `r1-eth1` from `203.0.113.2`, and `r1`'s NAT Table
  holds its entry
- A UDP entry counts down from about 30 seconds and disappears; a TCP entry
  starts at five days
- `remote` reaches `srv` through `203.0.113.2:8080`
- `remote` reaches `h2` at `203.0.113.5`, and `h2`'s pings leave as
  `203.0.113.5`
- `h1` reaches `203.0.113.2:8080` only after the two hairpin rules
- `remote` reaches `srv` over IPv6 at `2001:db8:1::20`, untranslated
