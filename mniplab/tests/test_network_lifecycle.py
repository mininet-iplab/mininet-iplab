"""Integration tests for mnIPLab network lifecycle.

These tests require root privileges and create actual network namespaces.
Run with: sudo python -m pytest -m integration mniplab/tests/test_network_lifecycle.py -v
"""

import os
import pytest

from mniplab import mnIPLab, mnIPLabCLI
from mniplab.exabgp import BGPNeighbor
from mniplab.utils import wait_until
from mniplab.host import mnIPLabHost
from mniplab.router import FRRouter
from mniplab.tests.labs import LabCase


pytestmark = [pytest.mark.integration, pytest.mark.requires_root]


class NetworkLifecycleTests(LabCase):
    """End-to-end tests for mnIPLab network orchestration."""

    def test_basic_network_start_stop(self):
        """A minimal network with one router and one host can start and stop cleanly."""
        net = self.lab()

        r1 = net.addRouter('r1')
        h1 = net.addHost('h1')
        net.addLink(r1, h1, params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})

        self.start(net)
        # Verify host has expected IP
        addr_output = h1.cmd('ip -4 addr show dev h1-eth0')
        assert '10.0.0.2' in addr_output, f"Host IP missing: {addr_output}"

        # Verify router interface is up
        link_output = r1.cmd('ip link show r1-eth0')
        assert 'state UP' in link_output or 'UP' in link_output, f"Router interface down: {link_output}"
    def test_frr_config_generation(self):
        """Router with OSPF should generate ephemeral FRR config files."""
        net = self.lab()

        r1 = net.addRouter('r1', proto='OSPF')
        net.addLink(r1, net.addHost('h1'),
                    params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})

        self.start(net)
        frr_dir = net.frr_config_dir('r1')
        assert os.path.isdir(frr_dir), f"FRR config dir missing: {frr_dir}"

        assert os.path.exists(os.path.join(frr_dir, 'frr.conf')), "frr.conf not generated"
        assert os.path.exists(os.path.join(frr_dir, 'daemons')), "daemons file not generated"

        # Verify ospfd is enabled in daemons file
        with open(os.path.join(frr_dir, 'daemons')) as f:
            daemons = f.read()
        assert 'ospfd=yes' in daemons, "ospfd not enabled in generated daemons file"
    def test_frr_daemon_startup(self):
        """FRR daemons should be running inside the router namespace after start."""
        net = self.lab()

        r1 = net.addRouter('r1', proto='OSPF')
        net.addLink(r1, net.addHost('h1'),
                    params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})

        self.start(net)
        # Check zebra is running in router namespace
        result = r1.cmd('pgrep zebra')
        assert result.strip(), f"zebra not running in r1 namespace: {result}"

        # Check ospfd is running
        result = r1.cmd('pgrep ospfd')
        assert result.strip(), f"ospfd not running in r1 namespace: {result}"
    def test_ipv6_host_configuration(self):
        """Hosts with IPv6 addresses should have them configured after start."""
        net = self.lab()

        h1 = net.addHost('h1')
        r1 = net.addRouter('r1')
        net.addLink(r1, h1, params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24', 'ip6': '2001:db8::2/64'})

        self.start(net)
        addr_output = h1.cmd('ip -6 addr show dev h1-eth0')
        assert '2001:db8::2' in addr_output, f"IPv6 address missing: {addr_output}"
    def test_exabgp_node_setup(self):
        """ExaBGP node should have config and FIFOs created after start."""
        from mniplab.exabgp.models import BGPRoute, BGPNeighbor

        net = self.lab()

        routes = [BGPRoute(prefix='203.0.113.0/24', next_hop='10.0.0.1', as_path=[65001])]
        neighbors = [BGPNeighbor(peer='10.0.0.2', local_address='10.0.0.1', local_as=65001, peer_as=65002)]

        exa = net.addExaBGP('exa1', neighbors=neighbors, routes=routes)
        net.addHost('peer1')
        net.addLink(exa, net.get('peer1'), params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})

        self.start(net)
        exabgp_dir = exa.get_exabgp_dir()
        assert exabgp_dir and os.path.exists(exabgp_dir), f"ExaBGP config dir missing: {exabgp_dir}"
        assert os.path.exists(os.path.join(exabgp_dir, 'exabgp.conf')), "exabgp.conf not generated"

        run_dir = exa.get_exabgp_run_dir()
        assert run_dir and os.path.exists(run_dir), f"ExaBGP run dir missing: {run_dir}"

    @pytest.mark.slow
    def test_link_failure_and_recovery(self):
        """Link down/up should affect connectivity between hosts."""
        net = self.lab()

        r1 = net.addRouter('r1')
        r2 = net.addRouter('r2')
        h1 = net.addHost('h1', defaultRoute='via 192.168.1.1')
        h2 = net.addHost('h2', defaultRoute='via 192.168.2.1')

        net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
        net.addLink(h2, r2, params1={'ip': '192.168.2.2/24'}, params2={'ip': '192.168.2.1/24'})
        net.addLink(r1, r2, params1={'ip': '10.10.1.1/30'}, params2={'ip': '10.10.1.2/30'})
        r1.add_frr_config('ip route 192.168.2.0/24 10.10.1.2')
        r2.add_frr_config('ip route 192.168.1.0/24 10.10.1.1')

        self.start(net)
        # zebra installs the static routes a moment after the daemons start
        assert wait_until(lambda: 'proto static' in r1.cmd('ip route show 192.168.2.0/24')
                          and 'proto static' in r2.cmd('ip route show 192.168.1.0/24'), 10.0), \
            "static routes not installed after start"

        # Verify initial connectivity
        result = h1.cmd('ping -c 1 -W 2 192.168.2.2')
        assert ' 0% packet loss' in result, f"Initial ping failed: {result}"

        # Bring down the r1-r2 link
        net.configLinkStatus('r1', 'r2', 'down')

        # Verify connectivity is lost
        result = h1.cmd('ping -c 1 -W 2 192.168.2.2')
        assert '100% packet loss' in result or '0 received' in result, f"Ping should fail after link down: {result}"

        # A flap faster than zebra can notice leaves it holding a route the
        # kernel has dropped, so let both routers see the link go down first
        assert wait_until(lambda: 'S>*' not in r1.cmd('vtysh -c "show ip route static"')
                          and 'S>*' not in r2.cmd('vtysh -c "show ip route static"'), 10.0), \
            "zebra still selects the static routes after link down"

        # Bring the link back up
        net.configLinkStatus('r1', 'r2', 'up')

        # zebra reinstalls the static routes once the link is back
        assert wait_until(lambda: 'proto static' in r1.cmd('ip route show 192.168.2.0/24')
                          and 'proto static' in r2.cmd('ip route show 192.168.1.0/24'), 10.0), \
            "static routes not reinstalled after link up"

        # Verify connectivity is restored
        result = h1.cmd('ping -c 1 -W 2 192.168.2.2')
        assert ' 0% packet loss' in result, f"Ping should succeed after link up: {result}"
    def test_popen_process_terminates_in_node(self):
        """A process started via Node.popen() runs inside the Node and
        terminating it kills the process inside the Node, orphaning
        nothing. Demonstrated with the iperf3 server the network-tools
        path starts (netutils.run_iperf3), terminated while it is still
        running so an orphan would be caught."""
        net = self.lab()

        h1 = net.addHost('h1')
        h2 = net.addHost('h2')
        net.addLink(h1, h2, params1={'ip': '10.0.0.2/24'}, params2={'ip': '10.0.0.3/24'})

        self.start(net)
        import time

        # Start an iperf3 server in h1 through the network-tools path.
        srv_proc = h1.popen('iperf3 -s')
        time.sleep(0.5)

        # The server must be alive and running inside the Node.
        assert srv_proc.poll() is None, \
            f"iperf3 server should still be running (rc={srv_proc.poll()})"

        # The process must be visible in the Node's own ps (the strong
        # promise: a process started in the Node appears in its ps).
        ps_before = h1.cmd('ps -e -o args 2>/dev/null || ps aux 2>/dev/null || true')
        assert any('iperf3' in line for line in ps_before.splitlines()), \
            f"iperf3 server not visible in node ps: {ps_before}"

        # Terminating the handle must kill the process inside the Node,
        # not just the nsenter wrapper.
        srv_proc.terminate()
        srv_proc.wait()

        # The server must be gone from the Node — nothing orphaned.
        time.sleep(0.3)
        ps_after = h1.cmd('ps -e -o args 2>/dev/null || ps aux 2>/dev/null || true')
        leftover = [line for line in ps_after.splitlines() if 'iperf3' in line]
        assert not leftover, f"Orphaned iperf3 left in Node: {leftover}"
    @pytest.mark.requires_docker
    @pytest.mark.slow
    def test_container_host_lifecycle(self):
        """ContainerHost should create a Docker container and clean it up on stop."""
        import subprocess

        net = self.lab()

        c1 = net.addContainer('c1', dimage='alpine:latest')
        net.addHost('h1')
        net.addLink(c1, net.get('h1'), params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})
        container = net.container_name('c1')

        self.start(net)
        try:
            # Verify container is running
            result = subprocess.run(
                ['docker', 'inspect', '-f', '{{.State.Running}}', container],
                capture_output=True, text=True, check=False
            )
            assert result.returncode == 0 and 'true' in result.stdout.lower(), \
                f"Container {container} not running: {result.stdout} {result.stderr}"

            # Verify we can execute commands inside the container
            output = c1.cmd('hostname')
            assert 'c1' in output, f"Unexpected hostname output: {output}"

            # Verify the ContainerLink still carries traffic to the host.
            output = c1.cmd('ping -c 1 -W 2 10.0.0.2')
            assert ' 0% packet loss' in output, \
                f"ContainerHost could not reach h1: {output}"
        finally:
            net.stop()

        # Verify container is removed after stop
        result = subprocess.run(
            ['docker', 'inspect', '-f', '{{.State.Running}}', container],
            capture_output=True, text=True, check=False
        )
        assert result.returncode != 0, f"Container {container} should be removed after stop: {result.stdout}"

    @pytest.mark.requires_docker
    @pytest.mark.slow
    def test_container_host_private_dirs_mounted(self):
        """ContainerHost should mount privateDirs as Docker volumes."""
        import tempfile

        net = self.lab()

        with tempfile.TemporaryDirectory() as tmpdir:
            hosts_file = os.path.join(tmpdir, 'hosts')
            with open(hosts_file, 'w') as f:
                f.write('127.0.0.1 test-host\n')

            c1 = net.addContainer(
                'c1',
                dimage='alpine:latest',
                privateDirs=[('/etc/hosts', hosts_file)],
            )
            net.addHost('h1')
            net.addLink(c1, net.get('h1'), params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})

            self.start(net)
            # Verify the mounted file is visible inside the container
            output = c1.cmd('cat /etc/hosts')
            assert 'test-host' in output, f"Mounted /etc/hosts not visible inside container: {output}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


