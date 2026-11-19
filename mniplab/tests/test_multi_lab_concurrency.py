"""Integration tests for multi-lab concurrency and scoped cleanup isolation.

Two Test Labs with identical Node names start in one process, both
converge, one stops, and the other is whole; the same holds when the first
stop is forced to fail and the emergency fallback runs. The first pair
covers Routers and Service Nodes; the second pair covers the objects that
live in the host's one namespace — a switch, a NAT Gateway and a Container
Host each — the case upstream Mininet has left open since 2012.
"""

from __future__ import annotations

import os
import select
import shlex
import socket
import subprocess
import threading
import time
import unittest
from typing import Dict, List
from unittest.mock import patch
import pytest

from mniplab import FRRouter, mnIPLab
from mniplab.constants import MNIPLAB_TMP_DIR
from mniplab.dhcp import DHCPService, Pool
from mniplab.dhcp.query import DHCPQueryService
from mniplab.dns import ARecord, DNSService, Zone
from mniplab.dns.query import DNSQueryService
from mniplab.exabgp import BGPNeighbor, BGPRoute
from mniplab.exabgp.query import SpeakerQueryService
from mniplab.resolver import ResolverService
from mniplab.resolver.query import ResolverQueryService
from mniplab.tests.labs import _stop_or_sweep


