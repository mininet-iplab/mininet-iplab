# Instructor Lab Authoring

Sign in as an Instructor and choose **Lab Authoring** in the header. Use it
to create a Lab Example beyond the shipped ones, adapt a shipped Lab Example,
or revise one you published, without writing the Python by hand.

Lab Authoring is used on the classroom server, `python -m mniplab serve`, in
Single-Lab or Multi-Lab Mode. Under `python3 my-lab.py --enable-web` the
launched Lab is already running, so Preview and Publish are refused while it
runs. A published Lab Example joins the Lab Catalog: in Single-Lab Mode the
Instructor starts it for everyone, and in Multi-Lab Mode Groups start it from
their Assignment. Learners cannot open drafts or an Instructor's Preview Lab.

## Create, adapt and reopen a draft

The **Labs** explorer, left of the canvas, holds the shared Instructor draft
library above the **Lab Catalog**. It folds to a narrow rail when a draft
opens and opens again when none is; its toggle folds or opens it either way.
Each section scrolls its own list, and its **(i)** button shows its notes.

**Drafts** offers **New draft** and **Upload a script…**, then one row per
Lab draft (the page's name for a Lab Example Draft). Choose a draft's name to
open it; the open draft is highlighted. Its **⋯** button offers **Rename**,
**Copy as new** and **Delete**. Delete asks for confirmation and does not
delete a published Lab Example. Deleting the last draft leads to New draft.

A draft's name is its Catalog name. Rename it from its row's **⋯** menu, by
choosing the open draft's name over the canvas, or in the Details sidebar's
Lesson tab. Both in-place renames take Enter or leaving the field to rename
and Escape to keep the name.

**Close draft**, in the toolbar over the canvas, saves pending edits, as
opening another draft does, and leaves no draft open; if an edit cannot be
saved yet, it asks before discarding it. This browser remembers that, so a
reload stays closed instead of reopening the most recent draft, until a draft
is opened again.

In the **Lab Catalog**, search by name or protocol. Each row shows a lab's
protocols, Node count and source label; choose it to show its description
and actions. **Copy** on a shipped Lab Example creates an independent Python
draft from its exact shipped script and declared file inventory, including its
FRR Seeds, Guide, Layout and assets. **Start from Canvas version** is a separate
action that opens the prepared Canvas definition; it can differ from that
shipped Python source. Copy on an authored entry preserves its authoritative
Canvas or Python source and complete files. A Copy publishes as a new Catalog
entry. **Revise**, available for authored entries, opens their latest version
and keeps its Catalog ID for **Update existing**. Authored Python entries can
be copied and revised just like Canvas entries. The **Python** row label marks
Python source mode; **Authored** marks authored Catalog entries.

A Lab Example published from Lab Authoring also offers **Remove from Catalog**,
which undoes Publish; shipped Lab Examples never offer it. One confirmation names
the Groups whose Config Copies of it are discarded.
Removal is refused, naming the Group, while any Lab of it runs; the refusal is
shown in the confirmation. Every published version and each Group's Config Copy
go; drafts that revise it stay. Remove from Catalog removes the authored
Catalog entry and all its versions, rather than removing one version while
retaining that entry.

**Upload a script…** takes one UTF-8 `.py` file of at most 500,000 bytes and
creates a new Python draft named after the file. It does not infer or collect
imported helpers, data or FRR Seed files. Add those in **Supporting lesson
files** or import a complete package. In a Python draft, the Script editor lets
you edit `lab.py` and existing Router Seeds directly; changes autosave with the
draft. **Replace script…** uploads a `.py` file into that same draft, keeping
its identity and other files. Save pending edits first. A changed script needs
a fresh Preview before publishing. Invalid or stale replacements do not
partially change the draft.

To continue a Canvas draft as Python, open **Script** and choose **Continue in
Python**. This saves the current Canvas draft and creates a separate Python
draft from the saved lesson and its files. The Canvas original remains in
Drafts; the two drafts do not synchronize, and this action does not publish or
alter a running Lab. It is a source-mode change, so use it when the Canvas
controls no longer fit the exercise.

**Download .zip** is the complete native lesson package as well as a runnable
folder. It preserves Canvas or Python source mode, complete files and the
editable definition. Use **Import package…** in Drafts to create a new
unpublished draft. If a Canvas package's generated script, Seeds or Layout was
changed outside Canvas, the import asks whether to **Import as Python** so
those exact files are retained; cancel keeps the current draft open. Imported
drafts have fresh server-owned identity and no Preview or publication
authority. Assignments and Group Config Copies are separate classroom records
and are never part of the package.

A draft saves itself about a second after the last edit and when a dialog
closes. There is no Save button. Before leaving or closing the page, check
**Draft: Saved** in the status line. Open it later from Drafts to continue.
Drafts are stored under the Git-ignored `authored-labs/` directory
(`MNIPLAB_AUTHORED_LABS_DIR` moves it).

If validation refuses an edit, **Can't save yet: reason** keeps the edit in
memory and offers a route to the offending field. Leaving an edit that cannot
be saved asks before discarding it. A shared-draft conflict says **Someone
else changed this draft · Reopen**. Reopen replaces your local edits with the
latest saved revision. Nothing is merged; coordinate edits with other
Instructors before reopening.

## The workspace

The toolbar over the canvas names the open draft and holds **Script**,
**Preview**, **Publish…** and **Close draft**; with no draft open, the canvas
says so. The visual workspace has a Node palette, Topology canvas and the
**Details** sidebar, and a **Script** pane beside the canvas when it is open.
Details starts folded to its rail on each page load; its toggle opens it, and
Enter or a double-click on a Node still opens its dialog while it is folded.
Drag its visible divider, use its arrow keys, or choose **Wider**/**Narrower**
to change its width. The width survives collapse and switching draft kinds.
Below 900 pixels the Labs explorer stacks above the canvas and the sidebar
under it, and below 600 pixels the palette becomes horizontal.

**Script**, in the canvas toolbar, opens the Script pane next to the
canvas and folds the sidebar to its rail; pressing it again closes the pane
and brings the sidebar back (a sidebar collapsed by hand stays collapsed).
The canvas fits its view again each time, and whenever its size changes:
resizing or folding the sidebar, or moving the pane's divider. The divider on the pane's left edge
(**Script width**) is dragged, or moved with the arrow keys, Home and End;
neither the canvas nor the pane gets narrower than 320 pixels. Below 1024
pixels the pane stacks under the canvas.

Selecting or focusing a Node or Link shows **Selection** above the sidebar
tabs. It lists Interfaces and addresses, enabled Services and relationships,
and offers **Edit…**, **Connect…** for a Node, and **Remove**. Selecting an
item preserves the current sidebar tab. Relationships appear as text and
selected dashed lines: Relay to DHCP Service, Secondary to Primary, Speaker
to peer Router, and DS parent to child.

The sidebar tabs are:

- **Lesson**: Lab address family and saved Router config settings, Catalog
  name, description, Area, and Protocols or Services labels. Choose an Area
  from the fixed list before publishing; older authored Lab Examples without
  one appear under Other in the Labs list until edited.
- **Guide**: Markdown. **Expand** opens a larger editor with Edit and rendered
  Preview tabs; edits still autosave into the draft. Use meaningful headings,
  descriptive link text and alternative text for Guide images.

The bottom status line shows Draft Saved/Saving…/Can't save yet and Preview
not running/starting…/running/out of date/failed. A running Preview offers
**Open Lab** and **Keep changes…**. Reloading the page restores its links.
Out of date means a Preview input changed, such as topology, source, Seed or a
Build file; the running Lab remains available, but publication needs a new
Preview. Outcomes are announced politely; blocking startup failures and
revision conflicts are alerts.

## Place, connect and edit Nodes

The palette offers Host, Container Host, Switch, Router, Internet Gateway,
Transit Internet Gateway and BGP Speaker. Drag an item onto the canvas or activate its
button to place a Node at a free spot. Its name appears on hover and keyboard
focus; Escape dismisses that tooltip. The grid is visual: positions do not
snap. Like the running Lab's Topology, the canvas has no fixed boundary:
Nodes can move to negative coordinates or beyond the visible area. Positions
use virtual units independent of screen pixels. Both views fit around all
Nodes, including on narrow screens; pan or zoom in for a closer view, and
use **Fit** to show everything again.

The palette and canvas each have one Tab stop. Arrows, Home and End navigate
palette items. On the canvas:

- Arrows focus the nearest Node in that direction. Home, End, Page Up and
  Page Down visit Nodes in reading order.
- Shift+Arrow moves a Node 10 units; Ctrl+Shift+Arrow moves it 50.
- Enter edits; Delete or Backspace removes. Removing a Node removes its Links.
- Escape immediately after placement undoes that placement.
- Selection's Interface buttons and the Node dialog's Links tab reach Links.

Drag a Node to move it, or edit x/y in **General → Position** for a pointer
alternative that needs no drag. To connect, drag a Node's **+** onto another
Node, or choose **Connect…** in Selection. Connect mode announces the source
and focuses another Node; Enter or a click selects the target. Escape or
Cancel returns to Connect…. Both routes create the next free Interface at
each end, select and focus the Link, and leave its dialog closed. Choose
Edit… or press Enter to set addresses. Interface identities stay attached
when a Node is renamed; FRR text using an old name is not rewritten.

## Node and Link dialogs

Choose **Address family** in the sidebar's **Lesson** tab: IPv4 (the default),
IPv6, or Dual-stack. Address fields and pickers use that family; Dual-stack
allows either family to be empty on an Interface. Changing family is refused
while conflicting addresses or DHCP clients remain, with a list of their
fields. Nothing removes them or fills in an address automatically, including
Connect, new Speaker Neighbors, and new Internet Gateways.

Resolver Access Control subnets, DNS Record data and native FRR text remain
as written. These are policy or lab content and never block family changes.

A Node dialog shows only the tabs appropriate to its kind:

| Tab | What it edits |
| --- | --- |
| General | Name, image for a Container Host, Host gateways/nameservers, Position |
| Routing | Router Protocols, fast timers, FRR text, VLAN subinterfaces, VRFs, Router NAT |
| Speaker | Local AS, Neighbors, Global and Per-Neighbor Routes |
| NAT | Gateway address, translated subnet, downstream Routers for Transit NAT |
| Services | Host/Router checklist for DHCP, Authoritative DNS, Resolver and Relay |
| Startup | Before-daemons and after-start commands |
| Interfaces | Each Interface's peer and addresses, a Router Interface's VRF and Outside (NAT) mark, with Edit Link… for each |

Tabs with invalid fields carry an error mark. Error-summary links switch to
the right tab, expand the relevant row or More section, and focus the field.
A dialog with errors focuses its summary; otherwise it focuses its first tab.
Escape closes it and returns to the item or Edit… control that opened it.
Long explanations sit behind labelled **(i)** buttons. Activate these to
show or hide the explanation; one-line hints remain visible.

The Link dialog has one column per end, stacked on narrow screens. It names
the Node and offers **Change…** to re-point that end. Choose a free Interface
by its runtime name, such as `r1-eth0`; then enter IPv4/IPv6 addresses with
prefixes. Host ends also offer MAC address and DHCPv4/DHCPv6 client options.
Switch ends offer access VLAN or trunk VLANs and an optional native VLAN.
A Switch has no addresses or Startup commands.

**Initial Link Condition** is collapsed unless set. It edits up/down,
delay, jitter, loss, bandwidth in Mbps and scheduler. Jitter needs delay.
Parallel Links between the same Node pair cannot have initial conditions
or kept runtime conditions: the runtime store identifies a Node pair.
Interface labels on the canvas appear only at readable zoom levels, or on
the selected item's Links.

A Container Host also needs a Docker image available to the Lab's daemon.
Internet Gateway and Transit Internet Gateway are infrastructure: visible in
the draft and on the Learner's running Topology, where each is drawn as the
Internet cloud, but hidden from the CLI. Attached translating Routers stay
visible. A Transit Internet Gateway needs downstream Routers before it saves.

A Router translates only what its Lab declares. On a Router's Interfaces tab,
tick **Outside (NAT)** for each Interface that faces the upstream network. The
Routing tab's **Router NAT** list then holds that Router's port forwards and
static 1:1 mappings: pick the kind, its addresses and ports, and, when the
Router has more than one outside Interface, which one the entry applies to.
**Hairpin** lets inside clients reach a forward through the outside address
too; it stays off unless ticked. A Python Lab declares the same things with
`'nat': 'outside'` on a Link end and `nat=[...]` on `addRouter`; see the
[API reference](api-reference.md#classes-portforward-staticnat).

## Speaker Routes

A new Speaker starts with **Per-Neighbor Routes** generators. Each generator
holds a Route count, sub-prefixes per prefix, a prefix seed and an AS-path seed.
Seeds are random editable numbers chosen once when the generator is created;
every start then announces the same prefixes and paths. Counts include base
prefixes, with up to the requested number of sub-prefixes for each.

Choose **Global Routes** to share generator settings across Neighbors. A
Neighbor's own generator overrides the Global generator. Each generated call
uses that Neighbor's family, local AS and local address as next hop. The script
calls `make_v4_peer_routes` or `make_v6_peer_routes`, as shipped Lab Examples do.

Hand-written Routes remain under **More** and announce alongside generated
ones. A Neighbor's hand-written rows replace the hand-written Global rows;
switching generator scope preserves those rows. Leave a generator disabled
when only crafted announcements are wanted. Addresses always remain authored.

## Routing and Startup commands

Router **Protocols** enable FRR Daemons; zebra and staticd always run, so
static routing needs no selected Protocol. Enter native FRR configuration as
in `frr.conf`; the hostname line is added for you.

For a fresh FRR exercise, use **Copy** on a shipped recipe to retain its
exact script, topology, addressing, declared files and required Daemons. Clear
the lesson's routing commands from each Router's FRR configuration or Python
Seed `frr.conf`, while preserving any minimum FRR settings the exercise needs.
Check Python code and Before-daemons/After-start **Startup** commands for
anything that writes those routes back when the Lab starts. Keep the minimum
addresses and Router Protocol selections intact. An empty Seed starts without
authored routing text; zebra and staticd still run, while selected Protocols
such as OSPF or BGP remain available for Learners to configure. Preview that
baseline, then **Publish as new** to create a new Catalog ID with no inherited
Group Config Copies. A later Seed-only revision changes the baseline for
future Group starts; existing Group copies keep their work until the Instructor
explicitly uses **Reset Router Config** (or a Group chooses **Start clean**).
Reset is refused while that Group's Lab is running; a class reset is
all-or-nothing.

In the sidebar's **Lesson** tab, **A Learner's saved Router config carries over
between sessions** applies to every Router. Off by default, each start uses
the authored FRR text. When on, every Router gets a Seed: a Group's first start
copies it into its own Config Copy, and subsequent starts resume that copy.
A later Seed edit reaches an existing copy only after an explicit Config Reset.
A Python draft's script decides persistence for each of its Routers.

**Fast OSPF timers** are on for new Routers. With OSPF/OSPF6 selected,
Interfaces get hello 1 s/dead 3 s; explicit Interface timers in FRR text take
precedence. These generated lines are not shown in the configuration editor.
Turn the option off for FRR's default 10 s/40 s timers. Preview verifies
startup, not protocol convergence.

After linking a Router, Routing can add VLAN subinterfaces with a parent
Interface, VLAN ID and optional IPv4/IPv6 prefixes. The parent's stable
identity survives a rename. Remove or reassign subinterfaces before removing
their parent Link.

Routing also holds the Router's **VRFs**, one row each with a name and a
routing table ID. Once a Router has a VRF, each of its Interfaces on the
**Interfaces** tab offers a **VRF** choice: Default table or one of the
Router's VRFs. The generated script declares them as `vrfs=` on `addRouter`
and `'vrf'` in that Link end's `params`. A VRF still holding Interfaces cannot
be removed until they move; renaming it carries them along. Routing inside a
VRF, such as `router bgp … vrf …` and route leaking, is written in the FRR
text, as is a loopback address (`interface lo`).

Startup rows run shell commands at **Before daemons** (Interfaces up, before
FRR and Services) or **After start** (IPv6 assignments, Relays and Hosts ready).
Nodes run concurrently within a stage; each Node's commands run in order.
Use the later stage for cross-Node dependencies. A command defaults to 5 s,
may be set to 1–60 s, and a stage may take at most 120 s. Failure stops the
Node's remaining commands and fails startup with the Node, stage, command
and output. **May fail** instead reports a warning in Preview.

## Structured Service and Speaker editors

Enable a Service in the Services checklist to expand its editor. Lists use
named fields and Add/Remove controls, not raw JSON. Collapse a row to its
summary; uncommon settings sit behind **More**. Required fields are marked,
and validation keeps incomplete edits in memory until corrected.

### DHCP and Relay

Add a **Pool** for each served Subnet: CIDR, range start/end, lease time,
options and nested **Reservations**. The CIDR determines the family. Router
options apply only to IPv4; preferred lifetime only to IPv6. A DHCPv6 client
gets its default route from a Router Advertisement. A Pool's CIDR must match
a serving Interface at startup. An unserved Subnet or two competing Services
can be intentional faults for Learners to find.

An IPv4 Reservation identifies a MAC; an IPv6 Reservation identifies exactly
one of MAC or DUID. Pin a client's MAC in its Link end for a repeatable
Reservation. A directly acquired DUID may change across starts. Mark a Host
Interface as a DHCPv4/v6 client to leave that family unassigned until the
Learner runs the client. A Host's gateway field leaves by its lowest-numbered
Interface, so it is refused when that Interface is a client of the gateway's
family.

Relay rows name relayed Subnets and a DHCP server address for each family.
The address picker offers Node · Interface · address; free typing permits
unreachable-server labs. The serving Node needs matching Pools and the
Routers need return routes. Serving and relaying the same Subnet conflicts.
The picker stores an address, so later address changes do not retarget it.

DHCPv6 also needs Router Advertisements configured explicitly in FRR, for
example:

```text
interface r1-eth0
 ipv6 nd prefix 2001:db8:1::/64 no-autoconfig
 ipv6 nd managed-config-flag
 no ipv6 nd suppress-ra
```

Set `no-autoconfig` before enabling advertisements to avoid a SLAAC address.
The managed flag requests an address; the other-config flag alone requests
options. Neither flag, or suppressed advertisements, leaves the client idle.

### Primary Zones, Secondaries and Resolver

Add a **Primary Zone** with an apex ending in `.`, default TTL, DNSSEC setting
and nested **Records**. Records have owner, type and data; individual TTL is
behind More. Types are A, AAAA, CNAME, PTR, TXT, MX, NS and SOA. For
`example.com.`, a Record named `web`, type A, data `10.0.0.2` describes
`web.example.com.`. Omitted SOA/apex NS use Service defaults.

Zone More holds NSEC3/salt, signed-child **DS delegations**, and transfer
permissions: **Default** derives declared Secondaries, **None** refuses all,
and **Listed** names Nodes or addresses. Choose a signed child by Node and
Zone; both parent and child must be signed. Generated keys, signatures and
DS values stay runtime data. A custom SOA Record can set retry/expire timers.

On another Node, choose **Primary Zone to serve as Secondary** and add it.
The Secondary holds the Primary Node reference and gets Records by transfer;
it has no editable Record list. Host Nameservers can likewise refer to Nodes
by stable identity or explicit IP addresses; Node references use static
Interface addresses and survive renames. They become the Host's private
`/etc/resolv.conf` when the Lab starts.

A Resolver offers Root Hint Nodes/addresses, Forwarders, TTL caps, DNSSEC trust
anchors and **Access Control** rows with Subnet and allow/refuse/deny action.
Forwarders take precedence. An empty Access Control list denies clients;
Unbound uses the most specific matching Subnet independently of row order.
Choose the signed root Node as a trust anchor for an in-lab signed hierarchy.
Knot derives child DS and Unbound's anchor from runtime keys. DNS policy,
Zone roles, delegation and Resolver topology remain declared in the draft.

### BGP Speaker

Set Local AS, **Neighbors** and **Global Routes**, with optional nested
**Per-Neighbor Routes**. Neighbors need unique local addresses, peer addresses
and AS numbers. The peer address picker fills an Interface address; it stores
an IP, not a continuing Node reference. A nonempty Per-Neighbor list replaces
Global Routes for that Neighbor. Route prefix/next hop stay visible; AS path,
communities, large communities and MED are behind More.

The Speaker listens passively. Configure its FRR peer to open the session;
`no bgp ebgp-requires-policy` is required when the lab accepts routes
without explicit policy. A successful Preview need not imply an established
BGP session.

### RPKI CA and Validator

Tick **RPKI CA** to add **Hosted CAs**, one row per Autonomous System: its
**ASN**, the **Prefixes** it holds (one per line), and optional **Starting
ROAs** (`10.1.0.0/24-24 => 65002`) and **Starting ASPAs**
(`AS65002 => AS65001, AS65005`), one per line. A starting ROA must fall
inside the row's prefixes, a starting ASPA must name the row's own ASN as
customer, and an ASN may appear only once; the row says which line is wrong.
**Hostname** defaults to the Node's name. A Lab holds at most one RPKI CA.

Tick **Validator** on another Node for Routinator; **Refresh (seconds)**
defaults to 10. A Validator needs an RPKI CA in the Lab. Routers still need
their `rpki` lines in their FRR text; see [RPKI.md](RPKI.md).

## Preview and selected Keep

**Preview** saves pending edits, starts an isolated Lab from the saved recipe,
and becomes **Stop Preview**. Repeated activation while saving/startup is
pending starts one Lab. Edits made during actual startup save after it finishes.
Use **Open Lab** for the running page, its Terminals, Tools and Service panels;
Terminals are not embedded in authoring.

Preview's persistent Routers use isolated copies of the draft Seeds, never a
Group's Config Copy or a published Seed. It counts toward the normal Lab cap.
In Multi-Lab Mode, replacing the Instructor's current Group Lab asks first;
other Groups keep running. In Single-Lab Mode another shared Lab must stop
before Preview can start. Learners cannot open the Preview.

**Keep changes…** compares supported configuration with the Preview's
snapshot. No checkbox is selected initially: nothing is kept unless you
tick it. One row represents each Node or Service, with **Select all** for the
review. Router rows name their changed FRR sections and added/removed line
counts. **Show changes** folds the diffs and Service Field/Before/After tables,
whose Added/Changed/Removed labels explain the change. Choose within a Router
by whole FRR section; ambiguous configurations can only be kept whole, then
edited in the Router's FRR text. Tab visits each row once; arrow keys reach
its disclosure and section choices. **Keep selected** writes only
what you selected, then marks the draft out of date. Preview it again before
publication. A setting changed after review is refused; review again.
When the review is empty, it offers **Close** and hides the selection
instruction. Before any Keep in that Preview it says “No supported changes
to Keep yet.” After Keep it says “Your changes are kept in the draft. No new
supported changes to Keep.” This state survives a page reload; a fresh
Preview starts a new review. Further edits in vtysh appear as new choices.

Use the (i) beside **Keep changes** for keyboard help and what Keep leaves out.
Selection shows **n changes in the Preview · Keep…** for a changed Node;
that review includes its FRR text and Services, already ticked. When the
sidebar is folded, the status line shows **n changes not yet kept · Keep…**.
These counts refresh after startup, on Selection and when returning from
the running Lab. Addresses in FRR text are shown in Selection and
**Addresses…** marked **(from r1's FRR config)**. Keep leaves them in that
text and never copies them onto a Link end.

Supported Keep includes:

- Router running FRR configuration, including unsaved vtysh edits. Keeping
  a section uses FRR's formatting for that section.
- Switch port VLAN settings and unambiguous Link Conditions.
- DHCP Pool ranges, lease times, options and Reservations, matched by Subnet.
- Each Primary Zone's editable Records and each Resolver's Access Control.
- Each Hosted CA's ROAs and ASPAs, as starting objects, each line Added or
  Removed.

Keep excludes learned routes, DHCP Leases/Relay activity, Resolver caches,
Secondary replicas, generated SOA/apex NS defaults, DNSSEC keys/signatures/DS
values, edits to daemon files, untracked shell traffic-control changes and
subinterfaces inferred from runtime Interfaces. Root Hints, Forwarders,
DNSSEC policy/delegations and Zone roles stay authored. Speaker announcements
stay a Learner's runtime lever: edit the Speaker's generator or hand-written
Routes to change the authored announcements.

Stop Preview offers Keep when changes would be lost, with **Keep
selected**, **Discard and continue**, or **Cancel**. Stop then Preview to
rebuild fresh from the draft. Changes not yet kept do not carry over. Stopping
from the main Labs page does not show this authoring warning.

## Publish, assign and revise

**Publish…** stays enabled and lists unmet requirements with fixes: open
the **Lesson** tab for a Catalog name and Area, add a Node or correct a script
error. With no successful Preview of the current saved source and build inputs, **Publish**
starts its own check Lab and publishes once startup succeeds. A failed start
leaves nothing published and shows its report. Lab caps and Single-Lab Mode limits apply
as they do to Preview; the report explains what to stop. Startup success is
the gate; routing/connectivity are the Instructor's checks, so buildable
faulty labs remain publishable.

With changes not yet kept in a running Preview, Publish shows the same
Router and Service rows as Keep and Stop. Tick the changes to Keep before
publication; unticked changes are left out of the published Lab Example. Keeping
invalidates startup evidence, so Publish checks the updated draft afresh.
That check replaces the Instructor's own Preview. Replacing another Group
Lab asks in the page's dialog, and never stops another Group's Lab.

Publishing creates a new immutable Catalog version. A draft of an authored
example can **Publish as new** or **Update existing** under its Catalog ID.
A stale update refuses if another Instructor has already published a newer
version. Saving alone changes neither the Catalog nor running Labs.

In Single-Lab Mode, return to the main page and start the published Lab
Example from the Lab Catalog in the **Labs** sidebar; every login shares it.
In Multi-Lab Mode, return to Labs and use a Group's **Assign…** dialog in the
Class Overview to select published Lab Examples; the Group starts them from
that Assignment. Revise from the Lab Catalog: choose the Lab Example's row,
then Revise. Existing Labs
keep their selected builder, Guide, Layout and configuration while new starts
receive the new published version. A failed publication leaves the previous
Catalog selection in place.

For Canvas drafts, network edits require new Preview evidence: Nodes,
Interfaces, addresses, Services, routing, Startup commands, conditions and the
Code section. For Python drafts, script and Seed edits require a new Preview.
Build-file changes in either source mode also require a new Preview. Guide,
**Lesson** metadata, Layout and Presentation-only files do not change Preview
inputs. A failed or stale Preview is not publication evidence.

Persistent Config Copies remain the Group's work. Compatible revisions resume
that copy; changed Seed text reaches it only through **Reset Router Config**
or **Start clean**. Structural changes to Router identities/names, Daemons,
configuration modes, attached Interface identities/ports/peers, or VLAN
subinterfaces make a copy incompatible. Publication reports affected Groups
and preserves their files. The start screen offers Start clean; the Instructor
can reset one Group or all copies for that example. Reset refuses running
Groups, and a class reset is all-or-nothing. Old copies without recorded
structure need an explicit reset. Compatibility does not validate arbitrary
FRR text against a Group's saved configuration.

## The Script pane and the Code section

The Script pane shows a Canvas draft's saved script as one editor. Once the
Code section holds code, its line numbers are the downloaded script's; while
it is empty, its heading and blank band add three lines the download does not
have. The script is generated from the canvas, so
only its **Code section** takes typing: the band where `build_network()`
ends, tinted and marked at the left, with a label naming its lines ("Code
section · lines 30–31 are yours"). Typing anywhere else is refused, and the
status line says which lines are the band. The pane opens scrolled to the
band with the cursor at its end. Ctrl+A then Ctrl+C copies the whole script.
**Download .zip** exports a runnable folder with the script, Router Seeds
under `frr-config/`, Guide/Layout under `examples/`, and README. Add at least
one Node to generate Python.

The Code section is Python for what the canvas cannot draw: a loop of
Routers, extra FRR lines, a helper. It runs before the network is returned,
with `net` and each Node by its Python name (`r1`, `h1`, …). It runs before
the Lab starts, so commands for the running Lab stay in a Node's After-start
Startup commands. The band shows it as it runs, indented four spaces into
`build_network()`; Enter keeps the indentation, and pasted lines are indented
like the line they land on, so code copied from column 0 keeps its blocks.
Saving takes off the indent every line shares, at most four spaces, so a
block keeps its shape; the script indents every line by four spaces, lines inside a multi-line string included, as it does for the FRR
text it generates. Tab leaves the editor; Ctrl+] / Ctrl+[ indent and outdent;
Ctrl+Space offers supported `mnIPLab` completions and F1 or **Show API help**
describes the method at the cursor, in the Code section only. **Keys** opens
these hints. The section autosaves with the draft and is kept exactly as
written when a canvas edit regenerates the script, so a rename on the canvas
never rewrites it: a Code section naming a renamed or removed Node fails the
Preview at the Code section's own line. That failure opens the pane with the
cursor on the line, marked ⚠ until the Code section is edited; the same
failure found while drawing marks the line without opening the pane. While
it holds code, the canvas shows **+ Code section**.

After each save, the script runs once against a stand-in network that
records Nodes and Links and builds nothing. The Nodes and Links the Code
section adds are drawn greyed, dashed and tagged "from Code section". They
can be moved, by pointer or Shift+Arrow, and their positions are part of the
Authored Layout, but not edited: Enter, or **Show in Code section** in the
Selection, opens the pane with the cursor on the line that makes them, and
Delete or F2 says to change them there. While the pane is open, selecting one
moves the script's cursor to its line and leaves focus on the canvas.

A Python draft uses its script and Seed files as source, rather than a
canvas topology definition. It uses the same Lesson/Guide, autosave, Preview
and Publish workflow, and opens with the Script pane shown. Edit `lab.py` and
Seed tabs in place; they autosave with the draft. A tab strip above the script
names `lab.py` and each Seed; the arrow keys, Home and End move between them.
A Seed is a Router's `frr.conf` under `frr-config/`, at the path its script
names: each Router renders `daemons`, `vtysh.conf` and
`support_bundle_commands.conf` from its Protocols when it starts, so they
never appear as tabs or in a download. An upload carries no Seeds, so a
Router whose script names a Seed folder the draft does not hold starts with
an empty configuration. The palette is absent. Start a Preview, configure
its Router in vtysh, then **Keep changes…** writes the selected FRR sections
into that Seed's `frr.conf`. A fresh Preview starts from the kept Seed.
Inline FRR text and Routers with no Seed folder cannot be kept; the review
explains how to fix their script. Services, VLANs and Link Conditions are
listed as **change in your script**. Kept Seeds invalidate Preview evidence.

### Supporting files and complete packages

The **Supporting lesson files** section manages supporting files alongside
either Canvas or Python source. Create a text file by path, edit its UTF-8
content, rename or delete it, and upload, replace, download or delete binary
files. Paths are canonical relative POSIX paths of at most 240 ASCII bytes.
Each component starts with an ASCII letter, digit or underscore, uses only
ASCII letters, digits, underscores, dots, spaces, parentheses, plus or hyphen,
and has no trailing dot or space. Case-insensitive aliases, file/directory
collisions and reserved script, Guide, Layout, Seed or FRR-generated paths are
rejected. Files default to **Build**, which means their bytes participate in
Preview and Config Copy compatibility. Only documentation or media under `presentation/` can be marked
**Presentation**: Markdown, text, PNG, JPEG, GIF, WebP and PDF. Presentation
files are for learners and do not affect build compatibility. Keep any file
read by Python while constructing the Lab marked Build, even if it is media.

The limits are 200 supporting files, 2,000,000 bytes per file and 20,000,000
bytes total. The uploaded/replaced Python script is limited to 500,000 bytes;
Python Seed files support up to 150,000 characters each. Native ZIP packages
are limited to 32,000,000 compressed bytes, 64,000,000 expanded bytes,
4,000,000 bytes per entry and 512 entries. Import accepts the versioned
Mininet-IPLab package format only; it rejects unsafe or ambiguous paths,
collisions, encrypted or special ZIP entries, ZIP64, multi-disk archives and
trailing data. It does not install Python dependencies or discover external
imports. Install runtime dependencies separately; the exported Lab still
requires Mininet-IPLab, FRR and any used external services or tools.

The canvas draws the script's Nodes and Links read-only
from the same record-only run, so loops appear as the Nodes
they make. Supported Node kinds keep their icons; any other class is a plain
grey Node. Move Nodes to arrange the Authored Layout; **Show in script** in
the Selection, or Enter, opens the pane at the line that makes one, and while
the pane is open, selecting one moves the cursor there. A script that
cannot be drawn shows why in a note over the canvas, and is drawn from your
running Preview of the draft when there is one. Preview runs from that folder:
`frr_dir='./frr-config/my-lab/%(name)s'` reads the corresponding Router folder.

The script editor has syntax highlighting, line numbers, bracket matching
and search: Ctrl+F (Cmd+F on macOS) opens search, and Tab leaves the editor.
Typing edits the source and autosaves it with the draft. Use **Replace
script…** above the editor to upload an external version into this same draft;
other files and its identity stay in place. **Show API help** is the reliable
help route: place the cursor on a supported method to
see its checked signature. F1 is a convenience and may be taken by the
browser. **Keys** in the pane lists these, worded for the file shown.

A script that does not compile can still be saved and shows its line/column
error, but cannot be previewed or published until fixed in the editor or with
**Replace script…**. Build/start failures name the script line. Guide/Layout and
Presentation-only file edits preserve Preview evidence. Script and Seed edits,
plus Build-file changes in either source mode, require a fresh Preview.
Published versions are immutable snapshots, with the Node count from a successful Preview. Revising starts from
the latest published source and retains the Catalog ID; a Copy creates a new
Catalog identity. Seed-text-only changes preserve Group Config Copy
compatibility. Script, helper or other Build-input changes can make existing
copies incompatible; publication reports affected Groups, and the Instructor
must explicitly reset them when needed.

## Run an exported Lab and current scope

Unpack the download, enter its folder and run as root with `mniplab`
importable, for example:

```bash
cd routed-lab
PYTHONPATH=/path/to/mininet-iplab python3 routed-lab.py
PYTHONPATH=/path/to/mininet-iplab python3 routed-lab.py --enable-web
```

The Lab needs Mininet and FRR, plus the dependencies its Nodes use: Open
vSwitch for Switches, Docker/images for Container Hosts, ExaBGP for Speakers,
Kea for DHCP, Knot for DNS and Unbound for Resolver. Run from the exported
folder so the Web UI finds Guide/Layout and persistent `write memory` uses
that folder's own `frr-config/`.

The canvas supports the Node kinds, Links/VLANs/subinterfaces, Link Conditions,
DHCP/Relay, Primary/Secondary DNS, Resolver/DNSSEC, Speaker declarations and
Startup commands and VRFs described above. All 22 shipped examples have
Canvas definitions preserving what each one teaches.

For request shapes, see the [Lab Authoring API reference](api-reference.md#instructor-lab-authoring).
