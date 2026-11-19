"""Network test and configuration commands shared by CLI and Web API.

This module centralises link configuration, bandwidth tests, ping,
traceroute, and route inspection behind a small, testable interface.
Both the interactive CLI and the Web API delegate to these services,
eliminating the duplication that previously existed between
``mniplab/cli.py`` and ``mniplab/webserver/services.py``.
"""

import json
import re
import shlex
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from mininet.log import info
from mniplab.logging import get_logger
from mniplab.router import FRRouter
from mniplab.utils import host_network_refusal, lives_in_host_network

if TYPE_CHECKING:
    from mniplab.net import mnIPLab

logger = get_logger(__name__)

# A Link Condition duration: a number with us, ms or s, or a bare number of
# milliseconds. The Canvas Link Condition validator in webserver/authoring.py
# takes the same units; keep the two in step. Whitespace around and inside the
# duration is ignored, so "10 ms" is applied as "10ms".
_DURATION = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(us|ms|s)?\s*")


def _checked_duration(field: str, duration: Optional[str]) -> Optional[str]:
    """Check a Link Condition duration and give it a unit.

    Args:
        field: The parameter's name, for the message.
        duration: The duration as given, or None when unset.

    Returns:
        The duration with a unit, without whitespace; a bare number becomes
        milliseconds.

    Raises:
        ValueError: If the duration is not a number with us, ms or s.
    """
    if not duration:
        return duration
    match = _DURATION.fullmatch(duration)
    if not match:
        raise ValueError(f"{field} needs a duration such as 10ms")
    number, unit = match.groups()
    return f"{number}{unit or 'ms'}"


def _format_bandwidth(bps: float) -> str:
    """Format bits-per-second value to a human-readable string (Kbps/Mbps/Gbps)."""
    if bps >= 1e9:
        return f"{bps / 1e9:.2f} Gbps"
    elif bps >= 1e6:
        return f"{bps / 1e6:.2f} Mbps"
    elif bps >= 1e3:
        return f"{bps / 1e3:.2f} Kbps"
    else:
        return f"{bps:.2f} bps"


def refuse_host_network(lab: Any, name: str, node: Any) -> None:
    """Refuse a command on a Node that lives in the Host machine's network.

    An Internet Gateway's namespace is the machine every Lab shares, so a
    command there runs on that machine. A Switch is not refused: its ports
    are the Lab's own to command, as they are today.

    Args:
        lab: mnIPLab network instance.
        name: The Node's name, for the message.
        node: The Node, or None when the Lab has no Node by that name.

    Raises:
        ValueError: If the Node is an Internet Gateway.
    """
    if lives_in_host_network(node) and node not in lab.switches:
        raise ValueError(host_network_refusal(name, is_switch=False))


class Iperf3ResultParser:
    """Parses iperf3 JSON output and produces human-readable summaries."""

    @staticmethod
    def parse(output_text: str, protocol: str) -> Optional[dict]:
        """Parse iperf3 output.

        Returns:
            Parsed JSON dict, or ``None`` if parsing fails.
        """
        parsed_data = None
        json_start = output_text.find('{')
        json_end = output_text.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            try:
                parsed_data = json.loads(output_text[json_start:json_end])
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"Failed to parse iperf3 JSON output: {e}")

        return parsed_data

    @staticmethod
    def format_summary(server: str, client: str, seconds: int,
                       protocol: str, parsed_data: Optional[dict],
                       raw_output: str) -> str:
        """Format a human-readable summary from iperf3 results."""
        output = f"*** Iperf3: testing {protocol.upper()} bandwidth between {server} and {client}\n"
        output += f"*** Duration: {seconds}s\n"

        if parsed_data and 'end' in parsed_data:
            end = parsed_data['end']
            try:
                if 'sum_sent' in end:
                    bps = end['sum_sent'].get('bits_per_second', 0)
                    output += f"*** Bandwidth (sent): {_format_bandwidth(bps)}\n"
                if 'sum_received' in end:
                    bps = end['sum_received'].get('bits_per_second', 0)
                    output += f"*** Bandwidth (received): {_format_bandwidth(bps)}\n"
                if protocol == 'udp' and 'sum' in end:
                    jitter = end['sum'].get('jitter_ms', 0)
                    lost = end['sum'].get('lost_packets', 0)
                    total = end['sum'].get('packets', 0)
                    loss_pct = (lost / total * 100) if total > 0 else 0
                    output += f"*** Jitter: {jitter:.2f}ms\n"
                    output += f"*** Packet Loss: {lost}/{total} ({loss_pct:.2f}%)\n"
            except (KeyError, TypeError, ValueError) as e:
                logger.debug(f"Failed to extract metrics from iperf3 JSON: {e}")
                output += raw_output
        else:
            output += raw_output

        return output