class IPv6AddressPersistenceTests(LabCase):
    """IPv6 addresses survive FRR daemon startup.

    FRR clears IPv6 addresses on router interfaces when its daemons come up.
    The reconciliation pass in start() restores them from interface state,
    which is the reason that pass still exists after the side-list was removed.
    """

    def test_router_ipv6_addresses_survive_startup(self):
        """A Lab with IPv6 links starts with every address in place."""
        net = self.lab()
        r1 = net.addRouter('r1', proto='OSPF')
        h1 = net.addHost('h1')
        net.addLink(
            h1, r1,
            params1={'ip6': '2001:db8:1::2/64'},
            params2={'ip6': '2001:db8:1::1/64'},
        )
        self.start(net)

        # Read back from the kernel rather than trusting recorded state.
        self_addr = r1.defaultIntf().updateIP6()
        assert self_addr == '2001:db8:1::1', (
            f'router IPv6 address missing after start: got {self_addr!r}'
        )
        assert h1.defaultIntf().updateIP6() == '2001:db8:1::2'

    def test_ipv6_addresses_are_in_place_before_a_binding_daemon_starts(self):
        """With autoSetMacs, configHosts() re-runs setMAC on each Node's
        default Interface — down then up — and the kernel flushes its IPv6
        address on the way down. A passive Speaker binds its
        local address at startup, so the address has to be back before the
        daemons start, not only after."""
        net = mnIPLab(topo=None, controller=None, autoSetMacs=True)
        sp1 = net.addExaBGP('sp1', local_as=65001, neighbors=[BGPNeighbor(
            peer='2001:db8:1::1', local_address='2001:db8:1::2', local_as=65001, peer_as=65000,
        )])
        r1 = net.addRouter('r1', proto='BGP')
        net.addLink(
            sp1, r1,
            params1={'ip6': '2001:db8:1::2/64'},
            params2={'ip6': '2001:db8:1::1/64'},
        )
        # Raises ExaBGPError if the daemon could not bind and exited.
        self.start(net)
        assert sp1.defaultIntf().updateIP6() == '2001:db8:1::2'
        assert sp1.control_client().is_running()

    def test_ipv6_addresses_survive_a_link_down_and_up_like_ipv4(self):
        """The Link Condition lever takes both Interfaces down and up. IPv4
        keeps its address through that and only loses the connected route
        while down; IPv6 must do the same, or a reconnected Link never
        speaks IPv6 again."""
        net = self.lab()
        h1 = net.addHost('h1')
        h2 = net.addHost('h2')
        net.addLink(
            h1, h2,
            params1={'ip': '10.0.1.1/24', 'ip6': '2001:db8:1::1/64'},
            params2={'ip': '10.0.1.2/24', 'ip6': '2001:db8:1::2/64'},
        )
        self.start(net)
        net.configLinkStatus('h1', 'h2', 'down')
        assert h1.defaultIntf().updateIP6() == '2001:db8:1::1', 'address gone while down'
        net.configLinkStatus('h1', 'h2', 'up')
        assert h1.defaultIntf().updateIP6() == '2001:db8:1::1'
        assert h2.defaultIntf().updateIP6() == '2001:db8:1::2'
        assert wait_until(lambda: '1 received' in h1.cmd('ping -6 -c1 -W1 2001:db8:1::2'), 10.0), \
            'IPv6 should work again once the Link is back up'

    def test_interfaces_report_their_configured_ipv6_address(self):
        """IP6() reports what was configured, updateIP6() what the kernel holds."""
        net = self.lab()
        h1 = net.addHost('h1')
        h2 = net.addHost('h2')
        net.addLink(
            h1, h2,
            params1={'ip6': '2001:db8:2::1/64'},
            params2={'ip6': '2001:db8:2::2/64'},
        )
        self.start(net)

        intf = h1.defaultIntf()
        assert intf.IP6() == '2001:db8:2::1'
        assert intf.updateIP6() == '2001:db8:2::1'


