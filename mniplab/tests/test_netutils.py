"""Unit tests for mniplab.netutils — network diagnostic services.

Tests exercise public behavior through pure functions and minimal mocks
for Mininet node boundaries.  They should survive internal refactors.
"""

import json
import unittest
from unittest.mock import Mock

from mniplab.netutils import (
    _format_bandwidth,
    Iperf3ResultParser,
    NetworkTestService,
    RoutingQueryService,
)
from mniplab.router import FRRouter


class FormatBandwidthTests(unittest.TestCase):
    """_format_bandwidth should render human-readable bitrates."""

    def test_gbps(self):
        self.assertEqual(_format_bandwidth(2_500_000_000), "2.50 Gbps")

    def test_mbps(self):
        self.assertEqual(_format_bandwidth(150_000_000), "150.00 Mbps")

    def test_kbps(self):
        self.assertEqual(_format_bandwidth(5_000), "5.00 Kbps")

    def test_bps(self):
        self.assertEqual(_format_bandwidth(42), "42.00 bps")

    def test_zero(self):
        self.assertEqual(_format_bandwidth(0), "0.00 bps")


class Iperf3ParserTests(unittest.TestCase):
    """Iperf3ResultParser extracts JSON from noisy iperf3 output."""

    def _sample_tcp_json(self):
        return {
            "start": {},
            "end": {
                "sum_sent": {"bits_per_second": 1_000_000_000},
                "sum_received": {"bits_per_second": 900_000_000},
            },
        }

    def _sample_udp_json(self):
        return {
            "start": {},
            "end": {
                "sum_sent": {"bits_per_second": 500_000_000},
                "sum": {
                    "jitter_ms": 1.23,
                    "lost_packets": 5,
                    "packets": 1000,
                },
            },
        }

    def test_parse_extracts_json_from_mixed_output(self):
        payload = json.dumps(self._sample_tcp_json())
        noisy = f"some header\n{payload}\nsome footer"
        result = Iperf3ResultParser.parse(noisy, "tcp")
        self.assertEqual(result["end"]["sum_sent"]["bits_per_second"], 1_000_000_000)

    def test_parse_returns_none_when_no_json(self):
        result = Iperf3ResultParser.parse("no json here", "tcp")
        self.assertIsNone(result)

    def test_parse_returns_none_on_malformed_json(self):
        result = Iperf3ResultParser.parse("{broken", "tcp")
        self.assertIsNone(result)

    def test_format_summary_tcp_with_data(self):
        parsed = self._sample_tcp_json()
        summary = Iperf3ResultParser.format_summary(
            "srv", "cli", 10, "tcp", parsed, "raw"
        )
        self.assertIn("srv", summary)
        self.assertIn("cli", summary)
        self.assertIn("1.00 Gbps", summary)
        self.assertIn("900.00 Mbps", summary)

    def test_format_summary_udp_with_jitter_and_loss(self):
        parsed = self._sample_udp_json()
        summary = Iperf3ResultParser.format_summary(
            "srv", "cli", 5, "udp", parsed, "raw"
        )
        self.assertIn("Jitter: 1.23ms", summary)
        self.assertIn("Packet Loss: 5/1000 (0.50%)", summary)

    def test_format_summary_fallback_to_raw_on_missing_end(self):
        summary = Iperf3ResultParser.format_summary(
            "srv", "cli", 10, "tcp", {"start": {}}, "raw-output"
        )
        self.assertIn("raw-output", summary)

    def test_format_summary_fallback_to_raw_when_parsed_is_none(self):
        summary = Iperf3ResultParser.format_summary(
            "srv", "cli", 10, "tcp", None, "raw-output"
        )
        self.assertIn("raw-output", summary)


