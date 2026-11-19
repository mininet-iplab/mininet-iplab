# Web UI Guide

Mininet-IPLab's web interface provides a modern, browser-based way to interact with your network labs. This guide covers launching, using, and configuring the web UI.

## Quick Start

```bash
# Start a lab with the web UI (one person, one Lab)
python3 examples/static-lab.py --enable-web

# Or specify a custom port
python3 examples/static-lab.py --enable-web --web-port 8080

# Or run the classroom server: no Lab up front, Labs started from the browser
python -m mniplab serve
```

Open your browser to **http://localhost:8050** (default) and login with:
- Username: `admin`
- Password: the one printed on the `*** Login username:` line at startup, generated
  afresh each time unless `MNIPLAB_PASSWORD` is set

`--enable-web` and `serve` both listen where `--web-host` / `--web-port` say, else where
`MNIPLAB_HOST` / `MNIPLAB_PORT` in `.env` say, else on `0.0.0.0:8050`. Docker
Compose publishes only port 8050, so another port also needs the `ports:`
mapping in `docker-compose.yml` changed.

> ⚠️ **Set your own password** before sharing access: the generated one changes at every start. See [Configuration](#configuration) below.
> For classroom use, run `python -m mniplab serve` with Instructor and Learner logins in `MNIPLAB_USERS`. See [Classroom Server](#classroom-server) and [Instructor and Learner logins](#instructor-and-learner-logins) below.

## Interface Overview

An Instructor also sees a **Lab Authoring** link in the header, which opens
the page for drawing and publishing new Labs; see
[Instructor Lab Authoring](lab-authoring.md). It is used on
`python -m mniplab serve`.

### 1. The Labs list and the Panes

With no Lab running, the left sidebar is the Labs list, for instructors. The
sidebar button (`Ctrl+B`) opens and closes it, and its resize handle (drag,
or focus it and use `ArrowLeft` / `ArrowRight`) sets its width.

While a Lab runs, the page is four **Panes** in one workspace: the **Guide**
at the Left, the **Topology** in the Centre, and the **Terminal** pane over
the **Packet Analysis** pane at the Right. The sidebar and its button are not
shown, and `Ctrl+B` does nothing. A Pane with nothing to show takes no room:
the Terminal pane appears when a Terminal opens, Packet Analysis when a
Capture or the NAT Table opens, and the Topology grows into the space they
leave. Each Pane is a named region, so a screen reader can jump between them
by landmark; the Guide's title row stays put while its text scrolls.

On a phone-width screen the open Panes stack in a fixed order — Topology,
Guide, Terminal, Packet Analysis — and the page scrolls.

### 2. Topology View

The main page displays your network topology using an interactive D3.js visualization:

- **Nodes**: Routers (blue), hosts (green), switches (orange), Speakers (purple), Container Hosts (Docker blue)
- **Service badges**: A Node offering a Service carries a badge above it: `DHCP`, `DHCPv6`, or `DHCP · DHCPv6`; `DNS` or `DNS · SECONDARY`; a teal `RESOLVER`; and a stacked `DHCP RELAY` badge listing the Subnets a Relay covers. Selecting a badge (mouse, or focus and `Enter`) opens that Service's panel; the badge's label says what it reveals
- **Relay Paths**: When a Lab declares a DHCP Relay, the relayed exchange (client Subnet, relaying Router, upstream Service) is drawn as a dashed overlay with a v4/v6 chip at its midpoint. Hover or focus the Relay badge to preview one path; select the Router to pin every path through it, and select a node with no Relay to unpin. A Service address no Node owns ends the path at an **unreachable** marker naming the address
- **Links**: Gray lines representing connections
- **Layout**: Auto-arranged for readability; drag nodes to reposition
- **Zoom**: Scroll to zoom in/out

**Actions**:
- **Click any node** → Opens an in-page terminal tab connected to that node
- **Right-click a node** → Opens node actions: the terminal, and **Open DNS Service** or **Open Recursive Caching Resolver** when the node offers one
- **Keyboard support** → Focus a node and press `Enter`/`Space` to open terminal; arrow keys move focus to connected nodes
- **Context-menu keyboard support** → Press the Context Menu key or `Shift+F10` on a focused node

### Service panels

Every Service panel is a modal opened from the Node's badge. Each one polls
its Node while open, so what it shows is the daemon's own state, not a copy
the framework keeps. What a Learner edits in a panel is Exercise
Configuration: applied to the running daemon at once, and discarded when the
Lab stops.

#### DHCP Service panel

Opened from a `DHCP` badge. Two sections:

- **Address Pool**: one editor per served Subnet. `Start` and `End` are
  read-only; `Lease time`, `Router`, `DNS server`, and the `Reservations`
  list (add or remove one, identified by MAC address or DUID) are editable,
  and **Apply** pushes them to the running Kea without a restart or a lost
  Lease. An IPv6 Pool has no `Router` field and gains `Preferred lifetime`.
- **Active Leases**: one table per address family. A v4 Lease shows the
  hardware address, a v6 Lease the DUID, each with a copy button. A Lease
  that arrived through a Relay carries the relaying Router in a `Via` column.

#### DHCP Relay panel

Opened from a `DHCP RELAY` badge. Shows the Subnets the Relay covers and the
address of the Service it forwards to, then a **Forwarded requests** table:
one row per request the Relay has carried, with its source and the server it
went to. **View on Canvas** closes the panel and pins the Relay Paths through
this Router.

#### DNS Service modal

Opened from a `DNS` or `DNS · SECONDARY` badge, or from **Open DNS Service**
in the node context menu. The modal contains:

- **Zone & Records**: choose a hosted zone, inspect active records, and add,
  edit, or delete records. Edits use one atomic Knot transaction and show
  server-side validation failures in the form banner. A signed Zone shows a
  green **Signed** badge beside the selector and a **Show signature Records**
  toggle that reveals the `RRSIG` and `NSEC` rows the editor otherwise
  hides.
- **Secondary replication** (a Secondary Zone only): a header naming the
  Primary, the current Serial, and the last transfer, above a runtime state
  banner (`Zone replicated and up to date`, `Primary unreachable`, `Transfer
  refused`, `Zone expired: answering SERVFAIL`, `Serial not advanced`) and
  the **Transfer Log** of every `AXFR`, `IXFR`, and `NOTIFY` with its Serial
  transition and wire bytes. **Refresh** asks the Primary whether its Serial
  moved; **Full Transfer** forces a complete copy. Records are read-only,
  with a link that opens the Primary's editor.
- **DNSSEC Details** (a signed Zone only): the signing algorithm and denial
  method (`NSEC` or `NSEC3`), the **Active Keys** table (key tag, role,
  algorithm, state), and the **DS** records the parent must publish, each
  with a **Copy DS** button so a 64-character digest is never retyped. A
  Secondary of a signed Zone lists no keys: it holds none.
- **Live Query Activity**: view query volume, per-type counters (`A`, `AAAA`,
  `NS`, `SOA`, `PTR`, `TXT`, `MX`, and others when present), and response
  codes. Auto-refresh polls every three seconds; **Reset Statistics** clears
  the displayed baseline even when auto-refresh is disabled.
- **Zone Status**: view the selected zone, role, SOA serial, primary
  nameserver, and Knot's current transaction/state value.

Record changes broadcast as `DNS_RECORD_UPDATED`, so other open DNS modals
refresh their selected zone without a page reload.

#### Resolver modal

Opened from the teal `RESOLVER` badge, or from **Open Recursive Caching
Resolver** in the node context menu. Two tabs:

- **Cache Inspector**: metric cards for queries, cache hits, hit ratio, and
  mean recursion time, over a table of everything the Resolver holds. Each
  row carries a green `HIT` badge or a yellow `NXDOMAIN`/`NODATA` badge in
  its Status column and, when the Resolver validates, `SECURE`, `BOGUS`, or
  `INSECURE` with an `AD` marker in its Validation column. TTLs count down
  every second between polls. Rows flush individually; **Flush Entire
  Cache** clears the lot.
- **Access Control**: the subnet rules the Resolver enforces (`allow`,
  `refuse`, `deny`), editable in place. Saving applies the whole policy in
  one hot reload that keeps the cache, and broadcasts
  `RESOLVER_ACCESS_UPDATED` to every open session.

#### Speaker panel

Opened from **Open Speaker** in a Speaker's node context menu (clicking the
Node itself still opens a Terminal). One section per Neighbor — address,
AS, session state and uptime — with the routes the Speaker has announced to
it and the routes it has sent back behind their counts; a Neighbor whose
session is down is listed as down, its announced routes shown as queued.
Above the sections, the **Announce a route** form takes the CLI's field set:
the Neighbor (or **All Neighbors**), the prefix, a next hop pre-filled with
the chosen Neighbor's own address, and optional AS-PATH, communities, large
communities and MED. Beside it, **Generate routes** announces a burst of
`Count` random routes of the Lab Examples' kind to one Neighbor — optional
`Sub-prefixes` for longest-match lessons, seeds to make a burst repeatable —
and lists the prefixes it announced. Every announced row has a
**Withdraw**; each Neighbor has a **Withdraw all** that asks first, because
it also takes the routes the Lab Example shipped. A refused or rejected write is shown with its reason
beside the form. The panel keeps nothing: it re-reads ExaBGP after every
write and every three seconds while open, so another Learner's announce
shows up within that interval.

#### RPKI CA panel

Opened from an `RPKI CA` badge. It shows one Hosted CA at a time, chosen
under **Hosted CA**, so the panel stays short however many the Lab holds.
For that Hosted CA it lists its **Resources** (the ASN and prefixes it holds), its **ROAs**
and its **ASPAs**, each row with **Edit** and **Remove**. The add form
starts filled with what a ROA there almost always says: the Hosted CA's own
AS as origin, and its prefix when it holds just one; leave **maxLength**
empty to allow the prefix itself and nothing longer. A ROA for a prefix the
Hosted CA does not hold is refused beside the form, with the reason.

The tables are Krill's own state: a change made with `krillc` in a Terminal
on the Node appears on the next poll, and a change made here is broadcast
to every open view of the Lab. See [RPKI.md](RPKI.md).

#### Validator panel

Opened from a `Validator` badge. Read-only: the **Validated ROA payloads
(VRPs)** and ASPAs the Validator serves, when it last fetched from the RPKI
CA and when it fetches next (as "6 s ago", "in 4 s"), and the **Connected
Routers** asking it over RTR. A ROA signed at the RPKI CA appears once the
Validator's next run has fetched it. A filter appears once there are more
than ten VRPs; it matches a prefix, or an origin AS with or without `AS`.

### 3. Terminal Access

A node's terminal is its own machine, not a window onto the Lab VM: `ps`
lists only the processes running inside that node, and `hostname` returns
the node's own name. That list includes anything started on your behalf,
such as a `network-tools` `iperf3` server, not only commands you typed
yourself — the same view CLI Mode's `connect_node.sh` gives.

Each node terminal provides a full shell environment:

- **Full terminal emulation** (xterm.js) with command history
- **Copy/Paste support** (Ctrl+C/V or Cmd+C/V on Mac)
- **Multiple concurrent terminals** (default: 5 per session)
- **Automatic cleanup** after 5 minutes of idle time

Click a Node in the Topology to open its Terminal in the **Terminal** pane,
at the Right of the Topology. The toolbar's Terminal button and `Ctrl+T` hide
the Terminal pane and show it again; hiding it keeps every Terminal running.
Full-screen programs redraw whenever the pane changes size.

**Example commands**:

```bash
# View network interfaces
ifconfig

# Ping a neighbor
ping 192.168.1.2

# Access FRR CLI (on routers)
vtysh

# View routing table (in vtysh)
show ip route

# View OSPF neighbors (in vtysh)
show ip ospf neighbor

# View BGP status (in vtysh)
show bgp summary
```

#### Packet Capture

A **Packet Capture** shows the packets crossing one Interface as they cross it,
decoded field by field, in the **Packet Analysis** pane beneath your
Terminals, so you can run a command and watch the exchange it causes arrive.

1. Click a Link and choose **Packet Capture** from its menu, below
   **Configure Link**. Or click a Router, Host or Speaker and choose
   **Packet Capture** from its menu: that lists every Interface the Node
   has, including one whose Link is not drawn.
2. In the Packet Capture dialog, choose the Interface to watch. Each button is
   named by its Interface (`Capture on r1-eth1 (r1)`), because a capture
   happens at a point and the same packet differs on either side of a Router.
3. The capture starts at once. Each packet is one row: number, time since the
   capture started, source, destination, protocol and the message it carries
   (`Discover`, `Solicit`, `Echo (ping) request`, `UPDATE Message`), read the
   same way for every protocol; one with no message type shows its layer's
   name. The list
   has the pane to itself until you select a row (click, or the arrow keys
   once the list has focus); its full field tree then opens below the list.
   The close button or Escape hides the fields again. Above the list, a badge
   says where the capture stands (Capturing, Ended, Summaries only) beside
   the packet count, and three buttons follow: **ⓘ** explains the pane,
   **Download pcap** (⤓) and **Stop capture** (■). Routine news is read out
   to a screen reader rather than shown; a problem, such as a rejected
   filter, stays in view until it is put right.

Narrowing a capture:

- **Protocol presets** — All, DHCP, DNS, OSPF, BGP, ICMP and ARP — pick the
  packets of one protocol without knowing its ports. Each preset's tooltip
  names the capture filter it expands to (DHCP is `udp port 67 or udp port 68
  or udp port 546 or udp port 547`, which covers DHCPv6 too), and choosing it
  writes that expression into the filter box.
- **The filter box** takes any BPF capture filter directly (`host 10.0.0.1 and
  icmp`); press Enter or **Apply**. Wireshark display filters are not
  accepted: download the pcap for those.
- **Changing the filter restarts the capture.** The kernel applies the filter
  as packets are captured, so it cannot reach packets already taken: the list
  is cleared and the pane says the capture restarted. An expression tshark
  rejects is read out with tshark's reason, and the pane stays open to correct
  it.

With no Terminal open, Packet Analysis has the whole Right of the page.

Comparing two points: open a second capture the same way and it appears as a
second tab in Packet Analysis. Two can run at once — one each side of a Router shows
the TTL fall by one and, in a NAT Lab, the source address rewritten. A third
is refused with the reason; stop one first.

What to expect:

- **Routers, Hosts and Speakers** can be captured. A switch port, a Container
  Host and an Internet Gateway cannot: a capture only ever shows its own Lab's
  traffic.
- **A capture is shared.** Everyone watching the same Interface watches one
  capture; opening it again attaches to the one already running.
- **Reloading the page keeps it.** The capture keeps running for the Terminal
  idle timeout (5 minutes by default) after its last viewer leaves, and a
  reloaded page reopens it, including the most recent packets. **Stop capture**
  ends it straight away unless someone else is still watching.
- **Very fast traffic is summarised.** Above 200 packets a second (an `iperf3`
  or `ping -f` on the captured Interface) only the summary rows are sent, and
  the pane says so; fields return below 100 a second. If packets arrive faster
  than your browser can take them, the pane keeps a count of how many it could
  not show; the pcap still has them.
- **Download pcap** saves the capture for Wireshark. It holds more than the
  list, which keeps only the latest 1000 packets. The pcap is written as the
  capture runs, into the Lab directory, and is **deleted when the Lab stops**,
  so download it before you finish. It is bounded (two files of 10 MB, the
  oldest discarded first). Each filter writes its own pcap; starting a capture
  on an Interface deletes the pcaps that ended captures on it left behind.

#### NAT Table

In a NAT Lab, a Router masquerades the traffic it sends out its NAT-facing
Interface, and remembers each connection it rewrote so that the replies can be
rewritten back. The **NAT Table** tab shows what it remembers. A packet only
ever carries one side of an entry, and never how long it has left, so no
capture can show you this.

1. Click the Router and choose **NAT Table** from its menu. The item appears
   only on a Router with Router NAT, one that translates for its Lab: `r1` in
   the NAT Lab (`nat-lab`).
2. The tab opens in Packet Analysis beside any Captures and does not count against
   their limit of two. It holds one table per translating Router, headed by
   the Router and the Interface it translates on (`r1 · r1-eth1`).
3. Each row is one entry, one connection: its protocol; the **inside**
   address and port it came from; the **outside** address and port the Router
   substituted (for ICMP, the echo identifier takes the port's place); how
   long the entry has left; its state (the TCP state, or `UNREPLIED` /
   `REPLIED` for UDP and ICMP); and where it is going.

What to expect:

- **The tables refresh every two seconds** while the tab is on show, and
  **Expires in** counts down. When it reaches zero the Router forgets the
  entry; more traffic on the connection renews it.
- **The header stays out of the way.** It shows only whether the tables are
  refreshing or **Paused**, and three buttons: ⓘ opens a short explanation of
  the columns, the pause button (⏸ / ▶) holds the tables still so they can be
  read, and ✕ closes the tab. Only a change in how many entries a Router holds
  is announced to a screen reader, never the countdown.
- **Only translated connections are listed.** A connection the Router forwards
  without rewriting, or its own BGP or OSPF sessions, is not an entry.
- **Only the Lab's own Routers.** Each table is read inside its Router's
  namespace, so the Internet Gateway's translation to the host's uplink and
  another Lab's entries never appear.

### 4. Lab Prompt

In the topology toolbar, beside **Tools**, the **Lab Prompt** button opens the Mininet-IPLab CLI (`mininet-iplab>`) inside an in-browser terminal tab. The button is shown only to an Instructor: the Lab Prompt is the Instructor's alone, and a Learner drives the Lab through Node Terminals and the Topology.

In Multi-Lab Mode, **Lab Prompt** in a Group's Overview details opens that
Group's Lab and selects the same terminal tab. The selected Group is shown
in navigation; there is no separate prompt dialog.

- **Shared prompt**: Exactly one Lab Prompt exists per running Lab. The server multiplexes the underlying pseudo-terminal across all viewers: output fans out to every connected browser and to the launching terminal, while input from any Instructor viewer is written through.
- **Full CLI commands**: Run network-wide commands such as `pingall`, `show_ips`, `link_config`, `show_routes`, and `py net.hosts`.
- **Lab isolation**: Python commands via `py` evaluate inside that specific Lab's process and cannot reach the server manager or other Labs.
- **Stopping feedback**: When the Lab is stopped, all attached viewers are notified immediately.

### 5. Session Management

The web UI maintains a Session for your login:

- **Login session cookie**: `session` cookie, 24-hour max age in the browser
- **Session storage**: In-memory on the server (Sessions are cleared on server restart)
- **Server-side idle expiry**: a Session unseen for 2.5 hours is treated as dead
  regardless of the cookie's own max age (configurable, see below)
- **Reload-safe**: signing in again from the same browser replaces its existing
  Session rather than piling up a new one
- **Per-Session terminals**: up to 5 concurrent Terminals per Session (configurable)
- **Host-wide terminal ceiling**: up to 100 Terminals across the whole host,
  independent of any one Session (configurable)
- **Session Cap**: optionally bounds how many Sessions one account may hold at
  once; off by default (see [Session and Terminal Limits](#session-and-terminal-limits))
- **Automatic cleanup**: idle Terminals close after 5 minutes (configurable)
- **One Session per login**: each login gets an isolated Session with separate Terminals

Open **Your Session** in the account menu (your name and role, at the right
of the header) to find your automatically assigned
nickname and connection code. You can change the nickname without signing
out, and copy the details to tell the Instructor which connection needs help.
In Multi-Lab Mode the details include your Group. Names may repeat; the
connection code distinguishes Sessions and stays the same through reloads
and nickname changes. A new Session receives a new code.

## Configuration

### Changing Credentials

**Important**: The generated password is for one person on one machine. Set your own for any shared or non-local environment.

**Step 1**: Copy the example configuration file:

```bash
cp .env.example .env
```

**Step 2**: Edit `.env` and customize credentials:

```bash
MNIPLAB_USERNAME=your_username
MNIPLAB_PASSWORD=your_secure_password
```

**Step 3**: Restart the lab:

```bash
# Kill the running lab (Ctrl+C in terminal)
# Start again
python3 examples/static-lab.py --enable-web
```

### All Configuration Options

Set these via environment variables or `.env` file:

| Variable | Default | Description |
| --- | --- | --- |
| `MNIPLAB_USERNAME` | `admin` | The one shared login's username, used while `MNIPLAB_USERS` is unset (an Instructor) |
| `MNIPLAB_PASSWORD` | _(generated)_ | The one shared login's password, used while `MNIPLAB_USERS` is unset; when unset, a random one is generated at every start and printed on the console only |
| `MNIPLAB_USERS` | _(unset)_ | Instructor and Learner logins: `user:pass:role,...`. When set, overrides `USERNAME`/`PASSWORD`. Roles: `instructor`, `learner`. |
| `MNIPLAB_HOST` | `0.0.0.0` | Web server bind address, for `--enable-web` and `serve` alike; a `--web-host` flag takes precedence |
| `MNIPLAB_PORT` | `8050` | Web server port, for `--enable-web` and `serve` alike; a `--web-port` flag takes precedence. Under Docker Compose, change the `ports:` mapping too |
| `MNIPLAB_SESSION_PER_USER_CAP` | `0` | Max Sessions per account, in Sessions; `0` disables the cap |
| `MNIPLAB_SESSION_IDLE_TIMEOUT` | `9000` | Max seconds a Session may go unseen before it is treated as dead (2.5 hours) |
| `MNIPLAB_TERMINAL_GLOBAL_CAP` | `100` | Max Terminals across the whole host, in Terminals |
| `MNIPLAB_TERMINAL_PER_SESSION_CAP` | `5` | Max Terminals per Session, in Terminals |
| `MNIPLAB_TERMINAL_IDLE_TIMEOUT` | `300` | Max seconds a Terminal may go unused before it auto-disconnects |
| `MNIPLAB_TERMINAL_PING_INTERVAL` | `15` | Seconds between a Terminal's WebSocket keepalive pings |
| `MNIPLAB_LAB_MODE` | `single` | `single` (one Lab for everyone) or `multi` (a Lab per Group — see [Multi-Lab Mode](#multi-lab-mode)). **Read at startup only** — change it and restart; there is no live switch, and a restart comes up with no Lab alive. |
| `MNIPLAB_LAB_CAP` | `4` | Multi-Lab Mode only: the most Labs alive at once on this host. Refuses Group and Instructor alike; Single-Lab Mode is a cap of one whatever this says. |
| `MNIPLAB_LAB_BUILDER_TIMEOUT` | `60` | Max seconds allowed for a Lab to build and report ready before timing out |
| `MNIPLAB_ENVIRONMENT` | `development` | Set to `production` to enforce stricter security checks |
| `MNIPLAB_ENV_FILE` | `.env` | The env file a Lab and the web server read their settings from, resolved against the working directory. A shell export only — it names the file, so it cannot come from one. Lets two web servers run from one checkout, each with its own settings. |
| `MNIPLAB_ALLOW_DEFAULT_PASSWORD` | `false` | Allow default password in production (demos only) |
| `MNIPLAB_USE_SECURE_COOKIES` | `false` | Set the Secure flag on the Session cookie; turn on when serving over HTTPS |
| `MNIPLAB_CORS_ORIGINS` | `["*"]` | Allowed CORS origins, as a JSON list; production mode refuses the wildcard |
| `MNIPLAB_LOGIN_RATE_LIMIT` | `5` | Wrong passwords allowed per minute for one login name; after that the login is locked for the rest of the minute, even to the right password. Correct logins never count, and the count is per login name, not per address, so a Group signing in on many screens, or a class behind one proxy or NAT, is never locked out |
| `MNIPLAB_WS_RATE_LIMIT` | `30` | Terminal connections a Session may open per minute; must be at least three times `TERMINAL_PER_SESSION_CAP` |
| `MNIPLAB_WS_ATTEMPT_LIMIT` | `120` | Terminal connection attempts a Session may make per minute, whatever the outcome; must be at least `WS_RATE_LIMIT` |
| `MNIPLAB_ACTIVITY_IDLE_THRESHOLD` | `300` | Seconds without Session or Terminal activity before a Group's legacy `activity_state` reads Idle |
| `MNIPLAB_AUTHORED_LABS_DIR` | `authored-labs/` in the checkout | Where Lab Authoring keeps drafts and published Lab Examples |

#### Session and Terminal Limits

The four limits above (`SESSION_PER_USER_CAP`, `SESSION_IDLE_TIMEOUT`,
`TERMINAL_GLOBAL_CAP`, `TERMINAL_PER_SESSION_CAP`) are **operational hygiene, never
security boundaries**. Within a Lab, account credentials are not secret, and
a Terminal already runs with full root privilege inside it; these knobs bound
resource use and accidental sprawl, nothing more.

- **A Session survives for as long as it is actually used.** Terminal
  traffic and any UI action both refresh it, so a Learner working through a
  long exercise is never interrupted. A browser tab left open with neither —
  no Terminal, no clicking — is swept once `SESSION_IDLE_TIMEOUT` passes.
- **`SESSION_PER_USER_CAP` is off by default** because a runtime cannot tell
  a group account from a personal one — `MNIPLAB_USERS=class:pass:learner` is
  one credential that may legitimately serve thirty people. Turn it on only
  when every account really does map to one person; with a credential shared
  across a class, a non-zero cap locks the whole class out, not just the
  extra devices.

**Example `.env` file**:

```bash
# Authentication
MNIPLAB_USERNAME=alice
MNIPLAB_PASSWORD=SecurePassword2024!

# Session and terminal limits (operational hygiene, not security boundaries)
MNIPLAB_SESSION_PER_USER_CAP=0
MNIPLAB_SESSION_IDLE_TIMEOUT=9000
MNIPLAB_TERMINAL_GLOBAL_CAP=100
MNIPLAB_TERMINAL_PER_SESSION_CAP=10
MNIPLAB_TERMINAL_IDLE_TIMEOUT=120

# Network (with Docker Compose, also map the port in docker-compose.yml)
MNIPLAB_PORT=8080
```

## Classroom Server

`python3 my-lab.py --enable-web` puts one Lab in the browser for one person,
with the Lab Prompt on the launching terminal. A class uses
`python -m mniplab serve` instead: the server starts with no Lab, the
Instructor starts Labs from the browser, and each Lab runs in a child process
of the server. It listens where `--web-host` / `--web-port`, else
`MNIPLAB_HOST` / `MNIPLAB_PORT`, say (default `0.0.0.0:8050`).

`MNIPLAB_LAB_MODE`, read once at startup, chooses how many Labs it serves:

- **Single-Lab Mode** (`single`, the default): one Lab for everyone. The
  Instructor picks a Lab Example from the Lab Catalog in the **Labs**
  sidebar, starts it and stops it, and every login shares it. A class that
  moves together uses it.
- **Multi-Lab Mode** (`multi`): a Lab per Group, for a class that works at its
  own pace; see [Multi-Lab Mode](#multi-lab-mode). It is meant for `serve`.

On `serve` the Instructor can also author new Lab Examples with
[Lab Authoring](lab-authoring.md) and Publish them into the Lab Catalog.

## Server settings

On `serve`, **Server settings** in the Instructor's account menu opens a
dialog with two tabs, **Limits** and **Accounts** (below). The Limits tab
changes six Limits while the class runs: Terminals per Session, Terminals
on the whole server, the idle Terminal timeout (which also sets how long a
Terminal waits for a reloaded page and how long a Packet Capture runs on after
its last viewer), the idle Session timeout, Sessions per login, and, in
Multi-Lab Mode only, Labs alive at once. A Lab script run with `--enable-web`
has no such dialog.

- A save applies at once. Lowering a cap closes nothing already open; it
  refuses only what comes next. Lowering a timeout reaches what is open from its
  next check.
- Each value has a minimum and a maximum, shown under its name with the value
  the server started with. A timeout also reads in minutes or hours under its
  box (9000 seconds is "2 h 30 min"). A value the other settings cannot
  accommodate is refused with a sentence naming the field, and nothing is
  changed. The ⓘ buttons show the longer explanations on request.
- Saved Limits are kept in `settings.json`, beside the Assignments file, holding
  only what was overridden. **`.env` stays the baseline.** Empty a field and save
  to return that Limit to its `.env` value (else its default); deleting
  `settings.json` does the same for every Limit.
- A Limit exported in the environment the server was started in wins over both,
  and shows disabled as "fixed where the server was started".

Routes: `GET` and `PUT /api/admin/server-settings` (Instructor only). `GET`
reports, per Limit, `value`, `started_with`, `min`, `max` and `pinned`; `PUT`
takes `{"limits": {name: number | null}}`, `null` clearing the Limit.

### Accounts

The second tab, **Accounts**, lists every login the server accepts, with their
number, in both Lab modes; in Multi-Lab Mode it adds that each Learner account
is a Group, with its own Lab. Logins
added here come first; logins set in `.env` follow under one heading, **Set in
.env · change them there**, folded when there are more than eight, since they
are changed only in `.env`. Above eight logins a **Filter by name** box narrows
both lists and says how many it shows.

- **Add login** takes a name — 2 to 32 lowercase letters, digits and hyphens,
  starting with a letter, and not already a login — and shows a generated
  password such as `crane-ruby-4827` at once. The login is a Learner and signs
  in straight away; in Multi-Lab Mode it is a Group like any other.
- The eye button beside a page-added login's masked password shows it, to tell
  a Group that forgot it, and hides it again.
- Its **⋮** menu holds the two actions that sign its Learners out.
- **Reset password…** (asks first) replaces a page-added login's password with a
  new generated one, shown at once, and signs out everyone using the old one.
  Their open Terminals close; a Group's Lab, Assignment and Config Copy stay,
  so they sign back in with the new password to the same work.
- **Remove…** (asks first) deletes a page-added login and ends its Sessions and
  Terminals. In Multi-Lab Mode it also stops the Group's running Lab and drops
  its Assignment, but keeps its Config Copy: adding the same name again gives
  the Learners their work back, with a new password and an empty Assignment.
- Logins set in `.env` show only their role, with neither control.
- Page-added logins are kept in `accounts.json` beside `settings.json`, readable
  by the server's owner only, and survive a restart. The start banner counts
  them but never prints their passwords.

> **Keep `accounts.json` out of anything shared with Learners.** It holds every
> page-added password in plain text, as `.env` does. Leave it out of a
> repository, a shared folder, a lab image or a backup that Learners can read,
> and do not hand out a copy of the directory it sits in.

Routes (Instructor only): `GET /api/admin/logins` lists `name`, `role` and
`added`, never a password; `POST /api/admin/logins` takes `{"name": ...}` and
returns the name and its password (422 for a name outside the rule, 409 for one
already a login); `GET /api/admin/logins/{name}/password` reads a page-added
login's password back; `POST /api/admin/logins/{name}/reset` returns the name
and a new password; `DELETE /api/admin/logins/{name}` removes the login. Reset
and remove answer 404 for an `.env` account or a name that is no login.

## Instructor and Learner logins

Use `MNIPLAB_USERS` to give the Instructor and the Learners their own logins.
When set, the one shared `MNIPLAB_USERNAME`/`PASSWORD` login is **ignored**;
that shared login is itself an Instructor.

**Roles:**

| Role | Permissions |
| --- | --- |
| `instructor` | Start and stop Labs (in Multi-Lab Mode, any Group's); set each Group's Assignment; open the Lab Prompt; watch every Session and Terminal, close a Terminal or sign a Session out; reset Router Config Copies; author Lab Examples with Lab Authoring; and everything a Learner may do |
| `learner` | In a running Lab: open Terminals and Packet Captures; set Link Conditions and switch port VLANs; author Exercise Configuration (DHCP Pools and Reservations, DNS Records, Resolver Access Control); flush a Resolver's cache, refresh or retransfer a Secondary Zone, roll DNSSEC keys; make Speakers announce and withdraw routes; run the ping, traceroute and iperf tools. In Multi-Lab Mode, also start and stop the Group's own Lab from its Assignment. Never the Lab Prompt, and never which Nodes, Links, Services or Neighbors exist |

**Configuration in `.env`:**

```bash
MNIPLAB_USERS=alice:InstructorPass1:instructor,bob:LearnerPass2:learner,carol:LearnerPass3:learner
```

**Startup output** (printed to console when the server starts):
```
*** Login accounts (user / password / role):
***   alice / InstructorPass1 / instructor
***   bob / LearnerPass2 / learner
***   carol / LearnerPass3 / learner
```

**Role-aware UI:**
- The account menu button at the right of the header shows the username and
  the role, **INSTRUCTOR** in amber or **LEARNER** in blue, in either Lab
  mode. The menu holds **Sessions &
  Terminals** (Instructor), **Your Session**, **Server settings** (Instructor,
  on `serve`), **Dark mode** and **Log out**.
- Instructors see **Stop Lab** and **Sessions & Terminals** monitoring for the
  selected Lab. Expand a named Session to inspect or close its Terminals, or
  sign out that Session. Each intervention confirms the target and impact.
- In Single-Lab Mode, an Instructor's status line also counts the signed-in
  **Learner Sessions**, beside Nodes and Active Terminals.
- In Single-Lab Mode, Learners share the one Lab the Instructor started, with
  the permissions above. In Multi-Lab Mode a Learner login is a Group with a
  Lab of its own; see below.

## Multi-Lab Mode

`MNIPLAB_LAB_MODE=multi` in `.env`, read when `python -m mniplab serve`
starts, runs several Labs at once, a **Lab** being *a Group running a Lab
Example*. Nothing inside a Lab changes — `r1` is `r1` in every copy — and no
Lab can reach another: every host-side object a Lab creates (bridges, switch
ports, container names, Lab directory) carries the Lab's key. Single-Lab Mode,
the default, is described under [Classroom Server](#classroom-server).

In Multi-Lab Mode:

- **A Group is a login** from `MNIPLAB_USERS`. The Instructor's login is a
  Group too, and the one shared login (`MNIPLAB_USERNAME`/`PASSWORD`) is
  one Group. A Group holds **one Lab at a time**: starting a second Lab
  Example is refused naming the current Lab. A Group sees only its own Lab;
  the Instructor sees every Lab and may start or stop any Group's.
- **The cap** (`MNIPLAB_LAB_CAP`, default four) bounds alive Labs on the host
  and refuses Group and Instructor alike with the same message.
- **An Assignment** is the Lab Examples a Group may start. The Instructor
  edits it from the Class Overview (**Assign…** on a Group) and it is saved to
  `assignments.json` beside `.env`, so it survives a restart. Changing an
  Assignment never touches a running Lab. The Instructor's own Group, and
  the one shared login, are assigned everything.
- **A Group's page** is the page above, for its Group: its Assignment in the
  sidebar with a **Start** on each entry, its Lab's Topology, Terminals and
  Lab Prompt, and **Stop Lab**. A **Group** badge in the header names it.
- **The Instructor's Class Overview** shows a table headed Group, Lab, Lab status,
  Sessions, Terminals and Last learner input. Running/Stopped describes the
  Lab independently of the number of signed-in Sessions. Search by Group/Lab
  name or filter Lab status and presence separately; updates preserve Group
  order. Narrow tables reflow into rows with visible field labels.
  Inspect a Group to reveal **Start**, **Stop**, **Assign…**, Router Config
  controls and its named Sessions. Expand a Session to inspect its Terminals.
  Last learner input records accepted Terminal typing or pasted text from
  currently signed-in Learners. Continuous output (including ping), connection
  keepalives, resizing and packet-capture traffic do not advance it. No input
  text is stored. The timestamp survives Terminal closure, but ends with its
  Session; "No input yet" means no input has been recorded for current Sessions.
  Session details show their own last input, with connection last-seen time
  in the expanded detail. A quiet timestamp does not mean learners are stuck.
- **Bounded navigation** provides **Overview**, the selected Lab and a
  searchable **Select Group** control. Groups without a Lab open their
  Overview details. The Lab workspace's **Sessions & Terminals** view is
  scoped to that Group/Lab; return to Overview for the whole class.
  Group details also show the Lab **key**, which matches host bridges and
  containers, and the reason if a Lab's process ended unexpectedly.
- **Persistent Config is per Group.** A Lab Example's `frr_dir` directory
  (`frr-config/<example>/` in this repository) is the **seed**, the
  Instructor's versioned copy. A Group's first start of that Lab Example
  copies each Router's seed into the Group's own copy,
  `group-config/<group>/<example>/<router>/` beside `frr-config/` and
  `assignments.json`; later starts resume the copy, and `write memory` lands
  in the copy only. Two Groups never share a file, and the seed is never
  written under the manager — the Instructor authors it in CLI Mode
  (`python3 examples/ospf-lab.py`), where the seed is used directly, as it
  is in Single-Lab Mode. Each entry on the Group's page says whether the
  next start **resumes your Group's saved Router Configs** or **begins from the seed**, and a Group with a copy has **Start clean**, which discards it
  and starts from the seed. Class Overview lists each Group's copies with **Reset** (one Group) and,
  per Lab Example, **Reset every Group**; a reset is refused, naming the Lab, while that Group's Lab on
  that Lab Example is alive. A Lab Example with Ephemeral Config (no
  `frr_dir`) has none of these controls; Exercise Configuration (Pools,
  Records, Leases, resolver rules) does not resume.
- **Stop all Labs…** in Class Overview stops every Lab listed in its
  confirmation, including the Instructor's Lab, regardless of Group filters.
  All Terminals close and temporary Lab configurations are lost; saved Router
  Config Copies remain and users stay signed in. Progress and results appear
  below the header; keep the page open until they finish. Failed stops can be
  retried separately. Labs started after the confirmation opens are excluded.
  With no running Labs, the button is disabled.
- Stopping the server stops every Lab; it restarts with none alive.

## API Endpoints

The web UI is powered by a FastAPI backend. These endpoints can also be accessed programmatically.

For drawing, previewing and publishing Labs in the browser, see the
[Instructor Lab Authoring guide](lab-authoring.md).

Every route that reads or writes a Lab lives under the Lab's **key**,
`/api/labs/{key}/…`. The key is a short token the server allocates when the
Lab starts; `GET /api/status` and `GET /api/labs` report it, and `POST
/api/labs` answers with it. A key that names no Lab alive is refused with
404 — and, when that Lab's process ended on its own, with the reason. The
catalog of Lab Examples is under `/api/examples`; its entries are addressed
by `example_id`.

Under `python -m mniplab serve` each Lab runs in a child process of the
server, reached through its socket at `<MNIPLAB_TMP_DIR>/labs/<key>/lab.sock`. A Lab whose
process dies is marked stopped, its host-side objects are swept, and its
Terminals are closed with the reason; nothing restarts it.

### Authentication

**Login**
```
POST /api/login
Authorization: Basic <base64(username:password)>

Response: { "authenticated": true, "username": "admin", "role": "instructor" }

# Also sets HTTP-only cookie:
Set-Cookie: session=<session-id>; HttpOnly; SameSite=Lax; Max-Age=86400
```

**Current User Info**
```
GET /api/me

Response: { "username": "alice", "role": "instructor", "group": "alice",
            "mode": "multi", "cap": 4 }
```
`group` is the login itself; `mode` is `single` or `multi` and `cap` the
most Labs alive at once (one in Single-Lab Mode).

**Logout**
```
POST /api/logout

Response: { "logged_out": true }
```

### Labs

**Catalog** — the Lab Examples the caller may start: the whole catalog in
Single-Lab Mode; in Multi-Lab Mode a Group's Assignment, and everything for
the Instructor
```
GET /api/examples

Response: { "examples": [ {"id": "ospf-lab", "name": "...", "file": "ospf-lab.py", ...}, ... ] }
```
In Multi-Lab Mode each entry also carries `persistent_config` (the Lab
Example declares `frr_dir`) and `resumes` (the caller's Group holds a copy,
so its next start resumes rather than seeds).

**Alive Labs** — the Labs the caller may see: the one Lab in Single-Lab
Mode; in Multi-Lab Mode a Group's own, and every Lab for the Instructor
```
GET /api/labs

Response: { "labs": [ {"key": "cb28", "group": "bob", "example": "ospf-lab", "uptime": 12,
                       "nodes": 18, "active_terminals": 0, "link_condition_drift": 0} ] }
```

**The caller's Lab** — the one Lab, or the caller's Group's own, and why its
last Lab ended when none is running
```
GET /api/status

Response: { "running": true, "key": "cb28", "group": "bob", "example": "ospf-lab", ... }
          { "running": false, "last_exit": {"key": "cb28", "example": "ospf-lab",
                                            "group": "bob", "reason": "killed by SIGKILL", ...} }
```

**Start a Lab** _(Single-Lab Mode: instructor only; Multi-Lab Mode: any
Group, for itself, from its Assignment)_
```
POST /api/labs
Content-Type: application/json

{ "example_id": "ospf-lab" }
{ "example_id": "ospf-lab", "group": "bob" }   # the Instructor, for another Group, from its Assignment
{ "example_id": "ospf-lab", "clean": true }    # "start clean": discard the Group's copy, start from the seed

Response: { "status": "started", "key": "cb28", "group": "bob", "example": "ospf-lab",
            "started_at": 1789535252.0, "nodes": 18 }

409 "Group 'bob' already runs Lab 'ospf-lab' (cb28); stop it first"
409 "Lab cap reached: 4 of 4 Labs running"          # Group and Instructor alike
403 "Lab Example 'bgp-lab' is not in Group 'bob's Assignment"
```

**One Lab, and stopping it** _(stop: the Instructor, or in Multi-Lab Mode
the Group that owns it)_
```
GET  /api/labs/{key}
POST /api/labs/{key}/stop
```
A key that names another Group's Lab is refused exactly as one that does not
exist: `404 "No Lab with key 'cb28' is running"`.

**Class Overview** _(instructor only)_ — every Group with its Assignment, its
Lab or why its last Lab ended, and every Lab with its key
```
GET /api/admin/dashboard

Response: { "mode": "multi", "cap": 4,
            "groups": [ {"name": "bob", "role": "learner", "assignment": ["ospf-lab"],
                         "config_copies": ["ospf-lab"], "incompatible_config_copies": [],
                         "session_count": 5, "last_learner_input": 1770000000 | null,
                         "activity_state": "active", "stop_reason": null,
                         "last_activity": 1770000000,
                         "lab": {"key": "cb28", ...} | null, "last_exit": {...} | null}, ... ],
            "labs": [ {"key": "cb28", "group": "bob", ...} ],
            "example_names": {"ospf-lab": "OSPF Lab", ...} }
```
`config_copies` names the Lab Examples the Group holds a copy of Persistent
Config for — what its next start resumes, and what a reset discards;
`incompatible_config_copies` names those an authored revision has made
incompatible. `last_learner_input` is the Group's Last learner input.
`activity_state` and `stop_reason` are the legacy combined liveness signal,
kept for existing API clients. `example_names` maps each Lab Example id to its
display name.

**Reset Persistent Config to the seed** _(instructor only)_ — one Group's
copy, or every Group's when `group` is omitted; nothing is reset while a
named Group's Lab on that Lab Example is alive
```
POST /api/admin/config/reset
Content-Type: application/json

{ "example_id": "ospf-lab", "group": "bob" }
{ "example_id": "ospf-lab" }                   # every Group holding a copy

Response: { "example": "ospf-lab", "reset": ["bob"] }
409 "Group 'bob' runs Lab 'ospf-lab' (cb28); stop it first"
400 an unknown Lab Example; 404 no such Group can log in
```

**Assignments** _(instructor only)_ — saved to `assignments.json` beside
`.env`; a running Lab is never touched
```
GET /api/admin/assignments

Response: { "groups": [ {"name": "bob", "role": "learner"}, ... ],
            "assignments": { "bob": ["ospf-lab"], "alice": ["static-lab", "ospf-lab", ...] } }

PUT /api/admin/assignments/{group}
Content-Type: application/json

{ "example_ids": ["ospf-lab", "static-lab"] }

Response: { "group": "bob", "example_ids": ["ospf-lab", "static-lab"] }
404 no such Group can log in; 400 an id names no Lab Example in the catalog
```

### Topology

**Get Topology**
```
GET /api/labs/{key}/topology

# Auth via session cookie from /api/login

Response:
{
  "nodes": [
    {"id": "r1", "name": "r1", "type": "router", "x": 100, "y": 100},
    {"id": "h1", "name": "h1", "type": "host", "x": 150, "y": 150},
    ...
  ],
  "links": [
    {"source": "r1", "target": "r2", "style": "solid"},
    {"source": "r1", "target": "h1", "style": "solid"},
    ...
  ],
  "relays": [
    {"relay_node": "r2", "server_ip": "10.0.0.1", "target_node": "r1",
     "reachable": true, "subnets": ["10.20.0.0/24"], "client_nodes": ["h2"],
     "families": [4]},
    ...
  ]
}
```

The top-level `translators` list names each Router with Router NAT and the
outside Interface it translates on (`[{"node": "r1", "interface": "r1-eth1"}]`);
it is empty in a Lab with no Router NAT.

Each node also carries `services` (the Service names it offers),
`service_details` (per Service: `name`, `badge`, `families`), and `relays`
(the Subnets a Relay on it covers). The top-level `relays` list is what the
Relay Path overlay draws; `reachable` is false when no Node owns `server_ip`.

### DHCP Service

All DHCP endpoints require the authenticated session cookie:

```text
GET  /api/labs/{key}/dhcp/leases/{node}
GET  /api/labs/{key}/dhcp/pools/{node}
POST /api/labs/{key}/dhcp/pools/{node}
GET  /api/labs/{key}/dhcp/relay/activity/{node}
```

`POST /api/labs/{key}/dhcp/pools/{node}` names the Pool by `cidr`, `start`, and `end`
and carries `lease_time`, `router`, `dns_server`, and `reservations`; the
change reaches the running Kea without a restart. Leases are returned per
family, a v6 Lease with its DUID and both lifetimes.

### DNS Service

All DNS endpoints require the authenticated session cookie:

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
```

The stats response includes `stats.query-type` and `stats.response-code`
maps. The status response includes `serial`, `primary_ns`, and Knot's
`status.transaction` or state field. Record mutations are atomic and return
HTTP 400 with structured details when Knot rejects the proposed data.
`transfers` is the Secondary's Transfer Log; `refresh` and `retransfer` are
the panel's two buttons. `dnssec` returns a signed Zone's keys and DS
records, and `rollover` takes `{"key_type": "zsk" | "ksk"}` and broadcasts
`DNSSEC_ROLLOVER_TRIGGERED`.

### Recursive Caching Resolver

All Resolver endpoints require the authenticated session cookie:

```text
GET  /api/labs/{key}/nodes/{node}/resolver/cache
POST /api/labs/{key}/nodes/{node}/resolver/cache/flush
GET  /api/labs/{key}/nodes/{node}/resolver/access
PUT  /api/labs/{key}/nodes/{node}/resolver/access
```

### Speaker

Any authenticated Session may read a Speaker (ExaBGP node); there is no
role gate on a lever in a running Lab:

```text
GET  /api/labs/{key}/speakers/{node}
POST /api/labs/{key}/speakers/{node}/announce
POST /api/labs/{key}/speakers/{node}/withdraw
POST /api/labs/{key}/speakers/{node}/generate
```

The payload carries the Speaker's Neighbors with session state and uptime,
and per Neighbor the routes announced to it and received from it, as
[api-reference.md](api-reference.md) documents. The two `POST` routes take
the CLI's field set (`prefix`, `next_hop`, `as_path`, `community`,
`large_community`, `med`, optional `neighbor`; `prefix` or `all: true` for
a withdraw; `count` with optional `neighbor`, `sub_prefixes`, `prefix_seed`
and `as_path_seed` for a generated burst, which answers the routes it
announced), validated before anything reaches ExaBGP: a bad value answers
400 naming the field. A Node that is not a Speaker answers 404; a daemon
that answers `error` or does not answer within five seconds answers 502
naming the command.

### RPKI

Any authenticated Session may read and change an RPKI CA's ROAs and ASPAs,
and read a Validator:

```text
GET    /api/labs/{key}/rpki/{node}/cas
GET    /api/labs/{key}/rpki/{node}/cas/{asn}/roas
POST   /api/labs/{key}/rpki/{node}/cas/{asn}/roas
DELETE /api/labs/{key}/rpki/{node}/cas/{asn}/roas
GET    /api/labs/{key}/rpki/{node}/cas/{asn}/aspas
POST   /api/labs/{key}/rpki/{node}/cas/{asn}/aspas
DELETE /api/labs/{key}/rpki/{node}/cas/{asn}/aspas
GET    /api/labs/{key}/rpki/{node}/vrps
```

A ROA carries `prefix`, `max_length` and `origin`; a `POST` with `old`
replaces that ROA in one change. An ASPA carries `customer` and
`providers`. A prefix the Hosted CA does not hold, or a malformed value,
answers 400 with the reason. See [RPKI.md](RPKI.md#rest).

### Tools, Links, Routing and VLANs

Any authenticated Session may call these on a Lab it can see:

```text
POST /api/labs/{key}/test/ping              {"source", "destination", "count": 4, "ipv6": false}
POST /api/labs/{key}/test/traceroute        {"source", "destination", "ipv6": false, "resolve": false}
POST /api/labs/{key}/test/iperf             {"server", "client"}
POST /api/labs/{key}/test/iperf3            {"server", "client", "seconds": 10, "protocol": "tcp", "json_output": false}
POST /api/labs/{key}/link/disconnect        {"node1", "node2"}
POST /api/labs/{key}/link/reconnect         {"node1", "node2"}
POST /api/labs/{key}/link/configure         {"node1", "node2", "delay", "jitter", "loss", "bandwidth", "scheduler": "hfsc"}
GET  /api/labs/{key}/routing/routes/{node}
GET  /api/labs/{key}/routing/ospf/{node}
GET  /api/labs/{key}/routing/bgp/{node}
GET  /api/labs/{key}/switches/{switch}/vlans
POST /api/labs/{key}/switches/{switch}/ports/{port}/vlan   {"mode": "access|trunk|none", "vlan", "trunks", "native"}
```

The `link` routes set Link Conditions, as **Configure Link** and
`link_config` do. The `test` routes are kept for scripts. The **Network
Tools** dialog's Ping, Traceroute and Iperf3 tabs stream through the
[Network Tools feed](#websocket-feeds-and-terminal-access) instead, so
their output shows as it is printed and they can be stopped; the `ping`,
`traceroute`, `iperf` and `iperf3` routes answer only once the tool has
finished, and are for scripts. The dialog has no tab for the plain `iperf`.

### Lab Assets

**Load Lab Guide Markdown**
```
GET /api/load_guide?example=<lab-id>

# Auth via session cookie from /api/login

# Returns text/markdown when available
# Returns 404 when no guide is defined for the lab
```

**Save a Lab Example's Layout** _(instructor only)_ writes the current node
positions to `examples/layouts/<lab-id>.json`; an authored Lab Example's Layout
is edited in its draft instead (409).
```
POST /api/save_layout
```

Guides are optional. The catalog and running Lab status expose `has_guide`;
the UI skips Guide requests and hides Open Guide links when it is false.
The Guide panel instead shows "No Guide provided for this Lab." Empty Guides
are treated the same way. Server and network failures remain visible as
loading errors. An authored Lab's availability follows the version it started.

Guide naming convention in `examples/`:

- Lab script: `<lab-id>.py`
- Layout file: `layouts/<lab-id>.json`
- Guide file: `guides/<lab-id>.md`

Example:

- `static-lab.py`
- `layouts/static-lab.json`
- `guides/static-lab.md`

### Terminals

Terminals are created over WebSocket when you click a node.

**Admin Terminal List** _(instructor only)_ — every Terminal on every Lab,
each row carrying the Lab's key as `lab`
```
GET /api/admin/terminals

Response:
{
  "terminals": [ {"id": "...", "lab": "cb28", "node": "r1", ...}, ... ],
  "total": 3
}
```

**Admin Terminal Close** _(instructor only)_
```
POST /api/admin/terminals/close
Content-Type: application/json

{
  "terminal_id": "..."
}

Response: { "closed": true, "terminal_id": "..." }
```

**Admin Session List** _(instructor only)_ — the glossary's Session, one
person's authenticated connection, not the Terminals they hold.
```
GET /api/admin/sessions

Response:
{
  "sessions": [ ... ],
  "total": 2
}
```

**Admin Force Logout** _(instructor only)_
```
POST /api/admin/sessions/logout
Content-Type: application/json

{
  "session_id": "..."
}

Response: { "logged_out": true, "session_id": "..." }
```

**Admin Metrics** _(instructor only)_
```
GET /api/admin/metrics

Response: { "active_terminals": 3, "connected_users": 2, "lab_uptime": 120 }
```

### WebSocket Feeds and Terminal Access

**Labs feed** — which Lab is alive for the login behind the page; every
signed-in page holds it
```
WebSocket: ws://localhost:8050/ws/labs

Receive: {"type": "status_update", "data": {"running": true, "key": "cb28", ...}}
```

**Lab feed** — a Lab's Topology and Service updates; closed when the Lab ends
```
WebSocket: ws://localhost:8050/ws/labs/{key}/topology

Receive: {"type": "topology_update", "data": {...}}
```

**Connect to terminal**
```
WebSocket: ws://localhost:8050/ws/labs/{key}/terminal?node=r1

# Auth uses the existing session cookie from /api/login

Send: JSON-encoded terminal input
{"type": "input", "data": "show ip route\n"}

Receive: JSON-encoded terminal output
{"type": "output", "data": "..."}
```

**Connect to Lab Prompt** _(instructor only)_
```
WebSocket: ws://localhost:8050/ws/labs/{key}/prompt

# Auth uses the existing session cookie from /api/login. A Learner is sent
# {"type": "error", "message": "The Lab Prompt is the Instructor's; use a Node Terminal"}
# and the connection closes with code 1008.

Send: JSON-encoded prompt input or resize
{"type": "input", "data": "pingall\n"}
{"type": "resize", "cols": 80, "rows": 24}

Receive: JSON-encoded prompt output
{"type": "output", "data": "..."}
```

**Watch a Packet Capture**
```
WebSocket: ws://localhost:8050/ws/labs/{key}/capture?node=r1&interface=r1-eth1&filter=proto%20ospf

# Auth uses the existing session cookie from /api/login.
# Starts the Capture of that Interface under that BPF filter (omit for
# everything), or attaches to the one already running. One viewer may hold
# two Captures; a third is refused with an "error" message.

Receive, in order:
{"type": "capture", "node": "r1", "interface": "r1-eth1", "filter": "proto ospf",
 "started_at": ..., "rate_guard": false}
{"type": "packet", "summary": {"number": 1, "time": ..., "relative_time": 0.87,
    "length": 82, "source": "10.10.1.1", "destination": "224.0.0.5",
    "protocol": "OSPF", "description": "Open Shortest Path First"},
 "pdml": "<packet>...</packet>"}          # "pdml" is absent while the rate guard is on
{"type": "rate_guard", "active": true}    # when it engages or releases
{"type": "dropped", "count": 12}          # packets this viewer could not be sent
{"type": "ended", "reason": "Lab stopped"}
{"type": "ended", "reason": "The filter \"udp prt 5\" is not a valid capture filter: ...",
 "filter_error": true}                    # the connection stays open to try again

Send, to change the filter (the Capture restarts; "restarted" precedes the
new Capture's "capture" message):
{"type": "filter", "filter": "udp port 67 or udp port 68"}

Send, when the pane is closed on purpose (stops an unwatched Capture at once):
{"type": "close"}
```

**Run a ping, traceroute or iperf3 test** _(the Network Tools dialog)_
```
WebSocket: ws://localhost:8050/ws/labs/{key}/network-tool

# Auth uses the existing session cookie from /api/login. One connection runs
# one test; one login may have two running at once. The tool runs in the
# source Node, outside the Lab, so it holds nothing else up while it runs.
# Runs spend no Terminal reconnect budget, but each one counts toward the
# login's MNIPLAB_WS_ATTEMPT_LIMIT connection attempts per minute.
# An iperf3 test starts a server in the "server" Node, which ends with the
# test. A second test on that server in the same Lab is refused while one
# runs. "bandwidth" is a number with K, M or G, above zero and at most 1G,
# such as "10M"; "" or no key means iperf3's default. It applies to TCP and
# UDP.

Send, first:
{"type": "start", "tool": "ping", "source": "h1", "destination": "h2",
 "count": 100, "ipv6": false}
{"type": "start", "tool": "traceroute", "source": "h1", "destination": "10.0.2.2",
 "ipv6": false, "resolve": false}        # resolve: name each hop (drops -n)
{"type": "start", "tool": "iperf3", "server": "h1", "client": "h2",
 "seconds": 10, "protocol": "tcp", "json_output": false, "bandwidth": "10M"}

Send, to stop it as Ctrl+C would (a ping or an iperf3 client still prints its summary):
{"type": "stop"}

Receive, in order:
{"type": "started"}
{"type": "output", "data": "64 bytes from 10.0.2.2: icmp_seq=1 ttl=63 time=0.1 ms\n"}
{"type": "exit", "code": 0, "stopped": false}   # then the connection closes
{"type": "error", "message": "..."}           # instead, when the test cannot run

# With json_output, an iperf3 test sends one output instead of its lines: the
# short summary, at the end.

Closing the connection kills a test that is still running, and the iperf3
server it started.
```

**List a Node's Interfaces for capture**
```bash
curl -b cookies.txt http://localhost:8050/api/labs/{key}/capture/interfaces/r1
# {"interfaces": [{"name": "r1-eth0", "addresses": ["100.64.0.2"], "peer": null, "drawn": false}, ...]}
```

**Download a Capture's pcap**
```bash
curl -b cookies.txt -o r1-eth1.pcapng \
  "http://localhost:8050/api/labs/{key}/capture/pcap?node=r1&interface=r1-eth1&filter=proto%20ospf"
```

**Read the NAT Tables**
```bash
curl -b cookies.txt http://localhost:8050/api/labs/{key}/nat-table
# {"tables": [{"node": "r1", "interface": "r1-eth0", "entries": [
#   {"protocol": "udp", "inside_address": "100.64.1.2", "inside_port": "51330",
#    "outside_address": "100.64.0.2", "outside_port": "51330",
#    "destination": "100.64.0.1", "destination_port": "9999",
#    "port_kind": "port", "state": "UNREPLIED", "lifetime": 28}]}]}
```

## Troubleshooting

### Reset Browser Preferences

Open **Help → Troubleshooting → Reset browser preferences…** and choose
**Reset and reload** to restore Mininet-IPLab's default browser settings.
This clears saved display preferences, including theme, panel sizes and
node grouping, together with this tab's saved panel state. Other
applications' browser storage is preserved. Saved Labs, drafts, Guides,
Authored Layouts and configurations stay on the server, and you remain
signed in.

This resets browser preferences; it does not clear the browser's cached
CSS or JavaScript files.

### Can't Connect to Web UI

**Port already in use**:
```bash
# Check what's using port 8050
lsof -i :8050

# Use a different port
python3 examples/static-lab.py --enable-web --web-port 8080
```

With Docker Compose, also change the `ports:` mapping in `docker-compose.yml`:
only port 8050 is published to the host.

**Firewall blocking connection**:
```bash
# Allow port 8050
sudo ufw allow 8050

# Or access from the host machine using SSH tunneling
ssh -L 8050:localhost:8050 user@remote-host
```

### Login Fails

**Wrong credentials**:
- Check your `.env` file for `MNIPLAB_USERNAME` and `MNIPLAB_PASSWORD`
- Without `MNIPLAB_PASSWORD`: `admin` and the password printed at startup, new each start
- Restart the lab after changing `.env`

**Session expired**:
- Log in again
- The server itself expires a Session unseen for 2.5 hours (`MNIPLAB_SESSION_IDLE_TIMEOUT`),
  which usually happens before the cookie's own 24-hour max age does
- Working in a Terminal, or any other action in the UI, resets that 2.5-hour clock
- Restarting the web server clears in-memory Sessions

**"Signed in on too many devices" (403 on login)**:
- The Session Cap (`MNIPLAB_SESSION_PER_USER_CAP`) is on and the account is
  already at its limit; sign out on another device, or wait for an idle
  Session to expire, and try again
- Off by default — see [Session and Terminal Limits](#session-and-terminal-limits)

### Terminal Closes Unexpectedly

**Idle timeout**:
- Default 300 seconds (5 minutes); increase in `.env`
- Set `MNIPLAB_TERMINAL_IDLE_TIMEOUT=600` for 10 minutes

**Terminal limit reached**:
- Default 5 concurrent Terminals per Session — increase
  `MNIPLAB_TERMINAL_PER_SESSION_CAP=10` in `.env`
- Or the host-wide ceiling was reached (`MNIPLAB_TERMINAL_GLOBAL_CAP`, default
  100) — this one is independent of any single Session

### Topology Not Showing

**Refresh browser**: Press F5 or Cmd+R

**Check API response**:
```bash
curl -u admin:<password> -c /tmp/mniplab.cookies \
  -X POST http://localhost:8050/api/login

KEY=$(curl -s -b /tmp/mniplab.cookies http://localhost:8050/api/status | python -c 'import json,sys; print(json.load(sys.stdin)["key"])')
curl -s -b /tmp/mniplab.cookies http://localhost:8050/api/labs/$KEY/topology \
  | python -m json.tool
```

**Check browser console**: Open developer tools (F12) and look for JavaScript errors

### Web Server Logs

**Log location**: `/tmp/mniplab-web.log`

**What's logged**:
- Web server startup/shutdown events
- HTTP authentication (login/logout)
- WebSocket connections (Terminal and topology)
- Terminal lifecycle (create, close, errors)
- API request errors
- Security events (rate limiting, authentication failures)

**What's NOT logged** (these appear on stdout in CLI Mode):
- Lab network operations (net.start, net.stop)
- Mininet events (link creation, node configuration)
- FRR daemon lifecycle (zebra, bgpd, ospfd startup)
- Router configuration changes
- ExaBGP route announcements

**Viewing logs**:
```bash
# Real-time monitoring
tail -f /tmp/mniplab-web.log

# Search for errors
grep ERROR /tmp/mniplab-web.log

# View recent activity
tail -n 50 /tmp/mniplab-web.log
```

**Log rotation**: Logs are automatically rotated at 10MB (keeps 5 backups)

> **Tip**: When troubleshooting web UI issues, check this log file first. For lab topology or routing issues, check the stdout output where you launched the lab script.

### Routes Not Visible

Use the terminal to check routing tables:

```bash
# Connect to a router terminal (click in web UI)
r1> vtysh
r1# show ip route
```

If no routes appear after 30+ seconds, FRR may still be converging. Wait a bit longer or check FRR daemon status:

```bash
show running-config
show processes
```

## Performance Tips

1. **Reduce concurrent terminals**: Close unused terminal tabs; each terminal consumes memory
2. **Adjust idle timeout**: Set `MNIPLAB_TERMINAL_IDLE_TIMEOUT=60` to auto-close unused terminals
3. **Restart periodically**: Long-running labs can accumulate stale processes; restart if performance degrades
4. **Monitor resources**: Watch CPU/memory on host; reduce lab size if needed

## Advanced Usage

### Scripting the Web API

Use curl or Python to automate web UI interactions. Starting a Lab with
`POST /api/labs` is for `python -m mniplab serve`: Single-Lab Mode refuses it
while a Lab already runs, and under `--enable-web` the launched Lab is that
Lab.

```bash
# Login
curl -s -u admin:<password> -c /tmp/mniplab.cookies \
  -X POST http://localhost:8050/api/login | jq .

# Start a Lab Example and keep its key
KEY=$(curl -s -b /tmp/mniplab.cookies -H 'Content-Type: application/json' \
  -d '{"example_id": "ospf-lab"}' http://localhost:8050/api/labs | jq -r .key)

# Get topology
curl -s -b /tmp/mniplab.cookies http://localhost:8050/api/labs/$KEY/topology | jq .

# List Terminals (admin endpoint)
curl -s -b /tmp/mniplab.cookies http://localhost:8050/api/admin/terminals | jq .
```

### Running Web UI in Docker

```bash
docker compose up -d
docker compose exec mniplab python3 examples/static-lab.py --enable-web

# Or the classroom server
docker compose exec mniplab python -m mniplab serve
```

Access at `http://<docker-host>:8050`. Compose publishes only port 8050; to
serve on another port, change both `--web-port` (or `MNIPLAB_PORT`) and the
`ports:` mapping in `docker-compose.yml`.

### Running Behind a Reverse Proxy

For nginx:

```nginx
server {
    listen 80;
    server_name lab.example.com;

    location / {
        proxy_pass http://localhost:8050;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Accessibility

The Web UI is built with accessibility in mind (targeting WCAG 2.2 AA), but manual testing is still recommended. Use tools like [Accessibility Insights](https://accessibilityinsights.io/) or your browser's built-in developer tools to check for issues, particularly when using the topology view with keyboard navigation or a screen reader.

## See Also

- [Quick Start Guide](QUICKSTART.md) - Getting started with CLI and web UI
- [Creating Labs](CREATE_LAB.md) - Building custom topologies
- [Instructor Lab Authoring](lab-authoring.md) - Drawing, previewing and publishing Labs in the browser
- [DHCP Service](DHCP.md) and [DNS Service](DNS.md) - What the Service panels operate on
- [Examples](../examples/) - Sample lab topologies