class TestLabHostsFileInsideANode:
    """A Lab's own hosts file is a readable file inside every Node.

    Mininet creates a missing bind source with a plain ``mkdir -p``, so the
    shared hosts file used to become a *directory* — mounted empty over every
    Node's /etc/hosts, and left behind to break every later Lab. The Lab now
    resets its own file when constructed; nothing is called first.
    """

    @pytest.fixture(autouse=True)
    def cleanup_after_test(self):
        yield
        os.system('mn -c >/dev/null 2>&1')

    def test_shared_hosts_file_is_a_file_and_readable_inside_a_node(self):
        net = mnIPLab(topo=None, controller=None)
        try:
            h1 = net.addHost('h1')
            h2 = net.addHost('h2')
            net.addLink(h1, h2, params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})
            net.start()

            assert os.path.isfile(net.hosts_file), (
                f'{net.hosts_file} is not a regular file after a Lab started'
            )
            mnIPLabCLI.update_hosts_file(net)

            listing = h1.cmd('ls -ld /etc/hosts')
            assert not listing.startswith('d'), f'/etc/hosts is a directory: {listing}'
            assert '10.0.0.2' in h1.cmd('cat /etc/hosts')
            assert 'h2' in h1.cmd('getent hosts h2') or '10.0.0.2' in h1.cmd('getent hosts h2')
        finally:
            net.stop()

    def test_a_started_lab_names_no_nodes_until_update_hosts(self):
        net = mnIPLab(topo=None, controller=None)
        try:
            h1 = net.addHost('h1')
            h2 = net.addHost('h2')
            net.addLink(h1, h2, params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})
            net.start()

            with open(net.hosts_file) as hosts:
                before = hosts.read()
            entries = [line.split() for line in before.splitlines()
                       if line.strip() and not line.startswith('#')]
            assert entries == [['127.0.0.1', 'localhost']], before
            assert h1.cmd('cat /etc/hosts').split() == before.split()

            mnIPLabCLI.update_hosts_file(net)

            after = h1.cmd('cat /etc/hosts')
            assert '10.0.0.2' in after and 'h2' in after, after
        finally:
            net.stop()