class NetworkTestServiceLinkConfigTests(unittest.TestCase):
    """NetworkTestService validates and transforms link configuration."""

    def test_parse_params_basic(self):
        result = NetworkTestService.parse_link_config_params(["delay=10ms", "bw=100"])
        self.assertEqual(result["delay"], "10ms")
        self.assertEqual(result["bw"], "100")
        self.assertEqual(result["scheduler"], "hfsc")

    def test_parse_params_adds_ms_suffix(self):
        result = NetworkTestService.parse_link_config_params(["delay=10"])
        self.assertEqual(result["delay"], "10ms")

    def test_parse_params_adds_percent_suffix(self):
        result = NetworkTestService.parse_link_config_params(["loss=5"])
        self.assertEqual(result["loss"], "5%")

    def test_parse_params_selects_scheduler(self):
        result = NetworkTestService.parse_link_config_params(["htb"])
        self.assertEqual(result["scheduler"], "htb")

    def test_parse_params_rejects_jitter_without_delay(self):
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.parse_link_config_params(["jitter=2ms"])
        self.assertIn("jitter requires delay", str(ctx.exception))

    def test_parse_params_rejects_invalid_format(self):
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.parse_link_config_params(["nonsense"])
        self.assertIn("Invalid parameter format", str(ctx.exception))

    def test_parse_params_rejects_unknown_key(self):
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.parse_link_config_params(["foo=bar"])
        self.assertIn("Unknown parameter", str(ctx.exception))

    def test_build_tc_config_bandwidth_and_hfsc(self):
        parsed = {"bw": "100", "delay": None, "jitter": None, "loss": None, "scheduler": "hfsc"}
        tc = NetworkTestService.build_tc_config(parsed)
        self.assertEqual(tc["bw"], 100.0)
        self.assertTrue(tc["use_hfsc"])

    def test_build_tc_config_tbf_scheduler(self):
        parsed = {"bw": "50", "delay": None, "jitter": None, "loss": None, "scheduler": "tbf"}
        tc = NetworkTestService.build_tc_config(parsed)
        self.assertTrue(tc["use_tbf"])

    def test_build_tc_config_delay_and_loss(self):
        parsed = {"bw": None, "delay": "10ms", "jitter": "2ms", "loss": "5%", "scheduler": "hfsc"}
        tc = NetworkTestService.build_tc_config(parsed)
        self.assertEqual(tc["delay"], "10ms")
        self.assertEqual(tc["jitter"], "2ms")
        self.assertEqual(tc["loss"], 5.0)

    def test_configure_link_success(self):
        """configure_link should find the link and apply TC config."""
        lab = Mock()
        n1, n2 = Mock(), Mock()
        lab.get.side_effect = [n1, n2]

        intf1, intf2 = Mock(), Mock()
        intf1.node = n1
        intf2.node = n2
        # Traffic control reports failures as non-empty command output, which
        # configure_link now inspects before recording the condition.
        intf1.config.return_value = {"tcoutputs": ["", ""]}
        intf2.config.return_value = {"tcoutputs": ["", ""]}
        link = Mock()
        link.intf1 = intf1
        link.intf2 = intf2
        lab.links = [link]

        result = NetworkTestService.configure_link(lab, "r1", "r2", delay="10ms", bandwidth="100")

        self.assertTrue(result["success"])
        self.assertEqual(result["link"], "r1 <-> r2")
        intf1.config.assert_called_once()
        intf2.config.assert_called_once()
        lab.link_conditions.set_impairment.assert_called_once()
        call_kwargs = intf1.config.call_args.kwargs
        self.assertEqual(call_kwargs["delay"], "10ms")
        self.assertEqual(call_kwargs["bw"], 100.0)
        self.assertTrue(call_kwargs["use_hfsc"])

    def test_configure_link_missing_node_raises(self):
        lab = Mock()
        lab.get.return_value = None
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.configure_link(lab, "r1", "r2")
        self.assertIn("Node not found", str(ctx.exception))

    def test_configure_link_no_link_raises(self):
        lab = Mock()
        n1, n2 = Mock(), Mock()
        lab.get.side_effect = [n1, n2]
        lab.links = []
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.configure_link(lab, "r1", "r2")
        self.assertIn("No link found", str(ctx.exception))

    def test_configure_link_rejects_jitter_without_delay(self):
        lab = Mock()
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.configure_link(lab, "r1", "r2", jitter="2ms")
        self.assertIn("jitter requires delay", str(ctx.exception))

    def test_configure_link_rejects_invalid_scheduler(self):
        lab = Mock()
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.configure_link(lab, "r1", "r2", scheduler="invalid")
        self.assertIn("Invalid scheduler", str(ctx.exception))

    @staticmethod
    def _lab_with_link():
        """Return a Mock lab whose one link joins r1 and r2, with the link's two Interfaces."""
        n1, n2 = Mock(), Mock()
        intf1, intf2 = Mock(node=n1), Mock(node=n2)
        intf1.config.return_value = {"tcoutputs": ["", ""]}
        intf2.config.return_value = {"tcoutputs": ["", ""]}
        lab = Mock()
        lab.get.side_effect = [n1, n2]
        lab.links = [Mock(intf1=intf1, intf2=intf2)]
        return lab, intf1, intf2

    def test_configure_link_refuses_a_duration_that_is_not_plain(self):
        """A quote or a command in delay or jitter is refused before any Node's shell is used."""
        cases = (
            ({"delay": "10ms'"}, "delay"),
            ({"delay": "10; reboot"}, "delay"),
            ({"delay": "ms"}, "delay"),
            ({"delay": "10ms", "jitter": "2ms\""}, "jitter"),
        )
        for kwargs, field in cases:
            with self.subTest(**kwargs):
                lab, intf1, intf2 = self._lab_with_link()

                with self.assertRaises(ValueError) as ctx:
                    NetworkTestService.configure_link(lab, "r1", "r2", **kwargs)

                self.assertIn(field, str(ctx.exception))
                intf1.config.assert_not_called()
                intf2.config.assert_not_called()

    def test_configure_link_applies_a_plain_duration_as_written(self):
        """A bare number is milliseconds; a number with us, ms or s keeps its unit."""
        cases = (
            ({"delay": "10"}, "delay", "10ms"),
            ({"delay": "10ms"}, "delay", "10ms"),
            ({"delay": "1.5ms"}, "delay", "1.5ms"),
            ({"delay": "500us"}, "delay", "500us"),
            ({"delay": "1s"}, "delay", "1s"),
            ({"delay": " 10 ms "}, "delay", "10ms"),
            ({"delay": "5 "}, "delay", "5ms"),
            ({"delay": "10ms", "jitter": "2"}, "jitter", "2ms"),
            ({"delay": "10ms", "jitter": "2ms"}, "jitter", "2ms"),
        )
        for kwargs, field, expected in cases:
            with self.subTest(**kwargs):
                lab, intf1, _ = self._lab_with_link()

                result = NetworkTestService.configure_link(lab, "r1", "r2", **kwargs)

                self.assertEqual(intf1.config.call_args.kwargs[field], expected)
                self.assertEqual(result["config"][field], expected)

    def test_configure_link_refuses_an_internet_gateway_at_either_end(self):
        """An Internet Gateway's uplink is outside the Lab, so no interface is touched."""
        for node1, node2 in (("nat0", "r1"), ("r1", "nat0")):
            with self.subTest(node1=node1, node2=node2):
                router, gateway = Mock(), Mock()
                gateway.inNamespace = False
                nodes = {"r1": router, "nat0": gateway}
                lab = Mock()
                lab.get.side_effect = lambda name: nodes[name]
                lab.switches = []
                intf1, intf2 = Mock(), Mock()
                lab.links = [Mock(intf1=intf1, intf2=intf2)]

                with self.assertRaises(ValueError) as ctx:
                    NetworkTestService.configure_link(lab, node1, node2, delay="10ms")

                self.assertEqual(
                    str(ctx.exception), "nat0 is the Internet, which is outside the Lab"
                )
                intf1.config.assert_not_called()
                intf2.config.assert_not_called()
                lab.link_conditions.set_impairment.assert_not_called()

    def test_configure_link_still_conditions_a_switch_link(self):
        """A Switch lives in the Host machine's network too, but its ports are the Lab's to condition."""
        switch, router = Mock(), Mock()
        switch.inNamespace = False
        nodes = {"s1": switch, "r1": router}
        lab = Mock()
        lab.get.side_effect = lambda name: nodes[name]
        lab.switches = [switch]
        intf1, intf2 = Mock(), Mock()
        intf1.node = switch
        intf2.node = router
        intf1.config.return_value = {"tcoutputs": ["", ""]}
        intf2.config.return_value = {"tcoutputs": ["", ""]}
        lab.links = [Mock(intf1=intf1, intf2=intf2)]

        result = NetworkTestService.configure_link(lab, "s1", "r1", delay="10ms")

        self.assertTrue(result["success"])
        intf1.config.assert_called_once()


