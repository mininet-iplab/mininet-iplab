"""Generate a runnable Lab Example script from a visual definition.

The generated script is the one executable form of a visual recipe: Preview,
publication and download all write it, so the Python an Instructor reads is
the Python a Lab runs. It calls the same public library API as the shipped
Lab Examples and ends with the usual ``parse_lab_args``/``run_lab`` entry
point, so it also runs from a terminal, with or without ``--enable-web``.
"""

import builtins
import keyword
import re
import textwrap
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from mniplab.rpki.service import DEFAULT_REFRESH
from mniplab.webserver.authoring import (
    AuthoringRepository, authored_nameservers, dhcp_pool, dns_record, has_code_section,
    router_frr_config,
)

#: Records a library name as imported from a module and returns the name.
Importer = Callable[[str, str], str]

#: The fields each Pool and Reservation declares, in their dataclass order.
_POOL_OPTIONS = ("lease_time", "preferred_lifetime", "router", "dns_server")
_RESERVATION_OPTIONS = ("mac", "duid", "hostname")
_ROUTE_OPTIONS = ("community", "as_path", "large_community", "med")


def script_slug(name: str) -> str:
    """The file stem a Lab Example's script and assets share.

    Args:
        name: The Catalog name.

    Returns:
        Lowercase words joined by hyphens, never empty.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60].strip("-")
    return slug or "lab-example"


def _text(value: str) -> str:
    """A Python literal for text, triple-quoted when that keeps it readable."""
    if ("\n" in value and "\\" not in value and '"""' not in value
            and not value.endswith('"')
            and all(char.isprintable() or char == "\n" for char in value)):
        return '"""\\\n' + value + '"""'
    return repr(value)


def _call(function: str, *args: str, **options: str) -> str:
    """Render one call from already-rendered argument expressions.

    Short calls stay on one line. Longer ones put their arguments on
    indented lines filled up to the line width; an argument that is itself
    several lines long gets lines of its own.
    """
    rendered = list(args) + [f"{key}={value}" for key, value in options.items()]
    single = f"{function}({', '.join(rendered)})"
    if len(single) <= 88 and "\n" not in single:
        return single
    lines: List[str] = []
    shareable = False
    for item in rendered:
        if "\n" in item:
            lines.extend(item.splitlines())
            lines[-1] += ","
            shareable = False
        elif shareable and len(lines[-1]) + len(item) + 2 <= 84:
            lines[-1] += f" {item},"
        else:
            lines.append(f"{item},")
            shareable = True
    return f"{function}(\n" + "".join(f"    {line}\n" for line in lines) + ")"


def _indent(text: str, prefix: str = "    ") -> str:
    """Indent every line of a rendered statement."""
    return "".join(f"{prefix}{line}\n" if line else "\n" for line in text.splitlines())


class _Names:
    """Unique, readable Python identifiers for Nodes, Zones and constants."""

    def __init__(self, reserved: Set[str]) -> None:
        self.used = set(reserved) | set(keyword.kwlist) | set(dir(builtins))

    def claim(self, wanted: str) -> str:
        """Reserve the identifier closest to ``wanted`` that is still free."""
        base = re.sub(r"\W", "_", wanted) or "node"
        if base[0].isdigit():
            base = f"n_{base}"
        name, index = base, 2
        while name in self.used:
            name, index = f"{base}_{index}", index + 1
        self.used.add(name)
        return name


#: The comment that opens a Code section in the generated script.
CODE_SECTION_HEADING = "    # Code section\n"


def generate_python(document: Dict[str, Any]) -> str:
    """Render the runnable script for a validated, buildable definition.

    Args:
        document: A draft or published definition that passed validation.

    Returns:
        The script's source. Persistent Routers read their Seeds from
        ``frr-config/<name>`` beside the script. A Code section runs at the
        end of the build step, as written, indented into ``build_network()``.
    """
    before, after = generated_python_parts(document)
    if not has_code_section(document):
        return before + after
    code = document["code_section"]
    if not code.endswith("\n"):
        code += "\n"
    return before + CODE_SECTION_HEADING + _indent(code) + "\n" + after


def code_section_first_line(document: Dict[str, Any]) -> int:
    """The generated script's line that holds a Code section's first line.

    Args:
        document: A Canvas draft with a Code section.

    Returns:
        The line after the Code section's heading.
    """
    return generated_python_parts(document)[0].count("\n") + 2


