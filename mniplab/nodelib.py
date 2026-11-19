"""Custom node classes for Mininet-IPLab.

Provides lightweight, purpose-built nodes that avoid the side effects of
the vanilla Mininet equivalents (e.g. no /etc/network/interfaces writes,
no network-manager restarts).
"""

from __future__ import annotations

import ipaddress
import shlex
from typing import Any, List, Optional, Union

from mininet.log import info
from mininet.node import Node
from mniplab.kernel import acquire_ip_forward_lease, ip_forward_holder, release_ip_forward_lease
from mniplab.logging import get_logger
from mniplab.shell import BinarySafeShellMixin

logger = get_logger(__name__)

# iptables constants
IPTABLES_INITIAL_MARK = 100
IPTABLES_TABLE_NAT = 'nat'
IPTABLES_TABLE_MANGLE = 'mangle'
IPTABLES_TABLE_RAW = 'raw'
IPTABLES_CHAIN_POSTROUTING = 'POSTROUTING'
IPTABLES_CHAIN_PREROUTING = 'PREROUTING'
IPTABLES_ACTION_MASQUERADE = 'MASQUERADE'

# 'iptables -C' reports existence via exit status and prints nothing to
# stdout, so presence is echoed as this sentinel for cmd() to return.
RULE_PRESENT_SENTINEL = 'MNIPLAB_RULE_PRESENT'


