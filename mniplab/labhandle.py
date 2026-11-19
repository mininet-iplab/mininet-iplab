"""The Lab handle: everything the web tier reads or writes through a Lab.

A :class:`LabHandle` is the seam between the web services and a running Lab.
Its interface is exactly the subset of Lab and Node calls the services make —
topology, status, Node attachment for a Terminal, Link Config, network tests,
routing queries, Service and Speaker reads and writes, switch VLAN operations
— and every answer is plain JSON-able data, so the same interface is served
in-process by :class:`LocalLabHandle` and across a process boundary by the
RPC client in :mod:`mniplab.labrpc`. No web service holds a network object.

The base class carries the signatures once; a subclass implements
:meth:`LabHandle._invoke`, which is also the RPC server's dispatch table.
"""

from __future__ import annotations

import glob
import ipaddress
import json
import os
import signal
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from mniplab.dhcp.leases import LeaseQueryService
from mniplab.dhcp.pool_service import PoolService
from mniplab.dhcp.relay_activity import RelayActivityQueryService
from mniplab.dns.control_client import KnotControlClient
from mniplab.dns.query import DNSQueryService
from mniplab.dns.record_service import DNSRecordService
from mniplab.errors import LinkError
from mniplab.exabgp.models import BGPRoute
from mniplab.exabgp.query import SpeakerQueryService
from mniplab.exabgp.routes import SpeakerRouteService
from mniplab.lab_key import derive_lab_key
from mniplab.logging import get_logger
from mniplab.netutils import NetworkTestService, RoutingQueryService, refuse_host_network
from mniplab.resolver.access import ResolverAccessService
from mniplab.resolver.models import AccessRule
from mniplab.resolver.query import ResolverQueryService
from mniplab.rpki.models import ASPA, ROA
from mniplab.rpki.objects import RPKIObjectService
from mniplab.rpki.query import RPKIQueryService
from mniplab.switch import SwitchService, parse_bridge_id
from mniplab.topology import TopologyBuilder
from mniplab.utils import host_network_refusal, is_drawn, is_hidden, lives_in_host_network

logger = get_logger(__name__)


@dataclass(frozen=True)
class NodeAttachment:
    """What a Terminal needs to attach a shell to one Node.

    Attributes:
        node: The Node's name.
        pid: A process in the Node's namespaces (the Namespace Anchor).
        home_dir: The Node's home directory on the host.
        container_name: The Docker container's name for a Container Host,
            ``None`` for every other Node.
    """

    node: str
    pid: int
    home_dir: str
    container_name: Optional[str] = None


