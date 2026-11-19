# Mininet-IPLab

Mininet-IPLab is a network emulation framework for teaching IP routing and core network services. It extends [Mininet](http://mininet.org/) with [FRR](https://frrouting.org/) routing daemons, ExaBGP speakers, and real servers for DHCP, DNS, DNSSEC, RPKI, and more, driven from a CLI or from a web UI with in-browser terminals.

One host runs simultaneous Group Labs: every Group in a class runs its own copy
of a Lab at the same time, isolated from the others by network namespaces, and
the Instructor follows all of them from one Class Overview in the browser.
Learners need nothing but a browser.

![Mininet-IPLab Web UI demo, sped up: the Instructor's Class Overview of four Groups running different Labs on one host; BGP routes in a Router Terminal; an OSPF link failure rerouting a traceroute while Packet Capture runs; a DHCP Lease; a DNS Resolver's cache; and a new Lab drawn and previewed in Lab Authoring](docs/assets/mininet-iplab-demo.gif)

## Features

- **FRR routing**: enable FRR's routing daemons per Router with `proto=` (OSPF, BGP, IS-IS, LDP, BFD, PIM, RIP and more), such as `proto='OSPF,BGP'` or `proto='ISIS,LDP,BFD'`
- **IPv6**: dual-stack Links, OSPFv3 and BGP IPv6 address families
- **ExaBGP Speakers** *(experimental)*: announce and withdraw BGP routes at runtime
- **RPKI** (Krill, Routinator): an RPKI CA with one Hosted CA per AS and a Validator serving Routers over RTR; Route Origin Validation against hijacks and route leaks stopped with BGP Roles, with live ROA and VRP panels
- **DHCP** (ISC Kea): IPv4 and IPv6 Pools, Reservations, Relay, and a rogue-server exercise, with a live Lease table
- **DNS and DNSSEC** (Knot DNS, Unbound): authoritative Zones with Secondaries, a caching Resolver, signed Zones and live key rollover
- **Container Hosts**: any Docker image as a Lab Node
- **Web UI**: live Topology (D3.js) with in-browser Terminals (xterm.js)
- **Packet Capture**: live capture on an Interface with full protocol decode (tshark), and a Router's NAT Table
- **Classroom server** (`python -m mniplab serve`): Instructor and Learner logins; either one Lab the whole class shares, or simultaneous Group Labs (Multi-Lab Mode), one per Group, with Assignments and a Class Overview
- **Lab Authoring**: draw a Lab in the browser, try it in a Preview Lab, and publish it into the Lab Catalog
- **Extended CLI**: `link_config`, `iperf3`, `show_leases`, `show_cache`, and more

Mininet-IPLab is for IP routing and network services. It does not cover OpenFlow
or SDN controllers; for those, use Mininet itself.

## Installation

### Requirements

Mininet-IPLab runs in a container, but two things must be true of the **host** first:

- A **64-bit Linux host** with Docker Engine and Docker Compose. On macOS or
  Windows that host is a Linux VM (OrbStack, Docker Desktop, WSL2), and the
  requirements below apply to the VM
- **Open vSwitch kernel modules loaded on the host.** Containers cannot supply kernel
  modules, so this must be done on the machine itself:

  ```bash
  lsmod | grep openvswitch          # check
  sudo modprobe openvswitch         # load if missing
  ```

  > ⚠️ Without this, labs that use switches stop at startup with a `NetworkError` naming
  > the missing module. Router-only labs are unaffected. This is the single most common
  > setup problem — see [docs/install.md](docs/install.md) if you hit it. The first
  > lab below (`static-lab`) has no switches, so it starts without the module; the
  > problem appears with the first switch-based lab.

  To make it persist across reboots:

  ```bash
  echo openvswitch | sudo tee /etc/modules-load.d/openvswitch.conf
  ```

### Tested on

- **macOS on Apple silicon, with OrbStack** (arm64): every Lab, and the
  integration and browser test suites
- **GitHub's Ubuntu 24.04 runner** (amd64): lint, type checks and the unit suite,
  in the Mininet-IPLab image. Running Labs, the integration suite and the browser
  suite have not yet been run on amd64

Other 64-bit Linux hosts that meet the requirements above should work but are
untested; reports are welcome.

### Install and run

```bash
# 1. Build the image
docker compose build

# 2. Start the container
docker compose up -d

# 3. Run a lab inside the container
docker compose exec mniplab python3 examples/static-lab.py --enable-web
```

Open `http://localhost:8050` in your browser and log in as `admin` with the password the Lab printed when it started
(`*** Login username: admin  password: …`). Without `MNIPLAB_PASSWORD` a new one
is generated at every start.

> The container runs `privileged: true`, which Mininet needs for its network
> namespaces. This gives it near-host access: run it on a machine set aside for
> the lab, not one holding other work.

> **Mininet itself is built into the image** at a pinned commit (the `MININET_VERSION`
> ARG in the `Dockerfile`), not installed from PyPI — the only Mininet on PyPI is a
> 2020 pre-release this project does not run against. `pip install .` from a checkout
> therefore does **not** bring Mininet with it; see [docs/install.md](docs/install.md#running-on-a-host-without-the-container)
> if you are installing outside the container.

For the dev container, running on a host with Mininet already installed, per-distribution
module loading, and troubleshooting, see **[docs/install.md](docs/install.md)**.

## Running Labs

Labs run **inside the container**. There are four ways to use them, each building on
the one before. The first two are for one Learner or Instructor working alone, and
work the way Mininet does; the last two are classroom deployments.

### 1. Build and run a Lab

CLI Mode: a Lab script opens the `mininet-iplab>` Lab Prompt, the Mininet workflow
extended with FRR routing and Services. Reach individual Nodes from a second shell:

```bash
docker compose exec mniplab python3 examples/static-lab.py

# From another shell in the container:
docker compose exec mniplab ./util/connect_node.sh r1 vtysh -c 'show ip route'
docker compose exec mniplab ./util/connect_node.sh h1 ping -c 4 192.168.2.2
# A Node's name (h2) works as a target only after `update_hosts` at the Lab Prompt
```

### 2. See it in the browser

Web UI Mode for one person: `--enable-web` adds the Topology and in-browser
Terminals, and the Lab Prompt stays on your terminal. Sign in with the shared login
`admin` and the password printed at startup, generated afresh each time unless you
set your own in `.env` (see [Web UI Security](#web-ui-security)).

```bash
docker compose exec mniplab python3 examples/static-lab.py --enable-web
# Open: http://localhost:8050
```

### 3. Run a class on one shared Lab

`python -m mniplab serve` starts the web server with no Lab running. Give the
Instructor and the Learners their own logins with `MNIPLAB_USERS`; the Instructor
picks a Lab Example from the Lab Catalog, starts and stops it, and everyone works on
that one Lab (Single-Lab Mode), suited to a class that moves through the
exercise together.

```bash
docker compose exec mniplab python -m mniplab serve
# Open: http://localhost:8050
```

### 4. Run simultaneous Group Labs

For a class that works at its own pace, add `MNIPLAB_LAB_MODE=multi` to `.env` in
the repository root (the container sees it through the project mount) and restart
`serve` (Multi-Lab Mode). The mode is read at startup only, and a restart begins
with no Lab running.

- Each Learner login in `MNIPLAB_USERS` is a **Group**, shared by the Learners
  who sit at it: a class of 35 in seven Groups needs seven Learner logins.
- Each Group runs **one Lab at a time**, and every Group's Lab runs alongside
  the others. Each is isolated by network namespaces, so the same exercise runs
  as many copies at once: `r1` is `r1` in every Group.
- **At most 4 Labs run at once by default**, the Instructor's own included; see
  [How big a class?](#how-big-a-class) to raise it.
- The Instructor assigns each Group its Lab Examples and follows every Group
  from the Class Overview. See [docs/web-ui.md](docs/web-ui.md#multi-lab-mode).

> ⚠️ Do not run `python -m mniplab cleanup` while `serve` is up: it stops every
> Group's Lab.

On `serve`, the Instructor can also create new Labs in the browser with Lab
Authoring; see [docs/lab-authoring.md](docs/lab-authoring.md).

#### How big a class?

It depends on the host and on the Lab. Each Group's Lab is its own set of
network namespaces and FRR daemons, so memory grows with the number of Groups
and with the number of Routers in the Lab. Start with the default of 4 Labs at
once and raise it in **Server settings** (or with `MNIPLAB_LAB_CAP`) while
watching the host's memory. The poster ([Citing](#citing)) reports an
evaluation on one host.

> The web server listens on `0.0.0.0:8050` unless `--web-host` / `--web-port` or
> `MNIPLAB_HOST` / `MNIPLAB_PORT` in `.env` say otherwise; a flag wins over `.env`.
> Docker Compose publishes only port 8050, so another port also needs the `ports:`
> mapping in `docker-compose.yml` changed.

> Running `python3 examples/...` directly on your host only works if Mininet, FRR and Open
> vSwitch are installed there. See [docs/install.md](docs/install.md#running-on-a-host-without-the-container).

For a full walkthrough of each stage, CLI commands, `connect_node.sh` usage, and troubleshooting, see [docs/QUICKSTART.md](docs/QUICKSTART.md).

## Lab Examples

Every Lab Example is a Python script under [`examples/`](examples/), and each ships with a step-by-step Guide.

| Lab Example | Teaches | Guide |
|---|---|---|
| [static-lab](examples/static-lab.py) | Static routing fundamentals: 2 routers, 2 hosts | [guide](examples/guides/static-lab.md) |
| [ospf-lab](examples/ospf-lab.py) | OSPF with 3 areas, area border routers, and 9 routers | [guide](examples/guides/ospf-lab.md) |
| [bgp-lab](examples/bgp-lab.py) | eBGP and iBGP across 3 autonomous systems, OSPF as the IGP | [guide](examples/guides/bgp-lab.md) |
| [bgp-ispixp-lab](examples/bgp-ispixp-lab.py) | An ISP topology with an internet exchange point, route servers, and ExaBGP speakers | [guide](examples/guides/bgp-ispixp-lab.md) |
| [dhcp-lab](examples/dhcp-lab.py) | A central DHCP Service with direct, relayed, IPv4-only and IPv6-only Subnets, the Router Advertisement M, O and A outcomes, and a rogue server | [guide](examples/guides/dhcp-lab.md) |
| [dns-lab](examples/dns-lab.py) | Root, TLD, Primary, Secondary, a caching Resolver and a client on one Router; Zone Transfer, cache, SOA expiry | [guide](examples/guides/dns-lab.md) |
| [dnssec-lab](examples/dnssec-lab.py) | A signed hierarchy and a validating Resolver; break and repair the chain of trust, roll keys | [guide](examples/guides/dnssec-lab.md) |
| [secure-bgp-lab](examples/secure-bgp-lab.py) | Route Origin Validation over a full RPKI chain: an origin hijack, a forged origin through a loose maxLength, and a route leak stopped with BGP Roles | [guide](examples/guides/secure-bgp-lab.md) |

And 6 more, from BGP path selection to NAT and VLANs, in [`examples/`](examples/).

## Web UI Security

> ⚠️ **Do not expose the Web UI publicly without setting your own credentials.** The generated password changes at every start and is printed on the console; set `MNIPLAB_PASSWORD` for a stable one.

- **The server speaks plain HTTP**, so passwords cross the network unencrypted.
  Keep it on the classroom network or a VPN, or put a TLS reverse proxy in
  front and set `MNIPLAB_USE_SECURE_COOKIES=true`.
- **Learners are trusted.** A Terminal is a root shell on the lab machine, so a
  Learner who looks can read `.env` and `accounts.json`, every password
  included. Logins keep Groups apart; they do not keep Learners out of the
  Instructor's account. Run a class only with Learners you would trust with
  the machine.

Copy `.env.example` to `.env` and set your own values before sharing access:

```bash
cp .env.example .env
# Edit: MNIPLAB_USERNAME, MNIPLAB_PASSWORD
```

For classroom use, configure multiple accounts with roles:

```bash
MNIPLAB_USERS=alice:Pass1:instructor,bob:Pass2:learner,carol:Pass3:learner
```

By default `python -m mniplab serve` runs one Lab that everyone shares; for
simultaneous Group Labs see [Running Labs, step 4](#4-run-simultaneous-group-labs).

Session and Terminal limits (concurrent Sessions per account, idle timeouts,
Terminal ceilings) are operational hygiene, not security boundaries — see
[docs/web-ui.md](docs/web-ui.md#session-and-terminal-limits). The per-account
Session Cap is off by default; a shared classroom credential will lock the
whole class out if it is turned on without every account mapping to one
person.

See [docs/web-ui.md](docs/web-ui.md) for the full configuration reference, all available options, and the API endpoint documentation.

## Documentation

| Document | What it covers |
|---|---|
| [docs/install.md](docs/install.md) | Host requirements, Open vSwitch modules, dev container, troubleshooting setup |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | Running labs, CLI commands, `connect_node.sh`, troubleshooting |
| [docs/CREATE_LAB.md](docs/CREATE_LAB.md) | Building topologies: `addRouter`, `addHost`, `addLink`, OSPF/BGP configs, Services, Container Hosts |
| [docs/lab-authoring.md](docs/lab-authoring.md) | Lab Authoring: drawing a Lab on the canvas, Preview Lab, Keep, Publish into the Lab Catalog |
| [docs/DHCP.md](docs/DHCP.md) | DHCP Service: Pools, Reservations, DHCPv6 and Router Advertisements, Relay, CLI and Web UI surfaces |
| [docs/DNS.md](docs/DNS.md) | DNS Services: Authoritative Zones, Secondary replication, the Resolver, DNSSEC, CLI and Web UI surfaces |
| [docs/web-ui.md](docs/web-ui.md) | Web UI usage, `.env` configuration, API and WebSocket reference |
| [docs/RPKI.md](docs/RPKI.md) | RPKI Services: the RPKI CA and its Hosted CAs, ROAs and ASPAs, the Validator, Route Origin Validation in FRR, CLI and Web UI surfaces |
| [docs/EXABGP.md](docs/EXABGP.md) | ExaBGP speakers: setup, FIFO control, per-neighbor routes, IPv6 peering |
| [docs/api-reference.md](docs/api-reference.md) | Python class and method reference |

## Cleanup

Run after an abnormal exit (Ctrl+C without `exit`) leaves network state behind:

```bash
# Full cleanup: stops every Lab on the host, kills FRR processes, removes temp files
docker compose exec mniplab python -m mniplab cleanup
docker compose exec mniplab python -m mniplab -c    # Shorthand
```

> ⚠️ Cleanup stops **every** Lab on the host, not only yours. Never run it while a
> classroom server (`python -m mniplab serve`) is up: it ends every Group's Lab.

## Contributing

Contributions welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** for the coding
standards, the test tiers, and how to get an environment that can run them (the
dev container in [docs/install.md](docs/install.md#dev-container-for-contributors)
is the shortest path). In short:

- Code follows PEP 8 and project conventions
- Web UI changes maintain accessibility standards (WCAG 2.2 AA)
- Documentation is updated alongside code changes
- Examples work in both CLI Mode and Web UI Mode

## Using it in your course

Teaching with Mininet-IPLab, or thinking about it? Open an Issue with the
**Using Mininet-IPLab in a course** template: tell us what you teach and what
you need. Questions, Lab ideas and reports from a class are all welcome.

## Citing

If you use Mininet-IPLab in your teaching or research, please cite the poster
(GitHub's **Cite this repository** gives the same, from
[CITATION.cff](CITATION.cff)):

> Akbar Fikri Abdillah, Izzah Faiq Putri Madani, Achmad Basuki, and Achmad Husni
> Thamrin. 2026. Mininet-IPLab: Lightweight Multi-Group Routing Labs on a Single
> Host for Network Education. Poster, 21st Asian Internet Engineering Conference
> (AINTEC 2026), Taipei, Taiwan.

```bibtex
@inproceedings{abdillah2026mininetiplab,
  author    = {Abdillah, Akbar Fikri and Madani, Izzah Faiq Putri and
               Basuki, Achmad and Thamrin, Achmad Husni},
  title     = {Mininet-IPLab: Lightweight Multi-Group Routing Labs on a
               Single Host for Network Education},
  booktitle = {21st Asian Internet Engineering Conference (AINTEC 2026)},
  address   = {Taipei, Taiwan},
  year      = {2026},
  month     = dec,
  note      = {Poster},
  url       = {https://interlab.ait.ac.th/aintec2026/program}
}
```

## License

MIT — see [LICENSE](LICENSE).

## Similar Projects

Built on Mininet, Mininet-IPLab is made for the classroom. An Instructor and
Learners sign in with their own roles, simultaneous Group Labs run one per
Group, and the Instructor creates new Labs with browser-based Lab Authoring
and publishes them into the Lab Catalog. Its Routers run FRR, and a Lab can add
network Services: DHCP, DNS, DNSSEC and RPKI.

- [MiniEdit](https://github.com/mininet/mininet/blob/master/examples/miniedit.py) — Mininet's own desktop (Tkinter) editor, shipped as an example; it draws hosts, OpenFlow switches and IP-forwarding routers, runs them, and exports a Mininet Python script, with no routing daemons
- [Mininet-GUI](https://github.com/latarc/mininet-gui) — a browser editor for Mininet SDN topologies (hosts, OpenFlow switches, controllers) with live editing, web terminals, flow-rule and traffic views, and Python/JSON export
- [IPMininet](https://github.com/cnp3/ipmininet) — a Mininet extension for IP networks that generates FRR daemon configurations from Python topologies; code only, with no web UI
- [Containernet](https://github.com/containernet/containernet) — a Mininet fork that uses Docker containers as hosts, driven from Python and the Mininet CLI; Container Hosts serve the same purpose here
- [mininet-sec](https://github.com/mininet-sec/mininet-sec) — a security-focused Mininet with a web UI that shows the topology, opens web terminals, and adds nodes and links to the running network

The closest teaching platform outside Mininet is the
[Mini-Internet](https://github.com/nsg-ethz/mini_internet_project) from ETH Zürich,
where each student group runs an AS of FRR routers in Docker containers and the
class connects them into one Internet. Mininet-IPLab is lighter: a Group's Lab is a
set of network namespaces, so many Labs fit on one host.

Outside the Mininet family, [containerlab](https://containerlab.dev/),
[Kathará](https://github.com/KatharaFramework/Kathara), [GNS3](https://www.gns3.com/)
and [CORE](https://github.com/coreemu/core) also emulate networks that run routing
software.