class MasqueradeNAT(BinarySafeShellMixin, Node):
    """Lightweight masquerade NAT gateway for root-namespace internet bridging.

    Unlike Mininet's built-in ``NAT`` class, this implementation:

    - Does **not** write to ``/etc/network/interfaces``.
    - Does **not** restart ``network-manager``.
    - Uses per-instance iptables marks so multiple NAT gateways in the same
      lab do not conflict with each other; the Lab allocates them from its
      key, so gateways of different Labs on one host do not conflict either.
    - Holds root ``ip_forward`` through a host-wide lease, so the first Lab
      to stop never restores it under another Lab's gateway.
    - Always runs in the root network namespace (``inNamespace=False``).

    Traffic path::

        [FRR router namespace]  <-- veth -->  [root namespace: MasqueradeNAT]  --> internet

    The node marks incoming traffic from the Mininet-facing interface via
    ``iptables mangle PREROUTING``, then masquerades all marked packets in
    ``iptables nat POSTROUTING``.  This approach is source-IP agnostic: it
    works regardless of the customer prefixes carried through the FRR routers.

    To seal its Lab on a shared NAT link subnet, the Gateway isolates its
    traffic in the root namespace with policy routing and conntrack zones:
    the mark is saved into the connection (``CONNMARK --save-mark``) on
    egress and restored (``CONNMARK --restore-mark``) on return — in
    ``mangle PREROUTING`` for a reply arriving on an uplink and in
    ``mangle OUTPUT`` for one the host itself sends, since a local socket's
    reply never passes PREROUTING and is re-routed only once it carries the
    mark — a policy routing rule (``ip rule fwmark <mark> lookup <mark>``)
    directs marked return traffic to a dedicated routing table holding the
    link route over this Gateway's veth, and incoming traffic from the Lab
    is assigned to an isolated conntrack zone (``CT --zone-orig <mark >> 8>``)
    derived from the Lab key to prevent 5-tuple connection collisions
    between Labs.

    Example usage (via the ``mnIPLab.addNAT`` wrapper)::

        nat = net.addNAT('nat1', subnet='192.168.100.0/24')
        net.addLink(nat, isp1, params1={'ip': '192.168.100.1/24'},
                    params2={'ip': '192.168.100.2/24'})

    Args:
        name: Node name.
        subnet: Optional CIDR subnet string (stored as metadata; not used in
            iptables rules directly but useful for documentation/tooling).
        peer_default_gw: When ``True`` (default), ``mnIPLab.addLink`` will
            automatically set a ``defaultRoute`` on the peer node pointing
            at this NAT's IP.  Set to ``False`` to manage the default route
            manually.
        mark: The iptables mark for this gateway's traffic; ``mnIPLab.addNAT``
            allocates one from the Lab key. Without one, a process-wide
            counter from 100 is used.
        lab_key: The owning Lab's key, naming this gateway's claim on root
            ``ip_forward``.
        **params: Additional parameters forwarded to :class:`mininet.node.Node`.
    """

    #: How the Topology draws this Node: an Internet Gateway is the cloud the
    #: Lab's traffic leaves by, not a device a Learner can open.
    display_type = "internet"

    #: Class-level counter for allocating unique iptables marks per instance.
    _next_mark: int = IPTABLES_INITIAL_MARK

    # Explicit type annotations for instance attributes (mypy guidance)
    _local_intf_name: Optional[str]
    _local_subnet: Optional[str]
    _ip_forward_holder: Optional[str]
    _peer_node: Optional[Any]
    _peer_intf_name: Optional[str]
    _terminated: bool

    @classmethod
    def _alloc_mark(cls) -> int:
        """Allocate a unique iptables mark for this instance."""
        mark = cls._next_mark
        cls._next_mark += 1
        return mark

    def _build_iptables_cmd(self, action: str, table: str, chain: str, rule: str) -> str:
        """Build iptables command string.
        
        Args:
            action: 'A' (append) or 'D' (delete)
            table: iptables table name ('nat', 'mangle')
            chain: iptables chain name ('PREROUTING', 'POSTROUTING')
            rule: iptables rule specification
            
        Returns:
            Complete iptables command string
        """
        return f'iptables -t {table} -{action} {chain} {rule}'

    def _add_masquerade_rule(self, node, intf_name: str) -> None:
        """Add masquerade rule to node's outbound interface.
        
        Args:
            node: Node to configure
            intf_name: Interface name
        """
        rule = f'-o {intf_name} -j {IPTABLES_ACTION_MASQUERADE}'
        cmd = self._build_iptables_cmd('A', IPTABLES_TABLE_NAT, IPTABLES_CHAIN_POSTROUTING, rule)
        node.cmd(cmd)

    @classmethod
    def _build_rule_check_cmd(cls, table: str, chain: str, rule: str) -> str:
        """Build a command that prints a sentinel when the rule is present.

        ``iptables -C`` reports existence through its **exit status** and
        prints nothing to stdout either way, so the status has to be turned
        into output for Mininet's ``cmd()``, which returns stdout only.

        Args:
            table: iptables table name.
            chain: iptables chain name.
            rule: Rule specification.

        Returns:
            A shell command echoing the sentinel iff the rule exists.
        """
        return (
            f'iptables -t {table} -C {chain} {rule} >/dev/null 2>&1 '
            f'&& echo {RULE_PRESENT_SENTINEL}'
        )

    def _rule_exists(self, node: Node, table: str, chain: str, rule: str) -> bool:
        """Return whether an iptables rule is currently installed.

        Args:
            node: Node to query.
            table: iptables table name.
            chain: iptables chain name.
            rule: Rule specification.

        Returns:
            True if the rule is present.
        """
        return RULE_PRESENT_SENTINEL in node.cmd(
            self._build_rule_check_cmd(table, chain, rule)
        )

    def _remove_rule_if_present(self, node: Node, table: str, chain: str,
                                rule: str, description: str) -> None:
        """Delete an iptables rule, tolerating its absence.

        Args:
            node: Node to configure.
            table: iptables table name.
            chain: iptables chain name.
            rule: Rule specification.
            description: Human-readable description for logging.
        """
        if not self._rule_exists(node, table, chain, rule):
            logger.debug('%s: %s not found, skipping removal', self.name, description)
            return

        node.cmd(self._build_iptables_cmd('D', table, chain, rule))

    def _remove_masquerade_rule(self, node, intf_name: str) -> None:
        """Remove masquerade rule from node's outbound interface (idempotent).

        Args:
            node: Node to configure
            intf_name: Interface name
        """
        rule = f'-o {intf_name} -j {IPTABLES_ACTION_MASQUERADE}'
        self._remove_rule_if_present(
            node, IPTABLES_TABLE_NAT, IPTABLES_CHAIN_POSTROUTING,
            rule, f'masquerade rule for {intf_name}'
        )

    def _add_zone_rule(self, intf_name: str) -> None:
        """Add conntrack zone rule so Lab-facing traffic lives in the Lab's zone."""
        rule = f'-i {intf_name} -j CT --zone-orig {self._mark >> 8}'
        cmd = self._build_iptables_cmd('A', IPTABLES_TABLE_RAW, IPTABLES_CHAIN_PREROUTING, rule)
        self.cmd(cmd)

    def _remove_zone_rule(self, intf_name: str) -> None:
        """Remove conntrack zone rule (idempotent)."""
        rule = f'-i {intf_name} -j CT --zone-orig {self._mark >> 8}'
        self._remove_rule_if_present(
            self, IPTABLES_TABLE_RAW, IPTABLES_CHAIN_PREROUTING,
            rule, f'zone rule for {intf_name}'
        )

    def _add_mark_rule(self, intf_name: str) -> None:
        """Add marking rule for incoming traffic.
        
        Args:
            intf_name: Interface name
        """
        rule = f'-i {intf_name} -j MARK --set-mark {self._mark}'
        cmd = self._build_iptables_cmd('A', IPTABLES_TABLE_MANGLE, IPTABLES_CHAIN_PREROUTING, rule)
        self.cmd(cmd)

    def _remove_mark_rule(self, intf_name: str) -> None:
        """Remove marking rule for incoming traffic (idempotent).
        
        Args:
            intf_name: Interface name
        """
        rule = f'-i {intf_name} -j MARK --set-mark {self._mark}'
        self._remove_rule_if_present(
            self, IPTABLES_TABLE_MANGLE, IPTABLES_CHAIN_PREROUTING,
            rule, f'mark rule for {intf_name}'
        )

    def _add_connmark_save_rule(self, intf_name: str) -> None:
        """Add CONNMARK save-mark rule on Lab-facing interface."""
        rule = f'-i {intf_name} -j CONNMARK --save-mark'
        cmd = self._build_iptables_cmd('A', IPTABLES_TABLE_MANGLE, IPTABLES_CHAIN_PREROUTING, rule)
        self.cmd(cmd)

    def _remove_connmark_save_rule(self, intf_name: str) -> None:
        """Remove CONNMARK save-mark rule (idempotent)."""
        rule = f'-i {intf_name} -j CONNMARK --save-mark'
        self._remove_rule_if_present(
            self, IPTABLES_TABLE_MANGLE, IPTABLES_CHAIN_PREROUTING,
            rule, f'connmark save rule for {intf_name}'
        )

    def _add_connmark_restore_rule(self, intf_name: str) -> None:
        """Add CONNMARK restore-mark rules for marked connections on return ingress and local output."""
        rule_prerouting = f'! -i {intf_name} -m connmark --mark {self._mark} -j CONNMARK --restore-mark'
        cmd1 = self._build_iptables_cmd('A', IPTABLES_TABLE_MANGLE, IPTABLES_CHAIN_PREROUTING, rule_prerouting)
        self.cmd(cmd1)
        rule_output = f'-m connmark --mark {self._mark} -j CONNMARK --restore-mark'
        cmd2 = self._build_iptables_cmd('A', IPTABLES_TABLE_MANGLE, 'OUTPUT', rule_output)
        self.cmd(cmd2)

    def _remove_connmark_restore_rule(self, intf_name: str) -> None:
        """Remove CONNMARK restore-mark rules (idempotent)."""
        rule_prerouting = f'! -i {intf_name} -m connmark --mark {self._mark} -j CONNMARK --restore-mark'
        self._remove_rule_if_present(
            self, IPTABLES_TABLE_MANGLE, IPTABLES_CHAIN_PREROUTING,
            rule_prerouting, f'connmark restore rule for mark {self._mark}'
        )
        rule_output = f'-m connmark --mark {self._mark} -j CONNMARK --restore-mark'
        self._remove_rule_if_present(
            self, IPTABLES_TABLE_MANGLE, 'OUTPUT',
            rule_output, f'connmark restore rule for mark {self._mark} in OUTPUT'
        )

    def _add_policy_route(self, intf_name: str) -> None:
        """Add policy routing rule and link route in the mark's table.

        Without a link subnet there is nothing to put in the table, and a
        rule pointing at an empty table would drop every marked reply; the
        Gateway then falls back to the main table, unsealed, and says so.
        """
        if not self._local_subnet:
            logger.warning('%s: no link subnet on %s; policy route not installed, Lab not sealed',
                           self.name, intf_name)
            return
        # The route first, so the rule never points at an empty table.
        self.cmd(f'ip route add {shlex.quote(self._local_subnet)} dev {shlex.quote(intf_name)} table {self._mark}')
        self.cmd(f'ip rule add fwmark {self._mark} lookup {self._mark}')

    def _remove_policy_route(self) -> None:
        """Remove policy routing rule and flush the mark's route table."""
        if not self._local_subnet:
            return
        self.cmd(f'ip rule del fwmark {self._mark} lookup {self._mark}')
        self.cmd(f'ip route flush table {self._mark}')

    def _add_marked_masquerade_rule(self) -> None:
        """Add masquerade rule for marked packets."""
        rule = f'-m mark --mark {self._mark} -j {IPTABLES_ACTION_MASQUERADE}'
        cmd = self._build_iptables_cmd('A', IPTABLES_TABLE_NAT, IPTABLES_CHAIN_POSTROUTING, rule)
        self.cmd(cmd)

    def _remove_marked_masquerade_rule(self) -> None:
        """Remove masquerade rule for marked packets (idempotent)."""
        rule = f'-m mark --mark {self._mark} -j {IPTABLES_ACTION_MASQUERADE}'
        self._remove_rule_if_present(
            self, IPTABLES_TABLE_NAT, IPTABLES_CHAIN_POSTROUTING,
            rule, f'marked masquerade rule (mark={self._mark})'
        )

    def _enable_ip_forwarding(self) -> None:
        """Claim root IP forwarding; the first claim on the host turns it on."""
        self._ip_forward_holder = ip_forward_holder(self._lab_key, self.name)
        acquire_ip_forward_lease(self._ip_forward_holder)

    def _restore_ip_forwarding(self) -> None:
        """Release the claim; the last claim on the host restores the original."""
        if self._ip_forward_holder is None:
            return
        release_ip_forward_lease(self._ip_forward_holder)
        self._ip_forward_holder = None

    def _discover_peer(self, local_intf) -> None:
        """Discover peer node through the link.
        
        Args:
            local_intf: Local interface connected to peer
        """
        if local_intf.link is not None:
            peer_intf = (
                local_intf.link.intf2
                if local_intf.link.intf1 is local_intf
                else local_intf.link.intf1
            )
            self._peer_node = peer_intf.node
            self._peer_intf_name = peer_intf.name

    def _configure_stage1_masquerade(self) -> None:
        """Configure Stage-1 peer masquerade."""
        if self._peer_node and self._peer_intf_name:
            self._add_masquerade_rule(self._peer_node, self._peer_intf_name)
            logger.debug('%s: peer masquerade configured on %s/%s', self.name, self._peer_node.name, self._peer_intf_name)

    def _remove_stage1_masquerade(self) -> None:
        """Remove Stage-1 peer masquerade."""
        if self._peer_node and self._peer_intf_name:
            # Only attempt cleanup if peer node's shell is still running
            if self._peer_node.shell:
                self._remove_masquerade_rule(self._peer_node, self._peer_intf_name)
                logger.debug('%s: peer masquerade removed on %s/%s', self.name, self._peer_node.name, self._peer_intf_name)
            else:
                logger.debug('%s: Skipping peer cleanup for %s (already terminated)', self.name, self._peer_node.name)

    def _determine_subnet(self, local_intf: Any) -> Optional[str]:
        """Determine subnet CIDR string for the local interface."""
        if self.nat_subnet:
            return self.nat_subnet
        ip = getattr(local_intf, 'ip', None)
        prefix_len = getattr(local_intf, 'prefixLen', None)
        if ip and prefix_len:
            return str(ipaddress.ip_network(f"{ip}/{prefix_len}", strict=False))
        params = getattr(local_intf, 'params', {})
        if isinstance(params, dict) and 'ip' in params:
            return str(ipaddress.ip_network(params['ip'], strict=False))
        return None

    def _configure_stage2_masquerade(self) -> None:
        """Configure Stage-2 root-namespace masquerade and sealing policy routing."""
        if self._local_intf_name:
            self._add_zone_rule(self._local_intf_name)
            self._add_mark_rule(self._local_intf_name)
            self._add_connmark_save_rule(self._local_intf_name)
            self._add_connmark_restore_rule(self._local_intf_name)
            self._add_marked_masquerade_rule()
            self._add_policy_route(self._local_intf_name)
            logger.debug('%s: NAT masquerade active on %s (mark=%d, subnet=%s)', self.name, self._local_intf_name, self._mark, self.nat_subnet or 'any')

    def _remove_stage2_masquerade(self) -> None:
        """Remove Stage-2 root-namespace masquerade and sealing rules."""
        if self._local_intf_name:
            self._remove_zone_rule(self._local_intf_name)
            self._remove_connmark_save_rule(self._local_intf_name)
            self._remove_connmark_restore_rule(self._local_intf_name)
            self._remove_marked_masquerade_rule()
            self._remove_policy_route()
            # The mark rule last: the sweeps derive every rule above from
            # it, so a stop cut short here still leaves them findable.
            self._remove_mark_rule(self._local_intf_name)
            logger.debug('%s: NAT masquerade rules removed (mark=%d)', self.name, self._mark)

    def __init__(self, name: str, subnet: Optional[str] = None, peer_default_gw: bool = True,
                 mark: Optional[int] = None, lab_key: Optional[str] = None, **params):
        self.nat_subnet = subnet
        self._peer_default_gw: bool = peer_default_gw
        self._mark: int = mark if mark is not None else self._alloc_mark()
        self._lab_key: Optional[str] = lab_key
        self._local_intf_name = None
        self._local_subnet = subnet
        self._ip_forward_holder = None
        self._peer_node = None
        self._peer_intf_name = None
        self._terminated = False  # Track cleanup state for idempotency
        # Always run in the root namespace so that Mininet veth pairs bridge
        # to the real network stack without requiring an extra hop.
        params.setdefault('inNamespace', False)
        super().__init__(name, **params)

    def config(self, **params):
        """Configure two-stage masquerade NAT rules (see class docstring for architecture detail).

        Called automatically by Mininet during net.start().
        Both stages are removed automatically in :meth:`terminate`.
        """
        super().config(**params)

        local_intf = self.defaultIntf()
        if local_intf is None:
            logger.warning(
                '%s: no interface found; NAT masquerade rules were NOT installed', self.name
            )
            return

        self._local_intf_name = local_intf.name
        self._local_subnet = self._determine_subnet(local_intf)
        self._discover_peer(local_intf)
        self._enable_ip_forwarding()
        self._configure_stage1_masquerade()
        self._configure_stage2_masquerade()

    def cleanup_rules(self):
        """Clean up iptables rules without terminating the node.
        
        This allows cleanup of rules on downstream/peer nodes while they
        are still alive, while deferring shell/interface cleanup to Mininet.
        """
        if self._terminated:
            return
        
        self._terminated = True
        self._remove_stage1_masquerade()
        self._remove_stage2_masquerade()
        self._restore_ip_forwarding()

    def terminate(self):
        """Remove iptables rules and restore original ip_forward state."""
        # Clean up rules if not already done
        if not self._terminated:
            self.cleanup_rules()
        # Always call parent to ensure proper shell/interface cleanup
        super().terminate()