class NetworkTestService:
    """Runs network connectivity and bandwidth tests against a lab."""

    VALID_SCHEDULERS = ['htb', 'tbf', 'hfsc']
    DEFAULT_SCHEDULER = 'hfsc'
    VALID_PROTOCOLS = ['tcp', 'udp']

    # --- Link configuration ------------------------------------------------

    @staticmethod
    def parse_link_config_params(params: List[str]) -> Dict[str, Any]:
        """Parse link configuration parameters.

        Returns:
            Dictionary with parsed parameters (delay, jitter, loss, bw, scheduler).

        Raises:
            ValueError: If parameters are invalid.
        """
        config: Dict[str, Any] = {
            'delay': None,
            'jitter': None,
            'loss': None,
            'bw': None,
            'scheduler': NetworkTestService.DEFAULT_SCHEDULER
        }

        for param in params:
            if param in NetworkTestService.VALID_SCHEDULERS:
                config['scheduler'] = param
                continue

            if '=' not in param:
                raise ValueError(f'Invalid parameter format: {param}')

            key, value = param.split('=', 1)

            if key == 'delay':
                config['delay'] = value if value.endswith('ms') else f'{value}ms'
            elif key == 'jitter':
                config['jitter'] = value if value.endswith('ms') else f'{value}ms'
            elif key == 'loss':
                config['loss'] = value if value.endswith('%') else f'{value}%'
            elif key == 'bw':
                config['bw'] = value
            else:
                raise ValueError(f'Unknown parameter: {key}')

        if config['jitter'] and not config['delay']:
            raise ValueError('jitter requires delay to be set')

        return config

    @staticmethod
    def build_tc_config(parsed_config: Dict[str, Any]) -> Dict[str, Any]:
        """Build TC (Traffic Control) configuration parameters.

        Args:
            parsed_config: Parsed link configuration from
                :meth:`parse_link_config_params`.

        Returns:
            Dictionary of parameters for ``TCIntf.config()``.
        """
        tc_params: Dict[str, Any] = {}

        if parsed_config['bw']:
            tc_params['bw'] = float(parsed_config['bw'])

        if parsed_config['delay']:
            tc_params['delay'] = parsed_config['delay']

        if parsed_config['jitter']:
            tc_params['jitter'] = parsed_config['jitter']

        if parsed_config['loss']:
            loss_val = parsed_config['loss'].replace('%', '').strip()
            tc_params['loss'] = float(loss_val)

        if 'bw' in tc_params:
            scheduler = parsed_config['scheduler']
            if scheduler == 'hfsc':
                tc_params['use_hfsc'] = True
            elif scheduler == 'tbf':
                tc_params['use_tbf'] = True

        return tc_params

    @staticmethod
    def configure_link(lab, node1: str, node2: str,
                       delay: Optional[str] = None,
                       jitter: Optional[str] = None,
                       loss: Optional[str] = None,
                       bandwidth: Optional[str] = None,
                       scheduler: str = 'hfsc') -> dict:
        """Configure link parameters between two nodes.

        Args:
            lab: mnIPLab network instance.
            node1: First node name.
            node2: Second node name.
            delay: Delay string (e.g. ``'10ms'``).
            jitter: Jitter string (e.g. ``'2ms'``).
            loss: Loss string (e.g. ``'5%'``).
            bandwidth: Bandwidth string (e.g. ``'100'`` for Mbps).
            scheduler: TC scheduler (``'hfsc'``, ``'htb'``, ``'tbf'``).

        Returns:
            Result dict with ``success``, ``link``, and ``config`` keys.

        Raises:
            ValueError: On validation errors or missing link.
        """
        if jitter and not delay:
            raise ValueError("jitter requires delay to be set")
        if scheduler not in NetworkTestService.VALID_SCHEDULERS:
            raise ValueError(f"Invalid scheduler: {scheduler}")
        delay = _checked_duration('delay', delay)
        jitter = _checked_duration('jitter', jitter)

        n1 = lab.get(node1)
        n2 = lab.get(node2)
        if not n1 or not n2:
            raise ValueError(f"Node not found: {node1} or {node2}")
        # A Switch's ports are conditioned in the Host machine's network on
        # purpose; an Internet Gateway's uplink is not the Lab's to change.
        for name, node in ((node1, n1), (node2, n2)):
            refuse_host_network(lab, name, node)

        link_found = False
        for link in lab.links:
            if (link.intf1.node == n1 and link.intf2.node == n2) or \
               (link.intf1.node == n2 and link.intf2.node == n1):
                config_params: Dict[str, Any] = {}
                if bandwidth:
                    bw_val = bandwidth.replace('Mbps', '').replace('mbps', '').replace('Mbit', '').strip()
                    config_params['bw'] = float(bw_val)
                if delay:
                    config_params['delay'] = delay
                if jitter:
                    config_params['jitter'] = jitter
                if loss:
                    loss_val = loss.replace('%', '').strip()
                    config_params['loss'] = float(loss_val)
                if 'bw' in config_params:
                    if scheduler == 'hfsc':
                        config_params['use_hfsc'] = True
                    elif scheduler == 'tbf':
                        config_params['use_tbf'] = True
                # Reject an unregistered link before touching the network, so
                # we can never apply a change we are unable to record.
                lab.link_conditions.get(n1.name, n2.name)

                for intf in (link.intf1, link.intf2):
                    NetworkTestService._apply_and_check(intf, config_params, node1, node2)
                link_found = True
                break

        if not link_found:
            raise ValueError(f"No link found between {node1} and {node2}")

        result_config: Dict[str, Any] = {}
        if delay:
            result_config['delay'] = delay
        if jitter:
            result_config['jitter'] = jitter
        if loss:
            result_config['loss'] = loss if loss.endswith('%') else f'{loss}%'
        if bandwidth:
            result_config['bandwidth'] = bandwidth
            result_config['scheduling'] = scheduler.upper()

        # Record only what actually applied. Reached only when every interface
        # accepted the change, so intent never outruns reality.
        recorded: Dict[str, Any] = {
            key: result_config[key]
            for key in ('delay', 'jitter', 'loss')
            if key in result_config
        }
        if 'bw' in config_params:
            recorded['bandwidth'] = config_params['bw']
            recorded['scheduler'] = scheduler
        if recorded:
            lab.link_conditions.set_impairment(n1.name, n2.name, **recorded)

        return {"success": True, "link": f"{node1} <-> {node2}", "config": result_config}

    @staticmethod
    def _apply_and_check(intf: Any, config_params: Dict[str, Any],
                         node1: str, node2: str) -> None:
        """Apply traffic-control parameters to an interface and verify them.

        Mininet reports traffic-control failures as non-empty command output
        and only logs them, so the output has to be inspected here for the
        caller to learn whether the change took effect.

        Args:
            intf: Interface to configure.
            config_params: Traffic-control parameters.
            node1: First node name, for the error message.
            node2: Second node name, for the error message.

        Raises:
            ValueError: If any traffic-control command reported an error.
        """
        result = intf.config(**config_params)
        failures = [out for out in (result or {}).get('tcoutputs', []) or [] if out]
        if failures:
            raise ValueError(
                f"Failed to apply link condition on {intf.name} "
                f"({node1} <-> {node2}): {'; '.join(failures)}"
            )

    # --- Ping / Traceroute / Iperf -----------------------------------------

    @staticmethod
    def _resolve_destination(lab, destination: str, ipv6: bool = False) -> str:
        """Turn a destination that is a Node's name into that Node's address.

        Network Tools never relies on the Lab's hosts file. A destination that
        is exactly a Node's name becomes the live address of that Node's
        default Interface, as the Lab Prompt does; anything else is passed
        through for the source Node's own resolver.

        Args:
            lab: mnIPLab network instance.
            destination: A Node's name, an address, or a domain name.
            ipv6: Read the global IPv6 address instead of the IPv4 one.

        Returns:
            The address to test, or ``destination`` unchanged.

        Raises:
            ValueError: If the destination is an Internet Gateway, or the
                Node's default Interface holds no address in the family.
        """
        try:
            node = lab.get(destination)
        except KeyError:  # Mininet raises for a name it does not know
            node = None
        if node is None:
            return destination
        refuse_host_network(lab, destination, node)
        intf = node.defaultIntf()
        update = getattr(intf, 'updateIP6' if ipv6 else 'updateIP', None)
        address = update() if update is not None else None
        if not address:
            family = 'IPv6' if ipv6 else 'IPv4'
            raise ValueError(
                f"{destination}'s interface {getattr(intf, 'name', None)} has no {family} address")
        return address

    @staticmethod
    def run_ping(lab, source: str, destination: str, count: int = 4, ipv6: bool = False) -> dict:
        target = NetworkTestService.resolve_test_target(lab, source, destination, ipv6=ipv6)
        src = lab.get(source)
        family = '-6 ' if ipv6 else ''
        result = src.cmd(f'ping {family}-c {int(count)} {shlex.quote(target)}')
        return {"success": True, "output": result}

    @staticmethod
    def run_traceroute(lab, source: str, destination: str, ipv6: bool = False,
                       resolve: bool = False) -> dict:
        target = NetworkTestService.resolve_test_target(lab, source, destination, ipv6=ipv6)
        src = lab.get(source)
        tool = 'traceroute6' if ipv6 else 'traceroute'
        numeric = '' if resolve else '-n '
        result = src.cmd(f'{tool} {numeric}{shlex.quote(target)}')
        if not result or 'command not found' in result.lower():
            return {"success": False, "detail": "Traceroute not installed on node"}
        return {"success": True, "output": result}

    @staticmethod
    def run_iperf(lab, server: str, client: str) -> dict:
        srv = lab.get(server)
        cli = lab.get(client)
        if not srv or not cli:
            raise ValueError(f"Node not found: {server} or {client}")
        refuse_host_network(lab, server, srv)
        refuse_host_network(lab, client, cli)

        try:
            bandwidth = lab.iperf((srv, cli), seconds=5)
            output = f"*** Iperf: testing TCP bandwidth between {server} and {client}\n"
            output += f"*** Results: {bandwidth}"
            return {"success": True, "output": output}
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            return {"success": False, "detail": f"Iperf test failed: {str(e)}"}

    @staticmethod
    def run_iperf3(lab, server: str, client: str, seconds: int = 10,
                   protocol: str = 'tcp', json_output: bool = False) -> dict:
        srv = lab.get(server)
        cli = lab.get(client)
        if not srv or not cli:
            raise ValueError(f"Node not found: {server} or {client}")
        refuse_host_network(lab, server, srv)
        refuse_host_network(lab, client, cli)

        # The Lab answers nothing else while the test runs, so it is kept short.
        if seconds < 1 or seconds > 60:
            raise ValueError("Duration must be between 1 and 60 seconds")
        if protocol not in NetworkTestService.VALID_PROTOCOLS:
            raise ValueError("Protocol must be 'tcp' or 'udp'")

        server_ip = None
        for intf in srv.intfs.values():
            if intf.ip:
                server_ip = intf.ip.split('/')[0]
                break

        if not server_ip:
            raise ValueError(f"Server '{server}' has no IP address")

        srv_proc = srv.popen('iperf3 -s')
        time.sleep(0.5)

        cmd = f'iperf3 -c {server_ip} -t {seconds}'
        if protocol == 'udp':
            cmd += ' -u'
        if json_output:
            cmd += ' -J'

        result = cli.cmd(cmd)
        srv_proc.terminate()
        srv_proc.wait()

        parsed = Iperf3ResultParser.parse(result, protocol) if json_output else None
        output = Iperf3ResultParser.format_summary(server, client, seconds, protocol, parsed, result)
        return {"success": True, "output": output}

    @staticmethod
    def resolve_test_target(lab: "mnIPLab", source: str, destination: str, ipv6: bool = False) -> str:
        """Check a test's source Node and turn its destination into what to test.

        Args:
            lab: mnIPLab network instance.
            source: The Node the test runs on.
            destination: A Node's name, an address, or a domain name.
            ipv6: Read a Node's global IPv6 address instead of its IPv4 one.

        Returns:
            The address or name the test's command is given.

        Raises:
            ValueError: The source is no Node or lives in the host's
                network, or the destination cannot be tested.
        """
        src = lab.get(source)
        if src is None:
            raise ValueError(f"Source node not found: {source}")
        refuse_host_network(lab, source, src)
        return NetworkTestService._resolve_destination(lab, destination, ipv6=ipv6)