@pytest.mark.integration
@pytest.mark.requires_root
class TestMultiLabConcurrency(unittest.TestCase):
    """Verify concurrency isolation between multiple Labs running in one process."""

    def _build_test_lab(self, lab_key: str) -> mnIPLab:
        net = mnIPLab(topo=None, controller=None, lab_key=lab_key)

        # 1. Router r1
        r1 = net.addRouter("r1", proto="OSPF")

        # 2. DHCP Server dhcp1
        dhcp1 = net.addHost("dhcp1", defaultRoute="via 10.0.1.1")
        dhcp_svc = DHCPService(Pool(cidr="10.0.1.0/24", start="10.0.1.10", end="10.0.1.50"))
        dhcp1.addService(dhcp_svc)

        # 3. DNS Server dns1
        dns1 = net.addHost("dns1", defaultRoute="via 10.0.2.1")
        dns_svc = DNSService()
        zone = Zone("example.com.")
        zone.add_record(ARecord(name="www", address="10.0.2.100"))
        dns_svc.add_zone(zone)
        dns1.addService(dns_svc)

        # 4. Resolver res1
        res1 = net.addHost("res1", defaultRoute="via 10.0.3.1")
        res_svc = ResolverService(forwarders=["10.0.2.2"])
        res1.addService(res_svc)

        # 5. Speaker sp1
        sp1 = net.addExaBGP(
            "sp1",
            asn=65001,
            neighbors=[
                BGPNeighbor(
                    peer="10.0.4.1",
                    local_address="10.0.4.2",
                    local_as=65001,
                    peer_as=65000,
                    routes=[BGPRoute("203.0.113.0/24", next_hop="10.0.4.2")],
                )
            ],
        )

        # Links connecting r1 to each node
        net.addLink(r1, dhcp1, params1={"ip": "10.0.1.1/24"}, params2={"ip": "10.0.1.2/24"})
        net.addLink(r1, dns1, params1={"ip": "10.0.2.1/24"}, params2={"ip": "10.0.2.2/24"})
        net.addLink(r1, res1, params1={"ip": "10.0.3.1/24"}, params2={"ip": "10.0.3.2/24"})
        net.addLink(r1, sp1, params1={"ip": "10.0.4.1/24"}, params2={"ip": "10.0.4.2/24"})

        return net

    def test_two_labs_concurrent_clean_stop_one_preserves_other(self) -> None:
        """One Lab stopping cleanly leaves the other Lab's trees and services answering."""
        net1 = self._build_test_lab("con1")
        net2 = self._build_test_lab("con2")

        try:
            net1.start()
            net2.start()

            # Verify both have their ephemeral trees populated
            labs_dir = os.path.join(MNIPLAB_TMP_DIR, "labs")
            active_keys = os.listdir(labs_dir)
            self.assertIn("con1", active_keys)
            self.assertIn("con2", active_keys)
            self.assertTrue(os.path.exists(net1.lab_dir))
            self.assertTrue(os.path.exists(net2.lab_dir))

            self.assertTrue(os.path.exists(net1.frr_config_dir("r1")))
            self.assertTrue(os.path.exists(net2.frr_config_dir("r1")))
            self.assertTrue(os.path.exists(net1.hosts_file))
            self.assertTrue(os.path.exists(net2.hosts_file))
            self.assertTrue(os.path.exists(net1.dns_dir("dns1")))
            self.assertTrue(os.path.exists(net2.dns_dir("dns1")))

            # Stop net1 cleanly
            net1.stop()

            # net1's Lab directory is gone whole
            self.assertFalse(os.path.exists(net1.lab_dir))
            self.assertFalse(os.path.exists(net1.frr_config_dir("r1")))
            self.assertFalse(os.path.exists(net1.hosts_file))
            self.assertFalse(os.path.exists(net1.dns_dir("dns1")))

            # net2's directory, trees, hosts file, and routing config bind source must be intact
            self.assertTrue(os.path.exists(net2.lab_dir))
            self.assertTrue(os.path.exists(net2.frr_config_dir("r1")))
            self.assertTrue(os.path.exists(net2.hosts_file))
            self.assertTrue(os.path.exists(net2.dhcp_dir("dhcp1")))
            self.assertTrue(os.path.exists(net2.dns_dir("dns1")))
            self.assertTrue(os.path.exists(net2.resolver_dir("res1")))
            self.assertTrue(os.path.exists(net2.exabgp_dir("sp1")))

            # net2's services must still answer
            dns_info = DNSQueryService.get_dns_info(net2, "dns1")
            self.assertTrue(dns_info["running"])
            self.assertIn("example.com.", dns_info["zones"])

            res_info = ResolverQueryService.get_resolver_info(net2, "res1")
            self.assertTrue(res_info["running"])

            _, _, dhcp_client = DHCPQueryService.get_client(net2, "dhcp1", family=4)
            self.assertTrue(dhcp_client.is_running())

            sp_info = SpeakerQueryService.get_speaker(net2, "sp1")
            self.assertEqual(sp_info["node"], "sp1")

            # In-namespace verification
            dns_ans = net2.get("dns1").cmd("dig @127.0.0.1 www.example.com +short")
            self.assertIn("10.0.2.100", dns_ans)

        finally:
            _stop_or_sweep(net1)
            _stop_or_sweep(net2)

    def _connect_node(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        """Run util/connect_node.sh with these arguments and environment."""
        script = os.path.join(os.path.dirname(__file__), "..", "..", "util", "connect_node.sh")
        run_env = {k: v for k, v in os.environ.items() if k != "MNIPLAB_LAB_KEY"}
        run_env.update(env)
        return subprocess.run(
            [os.path.abspath(script), *args], capture_output=True, text=True, env=run_env, check=False
        )

    def test_connect_node_needs_the_key_when_two_labs_share_a_node_name(self) -> None:
        """connect_node.sh refuses a bare r1 with two Labs alive and lands in the named one."""
        net1 = self._build_test_lab("con1")
        net2 = self._build_test_lab("con2")

        try:
            net1.start()
            net2.start()
            # A marker in each Lab's r1 home says which Lab a command ran in.
            for net in (net1, net2):
                with open(os.path.join(net.node_home_dir("r1"), "lab"), "w") as f:
                    f.write(net.lab_key)

            bare = self._connect_node("r1", "hostname")
            self.assertNotEqual(bare.returncode, 0)
            self.assertIn("con1", bare.stderr)
            self.assertIn("con2", bare.stderr)

            by_flag = self._connect_node("--lab", "con2", "r1", "cat", "lab")
            self.assertEqual(by_flag.returncode, 0, by_flag.stderr)
            self.assertEqual(by_flag.stdout.strip(), "con2")

            by_env = self._connect_node("r1", "cat", "lab", MNIPLAB_LAB_KEY="con1")
            self.assertEqual(by_env.returncode, 0, by_env.stderr)
            self.assertEqual(by_env.stdout.strip(), "con1")

            home = self._connect_node("--lab", "con1", "r1", "pwd")
            self.assertEqual(home.stdout.strip(), net1.node_home_dir("r1"))

            listed = self._connect_node("--lab", "con2", "--list")
            self.assertEqual(sorted(listed.stdout.split()), ["dhcp1", "dns1", "r1", "res1", "sp1"])

            net1.stop()

            # One Lab alive: the bare form works as it always did.
            alone = self._connect_node("r1", "cat", "lab")
            self.assertEqual(alone.returncode, 0, alone.stderr)
            self.assertEqual(alone.stdout.strip(), "con2")
        finally:
            _stop_or_sweep(net1)
            _stop_or_sweep(net2)

    def test_two_labs_concurrent_emergency_stop_fallback_preserves_other(self) -> None:
        """When net1.stop() fails and emergency cleanup runs, net2 remains intact."""
        net1 = self._build_test_lab("con3")
        net2 = self._build_test_lab("con4")

        try:
            net1.start()
            net2.start()

            labs_dir = os.path.join(MNIPLAB_TMP_DIR, "labs")
            active_keys = os.listdir(labs_dir)
            self.assertIn("con3", active_keys)
            self.assertIn("con4", active_keys)
            self.assertTrue(os.path.exists(net1.lab_dir))
            self.assertTrue(os.path.exists(net2.lab_dir))

            # Force net1.stop() to fail and invoke _stop_or_sweep emergency fallback
            with patch.object(net1, "_cleanup_ephemeral_config_dirs", side_effect=RuntimeError("forced failure")):
                with self.assertRaises(RuntimeError):
                    _stop_or_sweep(net1)

            # net1's Lab directory should have been cleaned whole by emergency_cleanup
            self.assertFalse(os.path.exists(net1.lab_dir))
            self.assertFalse(os.path.exists(net1.frr_config_dir("r1")))

            # net2's directory, trees, hosts file, and services must remain intact
            self.assertTrue(os.path.exists(net2.lab_dir))
            self.assertTrue(os.path.exists(net2.frr_config_dir("r1")))
            self.assertTrue(os.path.exists(net2.hosts_file))
            self.assertTrue(os.path.exists(net2.dns_dir("dns1")))
            self.assertTrue(os.path.exists(net2.resolver_dir("res1")))

            dns_info = DNSQueryService.get_dns_info(net2, "dns1")
            self.assertTrue(dns_info["running"])

            res_info = ResolverQueryService.get_resolver_info(net2, "res1")
            self.assertTrue(res_info["running"])

        finally:
            _stop_or_sweep(net1)
            _stop_or_sweep(net2)


def _host_default_gateway() -> str:
    """Return the host's default gateway: the outside world, one hop away."""
    out = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, check=False).stdout
    for line in out.splitlines():
        parts = line.split()
        if "via" in parts:
            return parts[parts.index("via") + 1]
    raise unittest.SkipTest("the host has no default gateway to reach")