def generated_python_parts(document: Dict[str, Any]) -> Tuple[str, str]:
    """The generated script before and after the place its Code section goes.

    Args:
        document: A draft or published definition that passed validation.

    Returns:
        The text up to the end of the build step, and from ``return net``.
    """
    topology = document["topology"]
    nodes = topology["nodes"]
    links = topology["links"]
    by_id = {node["id"]: node for node in nodes}
    imports: Dict[str, Set[str]] = {"mniplab": {"mnIPLab", "parse_lab_args", "run_lab"}}

    def need(module: str, name: str) -> str:
        imports.setdefault(module, set()).add(name)
        return name

    reserved = {"os", "net", "args", "SEEDS", "build_network", "mnIPLab", "parse_lab_args",
                "run_lab", "BGPNeighbor", "BGPRoute", "DHCPService", "Pool", "Reservation",
                "Relay", "DNSService", "SecondaryZone", "Zone", "AccessRule",
                "ResolverService", "ARecord", "AAAARecord", "CNAMERecord", "PTRRecord",
                "TXTRecord", "NSRecord", "MXRecord", "SOARecord",
                "HostedCA", "RPKICAService", "ValidatorService",
                "make_v4_peer_routes", "make_v6_peer_routes"}
    names = _Names(reserved)
    variables = {node["id"]: names.claim(node["name"]) for node in nodes}
    constants: List[str] = []
    body: List[str] = ["net = mnIPLab(topo=None, controller=None)", ""]
    persistent = False

    def node_ref(node_id: str) -> str:
        return variables[node_id]

    for node in nodes:
        var = variables[node["id"]]
        kind = node["type"]
        if kind in ("nat", "transit"):
            continue
        if kind == "switch":
            switch_options = {key: repr(node[key]) for key in ("stp", "priority") if key in node}
            body.append(f"{var} = " + _call("net.addSwitch", repr(node["name"]), **switch_options))
            continue
        if kind == "router":
            options: Dict[str, str] = {}
            if node["protocols"]:
                options["proto"] = repr(",".join(node["protocols"]))
            if document["persistent_config"]:
                persistent = True
                options["frr_dir"] = "os.path.join(SEEDS, '%(name)s')"
            else:
                text = router_frr_config(node, links)
                if text:
                    constant = names.claim(f"{var.upper()}_FRR")
                    constants.append(f"{constant} = {_text(text)}")
                    options["frr_config"] = constant
            if node.get("vrfs"):
                options["vrfs"] = repr({vrf["name"]: vrf["table"] for vrf in node["vrfs"]})
            if node.get("nat"):
                options["nat"] = _list([_nat_entry(entry, need) for entry in node["nat"]])
            body.append(f"{var} = " + _call("net.addRouter", repr(node["name"]), **options))
            continue
        if kind == "speaker":
            neighbors = [_call(
                need("mniplab.exabgp", "BGPNeighbor"),
                peer=repr(item["peer"]), local_address=repr(item["local_address"]),
                local_as=repr(item["local_as"]), peer_as=repr(item["peer_as"]),
                **_present(hold_time=None if item["hold_time"] is None
                           else repr(item["hold_time"]),
                           routes=_speaker_route_expression(node, item, need)),
            ) for item in node["neighbors"]]
            body.append(f"{var} = " + _call(
                "net.addExaBGP", repr(node["name"]), neighbors=_list(neighbors),
                **_present(routes=_list([_route(route, need) for route in node["routes"]])
                           if node["routes"] and "route_generator" not in node else None),
                local_as=repr(node["local_as"]),
            ))
            continue
        options = {}
        if kind == "container":
            options["dimage"] = repr(node["image"])
        if node["ipv4_gateway"]:
            options["defaultRoute"] = repr(f"via {node['ipv4_gateway']}")
        if node["ipv6_gateway"]:
            options["defaultRoute6"] = repr(f"via {node['ipv6_gateway']}")
        if "nameservers" in node:
            options["nameservers"] = repr(authored_nameservers(node["nameservers"], topology))
        method = "net.addContainer" if kind == "container" else "net.addHost"
        body.append(f"{var} = " + _call(method, repr(node["name"]), **options))
    for node in nodes:
        var = variables[node["id"]]
        if node["type"] == "nat":
            body.append(f"{var} = " + _call("net.addNAT", repr(node["name"]),
                                            subnet=repr(node["subnet"])))
        elif node["type"] == "transit":
            body.append(f"{var} = " + _call(
                "net.addTransit", repr(node["name"]),
                downstream="[" + ", ".join(node_ref(item) for item in node["downstream_ids"]) + "]",
                subnet=repr(node["subnet"]),
            ))

    # A NAT Gateway's address is its first Interface's, declared on that
    # Link end like every other Interface's.
    gateway_ends: Dict[str, int] = {}
    for link in links:
        for end in link["ends"]:
            if by_id[end["node_id"]]["type"] in ("nat", "transit"):
                lowest = gateway_ends.get(end["node_id"], end["port"])
                gateway_ends[end["node_id"]] = min(lowest, end["port"])

    if links:
        body.append("")
    for link in links:
        first, second = link["ends"]
        params = []
        for end in (first, second):
            values: Dict[str, Any] = {}
            if end.get("ipv4"):
                values["ip"] = end["ipv4"]
            if gateway_ends.get(end["node_id"]) == end["port"]:
                values["ip"] = by_id[end["node_id"]]["ip"]
            if end.get("ipv6"):
                values["ip6"] = end["ipv6"]
            for flag in ("dhcp4", "dhcp6"):
                if end.get(flag):
                    values[flag] = True
            if end.get("vrf"):
                values["vrf"] = end["vrf"]
            if end.get("outside"):
                values["nat"] = "outside"
            vlan = end.get("vlan")
            if vlan:
                values.update({"vlan": vlan["vlan"]} if vlan["mode"] == "access" else
                              {"trunks": vlan["trunks"], "native": vlan["native"]})
            params.append(values)
        options = {"port1": repr(first["port"]), "port2": repr(second["port"])}
        for index, values in enumerate(params, start=1):
            if values:
                options[f"params{index}"] = repr(values)
        for index, end in enumerate(link["ends"], start=1):
            if end.get("mac"):
                options[f"addr{index}"] = repr(end["mac"])
        condition = link.get("condition")
        if condition:
            for field in ("delay", "jitter"):
                if condition[field] is not None:
                    options[field] = repr(condition[field])
            if condition["loss"] is not None:
                options["loss"] = repr(float(condition["loss"][:-1]))
            if condition["bandwidth"] is not None:
                options["bw"] = repr(condition["bandwidth"])
                if condition["scheduler"] == "hfsc":
                    options["use_hfsc"] = "True"
                elif condition["scheduler"] == "tbf":
                    options["use_tbf"] = "True"
            if condition["status"] == "down":
                options["initial_status"] = "'down'"
        body.append(_call("net.addLink", node_ref(first["node_id"]),
                          node_ref(second["node_id"]), **options))
        if condition and condition["bandwidth"] is not None:
            body.append(_call(
                "net.link_conditions.set_impairment",
                repr(by_id[first["node_id"]]["name"]), repr(by_id[second["node_id"]]["name"]),
                scheduler=repr(condition["scheduler"]),
            ))

    for node in nodes:
        if node["type"] != "router" or not node.get("subinterfaces"):
            continue
        ports = {end["interface_id"]: end["port"] for link in links
                 for end in link["ends"] if end["node_id"] == node["id"]}
        for sub in node["subinterfaces"]:
            parent = f"{node['name']}-eth{ports[sub['parent_interface_id']]}"
            options = {}
            if sub["ipv4"]:
                options["ip"] = repr(sub["ipv4"])
            if sub["ipv6"]:
                options["ip6"] = repr(sub["ipv6"])
            body.append(_call(f"{variables[node['id']]}.add_subinterface",
                              repr(parent), repr(sub["vlan"]), **options))

    services = _services(nodes, topology, variables, names, need)
    if services:
        body.append("")
        body.extend(services)

    startup = [(node, command) for node in nodes for command in node.get("startup", [])]
    if startup:
        body.append("")
    for node, command in startup:
        options = {}
        if command["timeout"] != 5:
            options["timeout"] = repr(command["timeout"])
        if command["may_fail"]:
            options["may_fail"] = "True"
        body.append(_call("net.addStartupCommand", variables[node["id"]],
                          _text(command["command"]), repr(command["stage"]), **options))
    body.extend(["", _CODE_SECTION_SLOT, "return net"])

    catalog = document["catalog"]
    lines = ['"""' + _docstring(catalog["name"])]
    if catalog["description"].strip():
        lines += ["", _docstring(catalog["description"].strip())]
    lines += [
        "",
        "Generated by Mininet-IPLab Lab Authoring. Run it from this folder:",
        f"    python3 {script_slug(catalog['name'])}.py [--enable-web]",
        '"""',
        "",
    ]
    if persistent:
        lines += ["import os", ""]
    for module in sorted(imports):
        lines.append(f"from {module} import {', '.join(sorted(imports[module]))}")
    lines.append("")
    if persistent:
        lines += ["# Each persistent Router's Seed lives in frr-config/<name> beside this script.",
                  "SEEDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frr-config')",
                  ""]
    for constant in constants:
        lines += [constant, ""]
    lines += ["", "def build_network() -> mnIPLab:",
              '    """Build the Lab Example\'s Topology, Services and Startup commands."""']
    lines.append(_indent("\n".join(body)).rstrip("\n"))
    lines += [
        "", "",
        "if __name__ == '__main__':",
        f"    args = parse_lab_args({catalog['name']!r})",
        "    run_lab(",
        "        build_network(),",
        "        enable_web=args.enable_web,",
        "        web_host=args.web_host,",
        "        web_port=args.web_port,",
        "        log_level=args.log_level,",
        "    )",
        "",
    ]
    before, after = "\n".join(lines).rsplit(f"    {_CODE_SECTION_SLOT}\n", 1)
    return before, after


