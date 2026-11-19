# RPKI Services

Two Services bring the Resource Public Key Infrastructure into a Lab:

- **RPKI CA** (`RPKICAService`): signs RPKI objects on behalf of the Lab's
  Autonomous Systems, as an RIR's hosted portal does, backed by
  [Krill](https://nlnetlabs.nl/projects/routing/krill/). Under its
  **Registry** it holds one **Hosted CA** per Autonomous System, and each
  Hosted CA can sign ROAs and ASPAs only for the ASN and prefixes the Lab
  gives it.
- **Validator** (`ValidatorService`): backed by
  [Routinator](https://nlnetlabs.nl/projects/routing/routinator/), it fetches
  what the RPKI CA signs, validates it, and serves the result to Routers over
  RTR as VRPs (Validated ROA Payloads).

Routers then do **Route Origin Validation** in FRR: they mark each BGP path
`valid`, `invalid` or `not found` and, with a route-map, deny the invalid ones.
Like every Service, both attach to any Node, Host or Router.

## Quick Start

```bash
python3 examples/secure-bgp-lab.py --enable-web
```

The [Secure BGP Lab Guide](../examples/guides/secure-bgp-lab.md) walks through
an origin hijack, a ROA, pointing a Router at the Validator, denying invalid
routes, a forged origin through a loose maxLength, and a route leak stopped
with BGP Roles (RFC 9234).

## Requirements

All of these are built into the container image. On a host without the
container, see the [RPKI section of install.md](install.md#rpki-optional):

- FRR's RPKI module (`bgpd_rpki.so`, package `frr-rpki-rtrlib`); every
  Router's `bgpd` loads it when it is installed
- Routinator 0.16.1
- Krill v0.16.1 (`krill` and `krillc`)

A Lab with no RPKI Service needs none of them. A Lab that declares an RPKI CA
or a Validator stops at start, pointing to install.md, when one is missing.

## How It Works

1. At start the RPKI CA makes a private TLS root and a certificate for its
   hostname, names that hostname in the Lab's hosts file, and starts Krill in
   testbed mode on its Node. It then creates each Hosted CA under the
   Registry, with the resources and starting ROAs and ASPAs the script
   declares.
2. The Validator fetches the Lab's TAL from the RPKI CA, trusts the CA's TLS
   root, and starts Routinator. It serves RTR on TCP port **3323** and HTTP on
   port **8323**, and validates again every `refresh` seconds (10 by default).
   The Lab does not wait for the first VRPs.
3. A Router you point at the Validator receives the VRPs over RTR and marks
   its paths. Nothing in a Router's configuration changes on its own: you add
   the `rpki` lines to FRR.

A Lab holds at most **one RPKI CA**, and a Validator needs one. Both listen on
IPv4 only.

## Configuration

### Declaring the RPKI CA and Hosted CAs

```python
from mniplab.rpki import HostedCA, RPKICAService, ValidatorService

ca = net.addHost('ca', defaultRoute='via 10.99.0.1')
ca.addService(RPKICAService([
    HostedCA(65001, ['10.1.0.0/24']),
    HostedCA(65002, ['10.2.0.0/24'], roas=['10.2.0.0/24 => 65002']),
    HostedCA(65004, ['10.4.0.0/24'], aspas=['AS65004 => AS65001, AS65002']),
]))
```

`HostedCA(asn, prefixes, roas=(), aspas=())`:

- `asn`: the Autonomous System, as an int or text such as `AS65002`
- `prefixes`: the prefixes it holds, at least one, IPv4 or IPv6, with no host
  bits set
- `roas`: starting ROAs, written as Krill writes them:
  `<prefix>[-<maxLength>] => <origin>`. The maxLength defaults to the prefix
  length. Each ROA must fall inside one of the Hosted CA's prefixes.
- `aspas`: starting ASPAs, `<customer> => <provider>[, <provider>...]`. The
  customer must be the Hosted CA's own ASN.

`RPKICAService(hosted_cas, hostname=None)` takes the Hosted CAs, one per ASN,
and optionally the DNS name Krill answers to; it defaults to the Node's name.
A Hosted CA is known to Krill and `krillc` as `as<asn>`, for example `as65002`.

The models are also importable on their own: `ROA`, `ASPA` and their
`parse()` read the same text.

### Declaring the Validator

```python
rv = net.addHost('rv', defaultRoute='via 10.1.0.65')
rv.addService(ValidatorService())        # or ValidatorService(refresh=30)
```

The Validator takes its TAL and trust from the Lab's RPKI CA, so it needs no
other settings. Both Nodes need a route to each other.

### Pointing a Router at the Validator

In FRR, on each Router that validates:

```text
rpki
 rpki polling_period 10
 rpki cache tcp 10.1.0.66 3323 preference 1
exit
```

`show rpki cache-connection` ends in `(connected)` within a few seconds, and
`show rpki prefix-table` lists the VRPs. `show bgp ipv4 unicast` then marks
paths `V` (valid), `I` (invalid) and `N` (not found).

Validation alone rejects nothing. To deny invalid paths, put a deny ahead of
the permit entries of each inbound route-map:

```text
route-map FROM-PEER deny 5
 match rpki invalid
exit
```

### After a ROA change

FRR re-marks a path's validation state within seconds of a VRP change, but it
does **not** re-run inbound route-maps. A path a route-map denied stays out
after it turns valid, and an accepted path that turns invalid stays installed.
Re-apply the policy with a soft clear (the neighbor needs
`soft-reconfiguration inbound`):

```text
clear bgp ipv4 unicast * soft in
```

## Runtime Operations

ROAs and ASPAs added or removed while a Lab runs are Exercise Configuration:
they reach Krill at once and are discarded when the Lab stops. The script's
starting objects come back at the next start.

### CLI

| Command | What it does |
|---|---|
| `show_rpki [node]` | Each Hosted CA on an RPKI CA, with its resources, ROAs and ASPAs |
| `rpki_roas <node> <asn> add\|remove <prefix>[-<maxlen>] => <origin>` | Sign or withdraw a ROA at a Hosted CA |
| `rpki_aspas <node> <asn> add <customer> => <providers>` | Sign an ASPA at a Hosted CA |
| `rpki_aspas <node> <asn> remove <customer> [=> <providers>]` | Withdraw an ASPA |
| `show_vrps [node]` | What a Validator serves: VRPs and ASPAs, when it last ran and runs next, and its connected Routers |

In a Terminal on the RPKI CA's Node, `krillc` talks to Krill without any
setup, for example `krillc roas list --ca as65002`. A change made with
`krillc` shows in the panel and the CLI, and the other way round.

### Web UI

The RPKI CA and the Validator carry badges, `RPKI CA` and `Validator`; click
one to open the Node's panel. See
[web-ui.md](web-ui.md#rpki-ca-panel) for what each shows.

### REST

All endpoints require the authenticated session cookie; any role may sign
and withdraw:

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

A ROA body carries `prefix`, `max_length` and `origin`; with `old` (a ROA of
the same shape) it replaces that ROA in one change. An ASPA body carries
`customer` and `providers`. A prefix the Hosted CA does not hold is refused
with 400, naming the Hosted CA and the prefix.

## Current Limits

- **ASPA is signed and validated, not enforced.** FRR does not act on ASPAs
  yet; the Secure BGP Lab stops route leaks with BGP Roles instead.
- **IPv4 transport.** Krill and Routinator listen on IPv4 only; ROAs may still
  cover IPv6 prefixes.
- **One RPKI CA per Lab.**
- **Changes within one second.** The framework spaces its own changes at one
  Hosted CA at least a second apart, because a Validator keeps a manifest
  whose issue time is not newer (RFC 9286). Two raw `krillc` changes at one
  Hosted CA within the same second can leave the second unseen until that
  Hosted CA changes again.
- **BGP Roles** take effect only after the BGP session resets.

## Troubleshooting

### The Lab stops at start naming `bgpd_rpki.so`, Routinator or Krill

A piece is not installed on the host; see
[install.md](install.md#rpki-optional).

### `show rpki cache-connection` never says `(connected)`

Check that the Router reaches the Validator's address on TCP 3323
(`ping` from the Router, then `show_vrps`, which lists connected Routers).

### A new ROA does not show on the Validator

Its next run fetches it: wait up to `refresh` seconds plus the fetch, about
20 seconds with the default. The Validator panel shows when it last fetched
and when it fetches next.

### A path's state changed but the route did not

Run `clear bgp ipv4 unicast * soft in`; see [After a ROA change](#after-a-roa-change).

## Example Labs

- [`examples/secure-bgp-lab.py`](../examples/secure-bgp-lab.py), with its
  [Guide](../examples/guides/secure-bgp-lab.md)

## See Also

- [CREATE_LAB.md](CREATE_LAB.md#services): Services in a Lab script
- [lab-authoring.md](lab-authoring.md#rpki-ca-and-validator): the RPKI editors on the canvas
- [EXABGP.md](EXABGP.md): the Speaker the Secure BGP Lab hijacks with