class TransitNAT(MasqueradeNAT):
    """Transit NAT gateway for ExaBGP-mediated internet transit paths.

    Extends :class:`MasqueradeNAT` for the 3-hop topology::

        [TransitNAT, root-ns] <-- veth --> [ExaBGP peer] <-- veth --> [downstream FRR router(s)]

    In addition to the 2-stage masquerade that :class:`MasqueradeNAT` installs
    on the ExaBGP peer, ``TransitNAT`` configures a **Stage-0** masquerade and
    default route on each *downstream* FRR router, so the full transit chain is
    self-contained with no manual ``node.cmd()`` calls required::

        Stage 0a: each downstream masquerades on its intf toward the ExaBGP peer.
        Stage 0b: each downstream default route set via the ExaBGP peer's IP.
        Stage 1:  ExaBGP peer masquerades on its intf toward this node.
        Stage 1b: ExaBGP peer default route set via this node's IP.
        Stage 2:  This node marks + masquerades in root namespace.

    All stages are fully removed in :meth:`terminate`.

    ``downstream`` accepts either a single node or a list of nodes::

        # single downstream
        nat = net.addTransit('nat1', downstream=isp1)

        # multiple downstreams
        nat = net.addTransit('nat1', downstream=[up1, up2])

    Args:
        name: Node name.
        downstream: FRR router node (or list of nodes) one hop beyond the
            ExaBGP peer that require Stage-0 masquerade and default route
            configuration.
        subnet: Optional CIDR subnet string (metadata only).
        **params: Additional parameters forwarded to :class:`MasqueradeNAT`.
    """

    def __init__(self, name: str, downstream: Optional[Union[object, List]] = None, subnet: Optional[str] = None, **params):
        if downstream is None:
            self._downstreams = []
        elif isinstance(downstream, list):
            self._downstreams = downstream
        else:
            self._downstreams = [downstream]
        # Each entry: {'node': <Node>, 'intf': <str>, 'gw': <str>}
        self._downstream_states: list = []
        super().__init__(name, subnet=subnet, **params)

    def _configure_downstream(self, downstream_node) -> None:
        """Install Stage-0 rules for one downstream node."""
        assert self._peer_node is not None
        # Walk the ExaBGP peer's interfaces to find the link facing this downstream.
        peer_to_downstream_intf = None
        downstream_to_peer_intf = None
        for intf in self._peer_node.intfs.values():
            if intf.link is None:
                continue
            other = (
                intf.link.intf2
                if intf.link.intf1 is intf
                else intf.link.intf1
            )
            if other.node is downstream_node:
                peer_to_downstream_intf = intf
                downstream_to_peer_intf = other
                break

        if peer_to_downstream_intf is None:
            logger.warning(
                '%s: no link found between %s and %s; Stage-0 rules NOT installed',
                self.name, self._peer_node.name, downstream_node.name,
            )
            return

        assert downstream_to_peer_intf is not None

        # Use params dict — more reliable than intf.ip because the ExaBGP
        # peer's config() may not have run yet when config() ordering is
        # non-deterministic.
        raw_ip = peer_to_downstream_intf.params.get('ip', '')
        peer_to_downstream_ip = raw_ip.split('/')[0] if raw_ip else ''
        if not peer_to_downstream_ip:
            logger.warning(
                '%s: no IP in params for %s/%s; Stage-0 default route NOT installed',
                self.name, self._peer_node.name, peer_to_downstream_intf.name,
            )
            return

        # Stage 0a — downstream FRR router masquerades customer traffic to its
        # own IP so return traffic from root namespace can route back correctly.
        downstream_node.cmd(
            f'iptables -t nat -A POSTROUTING '
            f'-o {downstream_to_peer_intf.name} -j MASQUERADE'
        )

        # Stage 0b — downstream FRR router routes internet-bound traffic via
        # the ExaBGP peer.  Use 'append' so that multiple TransitNAT nodes
        # configuring the same downstream coexist as ECMP nexthops rather than
        # failing silently ("File exists") when a default route already exists.
        downstream_node.cmd(f'ip route append default via {peer_to_downstream_ip}')

        self._downstream_states.append({
            'node': downstream_node,
            'intf': downstream_to_peer_intf.name,
            'gw': peer_to_downstream_ip,
        })
        logger.debug('%s: Stage-0 transit configured on %s (intf=%s, gw=%s)', self.name, downstream_node.name, downstream_to_peer_intf.name, peer_to_downstream_ip)

    def config(self, **params):
        """Configure all transit stages.

        Calls :meth:`MasqueradeNAT.config` first (stages 1+2 and ExaBGP peer
        default gateway), then installs Stage-0 masquerade and default route on
        each downstream FRR router by walking the ExaBGP peer's link table.
        """
        super().config(**params)

        if not self._downstreams:
            logger.warning(
                '%s: no downstream node(s) set; Stage-0 transit rules NOT installed', self.name
            )
            return

        if self._peer_node is None:
            return

        for downstream_node in self._downstreams:
            self._configure_downstream(downstream_node)

    def cleanup_rules(self):
        """Clean up Stage-0 iptables rules without terminating the node (idempotent).
        
        This allows cleanup of rules on downstream nodes while they are
        still alive, while deferring shell/interface cleanup to Mininet.
        """
        if not self._terminated:
            for state in self._downstream_states:
                node = state['node']
                intf = state['intf']
                gw = state['gw']
                # Only attempt cleanup if node's shell is still running
                if node.shell:
                    self._remove_rule_if_present(
                        node, IPTABLES_TABLE_NAT, IPTABLES_CHAIN_POSTROUTING,
                        f'-o {intf} -j {IPTABLES_ACTION_MASQUERADE}',
                        f'Stage-0 masquerade for {node.name}/{intf}'
                    )

                    # 'ip route show' does print matching routes to stdout, so
                    # unlike 'iptables -C' its output is a usable existence test.
                    safe_gw = shlex.quote(str(gw))
                    if node.cmd(f'ip route show default via {safe_gw}').strip():
                        node.cmd(f'ip route del default via {safe_gw}')
                        logger.debug('%s: Stage-0 default route %s removed from %s', self.name, gw, node.name)
                    else:
                        logger.debug('%s: Stage-0 default route %s for %s not found, skipping', self.name, gw, node.name)
                else:
                    logger.debug('%s: Skipping cleanup for %s (already terminated)', self.name, node.name)
            self._downstream_states.clear()
        # Call parent to clean up Stage-1 and Stage-2
        super().cleanup_rules()

    def terminate(self):
        """Remove Stage-0 transit rules then delegate to super()."""
        # Clean up rules if not already done
        if not self._terminated:
            self.cleanup_rules()
        # Always call parent to ensure proper shell/interface cleanup
        super().terminate()
