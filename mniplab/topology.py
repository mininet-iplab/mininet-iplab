"""Topology payload: what the web UI draws, read from a Lab's Nodes and links."""

import ipaddress
from typing import Any, Dict, List, Optional, Set, Tuple

from mniplab.errors import LinkError
from mniplab.logging import get_logger
from mniplab.routernat import router_translators
from mniplab.utils import is_drawn, is_hidden

logger = get_logger(__name__)


class TopologyBuilder:
    """Builds topology JSON from a running mnIPLab instance.
    
    Stateless — can be instantiated once and reused.
    """

    @staticmethod
    def _link_condition(lab: Any, n1_name: str, n2_name: str) -> dict:
        """Return the recorded Link Condition as a payload fragment.

        Reads the authoritative record rather than the interface's creation
        parameters: Mininet sets ``Intf.params`` once at construction and never
        updates it, so impairment applied later never appeared there.

        Args:
            lab: The running Lab.
            n1_name: First node name.
            n2_name: Second node name.

        Returns:
            The condition as a dict, falling back to a plain 'up' status when
            no condition was recorded for this link.
        """
        try:
            return lab.link_conditions.get(n1_name, n2_name).to_dict()
        except (AttributeError, LinkError):
            return {"status": lab.get_link_condition(n1_name, n2_name)}

    @staticmethod
    def _link_params(condition: dict) -> dict:
        """Render a Link Condition's impairment in the payload's field names.

        The web UI reads ``params.bw`` for bandwidth, so that key is kept
        exactly as clients already expect it — the rename to Link Condition
        was internal and must not reach the wire.

        Args:
            condition: A serialized Link Condition.

        Returns:
            Impairment fields present on the condition, under payload names.
        """
        params = {
            key: condition[key]
            for key in ("delay", "jitter", "loss")
            if key in condition
        }
        if "bandwidth" in condition:
            params["bw"] = condition["bandwidth"]
        return params

    @staticmethod
    def _is_valid_link(link: Any) -> bool:
        """Check if a link has valid, populated interfaces.

        Args:
            link: Mininet link instance.

        Returns:
            True if both intf1 and intf2 exist and have nodes.
        """
        return bool(
            link
            and hasattr(link, 'intf1')
            and hasattr(link, 'intf2')
            and link.intf1
            and link.intf2
            and hasattr(link.intf1, 'node')
            and hasattr(link.intf2, 'node')
        )

    @staticmethod
    def build(lab: Any) -> dict:
        """Build topology data for visualization.
        
        Args:
            lab: mnIPLab network instance (must not be None)
            
        Returns:
            Dict with 'nodes' and 'links' lists
        """
        nodes = []
        links = []

        visible_hosts = [host for host in lab.hosts if is_drawn(host)]

        # Add hosts
        for host in visible_hosts:
            node_type = getattr(host, 'display_type', 'host')
            ip = ""
            try:
                if hasattr(host, 'IP') and host.defaultIntf():
                    ip = host.IP()
            except (AttributeError, IndexError):
                pass

            # Every Interface's address, not just the default one: a
            # multi-homed Node (one Interface a DHCP client, another static)
            # needs all of them for the Web UI to tell a Lease an Interface
            # has applied from one it hasn't, regardless of which Interface.
            ips: List[str] = []
            try:
                for intf in host.intfList():
                    addr = intf.IP()
                    if isinstance(addr, str) and addr:
                        ips.append(addr)
                    if hasattr(intf, 'IP6'):
                        addr6 = intf.IP6()
                        if isinstance(addr6, str) and addr6:
                            ips.append(addr6)
            except (AttributeError, IndexError, TypeError):
                pass

            host_services = getattr(host, 'services', None)
            if host_services and isinstance(host_services, (list, tuple)):
                services = [
                    s.name
                    for s in host_services
                    if getattr(s, 'name', None)
                ]
                service_details = []
                for s in host_services:
                    if not getattr(s, 'name', None):
                        continue
                    detail: Dict[str, Any] = {
                        "name": s.name,
                        "badge": getattr(s, 'badge_text', s.name.upper()),
                        "families": getattr(s, 'families', []),
                    }
                    if s.name == "dns" and hasattr(s, "zones"):
                        zones_dict = getattr(s, "zones", {})
                        if isinstance(zones_dict, dict):
                            detail["zones"] = list(zones_dict.keys())
                    service_details.append(detail)
            else:
                services = []
                service_details = []

            host_relays = getattr(host, 'relays', None)
            if host_relays and isinstance(host_relays, (list, tuple)):
                relays = [
                    {"subnets": list(relay.subnets)}
                    for relay in host_relays
                    if getattr(relay, 'subnets', None)
                ]
            else:
                relays = []

            nodes.append({
                "id": host.name,
                "type": node_type,
                "label": host.name,
                "ip": ip,
                "ips": ips,
                "services": services,
                "service_details": service_details,
                "relays": relays,
            })

        # Add switches
        for switch in lab.switches:
            nodes.append({
                "id": switch.name,
                "type": "switch",
                "label": switch.name,
                "ip": "",
                "ips": [],
                "services": [],
                "service_details": [],
                "relays": [],
            })

        # Collect switch VLAN mappings
        switch_vlans: Dict[str, Dict[str, Any]] = {}
        for sw in getattr(lab, "switches", []):
            if hasattr(sw, "get_vlans"):
                try:
                    switch_vlans[sw.name] = sw.get_vlans()
                except Exception as exc:
                    logger.debug(f"Failed to query VLANs from switch {sw.name}: {exc}")
                    switch_vlans[sw.name] = {}

        # Add links
        for link in lab.links:
            if not TopologyBuilder._is_valid_link(link):
                continue
            try:
                n1 = link.intf1.node
                n2 = link.intf2.node
                if not is_drawn(n1) or not is_drawn(n2):
                    continue
                n1_name = n1.name
                n2_name = n2.name

                condition = TopologyBuilder._link_condition(lab, n1_name, n2_name)

                source_vlan = None
                target_vlan = None
                if n1_name in switch_vlans and link.intf1:
                    port_cfg = switch_vlans[n1_name].get(link.intf1.name)
                    if port_cfg:
                        source_vlan = {
                            "mode": port_cfg.get("mode", "none"),
                            "tag": port_cfg.get("tag"),
                            "trunks": port_cfg.get("trunks", []),
                            "native": port_cfg.get("native"),
                        }
                if n2_name in switch_vlans and link.intf2:
                    port_cfg = switch_vlans[n2_name].get(link.intf2.name)
                    if port_cfg:
                        target_vlan = {
                            "mode": port_cfg.get("mode", "none"),
                            "tag": port_cfg.get("tag"),
                            "trunks": port_cfg.get("trunks", []),
                            "native": port_cfg.get("native"),
                        }

                link_vlan = None
                if source_vlan and source_vlan.get("mode") != "none":
                    link_vlan = source_vlan
                elif target_vlan and target_vlan.get("mode") != "none":
                    link_vlan = target_vlan
                elif source_vlan or target_vlan:
                    link_vlan = source_vlan or target_vlan

                links.append({
                    "source": n1_name,
                    "target": n2_name,
                    "source_intf": TopologyBuilder._shown_intf(n1, link.intf1),
                    "target_intf": TopologyBuilder._shown_intf(n2, link.intf2),
                    "source_addresses": TopologyBuilder._intf_addresses(link.intf1),
                    "target_addresses": TopologyBuilder._intf_addresses(link.intf2),
                    "status": condition.get("status", "up"),
                    "params": TopologyBuilder._link_params(condition),
                    "source_vlan": source_vlan,
                    "target_vlan": target_vlan,
                    "vlan": link_vlan,
                })
            except (AttributeError, TypeError):
                pass

        relays = TopologyBuilder._resolve_relays(lab, visible_hosts)

        return {
            "nodes": nodes,
            "links": links,
            "relays": relays,
            "translators": TopologyBuilder._translators(lab),
        }

    @staticmethod
    def _intf_addresses(intf: Any) -> List[str]:
        """Return the addresses an Interface has, IPv4 then IPv6.

        A Link end shows these beside its Interface name, so the Learner reads
        both ends of a Link to an Internet Gateway.

        Args:
            intf: The interface, or None.

        Returns:
            The addresses that are set, without prefix lengths.
        """
        if not intf:
            return []
        addresses = []
        for getter in ("IP", "IP6"):
            address = getattr(intf, getter, lambda: None)()
            if isinstance(address, str) and address:
                addresses.append(address)
        return addresses

    @staticmethod
    def _shown_intf(node: Any, intf: Any) -> str:
        """Return the interface name a Learner sees on a Link's end.

        A Switch port's interface carries the Lab key; it is shown as the
        plain ``<switch>-ethN``.

        Args:
            node: The Node owning the interface.
            intf: The interface, or None.

        Returns:
            The display name, or an empty string without an interface.
        """
        if not intf:
            return ""
        unkeyed = getattr(node, "unkeyed_port", None)
        return unkeyed(intf.name) if unkeyed else intf.name

    @staticmethod
    def _get_candidate_ips(candidate: Any) -> List[ipaddress._BaseAddress]:
        """Extract all valid IP addresses from a host's interfaces.

        Args:
            candidate: A Mininet host or node instance.

        Returns:
            List of parsed IP address objects.
        """
        ips: List[ipaddress._BaseAddress] = []
        try:
            for intf in candidate.intfList():
                for getter in ('IP', 'IP6'):
                    if hasattr(intf, getter):
                        addr_val = getattr(intf, getter)()
                        if isinstance(addr_val, str) and addr_val:
                            clean = addr_val.split('/')[0].strip()
                            if clean:
                                try:
                                    ips.append(ipaddress.ip_address(clean))
                                except ValueError:
                                    pass
        except (AttributeError, IndexError, TypeError):
            pass
        return ips

    @staticmethod
    def _resolve_relays(lab: Any, visible_hosts: List[Any]) -> List[Dict[str, Any]]:
        """Resolve relay definitions into structured topology relay entities.

        Resolves each relay's server_ip to target_node, resolves covered subnets
        to client_nodes, and identifies intermediate switches when client hosts
        reach the relay router through an L2 switch. Identical endpoints are
        unified into a single multi-family relay path.

        Args:
            lab: The running Lab instance.
            visible_hosts: List of non-hidden hosts in the lab.

        Returns:
            List of resolved relay dictionaries.
        """
        resolved_relays: List[Dict[str, Any]] = []
        all_hosts = getattr(lab, 'hosts', [])

        for host in visible_hosts:
            host_relays = getattr(host, 'relays', None)
            if not host_relays or not isinstance(host_relays, (list, tuple)):
                continue

            for idx, relay in enumerate(host_relays):
                raw_subnets = list(getattr(relay, 'subnets', []))
                if not raw_subnets:
                    continue

                server_ip = getattr(relay, 'server', None)
                if not server_ip and hasattr(relay, 'servers') and relay.servers:
                    server_ip = next(iter(relay.servers.values()), None)
                server_ip_str = str(server_ip).strip() if server_ip else ""

                relay_id = (
                    f"relay-{host.name}"
                    if len(host_relays) == 1
                    else f"relay-{host.name}-{idx + 1}"
                )

                # Resolve target_node and reachability
                target_node: Optional[str] = None
                reachable = False

                if server_ip_str:
                    try:
                        target_addr = ipaddress.ip_address(server_ip_str)
                        for candidate in all_hosts:
                            if is_hidden(candidate):
                                continue
                            if target_addr in TopologyBuilder._get_candidate_ips(candidate):
                                target_node = candidate.name
                                reachable = True
                                break
                    except ValueError:
                        pass

                # Resolve subnets, families, and client_nodes
                families: List[int] = []
                subnet_objs: List[ipaddress._BaseNetwork] = []
                for s in raw_subnets:
                    try:
                        net = ipaddress.ip_network(s, strict=False)
                        subnet_objs.append(net)
                        if net.version not in families:
                            families.append(net.version)
                    except ValueError:
                        pass
                families.sort()

                client_nodes_set: Set[str] = set()
                switches = getattr(lab, 'switches', [])
                links = getattr(lab, 'links', [])
                switch_names = {getattr(sw, 'name', str(sw)) for sw in switches}

                # 1. IP-based resolution for static hosts
                for candidate in all_hosts:
                    if candidate.name == host.name or is_hidden(candidate):
                        continue
                    cand_ips = TopologyBuilder._get_candidate_ips(candidate)
                    if any(cand_ip in net for cand_ip in cand_ips for net in subnet_objs):
                        client_nodes_set.add(candidate.name)

                # 2. Link-based topology resolution for subnets (including DHCP clients without static IPs)
                router_subnet_intfs = set()
                try:
                    for intf in host.intfList():
                        intf_name = getattr(intf, 'name', '')
                        intf_ips: List[ipaddress._BaseAddress] = []
                        for getter in ('IP', 'IP6'):
                            if hasattr(intf, getter):
                                val = getattr(intf, getter)()
                                if isinstance(val, str) and val:
                                    clean = val.split('/')[0].strip()
                                    if clean:
                                        try:
                                            intf_ips.append(ipaddress.ip_address(clean))
                                        except ValueError:
                                            pass
                        if any(ip_obj in net for ip_obj in intf_ips for net in subnet_objs):
                            router_subnet_intfs.add(intf_name)
                except (AttributeError, TypeError):
                    pass

                intermediate_switch: Optional[str] = None
                if links:
                    for link in links:
                        if not TopologyBuilder._is_valid_link(link):
                            continue
                        try:
                            n1_name = getattr(link.intf1.node, 'name', str(link.intf1.node))
                            n2_name = getattr(link.intf2.node, 'name', str(link.intf2.node))
                            i1_name = getattr(link.intf1, 'name', '')
                            i2_name = getattr(link.intf2, 'name', '')

                            is_router_link = False
                            neighbor_node = None
                            if n1_name == host.name and (not router_subnet_intfs or i1_name in router_subnet_intfs):
                                is_router_link = True
                                neighbor_node = link.intf2.node
                            elif n2_name == host.name and (not router_subnet_intfs or i2_name in router_subnet_intfs):
                                is_router_link = True
                                neighbor_node = link.intf1.node

                            if is_router_link and neighbor_node:
                                neighbor_name = getattr(neighbor_node, 'name', str(neighbor_node))
                                if neighbor_name in switch_names:
                                    intermediate_switch = neighbor_name
                                    # Find all non-hidden client hosts connected to this switch
                                    for sublink in links:
                                        if not TopologyBuilder._is_valid_link(sublink):
                                            continue
                                        sub_n1 = sublink.intf1.node
                                        sub_n2 = sublink.intf2.node
                                        sub_n1_name = getattr(sub_n1, 'name', str(sub_n1))
                                        sub_n2_name = getattr(sub_n2, 'name', str(sub_n2))
                                        if sub_n1_name == intermediate_switch and sub_n2_name != host.name and sub_n2_name not in switch_names:
                                            if not is_hidden(sub_n2):
                                                client_nodes_set.add(sub_n2_name)
                                        elif sub_n2_name == intermediate_switch and sub_n1_name != host.name and sub_n1_name not in switch_names:
                                            if not is_hidden(sub_n1):
                                                client_nodes_set.add(sub_n1_name)
                                elif neighbor_name != host.name and not is_hidden(neighbor_node):
                                    client_nodes_set.add(neighbor_name)
                        except (AttributeError, TypeError):
                            pass

                # If intermediate switch wasn't found directly via router_subnet_intfs, check via existing client_nodes
                if not intermediate_switch and client_nodes_set and switches and links:
                    for switch in switches:
                        switch_name = getattr(switch, 'name', str(switch))
                        connected_to_router = False
                        connected_to_client = False
                        for link in links:
                            if not TopologyBuilder._is_valid_link(link):
                                continue
                            try:
                                n1_name = getattr(link.intf1.node, 'name', str(link.intf1.node))
                                n2_name = getattr(link.intf2.node, 'name', str(link.intf2.node))
                                pair = {n1_name, n2_name}
                                if switch_name in pair and host.name in pair:
                                    connected_to_router = True
                                if switch_name in pair and any(c in pair for c in client_nodes_set):
                                    connected_to_client = True
                            except (AttributeError, TypeError):
                                pass
                        if connected_to_router and connected_to_client:
                            intermediate_switch = switch_name
                            break

                client_nodes = sorted(list(client_nodes_set))

                resolved_relays.append({
                    "id": relay_id,
                    "relay_node": host.name,
                    "server_ip": server_ip_str,
                    "target_node": target_node,
                    "reachable": reachable,
                    "subnets": raw_subnets,
                    "client_nodes": client_nodes,
                    "intermediate_switch": intermediate_switch,
                    "families": families,
                })

        # Deduplicate and unify relays between identical endpoints
        unified_relays: List[Dict[str, Any]] = []
        seen_endpoints: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
        for r in resolved_relays:
            key = (r["relay_node"], r["target_node"] or r["server_ip"])
            if key in seen_endpoints:
                existing = seen_endpoints[key]
                combined_families = sorted(list(set(existing["families"] + r["families"])))
                existing["families"] = combined_families
                for s in r["subnets"]:
                    if s not in existing["subnets"]:
                        existing["subnets"].append(s)
                combined_clients = sorted(list(set(existing["client_nodes"] + r["client_nodes"])))
                existing["client_nodes"] = combined_clients
                if not existing["intermediate_switch"] and r["intermediate_switch"]:
                    existing["intermediate_switch"] = r["intermediate_switch"]
            else:
                seen_endpoints[key] = r
                unified_relays.append(r)

        return unified_relays

    @staticmethod
    def _translators(lab: Any) -> List[Dict[str, str]]:
        """List the Routers that translate: each outside Interface its Router NAT declares.

        An Internet Gateway names nothing here. A Router shows a NAT Table only
        for translation the Lab declares, never for the Gateway's plumbing.

        Args:
            lab: The Lab whose Nodes are read.

        Returns:
            ``{"node", "interface"}`` per translating Router and Interface.
        """
        return [
            {"node": node.name, "interface": intf_name}
            for host in lab.hosts
            for node, intf_name in router_translators(host)
        ]