class LabHandle:
    """The subset of a Lab the web services reach, as one object.

    Attributes:
        key: The Lab key naming this Lab's host-side objects.
        example: The Lab Example id the Lab was built from.
    """

    key: str
    example: str

    def _invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        """Perform one operation of the interface below.

        Args:
            operation: The public method's name.
            *args: Its positional arguments.
            **kwargs: Its keyword arguments.

        Returns:
            JSON-able data.
        """
        raise NotImplementedError

    # -- Lab reads ---------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return the Lab's node count and Link Condition drift count."""
        return self._invoke("status")

    def topology(self) -> Dict[str, Any]:
        """Return the Topology payload the web UI draws."""
        return self._invoke("topology")

    def node_attachment(self, node: str) -> NodeAttachment:
        """Return what a Terminal needs to attach to ``node``.

        Only a Node with a namespace of its own has one. A Switch and an
        Internet Gateway live in the host's, where a shell is a root shell on
        the machine every Lab shares.

        Raises:
            ValueError: The Node does not exist, lives in the Host machine's
                network, or has no PID.
        """
        return NodeAttachment(**self._invoke("node_attachment", node))

    def capture_refusal(self, node: str) -> Optional[str]:
        """Return why Packet Capture refuses ``node``, or None when it may proceed.

        Only a Node in the Host machine's network that is not a Switch is
        refused: a Switch is captured through its own ports. A Node the Lab
        does not have is left to the caller's own not-found message.

        Args:
            node: The Node's name.

        Returns:
            The reason to show the Learner, or None.
        """
        return self._invoke("capture_refusal", node)

    def list_interfaces(self, node: str) -> List[Dict[str, Any]]:
        """Return every Interface ``node`` has, whether or not its Link is drawn.

        Each entry carries the Interface's ``name``, its ``addresses``, the
        ``peer`` Node at the Link's far end when that Node is drawn, and
        whether the Link itself is ``drawn`` on the Topology.  A Link to a
        Node the Topology does not draw is listed with no peer, so that Node
        is never named; an Internet Gateway is drawn, so it is named.

        Args:
            node: The Node's name.

        Returns:
            One entry per Interface, in port order.

        Raises:
            ValueError: The Node does not exist.
        """
        return self._invoke("list_interfaces", node)

    def switch_ports(self, switch: str) -> List[Dict[str, Any]]:
        """Return the ports of ``switch`` a Packet Capture may be opened on.

        A Switch's ports live in the host's root namespace, so each entry
        names the port by the plain name a Learner reads (``name``) and
        carries the keyed interface that exists there (``interface``), the
        ``peer`` Node at the Link's far end when drawn, and whether the
        Link is ``drawn``.  Nothing else of the root namespace is listed.

        Args:
            switch: The Switch's name.

        Raises:
            ValueError: No such Switch in this Lab.
        """
        return self._invoke("switch_ports", switch)

    # -- Link Config -------------------------------------------------------

    def set_link_status(self, node1: str, node2: str, status: str) -> Dict[str, Any]:
        """Bring the link between two Nodes ``'up'`` or ``'down'``."""
        return self._invoke("set_link_status", node1, node2, status)

    def configure_link(
        self, node1: str, node2: str, delay: Any = None, jitter: Any = None,
        loss: Any = None, bandwidth: Any = None, scheduler: str = "hfsc",
    ) -> Dict[str, Any]:
        """Apply a Link Condition to the link between two Nodes."""
        return self._invoke(
            "configure_link", node1, node2, delay, jitter, loss, bandwidth, scheduler,
        )

    def get_link_conditions(self) -> List[Dict[str, Any]]:
        """Return the Lab's recorded Link Condition intent for review."""
        return self._invoke("get_link_conditions")

    # -- Network tests -----------------------------------------------------

    def run_ping(self, source: str, destination: str, count: int = 4,
                 ipv6: bool = False) -> Dict[str, Any]:
        """Ping ``destination`` from ``source``, over IPv6 when ``ipv6``."""
        return self._invoke("run_ping", source, destination, count, ipv6)

    def run_traceroute(self, source: str, destination: str, ipv6: bool = False,
                       resolve: bool = False) -> Dict[str, Any]:
        """Trace the route from ``source`` to ``destination``, naming hops when ``resolve``."""
        return self._invoke("run_traceroute", source, destination, ipv6, resolve)

    def run_iperf(self, server: str, client: str) -> Dict[str, Any]:
        """Measure bandwidth between two Nodes with iperf."""
        return self._invoke("run_iperf", server, client)

    def run_iperf3(
        self, server: str, client: str, seconds: int = 10,
        protocol: str = "tcp", json_output: bool = False,
    ) -> Dict[str, Any]:
        """Measure bandwidth between two Nodes with iperf3."""
        return self._invoke("run_iperf3", server, client, seconds, protocol, json_output)

    # -- Routing queries ---------------------------------------------------

    def get_node_routes(self, node: str) -> Dict[str, Any]:
        """Return a Node's routing table."""
        return self._invoke("get_node_routes", node)

    def get_node_ospf(self, node: str) -> Dict[str, Any]:
        """Return a Router's OSPF state."""
        return self._invoke("get_node_ospf", node)

    def get_node_bgp(self, node: str) -> Dict[str, Any]:
        """Return a Router's BGP state."""
        return self._invoke("get_node_bgp", node)

    def get_running_config(self, node: str) -> str:
        """Return a Router's FRR running configuration, unsaved changes included."""
        return self._invoke("get_running_config", node)

    # -- DHCP --------------------------------------------------------------

    def get_node_leases(self, node: str) -> Dict[str, Any]:
        """Return the Leases a Node's DHCP Service holds."""
        return self._invoke("get_node_leases", node)

    def get_node_relay_activity(self, node: str) -> Dict[str, Any]:
        """Return the requests a Node's Relay forwarded."""
        return self._invoke("get_node_relay_activity", node)

    def get_node_pools(self, node: str) -> Dict[str, Any]:
        """Return a Node's DHCP Pools."""
        return self._invoke("get_node_pools", node)

    def set_node_pool(
        self, node: str, cidr: str, start: str, end: str,
        lease_time: Optional[int] = None, preferred_lifetime: Optional[int] = None,
        router: str = "", dns_server: str = "",
        reservations: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Update one Pool's editable fields on the running server."""
        return self._invoke(
            "set_node_pool", node, cidr, start, end,
            lease_time=lease_time, preferred_lifetime=preferred_lifetime,
            router=router, dns_server=dns_server, reservations=reservations,
        )

    # -- DNS ---------------------------------------------------------------

    def list_dns_nodes(self) -> List[Dict[str, Any]]:
        """List every Node with an Authoritative DNS Service and its Zones."""
        return self._invoke("list_dns_nodes")

    def get_dns_records(self, node: str, zone: str) -> List[Dict[str, Any]]:
        """Return the active records of one hosted zone."""
        return self._invoke("get_dns_records", node, zone)

    def get_dns_zone_status(self, node: str, zone: str) -> Dict[str, Any]:
        """Return SOA serial and operational status for one hosted zone."""
        return self._invoke("get_dns_zone_status", node, zone)

    def get_dns_transfers(
        self, node: str, zone: Optional[str] = None, include_notify: bool = False,
    ) -> Dict[str, Any]:
        """Return the Transfer Log and replication state of a Node's Zones."""
        return self._invoke("get_dns_transfers", node, zone, include_notify)

    def refresh_dns_zone(self, node: str, zone: str) -> Dict[str, Any]:
        """Ask a Secondary whether its Primary's Serial has moved."""
        return self._invoke("refresh_dns_zone", node, zone)

    def retransfer_dns_zone(self, node: str, zone: str) -> Dict[str, Any]:
        """Ask a Secondary to take a full copy regardless of the Serial."""
        return self._invoke("retransfer_dns_zone", node, zone)

    def get_dns_stats(self, node: str) -> Dict[str, Any]:
        """Return a DNS Service's query counters."""
        return self._invoke("get_dns_stats", node)

    def reset_dns_stats(self, node: str) -> None:
        """Reset a DNS Service's query counter baseline."""
        self._invoke("reset_dns_stats", node)

    def set_dns_record(
        self, node: str, zone: str, owner: str, rtype: str, data: str,
        ttl: Optional[int] = None, old_owner: Optional[str] = None,
        old_type: Optional[str] = None, old_data: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add or replace a record in one atomic zone transaction."""
        return self._invoke(
            "set_dns_record", node, zone, owner, rtype, data, ttl,
            old_owner=old_owner, old_type=old_type, old_data=old_data,
        )

    def unset_dns_record(
        self, node: str, zone: str, owner: str,
        rtype: Optional[str] = None, data: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove a record in one atomic zone transaction."""
        return self._invoke("unset_dns_record", node, zone, owner, rtype, data)

    def get_dnssec_details(self, node: str, zone: str) -> Dict[str, Any]:
        """Return the DNSSEC state, keys and DS records of a hosted zone."""
        return self._invoke("get_dnssec_details", node, zone)

    def trigger_dnssec_rollover(self, node: str, zone: str, key_type: str = "zsk") -> Dict[str, Any]:
        """Trigger a DNSSEC key rollover for a hosted zone."""
        return self._invoke("trigger_dnssec_rollover", node, zone, key_type)

    # -- Resolver ----------------------------------------------------------

    def get_resolver_cache(self, node: str) -> Dict[str, Any]:
        """Return a Resolver's cache entries and query counters."""
        return self._invoke("get_resolver_cache", node)

    def flush_resolver_cache(
        self, node: str, target: Optional[str] = None, scope: str = "name",
    ) -> Dict[str, Any]:
        """Evict cached entries from a Resolver."""
        return self._invoke("flush_resolver_cache", node, target, scope)

    def get_resolver_access(self, node: str) -> List[Dict[str, Any]]:
        """Return a Resolver's Access Control rules, in declaration order."""
        return self._invoke("get_resolver_access", node)

    def set_resolver_access(self, node: str, rules: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Replace a Resolver's Access Control rules, hot."""
        return self._invoke("set_resolver_access", node, rules)

    # -- Speaker -----------------------------------------------------------

    def get_speaker(self, node: str) -> Dict[str, Any]:
        """Return a Speaker's Neighbors with both RIBs."""
        return self._invoke("get_speaker", node)

    def announce_route(
        self, node: str, fields: Dict[str, Any], neighbor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Announce a route, given as ``BGPRoute`` fields, from a Speaker."""
        return self._invoke("announce_route", node, fields, neighbor)

    def generate_routes(
        self, node: str, neighbor: Optional[str], count: Any,
        sub_prefixes: Any = None, prefix_seed: Any = None, as_path_seed: Any = None,
    ) -> Dict[str, Any]:
        """Announce a burst of generated routes from a Speaker."""
        return self._invoke(
            "generate_routes", node, neighbor, count, sub_prefixes, prefix_seed, as_path_seed,
        )

    def withdraw_route(self, node: str, prefix: str, neighbor: Optional[str] = None) -> Dict[str, Any]:
        """Withdraw one prefix from a Speaker."""
        return self._invoke("withdraw_route", node, prefix, neighbor)

    def withdraw_all_routes(self, node: str, neighbor: Optional[str] = None) -> Dict[str, Any]:
        """Withdraw everything a Speaker's Neighbor was sent."""
        return self._invoke("withdraw_all_routes", node, neighbor)

    # -- RPKI --------------------------------------------------------------

    def list_rpki_cas(self, node: str) -> List[Dict[str, Any]]:
        """Return each Hosted CA of an RPKI CA with its ROAs and ASPAs."""
        return self._invoke("list_rpki_cas", node)

    def get_rpki_hosted_ca(self, node: str, asn: Any) -> Dict[str, Any]:
        """Return one Hosted CA of an RPKI CA with its ROAs and ASPAs."""
        return self._invoke("get_rpki_hosted_ca", node, asn)

    def add_rpki_roa(self, node: str, asn: Any, roa: Dict[str, Any]) -> Dict[str, Any]:
        """Sign a ROA at a Hosted CA; ``roa`` holds ``prefix``, ``max_length`` and ``origin``."""
        return self._invoke("add_rpki_roa", node, asn, roa)

    def replace_rpki_roa(
        self, node: str, asn: Any, old: Dict[str, Any], new: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Withdraw a ROA and sign its replacement at a Hosted CA, in one delta."""
        return self._invoke("replace_rpki_roa", node, asn, old, new)

    def remove_rpki_roa(self, node: str, asn: Any, roa: Dict[str, Any]) -> Dict[str, Any]:
        """Withdraw a ROA from a Hosted CA."""
        return self._invoke("remove_rpki_roa", node, asn, roa)

    def add_rpki_aspa(self, node: str, asn: Any, aspa: Dict[str, Any]) -> Dict[str, Any]:
        """Sign an ASPA at a Hosted CA; ``aspa`` holds ``customer`` and ``providers``."""
        return self._invoke("add_rpki_aspa", node, asn, aspa)

    def remove_rpki_aspa(self, node: str, asn: Any, customer: Any) -> Dict[str, Any]:
        """Withdraw a customer's ASPA from a Hosted CA."""
        return self._invoke("remove_rpki_aspa", node, asn, customer)

    def get_rpki_vrps(self, node: str) -> Dict[str, Any]:
        """Return what a Validator serves: its VRPs, ASPAs, refresh times and connected Routers."""
        return self._invoke("get_rpki_vrps", node)

    # -- Switch ------------------------------------------------------------

    def get_switch_vlans(self, switch: str) -> Dict[str, Any]:
        """Return a switch's per-port VLAN configuration."""
        return self._invoke("get_switch_vlans", switch)

    def set_switch_port_vlan(
        self, switch: str, port: str, mode: str,
        vlan: Any = None, trunks: Any = None, native: Any = None,
    ) -> Dict[str, Any]:
        """Configure one switch port's VLAN mode and membership."""
        return self._invoke("set_switch_port_vlan", switch, port, mode, vlan, trunks, native)

    # -- Lifecycle ---------------------------------------------------------

    def stop(self) -> None:
        """Stop the Lab, cleaning up everything it created."""
        self._invoke("stop")

    def get_switch_mac_table(self, switch: str) -> Dict[str, Any]:
        """Return a Switch's learned MAC addresses, ports, VLANs, and ages."""
        return self._invoke("get_switch_mac_table", switch)

    def flush_switch_mac_table(self, switch: str) -> Dict[str, Any]:
        """Flush learned MAC addresses and return the Switch's current table."""
        return self._invoke("flush_switch_mac_table", switch)

    def get_switch_spanning_tree(self, switch: str) -> Dict[str, Any]:
        """Return the Switch's spanning-tree protocol, root, and port states."""
        return self._invoke("get_switch_spanning_tree", switch)

    def resolve_test_target(self, source: str, destination: str, ipv6: bool = False) -> str:
        """Check ``source`` and return the address or name a test of ``destination`` uses."""
        return self._invoke("resolve_test_target", source, destination, ipv6)


#: Every operation a handle serves, by name; the RPC server's allowlist.
OPERATIONS = frozenset(
    name for name, value in vars(LabHandle).items()
    if callable(value) and not name.startswith("_")
)


class LocalLabHandle(LabHandle):
    """A Lab handle over a network object in this process.

    Wraps a real ``mnIPLab`` — in the Lab's own process — or a test double,
    and answers every operation by running the Lab-side services against it.
    """

    def __init__(self, net: Any, example: Optional[str] = None, key: Optional[str] = None) -> None:
        """Wrap a network object.

        Args:
            net: The network — ``mnIPLab`` or a fake with its shape.
            example: The Lab Example id; ``"lab"`` when none is known.
            key: The Lab key; defaults to the network's own key, or a derived
                one when the network has none.
        """
        self.net = net
        net_key = getattr(net, "lab_key", None)
        self.key = key or (net_key if isinstance(net_key, str) else derive_lab_key())
        self.example = example or "lab"
        # The RPC server answers each call on its own thread, and the Node
        # shells are not thread-safe (shared waitOutput/monitor state), so
        # concurrent status/topology calls would interleave on the same
        # node.cmd() and corrupt each other. One operation at a time.
        self._op_lock = threading.Lock()

    def _invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        if operation not in OPERATIONS:
            raise ValueError(f"unknown Lab operation: {operation}")
        with self._op_lock:
            return getattr(self, f"_op_{operation}")(*args, **kwargs)

    def _node(self, name: str) -> Any:
        """Return the Node or switch called ``name``.

        Raises:
            ValueError: No such Node.
        """
        for node in list(self.net.hosts) + list(self.net.switches):
            if node.name == name:
                return node
        raise ValueError(f"Node not found: {name}")

    def _op_status(self) -> Dict[str, Any]:
        visible = [h for h in self.net.hosts if not is_hidden(h)]
        return {
            "nodes": len(visible) + len(self.net.switches),
            "link_condition_drift": self._count_link_condition_drift(),
        }

    def _count_link_condition_drift(self) -> int:
        """Count links whose applied condition diverges from the recorded intent.

        Failing to read traffic control is not itself drift, so it counts as
        zero — whatever the read raises, including a RuntimeError from a
        Node shell that is momentarily unreachable.
        """
        try:
            return len(self.net.link_condition_drift())
        except Exception:
            return 0

    def _op_topology(self) -> Dict[str, Any]:
        return TopologyBuilder.build(self.net)

    def _op_node_attachment(self, node_name: str) -> Dict[str, Any]:
        node = self._node(node_name)
        if lives_in_host_network(node):
            raise ValueError(host_network_refusal(node_name, node in self.net.switches))
        pid = getattr(node, "pid", None)
        if not pid:
            raise ValueError(f"Cannot get PID for node: {node_name}")
        params = getattr(node, "params", None) or {}
        home_dir = params.get("homeDir", self.net.node_home_dir(node_name))
        return asdict(NodeAttachment(
            node=node_name,
            pid=int(pid),
            home_dir=home_dir,
            container_name=getattr(node, "container_name", None) if _is_container(node) else None,
        ))

    def _op_capture_refusal(self, node_name: str) -> Optional[str]:
        # A Switch is not among the hosts, so its ports are never refused here.
        node = next((h for h in self.net.hosts if h.name == node_name), None)
        if node is None or not lives_in_host_network(node):
            return None
        return host_network_refusal(node_name, is_switch=False)

    def _op_list_interfaces(self, node_name: str) -> List[Dict[str, Any]]:
        node = self._node(node_name)
        peers = {}
        for link in getattr(self.net, "links", []):
            for near, far in ((link.intf1, link.intf2), (link.intf2, link.intf1)):
                peers[id(near)] = far.node
        interfaces = []
        for intf in node.intfList():
            peer = peers.get(id(intf))
            drawn = bool(peer is not None and is_drawn(peer))
            addresses = [
                address for address in (intf.IP(), getattr(intf, "IP6", lambda: None)())
                if address
            ]
            interfaces.append({
                "name": intf.name,
                "addresses": addresses,
                "peer": peer.name if peer is not None and drawn else None,
                "drawn": drawn,
            })
        return interfaces

    def _op_switch_ports(self, switch_name: str) -> List[Dict[str, Any]]:
        switch = next((s for s in self.net.switches if s.name == switch_name), None)
        if switch is None or is_hidden(switch):
            raise ValueError(f"Node not found: {switch_name}")
        peers = {}
        for link in getattr(self.net, "links", []):
            for near, far in ((link.intf1, link.intf2), (link.intf2, link.intf1)):
                peers[near.name] = far.node
        ports = []
        for plain, keyed in switch.capture_ports().items():
            peer = peers.get(keyed)
            drawn = bool(peer is not None and is_drawn(peer))
            ports.append({
                "name": plain,
                "interface": keyed,
                "peer": peer.name if peer is not None and drawn else None,
                "drawn": drawn,
            })
        return ports

    def _op_set_link_status(self, node1: str, node2: str, status: str) -> Dict[str, Any]:
        # An Internet Gateway's interface lives in the Host machine's network,
        # so the Lab does not bring it down or up. An unknown Node is left to
        # configLinkStatus, which reports it.
        for name in (node1, node2):
            try:
                node = self.net.get(name)
            except KeyError:
                node = None
            refuse_host_network(self.net, name, node)
        try:
            self.net.configLinkStatus(node1, node2, status)
        except (LinkError, KeyError, AttributeError, RuntimeError, TypeError) as e:
            # LinkError covers an unknown node, an unregistered link, and a
            # refused interface change. Without it those escape as a 500
            # instead of reaching the client as a message.
            raise ValueError(f"Failed to {status} link: {e}") from e
        operation = "disconnected" if status == "down" else "reconnected"
        return {"success": True, "status": operation, "link": f"{node1} <-> {node2}"}

    def _op_configure_link(self, *args: Any) -> Dict[str, Any]:
        return NetworkTestService.configure_link(self.net, *args)

    def _op_get_link_conditions(self) -> List[Dict[str, Any]]:
        return [condition.__dict__.copy()
                for condition in getattr(self.net, "link_conditions", [])]

    def _op_run_ping(self, *args: Any) -> Dict[str, Any]:
        return NetworkTestService.run_ping(self.net, *args)

    def _op_run_traceroute(self, *args: Any) -> Dict[str, Any]:
        return NetworkTestService.run_traceroute(self.net, *args)

    def _op_run_iperf(self, *args: Any) -> Dict[str, Any]:
        return NetworkTestService.run_iperf(self.net, *args)

    def _op_run_iperf3(self, *args: Any) -> Dict[str, Any]:
        return NetworkTestService.run_iperf3(self.net, *args)

    def _op_get_node_routes(self, node: str) -> Dict[str, Any]:
        return RoutingQueryService.get_node_routes(self.net, node)

    def _op_get_node_ospf(self, node: str) -> Dict[str, Any]:
        return RoutingQueryService.get_node_ospf(self.net, node)

    def _op_get_node_bgp(self, node: str) -> Dict[str, Any]:
        return RoutingQueryService.get_node_bgp(self.net, node)

    def _op_get_running_config(self, node: str) -> str:
        result = RoutingQueryService.run_vtysh(self.net, node, "show running-config")
        if not result["success"]:
            raise ValueError(result["detail"])
        return result["output"]

    def _op_get_node_leases(self, node: str) -> Dict[str, Any]:
        return LeaseQueryService.get_leases(self.net, node)

    def _op_get_node_relay_activity(self, node: str) -> Dict[str, Any]:
        return RelayActivityQueryService.get_activity(self.net, node)

    def _op_get_node_pools(self, node: str) -> Dict[str, Any]:
        return PoolService.get_pools(self.net, node)

    def _op_set_node_pool(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return PoolService.set_pool(self.net, *args, **kwargs)

    def _op_list_dns_nodes(self) -> List[Dict[str, Any]]:
        return DNSQueryService.list_dns_nodes(self.net)

    def _op_get_dns_records(self, node: str, zone: str) -> List[Dict[str, Any]]:
        return DNSQueryService.get_dns_records(self.net, node, zone)

    def _op_get_dns_zone_status(self, node: str, zone: str) -> Dict[str, Any]:
        return DNSQueryService.get_dns_zone_status(self.net, node, zone)

    def _op_get_dns_transfers(self, *args: Any) -> Dict[str, Any]:
        return DNSQueryService.get_dns_transfers(self.net, *args)

    def _op_refresh_dns_zone(self, node: str, zone: str) -> Dict[str, Any]:
        return DNSQueryService.refresh_zone(self.net, node, zone)

    def _op_retransfer_dns_zone(self, node: str, zone: str) -> Dict[str, Any]:
        return DNSQueryService.retransfer_zone(self.net, node, zone)

    def _op_get_dns_stats(self, node: str) -> Dict[str, Any]:
        return DNSQueryService.get_dns_stats(self.net, node)

    def _op_reset_dns_stats(self, node: str) -> None:
        DNSQueryService.reset_dns_stats(self.net, node)

    def _op_set_dns_record(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return DNSRecordService.set_record(self.net, *args, **kwargs)

    def _op_unset_dns_record(self, *args: Any) -> Dict[str, Any]:
        return DNSRecordService.unset_record(self.net, *args)

    def _op_get_dnssec_details(self, node: str, zone: str) -> Dict[str, Any]:
        return DNSQueryService.get_zone_dnssec(self.net, node, zone)

    def _op_trigger_dnssec_rollover(self, node: str, zone: str, key_type: str) -> Dict[str, Any]:
        return DNSRecordService.trigger_dnssec_rollover(self.net, node, zone, key_type)

    def _op_get_resolver_cache(self, node: str) -> Dict[str, Any]:
        # Both come from the same control socket and the Cache Inspector
        # shows them side by side, so one read fetches both.
        entries = ResolverQueryService.get_cache(self.net, node)
        metrics = ResolverQueryService.get_metrics(self.net, node)
        return {
            "entries": [entry.to_dict() for entry in entries],
            "metrics": metrics.to_dict(),
        }

    def _op_flush_resolver_cache(self, *args: Any) -> Dict[str, Any]:
        return ResolverQueryService.flush_cache(self.net, *args)

    def _op_get_resolver_access(self, node: str) -> List[Dict[str, Any]]:
        return ResolverAccessService.list_rules(self.net, node)

    def _op_set_resolver_access(self, node: str, rules: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        parsed = [AccessRule(rule["subnet"], rule["action"]) for rule in rules]
        return ResolverAccessService.set_rules(self.net, node, parsed)

    def _op_get_speaker(self, node: str) -> Dict[str, Any]:
        return SpeakerQueryService.get_speaker(self.net, node)

    def _op_announce_route(self, node: str, fields: Dict[str, Any], neighbor: Optional[str]) -> Dict[str, Any]:
        return SpeakerRouteService.announce(self.net, node, BGPRoute(**fields), neighbor=neighbor)

    def _op_generate_routes(
        self, node: str, neighbor: Optional[str], count: Any,
        sub_prefixes: Any, prefix_seed: Any, as_path_seed: Any,
    ) -> Dict[str, Any]:
        return SpeakerRouteService.generate(
            self.net, node, neighbor, count,
            sub_prefixes=sub_prefixes, prefix_seed=prefix_seed, as_path_seed=as_path_seed,
        )

    def _op_withdraw_route(self, node: str, prefix: str, neighbor: Optional[str]) -> Dict[str, Any]:
        return SpeakerRouteService.withdraw(self.net, node, prefix, neighbor=neighbor)

    def _op_withdraw_all_routes(self, node: str, neighbor: Optional[str]) -> Dict[str, Any]:
        return SpeakerRouteService.withdraw_all(self.net, node, neighbor=neighbor)

    def _op_list_rpki_cas(self, node: str) -> List[Dict[str, Any]]:
        return RPKIQueryService.list_cas(self.net, node)

    def _op_get_rpki_hosted_ca(self, node: str, asn: Any) -> Dict[str, Any]:
        return RPKIQueryService.get_hosted_ca(self.net, node, asn)

    def _op_add_rpki_roa(self, node: str, asn: Any, roa: Dict[str, Any]) -> Dict[str, Any]:
        return RPKIObjectService.add_roa(self.net, node, asn, _rpki_roa(roa))

    def _op_replace_rpki_roa(
        self, node: str, asn: Any, old: Dict[str, Any], new: Dict[str, Any],
    ) -> Dict[str, Any]:
        return RPKIObjectService.replace_roa(self.net, node, asn, _rpki_roa(old), _rpki_roa(new))

    def _op_remove_rpki_roa(self, node: str, asn: Any, roa: Dict[str, Any]) -> Dict[str, Any]:
        return RPKIObjectService.remove_roa(self.net, node, asn, _rpki_roa(roa))

    def _op_add_rpki_aspa(self, node: str, asn: Any, aspa: Dict[str, Any]) -> Dict[str, Any]:
        return RPKIObjectService.add_aspa(self.net, node, asn, ASPA(**aspa))

    def _op_remove_rpki_aspa(self, node: str, asn: Any, customer: Any) -> Dict[str, Any]:
        return RPKIObjectService.remove_aspa(self.net, node, asn, customer)

    def _op_get_rpki_vrps(self, node: str) -> Dict[str, Any]:
        return RPKIQueryService.get_vrps(self.net, node)

    def _op_get_switch_vlans(self, switch: str) -> Dict[str, Any]:
        node = SwitchService._get_switch(self.net, switch)
        if hasattr(node, "get_plain_vlans"):
            return {"switch": switch, "ports": node.get_plain_vlans()}
        return SwitchService.get_vlans(self.net, switch)

    def _op_set_switch_port_vlan(
        self, switch: str, port: str, mode: str, vlan: Any, trunks: Any, native: Any,
    ) -> Dict[str, Any]:
        return SwitchService.set_port_vlan(
            self.net, switch, port, mode, vlan=vlan, trunks=trunks, native=native,
        )

    def _op_stop(self) -> None:
        """Stop the network, falling back to the keyed emergency sweep.

        The key is passed explicitly: a network object without one would
        otherwise turn the fallback into the host-wide sweep.
        """
        self._interrupt_prompt_commands()
        try:
            self.net.stop()
        except Exception as e:
            logger.warning(f"net.stop() raised {e!r}; falling back to emergency_cleanup")
            from mniplab.cleanup import emergency_cleanup
            emergency_cleanup(net=self.net, lab_key=self.key)
        finally:
            KnotControlClient.clear_baselines()

    @property
    def node_lock(self) -> threading.Lock:
        """The lock an operation holds while it uses the Node shells."""
        return self._op_lock

    def _interrupt_prompt_commands(self, grace: float = 5.0) -> None:
        """End a command the Lab Prompt still runs, before the Lab stops.

        Operations run one at a time under the node lock, so a Node busy now
        is running a Prompt command, a count-less ping left running, say.
        Ctrl+C ends it; a command that ignores Ctrl+C is killed, but not the
        Node's shell, which still ends the command so the Prompt returns.
        Stopping the Nodes under a running command would leave the Prompt
        waiting for its end forever.

        Args:
            grace: Seconds the Prompt has to collect the output, after Ctrl+C
                and again after a kill.
        """
        busy = [node for node in list(self.net.hosts) + list(self.net.switches)
                if getattr(node, "waiting", False) and getattr(node, "shell", None)]
        for node in busy:
            try:
                node.sendInt()
            except (OSError, ValueError):  # its shell closed meanwhile
                pass
        if _wait_until_idle(busy, grace):
            return
        for node in busy:
            if node.waiting:
                _kill_descendants(node.pid)
        _wait_until_idle(busy, grace)

    def _op_get_switch_mac_table(self, switch: str) -> Dict[str, Any]:
        return {"switch": switch,
                "entries": SwitchService._get_switch(self.net, switch).get_mac_table()}

    def _op_flush_switch_mac_table(self, switch: str) -> Dict[str, Any]:
        node = SwitchService._get_switch(self.net, switch)
        node.flush_mac_table()
        return {"switch": switch, "entries": node.get_mac_table()}

    def _op_get_switch_spanning_tree(self, switch: str) -> Dict[str, Any]:
        node = SwitchService._get_switch(self.net, switch)
        tree = node.get_spanning_tree()
        tree["root_name"] = self._root_switch_name(node, tree)
        tree["is_root"] = bool(tree["protocol"]) and tree["bridge_id"] == tree["root_bridge"]
        return {"switch": switch, **tree}

    def _root_switch_name(self, node: Any, tree: Dict[str, Any]) -> Optional[str]:
        """Name the Switch of this Lab whose bridge MAC is the tree's root.

        One ``ovs-vsctl`` call lists every bridge on the host; only bridges
        of this Lab's Switches are considered, so a Switch of another Lab
        with the same bridge ID is never named.
        """
        if not tree.get("protocol") or not tree.get("root_mac"):
            return None
        ours = {str(sw.bridge_name): sw.name for sw in self.net.switches
                if str(sw.bridge_name).startswith(f"{self.key}-")}
        output = node.cmd("ovs-vsctl --format=json --columns=name,stp_enable,rstp_enable,"
                          "status,rstp_status list Bridge")
        try:
            table = json.loads(output)
            for row in table["data"]:
                values = dict(zip(table["headings"], row))
                if values["name"] not in ours:
                    continue
                rstp = values["rstp_enable"]
                status = dict(values["rstp_status" if rstp else "status"][1])
                bridge_id = status.get("rstp_bridge_id" if rstp else "stp_bridge_id")
                if parse_bridge_id(bridge_id)[1] == tree["root_mac"]:
                    return ours[values["name"]]
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        return None

    def _op_resolve_test_target(self, *args: Any) -> str:
        return NetworkTestService.resolve_test_target(self.net, *args)


def _wait_until_idle(nodes: List[Any], timeout: float) -> bool:
    """Wait until no Node in ``nodes`` is running a command; say whether none is."""
    deadline = time.monotonic() + timeout
    while any(node.waiting for node in nodes):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _kill_descendants(pid: int) -> None:
    """Kill every process below ``pid``, leaving ``pid`` itself running."""
    descendants: List[int] = []
    parents = [pid]
    while parents:
        parent = parents.pop()
        for listing in glob.glob(f"/proc/{parent}/task/*/children"):
            try:
                with open(listing, encoding="ascii") as source:
                    children = [int(child) for child in source.read().split()]
            except OSError:
                continue
            descendants.extend(children)
            parents.extend(children)
    for child in descendants:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _is_container(node: Any) -> bool:
    """Tell a Container Host from every other Node without importing Docker.

    ``mniplab.container`` pulls in the Docker client, an import the web tier
    should only pay when a Container Host is actually in the Lab.
    """
    return any(cls.__name__ == "ContainerHost" for cls in type(node).__mro__)


def as_handle(lab: Any) -> LabHandle:
    """Return ``lab`` as a handle: itself if it is one, wrapped otherwise.

    Args:
        lab: A handle, or a network object to serve in-process.
    """
    if isinstance(lab, LabHandle):
        return lab
    return LocalLabHandle(lab, example=_script_name())


def _rpki_roa(fields: Dict[str, Any]) -> ROA:
    """Build a ROA from a request's fields, with maxLength defaulting to the prefix's length.

    Args:
        fields: ``prefix`` and ``origin``, and ``max_length`` when given.

    Returns:
        The ROA.

    Raises:
        ValueError: If the prefix, maxLength or origin is invalid.
    """
    prefix = str(fields.get("prefix"))
    max_length: Any = fields.get("max_length")
    if max_length is None:
        max_length = ipaddress.ip_network(prefix, strict=False).prefixlen
    origin: Any = fields.get("origin")
    return ROA(prefix=prefix, max_length=max_length, origin=origin)


def _script_name() -> str:
    """Return the running script's stem, the Lab Example id in CLI Mode."""
    import sys
    return os.path.splitext(os.path.basename(sys.argv[0]))[0] if sys.argv and sys.argv[0] else "lab"