def _lab_of(**nodes):
    """A fake Lab whose get() answers only for the Nodes given."""
    lab = Mock()
    lab.switches = []
    lab.get.side_effect = lambda name: nodes[name]  # KeyError, as Mininet's get()
    return lab


def _node(ip="10.0.0.2", ip6=None, name="h2", intf_name=None, with_ip6=True):
    """A fake Node whose default Interface reports live addresses."""
    node = Mock()
    node.inNamespace = True
    intf = Mock()
    intf.name = intf_name or f"{name}-eth0"
    intf.updateIP.return_value = ip
    if with_ip6:
        intf.updateIP6.return_value = ip6
    else:
        del intf.updateIP6
    node.defaultIntf.return_value = intf
    node.cmd.return_value = "ok"
    return node


class NetworkTestServicePingTests(unittest.TestCase):
    """NetworkTestService.run_ping delegates to node.cmd safely."""

    def test_run_ping_success(self):
        src = Mock()
        src.cmd.return_value = "64 bytes from 10.0.0.2: icmp_seq=1 ttl=64"
        lab = _lab_of(h1=src)

        result = NetworkTestService.run_ping(lab, "h1", "10.0.0.2", count=3)

        self.assertTrue(result["success"])
        src.cmd.assert_called_with("ping -c 3 10.0.0.2")

    def test_run_ping_missing_source_raises(self):
        lab = Mock()
        lab.get.return_value = None
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_ping(lab, "h1", "10.0.0.2")
        self.assertIn("Source node not found", str(ctx.exception))

    def test_run_ping_quotes_destination(self):
        src = Mock()
        src.cmd.return_value = "ok"
        lab = _lab_of(h1=src)

        NetworkTestService.run_ping(lab, "h1", "10.0.0.2; rm -rf /")
        # shlex.quote should protect against injection by quoting the argument
        call_args = src.cmd.call_args[0][0]
        self.assertIn("'10.0.0.2; rm -rf /'", call_args)

    def test_a_node_name_pings_its_default_interfaces_live_address(self):
        src, dst = _node(name="h1"), _node(ip="10.0.9.2")
        lab = _lab_of(h1=src, h2=dst)

        NetworkTestService.run_ping(lab, "h1", "h2", count=2)

        src.cmd.assert_called_once_with("ping -c 2 10.0.9.2")
        dst.defaultIntf.return_value.updateIP.assert_called_once_with()

    def test_a_domain_name_that_is_no_node_is_left_to_the_nodes_resolver(self):
        src = _node(name="h1")
        lab = _lab_of(h1=src, h2=_node())

        NetworkTestService.run_ping(lab, "h1", "web.example.com", count=1)

        src.cmd.assert_called_once_with("ping -c 1 web.example.com")

    def test_ipv6_ping_to_a_node_name_uses_its_default_interfaces_global_address(self):
        src, dst = _node(name="h1"), _node(ip6="2001:db8:1::2")
        lab = _lab_of(h1=src, h2=dst)

        NetworkTestService.run_ping(lab, "h1", "h2", count=2, ipv6=True)

        src.cmd.assert_called_once_with("ping -6 -c 2 2001:db8:1::2")

    def test_ipv6_ping_passes_an_address_and_a_domain_name_quoted(self):
        for destination, expected in (
            ("2001:db8::9", "ping -6 -c 1 2001:db8::9"),
            ("x; reboot", "ping -6 -c 1 'x; reboot'"),
        ):
            with self.subTest(destination=destination):
                src = _node(name="h1")
                NetworkTestService.run_ping(_lab_of(h1=src), "h1", destination, count=1, ipv6=True)
                src.cmd.assert_called_once_with(expected)

    def test_a_node_without_an_address_is_an_error_naming_it_and_its_interface(self):
        src, dst = _node(name="h1"), _node(ip=None, intf_name="h2-eth0")
        lab = _lab_of(h1=src, h2=dst)

        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_ping(lab, "h1", "h2")

        self.assertIn("h2", str(ctx.exception))
        self.assertIn("h2-eth0", str(ctx.exception))
        src.cmd.assert_not_called()