#: Marks where a Code section goes while the script is rendered.
_CODE_SECTION_SLOT = "# <code section>"


def _docstring(text: str) -> str:
    """Text safe to place inside the script's module docstring, wrapped."""
    escaped = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return "\n".join(textwrap.fill(line, 79) if line else "" for line in escaped.splitlines())


def _present(**options: Any) -> Dict[str, str]:
    """The rendered options that are set, leaving library defaults implicit."""
    return {key: value for key, value in options.items() if value is not None}


def _list(items: List[str]) -> str:
    """A list literal from rendered items, one per line once it is long."""
    single = "[" + ", ".join(items) + "]"
    if len(single) <= 72 and "\n" not in single:
        return single
    return "[\n" + "".join(_indent(item + ",") for item in items) + "]"


def _nat_entry(entry: Dict[str, Any], need: Importer) -> str:
    """A ``PortForward`` or ``StaticNAT`` call naming only the options the entry sets."""
    interface = {"interface": repr(entry["interface"])} if entry.get("interface") else {}
    if entry["kind"] == "port_forward":
        options = {"hairpin": "True"} if entry["hairpin"] else {}
        return _call(need("mniplab", "PortForward"), repr(entry["protocol"]),
                     repr(entry["outside_port"]), repr(entry["inside"]),
                     repr(entry["inside_port"]), **options, **interface)
    return _call(need("mniplab", "StaticNAT"), repr(entry["inside"]),
                 repr(entry["outside"]), **interface)