def _host_uplink_address() -> str:
    """Return the host's own IPv4 address on the default route interface."""
    out = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, check=False).stdout
    uplink_dev = None
    for line in out.splitlines():
        parts = line.split()
        if "dev" in parts:
            uplink_dev = parts[parts.index("dev") + 1]
            break
    if not uplink_dev:
        raise unittest.SkipTest("the host has no default gateway to reach")

    addr_out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", uplink_dev], capture_output=True, text=True, check=False).stdout
    for line in addr_out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            return parts[3].split("/")[0]
    raise unittest.SkipTest(f"the host has no IPv4 address on {uplink_dev}")


def _root_links() -> str:
    return subprocess.run(["ip", "-o", "link"], capture_output=True, text=True, check=False).stdout


def _ovs_show() -> str:
    return subprocess.run(["ovs-vsctl", "--timeout=5", "show"], capture_output=True, text=True, check=False).stdout


def _docker_names() -> str:
    return subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=False).stdout


def _nat_rules() -> str:
    mangle = subprocess.run(["iptables", "-t", "mangle", "-S"], capture_output=True, text=True, check=False).stdout
    nat = subprocess.run(["iptables", "-t", "nat", "-S"], capture_output=True, text=True, check=False).stdout
    return mangle + nat


def _raw_rules() -> str:
    return subprocess.run(["iptables", "-t", "raw", "-S"], capture_output=True, text=True, check=False).stdout


def _ip_rules() -> str:
    return subprocess.run(["ip", "rule", "show"], capture_output=True, text=True, check=False).stdout


def _table_routes(mark: int) -> str:
    return subprocess.run(["ip", "route", "show", "table", str(mark)], capture_output=True, text=True, check=False).stdout


def _ip_forward() -> str:
    with open("/proc/sys/net/ipv4/ip_forward", encoding="utf-8") as f:
        return f.read().strip()