class NetworkTestServiceNodeNameEdgeTests(unittest.TestCase):
    """Edges of turning a Node's name into an address."""

    def test_only_the_default_interface_is_read_never_another(self):
        src, dst = _node(name="h1"), _node(ip=None, intf_name="h2-eth0")
        other = Mock()
        other.updateIP.return_value = "10.9.9.9"
        dst.intfs = {1: other}
        lab = _lab_of(h1=src, h2=dst)

        with self.assertRaises(ValueError):
            NetworkTestService.run_ping(lab, "h1", "h2")

        other.updateIP.assert_not_called()
        src.cmd.assert_not_called()

    def test_a_node_without_interfaces_is_an_error_naming_the_node(self):
        src, dst = _node(name="h1"), Mock()
        dst.inNamespace = True
        dst.defaultIntf.return_value = None
        lab = _lab_of(h1=src, h2=dst)

        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_ping(lab, "h1", "h2")

        self.assertIn("h2", str(ctx.exception))
        src.cmd.assert_not_called()

    def test_ipv4_traceroute_passes_a_domain_name_and_metacharacters_quoted(self):
        for destination, expected in (
            ("web.example.com", "traceroute -n web.example.com"),
            ("x; reboot", "traceroute -n 'x; reboot'"),
        ):
            with self.subTest(destination=destination):
                src = _node(name="h1")
                NetworkTestService.run_traceroute(_lab_of(h1=src), "h1", destination)
                src.cmd.assert_called_once_with(expected)

    def test_a_gateway_name_is_refused_before_its_address_is_read_in_every_tool(self):
        for call in (
            lambda lab: NetworkTestService.run_ping(lab, "h1", "nat0"),
            lambda lab: NetworkTestService.run_traceroute(lab, "h1", "nat0"),
            lambda lab: NetworkTestService.run_traceroute(lab, "h1", "nat0", ipv6=True),
        ):
            with self.subTest():
                src, gateway = _node(name="h1"), _node(name="nat0")
                gateway.inNamespace = False
                with self.assertRaises(ValueError):
                    call(_lab_of(h1=src, nat0=gateway))
                gateway.defaultIntf.assert_not_called()
                src.cmd.assert_not_called()