def _route(route: Dict[str, Any], need: Importer) -> str:
    """A ``BGPRoute`` call naming only the attributes the route sets."""
    options = {field: repr(route[field]) for field in _ROUTE_OPTIONS
               if route.get(field) not in (None, [])}
    return _call(need("mniplab.exabgp", "BGPRoute"), prefix=repr(route["prefix"]),
                 next_hop=repr(route["next_hop"]), **options)


def _speaker_route_expression(speaker: Dict[str, Any], neighbor: Dict[str, Any],
                              need: Importer) -> Optional[str]:
    """Render a Neighbor's generated and crafted Routes, inheriting Global ones."""
    generator = neighbor.get("route_generator", speaker.get("route_generator"))
    hand_written = neighbor["routes"] or speaker["routes"]
    rows = _list([_route(route, need) for route in hand_written])
    if generator is None:
        return rows if neighbor["routes"] else None
    family = "v6" if ":" in neighbor["peer"] else "v4"
    generated = _call(need("mniplab.exabgp", f"make_{family}_peer_routes"),
                      num_prefixes=repr(generator["count"]),
                      local_asn=repr(neighbor["local_as"]),
                      next_hop=repr(neighbor["local_address"]),
                      prefix_seed=repr(generator["prefix_seed"]),
                      as_path_seed=repr(generator["as_path_seed"]),
                      num_sub_prefixes=repr(generator["sub_prefixes"]))
    return generated + (f" + {rows}" if hand_written else "")