@pytest.mark.integration
@pytest.mark.requires_root
@pytest.mark.requires_ovs
@pytest.mark.requires_docker
@pytest.mark.slow
class TestMultiLabRootNamespaceObjects(unittest.TestCase):
    """Two Labs with the same switch, NAT Gateway and Container Host names share one host."""

    def _build_test_lab(self, lab_key: str) -> mnIPLab:
        """Build a Test Lab: h1 and d1 on s1 behind r1, which reaches out through nat1.

        Both Labs share the same NAT link subnet (100.64.0.0/24); policy routing
        and conntrack zones isolate each Lab's traffic in the root namespace.
        """
        net = mnIPLab(topo=None, controller=None, lab_key=lab_key)
        r1 = net.addRouter("r1")
        nat1 = net.addNAT("nat1", subnet="100.64.0.0/24")
        net.addLink(nat1, r1, params1={"ip": "100.64.0.1/24"}, params2={"ip": "100.64.0.2/24"})
        s1 = net.addSwitch("s1")
        h1 = net.addHost("h1", defaultRoute="via 10.0.0.1")
        d1 = net.addContainer("d1", dimage="alpine:3.20", defaultRoute="via 10.0.0.1")
        net.addLink(r1, s1, params1={"ip": "10.0.0.1/24"})
        net.addLink(h1, s1, params1={"ip": "10.0.0.2/24"})
        net.addLink(d1, s1, params1={"ip": "10.0.0.3/24"})
        return net

    def _assert_lab_whole(self, net: mnIPLab, gateway: str) -> None:
        """The Lab's root-namespace objects exist and traffic still flows through them."""
        key = net.lab_key
        mark = net.get("nat1")._mark
        zone = mark >> 8
        self.assertIn(net.bridge_name("s1"), _ovs_show())
        self.assertIn(net.root_intf_name("s1", 1), _ovs_show())
        links = _root_links()
        self.assertIn(net.root_intf_name("s1", 1), links)
        self.assertIn(net.root_intf_name("nat1", 0), links)
        self.assertIn(net.container_name("d1"), _docker_names())
        self.assertTrue(os.path.islink(net.netns_link_path("d1")), net.netns_link_path("d1"))
        rules = _nat_rules()
        self.assertIn(f"-i {net.root_intf_name('nat1', 0)} -j MARK", rules)
        self.assertIn(f"--mark {hex(mark)}", rules)
        self.assertIn(f"-i {net.root_intf_name('nat1', 0)} -j CONNMARK --save-mark", rules)
        self.assertIn(f"-j CT --zone-orig {zone}", _raw_rules())
        self.assertEqual(_ip_forward(), "1")

        h1, d1 = net.get("h1"), net.get("d1")
        self.assertIn(" 0% packet loss", h1.cmd("ping -c 2 -W 1 10.0.0.3"), f"{key}: h1 cannot reach d1 over s1")
        self.assertIn(" 0% packet loss", h1.cmd(f"ping -c 2 -W 2 {gateway}"), f"{key}: h1 cannot reach the outside world")
        self.assertIn(" 0% packet loss", d1.cmd(f"ping -c 2 -W 2 {gateway}"), f"{key}: d1 cannot reach the outside world")

    def _assert_lab_gone(self, net: mnIPLab) -> None:
        """Nothing of the Lab remains in the host's namespace."""
        mark = net.get("nat1")._mark
        self.assertNotIn(net.bridge_name("s1"), _ovs_show())
        self.assertNotIn(net.root_intf_name("s1", 1), _root_links())
        self.assertNotIn(net.root_intf_name("nat1", 0), _root_links())
        self.assertNotIn(net.container_name("d1"), _docker_names())
        self.assertFalse(os.path.lexists(net.netns_link_path("d1")))
        self.assertNotIn(net.root_intf_name("nat1", 0), _nat_rules())
        self.assertNotIn(f"fwmark {hex(mark)}", _ip_rules())
        self.assertNotIn(net.root_intf_name("nat1", 0), _raw_rules())
        self.assertEqual(_table_routes(mark).strip(), "")

    def _assert_names_are_unchanged_inside(self, net: mnIPLab) -> None:
        """A Learner in a Node sees the same hostname and interface names as ever."""
        for name, intf in (("h1", "h1-eth0"), ("r1", "r1-eth1"), ("d1", "d1-eth0")):
            node = net.get(name)
            self.assertEqual(node.cmd("hostname").strip(), name)
            self.assertIn(intf, node.cmd("ip -o link"))
            self.assertNotIn(net.lab_key, node.cmd("ip -o link"))

    def test_two_labs_side_by_side_then_one_clean_stop(self) -> None:
        """Both Labs' objects show side by side on the host; stopping one leaves the other whole."""
        gateway = _host_default_gateway()
        forward_before = _ip_forward()
        net1 = self._build_test_lab("rn1")
        net2 = self._build_test_lab("rn2")
        try:
            net1.start()
            net2.start()

            for net in (net1, net2):
                self._assert_lab_whole(net, gateway)
                self._assert_names_are_unchanged_inside(net)

            # Both Labs' objects, each carrying its key, side by side on the host
            show, names = _ovs_show(), _docker_names()
            self.assertIn("rn1-s1", show)
            self.assertIn("rn2-s1", show)
            self.assertIn("mn.rn1.d1", names)
            self.assertIn("mn.rn2.d1", names)

            net1.stop()

            self._assert_lab_gone(net1)
            self._assert_lab_whole(net2, gateway)
        finally:
            _stop_or_sweep(net1)
            _stop_or_sweep(net2)

        self._assert_lab_gone(net2)
        self.assertEqual(_ip_forward(), forward_before, "the last Lab to stop restores ip_forward")

    def test_two_labs_then_one_emergency_stop(self) -> None:
        """When one Lab's stop fails at its first step, the fallback tears down a fully alive Lab and the other is whole."""
        gateway = _host_default_gateway()
        net1 = self._build_test_lab("rn3")
        net2 = self._build_test_lab("rn4")
        try:
            net1.start()
            net2.start()

            with patch.object(net1._lifecycle, "cleanup_all", side_effect=RuntimeError("forced failure")):
                with self.assertRaises(RuntimeError):
                    _stop_or_sweep(net1)

            self._assert_lab_gone(net1)
            self._assert_lab_whole(net2, gateway)
        finally:
            _stop_or_sweep(net1)
            _stop_or_sweep(net2)

    def test_key_alone_sweeps_a_lab_whose_process_is_gone(self) -> None:
        """The server's fallback has only a key: it must still clear the host's namespace."""
        from mniplab.cleanup import emergency_cleanup

        gateway = _host_default_gateway()
        net1 = self._build_test_lab("rn5")
        net2 = self._build_test_lab("rn6")
        try:
            net1.start()
            net2.start()

            emergency_cleanup(lab_key=net1.lab_key)

            self._assert_lab_gone(net1)
            self._assert_lab_whole(net2, gateway)
        finally:
            _stop_or_sweep(net1)
            _stop_or_sweep(net2)

    def test_two_labs_identical_5_tuple_collision_against_host_listener(self) -> None:
        """Two Labs opening identical 5-tuples simultaneously connect without delay.

        The listener answers neither client until both have sent, so both
        connections are open and carrying data at the same moment.

        When two Labs share the same NAT link subnet, both peer Routers egress
        with the identical source IP (100.64.0.2). If both open a TCP connection
        to the same destination (uplink_ip:port) from the same source port
        (40000), the 5-tuple (TCP, 100.64.0.2, 40000, uplink_ip, port) is
        completely identical.

        Motivation / Failure without raw CT rule:
        Without the '-t raw -A PREROUTING -i <veth> -j CT --zone-orig <zone>' rule,
        Linux conntrack tracks both flows in the default zone 0. When the second
        SYN arrives while the first connection is established, conntrack treats
        it as an out-of-window duplicate packet belonging to the first session,
        causing a retransmit-length timeout / failure. With the conntrack zone
        per Lab, conntrack separates the original tuples by zone, allowing both
        connections to complete immediately without retransmit delays.
        """
        uplink_ip = _host_uplink_address()

        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((uplink_ip, 0))
        server_port = server_sock.getsockname()[1]
        server_sock.listen(5)

        server_running = True

        def server_loop() -> None:
            """Accept both clients, hold both open, and answer neither until both have sent."""
            clients: List[socket.socket] = []
            pending: Dict[socket.socket, bytes] = {}
            while server_running:
                try:
                    readable, _, _ = select.select([server_sock] + clients, [], [], 0.2)
                except (OSError, ValueError):
                    break
                for sock in readable:
                    if sock is server_sock:
                        try:
                            clients.append(sock.accept()[0])
                        except OSError:
                            pass
                        continue
                    try:
                        data = sock.recv(1024)
                    except OSError:
                        data = b""
                    if data:
                        pending[sock] = data
                    else:
                        clients.remove(sock)
                if len(pending) == 2:
                    for sock, data in pending.items():
                        try:
                            sock.sendall(b"OK:" + data)
                        except OSError:
                            pass
                    pending.clear()
            for client in clients:
                client.close()

        server_thread = threading.Thread(target=server_loop, daemon=True)
        server_thread.start()

        net1 = self._build_test_lab("col1")
        net2 = self._build_test_lab("col2")

        try:
            net1.start()
            net2.start()

            client_py = (
                "import socket, sys, time; "
                "tag = sys.argv[1]; "
                "t0 = time.monotonic(); "
                "s = socket.socket(); "
                "s.settimeout(6); "
                "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
                "s.bind(('100.64.0.2', 40000)); "
                f"s.connect(('{uplink_ip}', {server_port})); "
                "t_connect = time.monotonic() - t0; "
                "time.sleep(1.5); "
                "s.sendall(tag.encode('utf-8')); "
                "reply = s.recv(64); "
                "s.close(); "
                "print(f'{tag}:connect={t_connect:.3f}:reply={reply.decode(\"utf-8\")}')"
            )

            results: Dict[str, str] = {}

            def run_client(node: FRRouter, tag: str, delay: float) -> None:
                time.sleep(delay)
                results[tag] = node.cmd(f"python3 -c {shlex.quote(client_py)} {tag}").strip()

            threads = [
                threading.Thread(target=run_client, args=(net1.get("r1"), "LAB1", 0.0)),
                threading.Thread(target=run_client, args=(net2.get("r1"), "LAB2", 0.3)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            out1 = results.get("LAB1", "")
            out2 = results.get("LAB2", "")

            self.assertIn("LAB1:connect=", out1, f"Lab 1 client failed: {out1}")
            self.assertIn("LAB2:connect=", out2, f"Lab 2 client failed: {out2}")
            self.assertIn("reply=OK:LAB1", out1)
            self.assertIn("reply=OK:LAB2", out2)

            connect_time1 = float(out1.split("connect=")[1].split(":")[0])
            connect_time2 = float(out2.split("connect=")[1].split(":")[0])
            self.assertLess(connect_time1, 1.0, f"Lab 1 connect delayed by retransmit: {connect_time1}s")
            self.assertLess(connect_time2, 1.0, f"Lab 2 connect delayed by retransmit: {connect_time2}s")

        finally:
            server_running = False
            server_thread.join(timeout=2)
            server_sock.close()
            _stop_or_sweep(net1)
            _stop_or_sweep(net2)

    def test_full_cleanup_removes_rules_and_tables_for_all_labs(self) -> None:
        """python -m mniplab cleanup leaves no ip rules, table routes, or raw CT rules."""
        cleaned = False
        net1 = self._build_test_lab("cln1")
        net2 = self._build_test_lab("cln2")
        try:
            net1.start()
            net2.start()

            mark1 = net1.get("nat1")._mark
            mark2 = net2.get("nat1")._mark

            from mniplab.cleanup import full_cleanup
            full_cleanup(verbose=False)
            cleaned = True

            rules_out = subprocess.run(["ip", "rule", "show"], capture_output=True, text=True, check=False).stdout
            self.assertNotIn(f"fwmark {hex(mark1)}", rules_out)
            self.assertNotIn(f"fwmark {hex(mark2)}", rules_out)
            self.assertNotIn(f"lookup {mark1}", rules_out)
            self.assertNotIn(f"lookup {mark2}", rules_out)

            table1_out = subprocess.run(["ip", "route", "show", "table", str(mark1)], capture_output=True, text=True, check=False).stdout
            table2_out = subprocess.run(["ip", "route", "show", "table", str(mark2)], capture_output=True, text=True, check=False).stdout
            self.assertEqual(table1_out.strip(), "")
            self.assertEqual(table2_out.strip(), "")

            raw_out = subprocess.run(["iptables", "-t", "raw", "-S", "PREROUTING"], capture_output=True, text=True, check=False).stdout
            self.assertNotIn(f"--zone-orig {mark1 >> 8}", raw_out)
            self.assertNotIn(f"--zone-orig {mark2 >> 8}", raw_out)
            self.assertNotIn(net1.root_intf_name("nat1", 0), raw_out)
            self.assertNotIn(net2.root_intf_name("nat1", 0), raw_out)
        finally:
            if not cleaned:
                _stop_or_sweep(net1)
                _stop_or_sweep(net2)


if __name__ == "__main__":
    unittest.main()