class NetworkTestServiceTracerouteTests(unittest.TestCase):
    """NetworkTestService.run_traceroute handles IPv4 and IPv6 paths."""

    def test_traceroute_ipv4(self):
        src = Mock()
        src.cmd.return_value = "traceroute to 10.0.0.2 (10.0.0.2), 30 hops max"
        lab = _lab_of(h1=src)

        result = NetworkTestService.run_traceroute(lab, "h1", "10.0.0.2")

        self.assertTrue(result["success"])
        src.cmd.assert_called_with("traceroute -n 10.0.0.2")

    def test_resolve_drops_the_numeric_flag_in_both_families(self):
        for ipv6, expected in ((False, "traceroute 10.0.0.2"), (True, "traceroute6 2001:db8::2")):
            with self.subTest(ipv6=ipv6):
                src = _node(name="h1")
                destination = "2001:db8::2" if ipv6 else "10.0.0.2"
                NetworkTestService.run_traceroute(_lab_of(h1=src), "h1", destination,
                                                  ipv6=ipv6, resolve=True)
                src.cmd.assert_called_once_with(expected)

    def test_traceroute_ipv6_fallback_when_not_installed(self):
        src = Mock()
        src.cmd.return_value = "traceroute6: command not found"
        lab = _lab_of(h1=src, h2=_node(ip6="2001:db8::2"))

        result = NetworkTestService.run_traceroute(lab, "h1", "h2", ipv6=True)

        self.assertFalse(result["success"])
        self.assertIn("Traceroute not installed", result["detail"])

    def test_traceroute_missing_source_raises(self):
        lab = Mock()
        lab.get.return_value = None
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_traceroute(lab, "h1", "10.0.0.2")
        self.assertIn("Source node not found", str(ctx.exception))

    def test_ipv4_traceroute_to_a_node_name_uses_its_default_interfaces_live_address(self):
        src, dst = _node(name="h1"), _node(ip="10.0.9.2")
        lab = _lab_of(h1=src, h2=dst)

        NetworkTestService.run_traceroute(lab, "h1", "h2")

        src.cmd.assert_called_once_with("traceroute -n 10.0.9.2")

    def test_ipv6_traceroute_to_a_node_name_uses_the_default_interfaces_global_address(self):
        """A loopback's address on the same Node is not the one chosen."""
        src, dst = _node(name="h1"), _node(ip6="2001:db8:1::2")
        dst.cmd.return_value = "1: lo inet6 2001:db8:ffff::1/128 scope global"
        lab = _lab_of(h1=src, r2=dst)

        NetworkTestService.run_traceroute(lab, "h1", "r2", ipv6=True)

        src.cmd.assert_called_once_with("traceroute6 -n 2001:db8:1::2")
        dst.cmd.assert_not_called()

    def test_traceroute_passes_an_address_and_a_domain_name_unchanged_and_quoted(self):
        for destination, expected in (
            ("2001:db8::9", "traceroute6 -n 2001:db8::9"),
            ("web.example.com", "traceroute6 -n web.example.com"),
            ("x; reboot", "traceroute6 -n 'x; reboot'"),
        ):
            with self.subTest(destination=destination):
                src = _node(name="h1")
                lab = _lab_of(h1=src, h2=_node())
                NetworkTestService.run_traceroute(lab, "h1", destination, ipv6=True)
                src.cmd.assert_called_once_with(expected)

    def test_a_node_without_an_address_in_the_family_is_an_error_and_nothing_runs(self):
        for ipv6, dst in (
            (False, _node(ip=None, intf_name="h2-eth0")),
            (True, _node(ip6=None, intf_name="h2-eth0")),
            (True, _node(with_ip6=False, intf_name="h2-eth0")),
        ):
            with self.subTest(ipv6=ipv6):
                src = _node(name="h1")
                lab = _lab_of(h1=src, h2=dst)

                with self.assertRaises(ValueError) as ctx:
                    NetworkTestService.run_traceroute(lab, "h1", "h2", ipv6=ipv6)

                self.assertIn("h2", str(ctx.exception))
                self.assertIn("h2-eth0", str(ctx.exception))
                src.cmd.assert_not_called()