def _record(value: Dict[str, Any], need: Importer) -> str:
    """The typed Record call a declaration builds, as :func:`dns_record` does."""
    record = dns_record(value)
    kind = value["type"]
    name, data, ttl = value["name"], value["rdata"], value["ttl"]
    if kind == "MX":
        return _call(need("mniplab.dns", "MXRecord"), repr(name), repr(record.exchange),
                     preference=repr(record.preference), ttl=repr(ttl))
    if kind == "SOA":
        mname, rname, *numbers = data.split()
        serial, refresh, retry, expire, minimum = (int(item) for item in numbers)
        return _call(need("mniplab.dns", "SOARecord"), repr(mname), repr(rname),
                     serial=repr(serial), refresh=repr(refresh), retry=repr(retry),
                     expire=repr(expire), minimum=repr(minimum), ttl=repr(ttl),
                     name=repr(name))
    return _call(need("mniplab.dns", f"{kind}Record"), repr(name), repr(data), ttl=repr(ttl))


def _services(nodes: List[Dict[str, Any]], topology: Dict[str, Any],
              variables: Dict[str, str], names: _Names, need: Importer) -> List[str]:
    """Zones, then each Node's DNS, Resolver, DHCP and Relay in Node order."""
    lines: List[str] = []
    primaries = {(node["id"], zone["name"]): zone for node in nodes
                 for zone in node.get("dns", {}).get("zones", []) if zone["kind"] == "primary"}
    zone_variables: Dict[tuple, str] = {}
    building: Set[tuple] = set()

    def build_zone(key: tuple) -> str:
        """Declare a signed child's Zone before the parent that carries its DS."""
        if key in zone_variables:
            return zone_variables[key]
        if key in building:
            raise ValueError("DS delegation contains a cycle")
        building.add(key)
        zone = primaries[key]
        children = [build_zone((child["node_id"], child["zone"]))
                    for child in zone.get("ds_delegations", [])]
        label = "root" if zone["name"] == "." else zone["name"].rstrip(".")
        var = names.claim(f"{label}_zone")
        options: Dict[str, str] = {"default_ttl": repr(zone["default_ttl"])}
        if zone.get("allow_transfer") is not None:
            options["allow_transfer"] = "[" + ", ".join(
                variables[target["node_id"]] if "node_id" in target else repr(target["address"])
                for target in zone["allow_transfer"]) + "]"
        if zone.get("dnssec"):
            options["dnssec"] = "True"
        if zone.get("nsec3"):
            options["nsec3"] = "True"
        if zone.get("nsec3_salt") is not None:
            options["nsec3_salt"] = repr(zone["nsec3_salt"])
        records = _list([_record(record, need) for record in zone["records"]])
        lines.append(f"{var} = " + _call(need("mniplab.dns", "Zone"), repr(zone["name"]),
                                         records, **options))
        for child in children:
            lines.append(f"{var}.add_record({child}.ds_record())")
        building.remove(key)
        zone_variables[key] = var
        return var

    for key in primaries:
        build_zone(key)
    for node in nodes:
        var = variables[node["id"]]
        if "dns" in node:
            zones = [zone_variables[(node["id"], zone["name"])] if zone["kind"] == "primary"
                     else _call(need("mniplab.dns", "SecondaryZone"), repr(zone["name"]),
                                primary=variables[zone["primary_node_id"]])
                     for zone in node["dns"]["zones"]]
            lines.append(_call(f"{var}.addService",
                               _call(need("mniplab.dns", "DNSService"), *zones)))
        if "resolver" in node:
            lines.append(_call(f"{var}.addService", _resolver(node["resolver"], topology,
                                                              variables, need)))
        if "dhcp" in node:
            pools = [_pool(pool, need) for pool in node["dhcp"]["pools"]]
            lines.append(_call(f"{var}.addService",
                               _call(need("mniplab.dhcp", "DHCPService"), *pools)))
        if "relay" in node:
            relay = node["relay"]
            lines.append(_call(f"{var}.addRelay", _call(
                need("mniplab.dhcp", "Relay"), *(repr(subnet) for subnet in relay["subnets"]),
                server=repr(relay["servers"]))))
        if "rpki_ca" in node:
            lines.append(_call(f"{var}.addService", _rpki_ca(node["rpki_ca"], need)))
        if "validator" in node:
            lines.append(_call(f"{var}.addService", _validator(node["validator"], need)))
    return lines


