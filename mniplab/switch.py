"""Manageable Open vSwitch bridge and 802.1Q VLAN support for mnIPLab."""

from __future__ import annotations

import ipaddress
import json
import shlex
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from mininet.node import OVSBridge
from mininet.link import Intf
from mniplab.constants import VLAN_MIN, VLAN_MAX
from mniplab.errors import ConfigurationError
from mniplab.link import IPv6Intf
from mniplab.logging import get_logger

logger = get_logger(__name__)

#: Why a Switch Interface takes no address, and where the address belongs.
SWITCH_ADDRESS_HINT = (
    "a Switch is layer 2 and runs in the host's own namespace, so an address on "
    "its Interface would land on the host machine. Put the address on the "
    "Host or Internet Gateway end of the Link instead."
)


def validate_vlan_id(vlan: Any, name: str = "VLAN ID") -> int:
    """Validate that a VLAN ID is an integer within 1-4094.

    Args:
        vlan: VLAN ID to validate (int or str converting to int).
        name: Parameter name for error messages.

    Returns:
        The validated integer VLAN ID.

    Raises:
        ValueError: If vlan is not an integer or outside the range 1-4094.
    """
    try:
        val = int(vlan)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be an integer, got {vlan!r}") from exc
    if not (VLAN_MIN <= val <= VLAN_MAX):
        raise ValueError(
            f"{name} {val} is out of valid range ({VLAN_MIN}-{VLAN_MAX})"
        )
    return val


def parse_vlan_list(vlan_input: Union[str, Sequence[Union[int, str]]]) -> List[int]:
    """Parse a list or comma-separated string of VLAN IDs.

    Args:
        vlan_input: Sequence of ints/strings or comma-separated string (e.g. "10,20").

    Returns:
        List of validated integer VLAN IDs.

    Raises:
        ValueError: If any VLAN ID is invalid or list is empty.
    """
    if isinstance(vlan_input, str):
        tokens = [t.strip() for t in vlan_input.split(",") if t.strip()]
    elif isinstance(vlan_input, (list, tuple, set)):
        tokens = list(vlan_input)
    else:
        raise ValueError(f"Expected string or sequence of VLAN IDs, got {type(vlan_input).__name__}")

    if not tokens:
        raise ValueError("At least one VLAN ID must be specified")

    return [validate_vlan_id(t) for t in tokens]