class NetworkTestServiceIperf3Tests(unittest.TestCase):
    """NetworkTestService.run_iperf3 validates inputs and orchestrates test."""

    def test_run_iperf3_validates_duration_range(self):
        lab = Mock()
        srv, cli = Mock(), Mock()

        with self.assertRaises(ValueError) as ctx:
            lab.get.side_effect = [srv, cli]
            NetworkTestService.run_iperf3(lab, "srv", "cli", seconds=0)
        self.assertIn("between 1 and 60", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            lab.get.side_effect = [srv, cli]
            NetworkTestService.run_iperf3(lab, "srv", "cli", seconds=61)
        self.assertIn("between 1 and 60", str(ctx.exception))

    def test_run_iperf3_validates_protocol(self):
        lab = Mock()
        srv, cli = Mock(), Mock()
        lab.get.side_effect = [srv, cli]

        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_iperf3(lab, "srv", "cli", protocol="sctp")
        self.assertIn("'tcp' or 'udp'", str(ctx.exception))

    def test_run_iperf3_missing_node_raises(self):
        lab = Mock()
        lab.get.return_value = None
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_iperf3(lab, "srv", "cli")
        self.assertIn("Node not found", str(ctx.exception))

    def test_run_iperf3_missing_server_ip_raises(self):
        lab = Mock()
        srv = Mock()
        srv.intfs = {}
        lab.get.side_effect = [srv, Mock()]

        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_iperf3(lab, "srv", "cli")
        self.assertIn("has no IP address", str(ctx.exception))

    def test_run_iperf3_success(self):
        lab = Mock()
        srv = Mock()
        cli = Mock()
        srv.intfs = {0: Mock(ip="10.0.0.1/24")}
        srv.popen.return_value = Mock(terminate=Mock(), wait=Mock())
        cli.cmd.return_value = json.dumps({
            "start": {},
            "end": {
                "sum_sent": {"bits_per_second": 1_000_000_000},
                "sum_received": {"bits_per_second": 900_000_000},
            },
        })
        lab.get.side_effect = [srv, cli]

        result = NetworkTestService.run_iperf3(lab, "srv", "cli", seconds=5, json_output=True)

        self.assertTrue(result["success"])
        srv.popen.assert_called_with("iperf3 -s")
        cli.cmd.assert_called_with("iperf3 -c 10.0.0.1 -t 5 -J")

    def test_run_iperf3_udp_mode(self):
        lab = Mock()
        srv = Mock()
        cli = Mock()
        srv.intfs = {0: Mock(ip="10.0.0.1/24")}
        srv.popen.return_value = Mock(terminate=Mock(), wait=Mock())
        cli.cmd.return_value = ""
        lab.get.side_effect = [srv, cli]

        NetworkTestService.run_iperf3(lab, "srv", "cli", seconds=2, protocol="udp")
        cli.cmd.assert_called_with("iperf3 -c 10.0.0.1 -t 2 -u")


class RoutingQueryServiceTests(unittest.TestCase):
    """RoutingQueryService queries node routing state safely."""

    def test_get_node_routes_ipv4_only(self):
        lab = Mock()
        node = Mock()
        node.cmd.return_value = "default via 10.0.0.1 dev eth0\n10.0.0.0/24 dev eth0"
        lab.get.return_value = node

        result = RoutingQueryService.get_node_routes(lab, "r1", ip_version="4")

        self.assertTrue(result["success"])
        self.assertIn("IPv4 Routes", result["output"])
        self.assertNotIn("IPv6 Routes", result["output"])

    def test_get_node_routes_ipv6_only(self):
        lab = Mock()
        node = Mock()
        node.cmd.return_value = "2001:db8::/64 dev eth0"
        lab.get.return_value = node

        result = RoutingQueryService.get_node_routes(lab, "r1", ip_version="6")

        self.assertTrue(result["success"])
        self.assertIn("IPv6 Routes", result["output"])
        self.assertNotIn("IPv4 Routes", result["output"])

    def test_get_node_routes_both_versions(self):
        lab = Mock()
        node = Mock()
        node.cmd.return_value = ""
        lab.get.return_value = node

        result = RoutingQueryService.get_node_routes(lab, "r1")

        self.assertTrue(result["success"])
        self.assertIn("IPv4 Routes", result["output"])
        self.assertIn("IPv6 Routes", result["output"])

    def test_get_node_routes_missing_node_raises(self):
        lab = Mock()
        lab.get.return_value = None
        with self.assertRaises(ValueError) as ctx:
            RoutingQueryService.get_node_routes(lab, "r1")
        self.assertIn("Node not found", str(ctx.exception))

    def test_run_vtysh_on_router(self):
        lab = Mock()
        router = Mock(spec=FRRouter)
        router.cmd.return_value = " zebra bgpd"
        lab.get.return_value = router

        result = RoutingQueryService.run_vtysh(lab, "r1", "show daemons")

        self.assertTrue(result["success"])
        router.cmd.assert_called_with('vtysh -c "show daemons"')

    def test_run_vtysh_rejects_non_router(self):
        lab = Mock()
        host = Mock()  # Not an FRRouter
        lab.get.return_value = host

        result = RoutingQueryService.run_vtysh(lab, "h1", "show ip route")

        self.assertFalse(result["success"])
        self.assertIn("not an FRR router", result["detail"])

    def test_run_vtysh_missing_node_raises(self):
        lab = Mock()
        lab.get.return_value = None
        with self.assertRaises(ValueError) as ctx:
            RoutingQueryService.run_vtysh(lab, "r1", "show ip route")
        self.assertIn("Node not found", str(ctx.exception))

    def test_get_node_ospf_composes_output(self):
        lab = Mock()
        router = Mock(spec=FRRouter)
        router.cmd.return_value = "Neighbor ID\nr1\n"
        lab.get.return_value = router

        result = RoutingQueryService.get_node_ospf(lab, "r1")

        self.assertTrue(result["success"])
        self.assertIn("OSPF Neighbors", result["output"])
        self.assertIn("OSPF Routes", result["output"])

    def test_get_node_bgp_composes_output(self):
        lab = Mock()
        router = Mock(spec=FRRouter)
        router.cmd.return_value = "BGP table version\n"
        lab.get.return_value = router

        result = RoutingQueryService.get_node_bgp(lab, "r1")

        self.assertTrue(result["success"])
        self.assertIn("BGP Summary", result["output"])
        self.assertIn("BGP Routes", result["output"])


class HostNetworkCommandRefusalTests(unittest.TestCase):
    """A command on an Internet Gateway is refused; a Switch's is not."""

    REASON = "nat0 is the Internet, which is outside the Lab"

    def setUp(self):
        self.gateway = Mock()
        self.gateway.inNamespace = False
        self.switch = Mock()
        self.switch.inNamespace = False
        self.router = Mock()
        self.nodes = {"nat0": self.gateway, "s1": self.switch, "r1": self.router}
        self.lab = Mock()
        self.lab.get.side_effect = lambda name: self.nodes.get(name)
        self.lab.switches = [self.switch]

    def test_ping_from_an_internet_gateway_is_refused_and_runs_nothing(self):
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_ping(self.lab, "nat0", "10.0.0.1")

        self.assertEqual(str(ctx.exception), self.REASON)
        self.gateway.cmd.assert_not_called()

    def test_ping_from_a_switch_still_runs(self):
        result = NetworkTestService.run_ping(self.lab, "s1", "10.0.0.1")

        self.assertTrue(result["success"])
        self.switch.cmd.assert_called_once()

    def test_traceroute_from_an_internet_gateway_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_traceroute(self.lab, "nat0", "10.0.0.1")

        self.assertEqual(str(ctx.exception), self.REASON)
        self.gateway.cmd.assert_not_called()

    def test_ipv6_traceroute_to_an_internet_gateway_is_refused_before_its_addresses_are_read(self):
        with self.assertRaises(ValueError) as ctx:
            NetworkTestService.run_traceroute(self.lab, "r1", "nat0", ipv6=True)

        self.assertEqual(str(ctx.exception), self.REASON)
        self.gateway.cmd.assert_not_called()
        self.router.cmd.assert_not_called()

    def test_iperf_refuses_an_internet_gateway_at_either_end(self):
        for server, client in (("nat0", "r1"), ("r1", "nat0")):
            with self.subTest(server=server, client=client):
                with self.assertRaises(ValueError) as ctx:
                    NetworkTestService.run_iperf(self.lab, server, client)

                self.assertEqual(str(ctx.exception), self.REASON)
                self.lab.iperf.assert_not_called()

    def test_iperf3_refuses_an_internet_gateway_at_either_end(self):
        for server, client in (("nat0", "r1"), ("r1", "nat0")):
            with self.subTest(server=server, client=client):
                with self.assertRaises(ValueError) as ctx:
                    NetworkTestService.run_iperf3(self.lab, server, client)

                self.assertEqual(str(ctx.exception), self.REASON)
        self.gateway.popen.assert_not_called()
        self.gateway.cmd.assert_not_called()

    def test_routes_of_an_internet_gateway_are_refused(self):
        with self.assertRaises(ValueError) as ctx:
            RoutingQueryService.get_node_routes(self.lab, "nat0")

        self.assertEqual(str(ctx.exception), self.REASON)
        self.gateway.cmd.assert_not_called()

    def test_vtysh_on_an_internet_gateway_is_refused_rather_than_answered(self):
        with self.assertRaises(ValueError) as ctx:
            RoutingQueryService.run_vtysh(self.lab, "nat0", "show ip ospf route")

        self.assertEqual(str(ctx.exception), self.REASON)
        self.gateway.cmd.assert_not_called()


if __name__ == "__main__":
    unittest.main()