class RoutingQueryService:
    """Queries FRR routing state from router nodes."""

    @staticmethod
    def get_node_routes(lab, node_name: str, ip_version: Optional[str] = None) -> dict:
        node = lab.get(node_name)
        if not node:
            raise ValueError(f"Node not found: {node_name}")
        refuse_host_network(lab, node_name, node)

        show_v4 = ip_version in (None, '4')
        show_v6 = ip_version in (None, '6')

        parts = []
        if show_v4:
            v4_routes = node.cmd('ip -4 route show')
            parts.append(f"--- IPv4 Routes ({node_name}) ---\n{v4_routes}")
        if show_v6:
            v6_routes = node.cmd('ip -6 route show')
            parts.append(f"--- IPv6 Routes ({node_name}) ---\n{v6_routes}")

        output = "\n\n".join(parts)
        return {"success": True, "output": output}

    @staticmethod
    def get_node_ospf(lab, node_name: str) -> dict:
        neighbors = RoutingQueryService.run_vtysh(lab, node_name, "show ip ospf neighbor")
        routes = RoutingQueryService.run_vtysh(lab, node_name, "show ip ospf route")
        output = "--- OSPF Neighbors ---\n"
        output += (neighbors["output"] if neighbors["success"] else neighbors["detail"]) + "\n\n--- OSPF Routes ---\n"
        output += (routes["output"] if routes["success"] else routes["detail"])
        return {"success": True, "output": output}

    @staticmethod
    def get_node_bgp(lab, node_name: str) -> dict:
        summary = RoutingQueryService.run_vtysh(lab, node_name, "show bgp summary")
        routes = RoutingQueryService.run_vtysh(lab, node_name, "show bgp")
        output = "--- BGP Summary ---\n"
        output += (summary["output"] if summary["success"] else summary["detail"]) + "\n\n--- BGP Routes ---\n"
        output += (routes["output"] if routes["success"] else routes["detail"])
        return {"success": True, "output": output}

    @staticmethod
    def run_vtysh(lab, node_name: str, command: str) -> dict:
        node = lab.get(node_name)
        if not node:
            raise ValueError(f"Node not found: {node_name}")
        refuse_host_network(lab, node_name, node)
        if not isinstance(node, FRRouter):
            return {
                "success": False,
                "detail": f"Node {node_name} is not an FRR router. FRR commands are only available on routers."
            }
        try:
            output = node.cmd(f'vtysh -c "{command}"')
            return {"success": True, "output": output}
        except (OSError, RuntimeError, ValueError, TypeError) as e:
            return {"success": False, "detail": str(e)}
