# Installation

Mininet-IPLab runs in a container. There are two supported ways to get one, and one
unsupported way to run without it.

| Mode | For | Setup |
|---|---|---|
| **Docker Compose** | Users, classrooms | [Below](#docker-compose-the-usual-way) — this is the official path |
| **Dev container** | Contributors | [Below](#dev-container-for-contributors) |
| Bare host | Nobody, unless Mininet is already installed | [Below](#running-on-a-host-without-the-container) — unsupported |

---

## Host requirements

These apply to **every** mode, including the dev container. A container shares the host's
kernel, so it cannot supply any of them.

### 1. A 64-bit Linux host

Mininet uses network namespaces and `veth` pairs, which are Linux kernel features. On
macOS and Windows you need a Linux VM — Docker Desktop's VM, OrbStack, WSL2, or similar —
and the requirements below apply to *that* VM, not to macOS or Windows.

### 2. Open vSwitch kernel modules

Mininet's switches are Open vSwitch bridges. Without the kernel module, a lab that
declares a switch **stops at `addSwitch` with a `NetworkError`** naming this cause
(plain Mininet's switch setup would block instead, with no error at all).

```bash
lsmod | grep openvswitch     # is it loaded?
sudo modprobe openvswitch    # load it
```

Persist it across reboots:

```bash
echo openvswitch | sudo tee /etc/modules-load.d/openvswitch.conf
```

If `modprobe` reports the module is missing, install the package on the **host**:

| Distribution | Command |
|---|---|
| Debian / Ubuntu | `sudo apt-get install openvswitch-switch` |
| RHEL / CentOS / Fedora | `sudo dnf install openvswitch` |
| Arch | `sudo pacman -S openvswitch` |
| Alpine | `sudo apk add openvswitch` |

Both `docker-compose.yml` and `.devcontainer/devcontainer.json` bind-mount
`/lib/modules` read-only so the container can see the host's modules. **Keep that mount.**

> Only labs that create switches need Open vSwitch. Router-only labs — including
> `examples/static-lab.py` — run without it. `examples/ospf-lab.py` and
> `examples/vlan-lab.py` do use switches.

### 3. Docker

Docker Engine and the Compose plugin. The container runs `privileged` with `NET_ADMIN` and
`SYS_ADMIN`, which Mininet needs to create network namespaces.

---

## Docker Compose (the usual way)

```bash
git clone https://github.com/mininet-iplab/mininet-iplab.git
cd mininet-iplab

docker compose build
docker compose up -d

docker compose exec mniplab python3 examples/static-lab.py --enable-web
```

Then open `http://localhost:8050`.

Before sharing access, copy `.env.example` to `.env` and set your own credentials — see
[web-ui.md](web-ui.md).

---

## Dev container (for contributors)

Open the repository in VS Code with the Dev Containers extension and choose
**"Reopen in Container"**. `.devcontainer/Dockerfile` installs Mininet, FRR, ExaBGP and
Open vSwitch, and starts the OVS service on launch.

The host requirements above still apply — in particular the Open vSwitch modules, which
the dev container mounts from `/lib/modules` but cannot load for you.

**The dev container runs as `root`.** Do not prefix commands with `sudo`; it is
unnecessary, and every command in this project's docs assumes you are already root.

```bash
python -m pytest -m unit -q                      # fast, unprivileged unit suite
python -m pytest -m browser -q                   # frontend UI and API suite
python -m pytest -m integration                  # privileged integration suite (real daemons)
python -m mniplab cleanup                        # between lab runs
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) for coding standards and the test layout.

---

## Running on a host without the container

**This is unsupported.** It works only if Mininet, FRR and Open vSwitch are installed
directly on the machine — which is what the containers exist to avoid.

If you have all three and want to anyway:

```bash
# mniplab does not declare Mininet as a dependency -- the only Mininet on PyPI
# is a 2020 pre-release this project does not run against, and a git pin cannot
# be declared in package metadata. Install the pinned ref explicitly. It is the
# MININET_VERSION ARG in the repository-root Dockerfile.
MININET_REF=$(grep -oP 'ARG MININET_VERSION=\K\S+' Dockerfile)
pip install . "git+https://github.com/mininet/mininet@${MININET_REF}"

python3 examples/static-lab.py
```

`pip install .` on its own installs mniplab without Mininet, and importing it
then fails with `ModuleNotFoundError: No module named 'mininet'`. If Mininet,
FRR or Open vSwitch are missing, labs fail or hang. Use Docker Compose instead.

Packet Analysis in the web UI needs two more system packages, which the
containers already carry: `tshark` for Packet Capture, and `conntrack`
(conntrack-tools) for the NAT Table tab of a NAT Lab. The NAT Table is read
with `conntrack -L` rather than from `/proc/net/nf_conntrack`, which
kernels built without `CONFIG_NF_CONNTRACK_PROCFS` do not have.

```bash
sudo apt-get install tshark conntrack
```

### RPKI (optional)

Only Labs that declare an RPKI CA or a Validator need this; every other Lab
runs without it. The containers already carry all three pieces, and a Lab
that declares a Validator stops at start, pointing here, when one is missing.

- **FRR's RPKI module** (`bgpd_rpki.so`), from the `frr-rpki-rtrlib` package
  in the same FRR repository as `frr`, at the same version. When it is
  installed, every Router's `bgpd` loads it (`-M rpki`).
- **Routinator** 0.16.1, the Validator, from NLnet Labs' apt repository
  ([packages.nlnetlabs.nl](https://packages.nlnetlabs.nl/)), packaged for
  amd64 and arm64.
- **Krill** v0.16.1, the RPKI CA (`krill` and `krillc` on `PATH`), the
  same release on both architectures. On amd64 the containers install
  NLnet Labs' Debian package from the same repository; on arm64, which it
  has no package for, they copy the static binaries from the official
  `nlnetlabs/krill:v0.16.1` image. Krill's source is
  <https://github.com/NLnetLabs/krill/tree/v0.16.1>, under MPL-2.0; the
  containers carry that licence and the notices of what is linked into it
  under `/usr/share/doc/krill/` (in the repository, `licenses/krill/`).

```bash
sudo apt-get install frr-rpki-rtrlib routinator
# Krill on amd64
sudo apt-get install krill=0.16.1-1bookworm
# Krill on arm64: the static binaries from the official image
cid=$(docker create --platform linux/arm64 nlnetlabs/krill:v0.16.1)
for b in krill krillc; do sudo docker cp "$cid:/usr/local/bin/$b" /usr/local/bin/; done
docker rm "$cid"
```

---

## Troubleshooting

### A lab stops with a `NetworkError` about Open vSwitch, or hangs

Almost always the Open vSwitch modules. Check on the **host**, not in the container:

```bash
lsmod | grep openvswitch
```

Empty output means the module is not loaded — see [above](#2-open-vswitch-kernel-modules).
Since Mininet-IPLab 0.1, creating a switch raises a `NetworkError` naming this cause
rather than hanging, so an actual hang suggests an older version.

### `ovs-vsctl` reports a connection failure

The daemon is not running inside the container:

```bash
docker compose exec mniplab service openvswitch-switch start
```

### Errors about leftover network state

A previous lab did not shut down cleanly:

```bash
docker compose exec mniplab python -m mniplab cleanup
```

### `Cannot find required executable mnexec`

Mininet is not installed in the environment you are running from — usually a sign of
running on the host rather than inside the container.

### Privileged tests all skip

They are deselected from the default suite by design. Run them explicitly with
`python -m pytest -m integration`. If they report *skipped* rather than running, ask why
with `python -m pytest -m integration -rs` — the reason names the missing capability.

### `*** Error setting resource limits. Mininet's performance may be affected.`

Mininet's limit-fixing routine runs roughly thirteen tuning steps inside a single broad
exception handler. On the kernel used here, the network device backlog knob it tries to
set does not exist; the resulting error escapes that one step, and the broad handler
prints a single warning while silently skipping every remaining step — including the PTY
maximum, which is the one step Mininet actually cares about for node count. The other
skipped settings size network buffers, the ARP table, and the route table, and only
matter at hundreds to thousands of emulated hosts — negligible at the scale this project
targets.

This is a kernel-feature gap, not a sign that the container is adding overhead: the same
kernel booted on bare metal produces the identical warning. Do not take it as evidence
that Docker Compose mode is slower than Bare host mode, or as a reason to switch.