def _rpki_ca(value: Dict[str, Any], need: Importer) -> str:
    """The ``RPKICAService`` call a Node's RPKI CA declaration builds."""
    hosted = []
    for row in value["hosted_cas"]:
        options: Dict[str, str] = {}
        if row["roas"]:
            options["roas"] = repr(row["roas"])
        if row["aspas"]:
            options["aspas"] = repr(row["aspas"])
        hosted.append(_call(need("mniplab.rpki", "HostedCA"), repr(row["asn"]),
                            repr(row["prefixes"]), **options))
    options = {}
    if value["hostname"] is not None:
        options["hostname"] = repr(value["hostname"])
    return _call(need("mniplab.rpki", "RPKICAService"), _list(hosted), **options)


def _validator(value: Dict[str, Any], need: Importer) -> str:
    """The ``ValidatorService`` call a Validator declaration builds, naming refresh only when it differs."""
    options = {} if value["refresh"] == DEFAULT_REFRESH else {"refresh": repr(value["refresh"])}
    return _call(need("mniplab.rpki", "ValidatorService"), **options)


def _resolver(value: Dict[str, Any], topology: Dict[str, Any],
              variables: Dict[str, str], need: Importer) -> str:
    """The ``ResolverService`` call a Resolver declaration builds."""
    hints = [variables[item["node_id"]] if "node_id" in item else repr(item["address"])
             for item in value["root_hints"]]
    forwarders: List[str] = []
    for reference in value["forwarders"]:
        forwarders.extend(authored_nameservers([reference], topology))
    rules = [_call(need("mniplab.resolver", "AccessRule"), repr(rule["subnet"]),
                   repr(rule["action"])) for rule in value["access_control"]]
    options: Dict[str, str] = {}
    if hints:
        options["root_hints"] = "[" + ", ".join(hints) + "]"
    if forwarders:
        options["forwarders"] = repr(forwarders)
    options["max_ttl"] = repr(value["max_ttl"])
    options["max_negative_ttl"] = repr(value["max_negative_ttl"])
    options["access_control"] = _list(rules)
    anchors = [variables[item["node_id"]] for item in value.get("trust_anchors", [])]
    if anchors:
        options["trust_anchors"] = "[" + ", ".join(anchors) + "]"
    return _call(need("mniplab.resolver", "ResolverService"), **options)


def _pool(value: Dict[str, Any], need: Importer) -> str:
    """The ``Pool`` call a declaration builds, as :func:`dhcp_pool` does."""
    pool = dhcp_pool(value)
    options = {field: repr(getattr(pool, field)) for field in _POOL_OPTIONS
               if getattr(pool, field) not in (None, "")}
    reservations = [_call(need("mniplab.dhcp", "Reservation"), ip=repr(item.ip), **{
        field: repr(getattr(item, field)) for field in _RESERVATION_OPTIONS
        if getattr(item, field)
    }) for item in pool.reservations]
    if reservations:
        options["reservations"] = "(" + ", ".join(reservations) + ",)"
    return _call(need("mniplab.dhcp", "Pool"), cidr=repr(pool.cidr), start=repr(pool.start),
                 end=repr(pool.end), **options)


README = """# {name}

A Mininet-IPLab Lab Example. Its Guide and Authored Layout are in
`examples/`, where the Web UI looks for them when you run the Lab from this
folder; persistent Routers read their Seeds from `frr-config/`.

## Run it

From this folder, as root on a machine with Mininet-IPLab's requirements:

    python3 {script}                 # CLI
    python3 {script} --enable-web    # CLI plus the Web UI on port 8050

`mniplab` must be importable: install the Mininet-IPLab checkout with
`pip install -e <checkout>`, or set `PYTHONPATH=<checkout>`.

## Requirements

- Linux, root or `CAP_NET_ADMIN`, Mininet and FRR
- Open vSwitch, when the Lab has Switches
- ExaBGP, when it has BGP Speakers
- Kea (DHCP), Knot (DNS) and Unbound (Resolver), when it uses those Services
- Docker with each Container Host's image, when it has Container Hosts

Persistent Routers write `write memory` into `frr-config/` when run this way.
"""


def package_archive(repository: AuthoringRepository, document: Dict[str, Any]) -> bytes:
    """Export a native runnable package through the shared package provider.

    Args:
        repository: Repository providing the saved recipe's materialization.
        document: A validated, buildable Canvas or Python definition.

    Returns:
        Complete native lesson ZIP bytes, retaining the legacy folder layout.
    """
    from mniplab.webserver.authoring_package import export_package

    return export_package(repository, document)