def parse_bridge_id(bridge_id: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    """Split an Open vSwitch spanning-tree bridge ID into priority and MAC.

    Args:
        bridge_id: ``1.000.0000000000aa`` (RSTP) or ``1000.0000000000aa`` (STP):
            the priority in hexadecimal, then the MAC address.

    Returns:
        The priority and the colon-separated MAC, or ``(None, None)`` when the
        ID is absent or not in either form.
    """
    if not bridge_id or "." not in bridge_id:
        return None, None
    head, _, tail = bridge_id.rpartition(".")
    try:
        priority = int(head.replace(".", ""), 16)
        int(tail, 16)
    except ValueError:
        return None, None
    if len(tail) != 12:
        return None, None
    return priority, ":".join(tail[i:i + 2] for i in range(0, 12, 2)).lower()


def resolve_peer_info(intf: Any) -> Tuple[Optional[str], Optional[str]]:
    """Resolve peer node name and peer interface name for an interface.

    Args:
        intf: Mininet interface object.

    Returns:
        Tuple of (peer_node_name, peer_intf_name).
    """
    link = getattr(intf, "link", None)
    if not link:
        return None, None
    peer = getattr(link, "intf1", None) if getattr(link, "intf2", None) == intf else getattr(link, "intf2", None)
    if not peer:
        return None, None
    peer_intf = getattr(peer, "name", None)
    peer_node = getattr(peer.node, "name", None) if getattr(peer, "node", None) else None
    return peer_node, peer_intf


class SubinterfaceMixin:
    """Mixin providing 802.1Q sub-interface creation on Linux nodes."""

    def add_subinterface(
        self: Any,
        parent_intf: Union[str, Intf],
        vlan: Union[int, str],
        ip: Optional[str] = None,
        ip6: Optional[str] = None,
    ) -> IPv6Intf:
        """Create a Linux 802.1Q sub-interface on this node.

        Args:
            parent_intf: Parent interface name or Intf object (e.g. 'r1-eth1').
            vlan: 802.1Q VLAN tag ID (1-4094).
            ip: Optional IPv4 address with prefix length (e.g. '192.168.10.1/24').
            ip6: Optional IPv6 address with prefix length (e.g. '2001:db8:10::1/64').

        Returns:
            The created interface object.

        Raises:
            ValueError: If vlan ID is invalid or address format is invalid.
        """
        vlan_id = validate_vlan_id(vlan)
        parent_name = parent_intf.name if hasattr(parent_intf, "name") else str(parent_intf)
        sub_name = f"{parent_name}.{vlan_id}"

        # Validate IP addresses before executing commands
        if ip:
            if "/" not in ip:
                raise ValueError(
                    f"No prefix length set for IPv4 address {ip}. Use format 'ip/prefix', e.g. '{ip}/24'"
                )
            ipaddress.IPv4Interface(ip)

        if ip6:
            if "/" not in ip6:
                raise ValueError(
                    f"No prefix length set for IPv6 address {ip6}. Use format 'ip6/prefix', e.g. '{ip6}/64'"
                )
            ipaddress.IPv6Interface(ip6)

        quoted_parent = shlex.quote(parent_name)
        quoted_sub = shlex.quote(sub_name)

        # Create sub-interface in the node's namespace and bring it up
        self.cmd(f"ip link set dev {quoted_parent} up")
        self.cmd(f"ip link add link {quoted_parent} name {quoted_sub} type vlan id {vlan_id}")
        self.cmd(f"ip link set dev {quoted_sub} up")

        # Assign IPv4 address if provided
        if ip:
            quoted_ip = shlex.quote(ip)
            self.cmd(f"ip addr add {quoted_ip} dev {quoted_sub}")

        # Assign IPv6 address if provided
        if ip6:
            quoted_ip6 = shlex.quote(ip6)
            self.cmd(f"ip -6 addr add {quoted_ip6} dev {quoted_sub}")

        # Register interface with Mininet tracking
        intf = IPv6Intf(name=sub_name, node=self, moveIntfFn=lambda *_: None, up=False)
        if ip:
            intf.ip, intf.prefixLen = ip.split("/", 1)
        if ip6:
            address, _, prefix = ip6.partition("/")
            intf.ip6, intf.prefixLen6 = address, prefix

        if hasattr(self, "intfs"):
            port_num = len(self.intfs)
            while port_num in self.intfs:
                port_num += 1
            self.intfs[port_num] = intf
            if hasattr(self, "ports"):
                self.ports[intf] = port_num
        if hasattr(self, "nameToIntf"):
            self.nameToIntf[sub_name] = intf

        logger.debug(
            "Created sub-interface %s on %s (vlan=%d, ip=%s, ip6=%s)",
            sub_name,
            getattr(self, "name", "node"),
            vlan_id,
            ip,
            ip6,
        )
        return intf


class ManagedSwitch(OVSBridge):
    """Open vSwitch bridge with manageable 802.1Q VLAN capabilities.

    The Node name is what a Learner sees; the bridge in the host's one
    Open vSwitch database is named separately so two Labs declaring the
    same switch never share a bridge. Mininet's own ``ovs-vsctl`` calls
    format the switch with ``%s``, which is why :meth:`__str__` answers
    with the bridge name.
    """

    def __init__(self, name: str, bridge_name: Optional[str] = None,
                 stp: Union[bool, str] = False, priority: Optional[int] = None,
                 **kwargs: Any) -> None:
        """Initialize ManagedSwitch.

        Args:
            name: Switch name.
            bridge_name: Open vSwitch bridge name; defaults to ``name``.
            stp: False for off, True for classic STP, or 'rstp' for rapid STP.
            priority: Bridge priority when spanning tree is enabled. RSTP uses
                multiples of 4096 from 0 to 61440; STP accepts 0 to 65535.
            **kwargs: Arguments passed to OVSBridge.
        """
        self.validate_spanning_tree(stp, priority)
        self.priority = priority
        super().__init__(name, stp=stp, **kwargs)
        self.bridge_name: str = bridge_name or name
        self._port_vlans: Dict[str, Dict[str, Any]] = {}

    def __str__(self) -> str:
        """Return the bridge name, which every ``ovs-vsctl`` call addresses."""
        return self.bridge_name

    def resolve_port(self, port: Union[str, Intf, int]) -> str:
        """Resolve a port to its interface name.

        A port's interface lives in the root namespace and carries the Lab
        key (``<bridge>-ethN``); the unkeyed ``<switch>-ethN`` a Guide or a
        Learner writes is accepted as the same port.

        Args:
            port: Port identifier (Intf, int, or str).

        Returns:
            String interface name.
        """
        if hasattr(port, "name"):
            return str(port.name)
        if isinstance(port, int):
            intf = self.intfs.get(port)
            if intf:
                return str(intf.name)
            return str(port)
        name = str(port)
        known = getattr(self, "nameToIntf", {})
        if name not in known and name.startswith(f"{self.name}-"):
            keyed = f"{self.bridge_name}{name[len(self.name):]}"
            if keyed in known or keyed in self._port_vlans:
                return keyed
        return name

    def unkeyed_port(self, port: str) -> str:
        """Return the unkeyed ``<switch>-ethN`` name of a keyed port name.

        The inverse of :meth:`resolve_port`; names that do not carry the
        bridge prefix come back untouched.

        Args:
            port: Interface name, keyed or not.

        Returns:
            The name a Guide or a Learner would write.
        """
        prefix = f"{self.bridge_name}-"
        if self.bridge_name != self.name and port.startswith(prefix):
            return f"{self.name}-{port[len(prefix):]}"
        return port

    def set_access(self, port: Union[str, Intf, int], vlan: Union[int, str]) -> None:
        """Configure a port as an 802.1Q access port.

        Args:
            port: Port name, Intf, or port number.
            vlan: VLAN ID (1-4094).

        Raises:
            ValueError: If vlan is outside 1-4094.
        """
        vlan_id = validate_vlan_id(vlan)
        port_name = self.resolve_port(port)
        quoted_port = shlex.quote(port_name)
        self.vsctl(
            f"set port {quoted_port} tag={vlan_id} vlan_mode=access trunks=[]"
        )
        self._port_vlans[port_name] = {
            "port": port_name,
            "mode": "access",
            "tag": vlan_id,
            "trunks": [],
            "native": None,
        }
        logger.debug(
            "Configured %s port %s as access VLAN %d", self.name, port_name, vlan_id
        )

    def set_trunk(
        self,
        port: Union[str, Intf, int],
        trunks: Union[str, Sequence[Union[int, str]]],
        native: Optional[Union[int, str]] = None,
    ) -> None:
        """Configure a port as an 802.1Q trunk port.

        Args:
            port: Port name, Intf, or port number.
            trunks: List of VLAN IDs (1-4094) or comma-separated string.
            native: Optional native untagged VLAN ID (1-4094).

        Raises:
            ValueError: If any VLAN ID is invalid.
        """
        trunk_list = parse_vlan_list(trunks)
        port_name = self.resolve_port(port)
        quoted_port = shlex.quote(port_name)
        trunks_str = "[" + ",".join(str(v) for v in trunk_list) + "]"

        if native is not None:
            native_id = validate_vlan_id(native, name="Native VLAN ID")
            self.vsctl(
                f"set port {quoted_port} tag={native_id} vlan_mode=native-untagged trunks={trunks_str}"
            )
            self._port_vlans[port_name] = {
                "port": port_name,
                "mode": "trunk",
                "tag": None,
                "trunks": trunk_list,
                "native": native_id,
            }
            logger.debug(
                "Configured %s port %s as trunk VLANs %s with native %d",
                self.name,
                port_name,
                trunks_str,
                native_id,
            )
        else:
            self.vsctl(
                f"set port {quoted_port} tag=[] vlan_mode=trunk trunks={trunks_str}"
            )
            self._port_vlans[port_name] = {
                "port": port_name,
                "mode": "trunk",
                "tag": None,
                "trunks": trunk_list,
                "native": None,
            }
            logger.debug(
                "Configured %s port %s as trunk VLANs %s",
                self.name,
                port_name,
                trunks_str,
            )

    def clear_vlan(self, port: Union[str, Intf, int]) -> None:
        """Clear VLAN configuration on a port, reverting to untagged bridging.

        Args:
            port: Port name, Intf, or port number.
        """
        port_name = self.resolve_port(port)
        quoted_port = shlex.quote(port_name)
        self.vsctl(f"clear port {quoted_port} tag trunks vlan_mode")
        self._port_vlans[port_name] = {
            "port": port_name,
            "mode": "none",
            "tag": None,
            "trunks": [],
            "native": None,
        }
        logger.debug("Cleared VLAN configuration on %s port %s", self.name, port_name)

    def get_vlans(self) -> Dict[str, Dict[str, Any]]:
        """Query switch ports and return structured VLAN metadata.

        Returns:
            Dictionary mapping port names to their VLAN configuration:
            {
                "s1-eth1": {
                    "port": "s1-eth1",
                    "mode": "access",  # "access" | "trunk" | "none"
                    "tag": 10,
                    "trunks": [],
                    "native": None,
                    "peer_node": "h1",
                    "peer_intf": "h1-eth0"
                }, ...
            }
        """
        results: Dict[str, Dict[str, Any]] = {}

        # 1. Determine ports on this switch
        quoted_switch = shlex.quote(self.bridge_name)
        ovs_ports_out = self.cmd(f"ovs-vsctl list-ports {quoted_switch}").strip()
        port_names = [p.strip() for p in ovs_ports_out.split() if p.strip()]

        # Fallback to Mininet's interface registry or tracked state if list-ports returned empty
        if not port_names and hasattr(self, "nameToIntf"):
            port_names = [p for p in self.nameToIntf if p not in (self.name, self.bridge_name)]
        if not port_names and hasattr(self, "_port_vlans"):
            port_names = list(self._port_vlans.keys())

        for port_name in port_names:
            # Query OVS for port settings
            quoted_port = shlex.quote(port_name)
            ovs_out = self.cmd(f"ovs-vsctl get port {quoted_port} tag vlan_mode trunks").strip()
            lines = [l.strip() for l in ovs_out.split("\n") if l.strip()]

            mode = "none"
            tag: Optional[int] = None
            trunks: List[int] = []
            native: Optional[int] = None

            if len(lines) >= 3 and not any("error" in l.lower() for l in lines):
                raw_tag = lines[0]
                raw_mode = lines[1].strip('"')
                raw_trunks = lines[2]

                if raw_tag != "[]":
                    try:
                        parsed_tag = int(raw_tag)
                    except ValueError:
                        parsed_tag = None
                else:
                    parsed_tag = None

                if raw_trunks.startswith("[") and raw_trunks.endswith("]"):
                    inner = raw_trunks[1:-1].strip()
                    if inner:
                        for t in inner.split(","):
                            if t.strip():
                                try:
                                    trunks.append(int(t.strip()))
                                except ValueError:
                                    pass

                if raw_mode == "access" or (raw_mode == "[]" and parsed_tag is not None and not trunks):
                    mode = "access"
                    tag = parsed_tag
                elif raw_mode in ("trunk", "native-untagged", "native-tagged") or (raw_mode == "[]" and trunks):
                    mode = "trunk"
                    native = parsed_tag
                else:
                    mode = "none"
            elif port_name in self._port_vlans:
                # Fallback to tracked state if OVS query returned empty / error
                cached = self._port_vlans[port_name]
                mode = cached["mode"]
                tag = cached["tag"]
                trunks = list(cached["trunks"])
                native = cached["native"]

            # Peer resolution via Mininet link interface
            intf = getattr(self, "nameToIntf", {}).get(port_name)
            peer_node, peer_intf = resolve_peer_info(intf) if intf else (None, None)
            if peer_node is None and port_name in self._port_vlans:
                cached = self._port_vlans[port_name]
                peer_node = cached.get("peer_node")
                peer_intf = cached.get("peer_intf")

            results[port_name] = {
                "port": port_name,
                "mode": mode,
                "tag": tag,
                "trunks": trunks,
                "native": native,
                "peer_node": peer_node,
                "peer_intf": peer_intf,
            }

        return results

    def get_plain_vlans(self) -> Dict[str, Dict[str, Any]]:
        """Return :meth:`get_vlans` with ports and peer interfaces unkeyed.

        Returns:
            The same rows, keyed and labelled ``<switch>-ethN``.
        """
        plain: Dict[str, Dict[str, Any]] = {}
        for port_name, row in self.get_vlans().items():
            shown = self.unkeyed_port(port_name)
            plain[shown] = {**row, "port": shown}
            peer, peer_intf = row.get("peer_node"), row.get("peer_intf")
            lab_prefix = self.bridge_name[:len(self.bridge_name) - len(self.name)]
            if peer and peer_intf and lab_prefix and peer_intf.startswith(f"{lab_prefix}{peer}-"):
                plain[shown]["peer_intf"] = peer_intf[len(lab_prefix):]
        return plain

    def setIP(self, ip: str, prefixLen: Optional[int] = None,
              intf: Optional[Union[str, Intf]] = None, **kwargs: Any) -> None:
        """Refuse an address: a Switch Interface carries none.

        Raises:
            ConfigurationError: Always, naming the Link the Interface is on.
        """
        port = self.intf(intf)
        link = getattr(port, 'link', None)
        where = (f"Link {link.intf1.node.name}–{link.intf2.node.name}: " if link else "")
        raise ConfigurationError(
            f"{where}Switch '{self.name}' cannot take address {ip}: {SWITCH_ADDRESS_HINT}"
        )

    @staticmethod
    def validate_spanning_tree(stp: Union[bool, str], priority: Optional[int]) -> None:
        """Validate a Switch's spanning-tree choice and bridge priority.

        Args:
            stp: False, True (classic STP), or 'rstp'.
            priority: Optional bridge priority for the enabled protocol.

        Raises:
            ValueError: If the protocol or priority is unsupported.
        """
        if stp is not False and stp is not True and stp != "rstp":
            raise ValueError("Spanning tree must be off, True (STP), or 'rstp'")
        if priority is None:
            return
        if stp is False:
            raise ValueError("Switch priority requires spanning tree")
        limit = 61440 if stp == "rstp" else 65535
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= limit:
            raise ValueError(f"Switch priority must be an integer from 0 to {limit}")
        if stp == "rstp" and priority % 4096:
            raise ValueError("RSTP priority must be a multiple of 4096")

    def bridgeOpts(self) -> str:
        """Return Mininet's bridge options with the selected spanning tree."""
        options = super().bridgeOpts()
        if self.stp == "rstp":
            options = options.replace(" stp_enable=true", " stp_enable=false rstp_enable=true")
        if self.priority is not None:
            protocol = "rstp" if self.stp == "rstp" else "stp"
            options += f" other_config:{protocol}-priority={self.priority}"
        return options

    def get_spanning_tree(self) -> Dict[str, Any]:
        """Read the active protocol, root bridge, and each port's role and state.

        Returns:
            Protocol (or None when off), bridge/root identifiers, and port rows.

        Raises:
            ValueError: If Open vSwitch cannot return bridge or port status.
        """
        bridge = shlex.quote(self.bridge_name)
        output = self.cmd(
            f"ovs-vsctl --format=json --columns=stp_enable,rstp_enable,status,rstp_status "
            f"list Bridge {bridge}"
        )
        try:
            table = json.loads(output)
            values = dict(zip(table["headings"], table["data"][0]))
            protocol = "rstp" if values["rstp_enable"] else "stp" if values["stp_enable"] else None
            status = dict(values["rstp_status" if protocol == "rstp" else "status"][1])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unable to read spanning tree on Switch '{self.name}'") from exc
        result: Dict[str, Any] = {
            "protocol": protocol,
            "bridge_id": status.get("rstp_bridge_id" if protocol == "rstp" else "stp_bridge_id"),
            "root_bridge": status.get("rstp_root_id" if protocol == "rstp" else "stp_designated_root"),
            "ports": [],
        }
        result["priority"], result["mac"] = parse_bridge_id(result["bridge_id"])
        result["root_priority"], result["root_mac"] = parse_bridge_id(result["root_bridge"])
        if protocol is None:
            return result
        ports = self.cmd(f"ovs-vsctl list-ports {bridge}").split()
        if not ports:
            return result
        names = " ".join(shlex.quote(port) for port in ports)
        output = self.cmd(
            f"ovs-vsctl --format=json --columns=name,status,rstp_status list Port {names}"
        )
        try:
            table = json.loads(output)
            for row in table["data"]:
                values = dict(zip(table["headings"], row))
                status = dict(values["rstp_status" if protocol == "rstp" else "status"][1])
                role = status.get("rstp_port_role" if protocol == "rstp" else "stp_role", "")
                state = status.get("rstp_port_state" if protocol == "rstp" else "stp_state", "")
                result["ports"].append({
                    "port": self.unkeyed_port(values["name"]), "role": role.title(),
                    "state": "Discarding" if state.lower() == "blocking" else state.title(),
                })
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unable to read spanning-tree ports on Switch '{self.name}'") from exc
        return result

    def get_mac_table(self) -> List[Dict[str, Any]]:
        """Read the Switch's learned MAC addresses, ports, VLANs, and ages.

        Returns:
            One row per learned MAC address, with age in seconds.

        Raises:
            ValueError: If Open vSwitch cannot return the MAC address table.
        """
        output = self.cmd(f"ovs-appctl fdb/show {shlex.quote(self.bridge_name)}")
        lines = output.splitlines()
        if not lines or lines[0].split() != ["port", "VLAN", "MAC", "Age"]:
            raise ValueError(f"Unable to read MAC address table on Switch '{self.name}'")
        entries: List[Dict[str, Any]] = []
        try:
            for line in lines[1:]:
                if not line.strip():
                    continue
                port, vlan, mac, age = line.split()
                interface = self.intfs.get(int(port)) if port.isdigit() else None
                entries.append({"mac": mac, "port": self.unkeyed_port(interface.name if interface else port),
                                "vlan": int(vlan), "age": int(age)})
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Unable to read MAC address table on Switch '{self.name}'") from exc
        return entries

    def flush_mac_table(self) -> None:
        """Flush learned MAC addresses so traffic teaches the Switch again.

        Raises:
            ValueError: If Open vSwitch refuses the Flush.
        """
        output = self.cmd(f"ovs-appctl fdb/flush {shlex.quote(self.bridge_name)}")
        if output.strip() not in ("", "table successfully flushed"):
            raise ValueError(f"Unable to flush MAC address table on Switch '{self.name}'")

    def capture_ports(self) -> Dict[str, str]:
        """Return the ports a Packet Capture may be opened on.

        A port is a veth end in the host's root namespace, which every Lab
        and the host share, so only an interface this Switch itself owns
        and that carries its bridge's prefix is listed: not the bridge's
        internal port, the loopback, or anything of another Lab.

        Returns:
            The plain ``<switch>-ethN`` name of each port, mapped to the
            keyed interface name that exists in the root namespace.
        """
        prefix = f"{self.bridge_name}-"
        ports: Dict[str, str] = {}
        for keyed, intf in getattr(self, "nameToIntf", {}).items():
            if keyed in ("lo", self.bridge_name) or not keyed.startswith(prefix):
                continue
            if getattr(intf, "name", None) != keyed:
                continue
            ports[self.unkeyed_port(keyed)] = keyed
        return ports


class SwitchService:
    """Service for managing switch VLAN configurations."""

    @staticmethod
    def _get_switch(lab: Any, switch_name: str) -> Any:
        """Find switch node in the lab.

        Args:
            lab: Running lab instance.
            switch_name: Name of the switch.

        Returns:
            Switch node instance.

        Raises:
            ValueError: If switch is not found.
        """
        for sw in getattr(lab, "switches", []):
            if sw.name == switch_name:
                return sw

        try:
            candidate = lab.get(switch_name) if hasattr(lab, "get") else None
        except (KeyError, ValueError, Exception):
            candidate = None

        if candidate and (hasattr(candidate, "get_vlans") or hasattr(candidate, "set_access")):
            return candidate

        raise ValueError(f"Switch '{switch_name}' not found")

    @staticmethod
    def _verify_port(lab: Any, switch: Any, port_name: str) -> None:
        """Verify that a port exists on the switch.

        Args:
            lab: Running lab instance.
            switch: Switch node.
            port_name: Port interface name.

        Raises:
            ValueError: If port does not exist on switch.
        """
        port_found = False
        if hasattr(switch, "nameToIntf") and port_name in switch.nameToIntf:
            port_found = True
        elif hasattr(switch, "_port_vlans") and port_name in switch._port_vlans:
            port_found = True
        elif hasattr(switch, "intfs") and hasattr(switch, "intfList"):
            for intf in switch.intfList():
                if getattr(intf, "name", None) == port_name:
                    port_found = True
                    break
        elif hasattr(switch, "intfs") and isinstance(switch.intfs, dict):
            for intf in switch.intfs.values():
                if getattr(intf, "name", None) == port_name:
                    port_found = True
                    break

        if not port_found and hasattr(lab, "links"):
            for link in getattr(lab, "links", []):
                for intf in (getattr(link, "intf1", None), getattr(link, "intf2", None)):
                    if intf and getattr(intf, "name", None) == port_name:
                        node = getattr(intf, "node", None)
                        if node == switch or getattr(node, "name", None) == switch.name:
                            port_found = True
                            break

        if not port_found and hasattr(switch, "cmd"):
            try:
                out = switch.cmd(f"ovs-vsctl list-ports {shlex.quote(str(switch))}")
                if port_name in out.split():
                    port_found = True
            except Exception:
                pass

        if not port_found:
            raise ValueError(f"Port '{port_name}' not found on switch '{switch.name}'")


    @staticmethod
    def get_vlans(lab: Any, switch_name: str) -> Dict[str, Any]:
        """Query switch ports and return structured VLAN configuration.

        Args:
            lab: Running lab instance.
            switch_name: Name of the switch.

        Returns:
            Dictionary with switch name and ports mapping.
        """
        switch = SwitchService._get_switch(lab, switch_name)
        if hasattr(switch, "get_vlans"):
            vlans = switch.get_vlans()
        else:
            vlans = {}
            if hasattr(switch, "intfList"):
                for intf in switch.intfList():
                    if intf.name != switch.name:
                        vlans[intf.name] = {
                            "port": intf.name,
                            "mode": "none",
                            "tag": None,
                            "trunks": [],
                            "native": None,
                        }
        return {"switch": switch_name, "ports": vlans}

    @staticmethod
    def set_port_vlan(
        lab: Any,
        switch_name: str,
        port_name: str,
        mode: str,
        vlan: Optional[Any] = None,
        trunks: Optional[Any] = None,
        native: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Configure a switch port's VLAN mode and membership.

        Args:
            lab: Running lab instance.
            switch_name: Name of the switch.
            port_name: Name of the port.
            mode: Mode ('access', 'trunk', or 'none').
            vlan: VLAN ID for access mode.
            trunks: VLAN IDs for trunk mode.
            native: Optional native VLAN for trunk mode.

        Returns:
            Dictionary with the updated port configuration and all ports.
        """
        switch = SwitchService._get_switch(lab, switch_name)
        if hasattr(switch, "resolve_port"):
            port_name = switch.resolve_port(port_name)
        SwitchService._verify_port(lab, switch, port_name)

        normalized_mode = mode.strip().lower()
        if normalized_mode not in ("access", "trunk", "none"):
            raise ValueError(
                f"Invalid VLAN mode '{mode}'. Must be 'access', 'trunk', or 'none'."
            )

        if normalized_mode == "access":
            if vlan is None:
                raise ValueError("VLAN ID is required for access mode")
            vlan_id = validate_vlan_id(vlan)
            switch.set_access(port_name, vlan_id)
        elif normalized_mode == "trunk":
            if trunks is None:
                raise ValueError("Trunk VLAN IDs are required for trunk mode")
            trunk_list = parse_vlan_list(trunks)
            native_id = None
            if native is not None:
                native_id = validate_vlan_id(native, name="Native VLAN ID")
            switch.set_trunk(port_name, trunk_list, native=native_id)
        elif normalized_mode == "none":
            switch.clear_vlan(port_name)

        if hasattr(switch, "get_plain_vlans"):
            vlans = switch.get_plain_vlans()
            port_name = switch.unkeyed_port(port_name)
        else:
            vlans = switch.get_vlans() if hasattr(switch, "get_vlans") else {}
        return {
            "success": True,
            "switch": switch_name,
            "port": port_name,
            "config": vlans.get(port_name, {}),
            "ports": vlans,
        }
