"""Unit tests for the per-Node private directory set.

`/var/tmp` is private to every Router (watchfrr's pid-keyed paths collide
across Routers on the shared host directory), and `/etc/hostname` is private
to every Host and Router so a Node can write its own hostname while `/etc`
stays shared. `/etc/hosts` is deliberately left alone — a Node must keep
resolving its own name over the Lab, not on loopback.

`/etc/resolv.conf` is private to every Host and Router as well: a DHCP
client rewrites it when a Lease arrives, and without isolation that write
lands on the host's own resolver configuration (the dev container's
`/etc/resolv.conf` is the host's file).
"""

import os
import tempfile
import unittest
from unittest.mock import call, patch

from mniplab.cli import mnIPLabCLI
from mniplab.host import mnIPLabHost
from mniplab.tests.fakes import FakeLab
from mniplab.tests.labs import unstarted_lab


def _mapping(private_dirs, container_path):
    """Return the host path bound at container_path, or None."""
    for entry in private_dirs:
        if isinstance(entry, tuple) and entry[0] == container_path:
            return entry[1]
    return None


class RouterPrivateDirsTests(unittest.TestCase):
    """The Router set gains a private /var/tmp and /etc/hostname."""

    def setUp(self):
        self.net = FakeLab()

    def test_router_var_tmp_is_private(self):
        self.assertIn('/var/tmp', self.net.router_private_dirs('r1'))

    def test_router_etc_hostname_is_bound(self):
        self.assertEqual(
            _mapping(self.net.router_private_dirs('r1'), '/etc/hostname'),
            self.net.hostname_file('r1'),
        )

    def test_router_etc_hosts_binding_is_the_labs_shared_file(self):
        self.assertEqual(
            _mapping(self.net.router_private_dirs('r1'), '/etc/hosts'), self.net.hosts_file
        )

    def test_hostname_backing_path_is_per_node(self):
        self.assertNotEqual(self.net.hostname_file('r1'), self.net.hostname_file('r2'))


class HostPrivateDirsTests(unittest.TestCase):
    """The Host set gains /etc/hostname and is what addHost builds from."""

    def setUp(self):
        self.net = FakeLab()

    def test_host_etc_hostname_is_bound(self):
        self.assertEqual(
            _mapping(self.net.host_private_dirs('h1'), '/etc/hostname'),
            self.net.hostname_file('h1'),
        )

    def test_host_etc_hosts_binding_is_the_labs_shared_file(self):
        self.assertEqual(
            _mapping(self.net.host_private_dirs('h1'), '/etc/hosts'), self.net.hosts_file
        )

    def test_addhost_uses_host_private_dirs(self):
        """addHost's default privateDirs binds the Lab-resolved host directories."""
        with unstarted_lab() as net:
            host = net.addHost('h1')
            expected_dirs = [
                ('/etc/hosts', net.hosts_file),
                ('/etc/hostname', net.hostname_file('h1')),
                ('/etc/resolv.conf', net.resolv_conf_file('h1')),
                ('/var/lib/dhcpcd', net.dhcpcd_lib_dir('h1')),
                ('/run/dhcpcd', net.dhcpcd_run_dir('h1')),
                ('/root', net.node_home_dir('h1')),
            ]
            for entry in expected_dirs:
                self.assertIn(entry, host.privateDirs)

    def test_addhost_with_nameservers_seeds_private_resolv_conf(self):
        with unstarted_lab() as net:
            h1 = net.addHost('h1', nameservers=['10.0.0.1', '10.0.0.2'])
            h2 = net.addHost('h2')
            self.assertEqual(h1.nameservers, ['10.0.0.1', '10.0.0.2'])
            self.assertEqual(h2.nameservers, [])
            resolv_path = net.resolv_conf_file('h1')
            self.assertTrue(os.path.isfile(resolv_path))
            with open(resolv_path) as f:
                self.assertEqual(f.read(), 'nameserver 10.0.0.1\nnameserver 10.0.0.2\n')


class InitHostsFileTests(unittest.TestCase):
    """init_hosts_file leaves a usable shared hosts file behind, always."""

    def test_writes_the_shared_hosts_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hosts_file = os.path.join(tmpdir, 'etc', 'hosts')
            mnIPLabCLI.init_hosts_file(hosts_file)
            with open(hosts_file) as f:
                self.assertIn('127.0.0.1 localhost', f.read())

    def test_replaces_a_stale_directory_left_by_a_previous_lab(self):
        """A Lab started before this ran leaves the path behind as a directory.

        Mininet's mountPrivateDirs does a plain ``mkdir -p`` on the bind
        source, so the next Lab would otherwise fail on IsADirectoryError
        rather than start.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            hosts_file = os.path.join(tmpdir, 'etc', 'hosts')
            os.makedirs(hosts_file)
            self.assertTrue(os.path.isdir(hosts_file))

            mnIPLabCLI.init_hosts_file(hosts_file)

            self.assertTrue(os.path.isfile(hosts_file))
            with open(hosts_file) as f:
                self.assertIn('127.0.0.1 localhost', f.read())


class EnsureHostsFileTests(unittest.TestCase):
    """ensure_hosts_file makes the bind source a regular file, at the point of use.

    Mininet's ``mountPrivateDirs`` creates a missing bind source with a blind
    ``mkdir -p``, so a Lab started before anyone wrote the shared hosts file
    turned that path into a directory — its own Nodes then mount an empty
    directory over ``/etc/hosts``. Unlike ``init_hosts_file``, this keeps the
    entries an already-running Lab wrote.
    """

    def test_creates_the_file_when_absent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hosts_file = os.path.join(tmpdir, 'etc', 'hosts')
            mnIPLabCLI.ensure_hosts_file(hosts_file)
            self.assertTrue(os.path.isfile(hosts_file))
            with open(hosts_file) as f:
                self.assertIn('127.0.0.1 localhost', f.read())

    def test_keeps_the_entries_an_existing_file_already_holds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hosts_file = os.path.join(tmpdir, 'etc', 'hosts')
            os.makedirs(os.path.dirname(hosts_file))
            with open(hosts_file, 'w') as f:
                f.write('10.0.0.1 h1\n')

            mnIPLabCLI.ensure_hosts_file(hosts_file)

            with open(hosts_file) as f:
                self.assertEqual(f.read(), '10.0.0.1 h1\n')

    def test_replaces_a_stale_directory_left_by_a_previous_lab(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hosts_file = os.path.join(tmpdir, 'etc', 'hosts')
            os.makedirs(hosts_file)

            mnIPLabCLI.ensure_hosts_file(hosts_file)

            self.assertTrue(os.path.isfile(hosts_file))


class AddHostSeedsSharedHostsFileTests(unittest.TestCase):
    """A Node bound to a caller-supplied hosts path gets a file there, not a directory."""

    def test_addhost_creates_the_shared_hosts_file_it_binds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hosts_file = os.path.join(tmpdir, 'etc', 'hosts')
            with unstarted_lab() as net:
                net.addHost('h1', privateDirs=[('/etc/hosts', hosts_file)])

            self.assertTrue(os.path.isfile(hosts_file))

    def test_addhost_replaces_a_stale_directory_at_the_bind_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hosts_file = os.path.join(tmpdir, 'etc', 'hosts')
            os.makedirs(hosts_file)
            with unstarted_lab() as net:
                net.addHost('h1', privateDirs=[('/etc/hosts', hosts_file)])

            self.assertTrue(os.path.isfile(hosts_file))


class SeedHostnameFileTests(unittest.TestCase):
    """seed_hostname_file writes a Node's name to its backing file."""

    def test_seeds_file_with_node_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            template = os.path.join(tmpdir, '%(name)s')
            mnIPLabCLI.seed_hostname_file('h1', hostname_file=template)
            with open(os.path.join(tmpdir, 'h1')) as f:
                self.assertEqual(f.read(), 'h1\n')

    def test_backing_files_are_per_node(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            template = os.path.join(tmpdir, '%(name)s')
            mnIPLabCLI.seed_hostname_file('r1', hostname_file=template)
            mnIPLabCLI.seed_hostname_file('r2', hostname_file=template)
            with open(os.path.join(tmpdir, 'r1')) as f:
                self.assertEqual(f.read().strip(), 'r1')
            with open(os.path.join(tmpdir, 'r2')) as f:
                self.assertEqual(f.read().strip(), 'r2')

    def test_creates_missing_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            template = os.path.join(tmpdir, 'nested', '%(name)s')
            mnIPLabCLI.seed_hostname_file('r1', hostname_file=template)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, 'nested', 'r1')))


class ResolvConfPrivateDirsTests(unittest.TestCase):
    """Every Host and Router gets a private /etc/resolv.conf."""

    def setUp(self):
        self.net = FakeLab()

    def test_host_etc_resolv_conf_is_bound(self):
        self.assertEqual(
            _mapping(self.net.host_private_dirs('h1'), '/etc/resolv.conf'),
            self.net.resolv_conf_file('h1'),
        )

    def test_router_etc_resolv_conf_is_bound(self):
        self.assertEqual(
            _mapping(self.net.router_private_dirs('r1'), '/etc/resolv.conf'),
            self.net.resolv_conf_file('r1'),
        )

    def test_resolv_conf_backing_path_is_per_node(self):
        self.assertNotEqual(self.net.resolv_conf_file('h1'), self.net.resolv_conf_file('h2'))


class ResolvConfSingleSeedSeamTests(unittest.TestCase):
    """Creating a Host with nameservers= seeds /etc/resolv.conf exactly once."""

    def test_add_host_with_nameservers_seeds_resolv_conf_exactly_once(self) -> None:
        with unstarted_lab("resolv") as net:
            with patch.object(mnIPLabCLI, "seed_resolv_conf_file", wraps=mnIPLabCLI.seed_resolv_conf_file) as mock_seed:
                net.addHost("h1", nameservers=["10.0.0.1", "10.0.0.2"])
                self.assertEqual(mock_seed.call_count, 1)
                self.assertEqual(
                    mock_seed.call_args,
                    call("h1", resolv_conf_file=net.resolv_conf_file("h1"), nameservers=["10.0.0.1", "10.0.0.2"])
                )
            with open(net.resolv_conf_file("h1")) as f:
                self.assertEqual(f.read(), "nameserver 10.0.0.1\nnameserver 10.0.0.2\n")

    def test_host_init_does_not_seed_resolv_conf_independently(self) -> None:
        with patch.object(mnIPLabCLI, "seed_resolv_conf_file") as mock_seed:
            mnIPLabHost("h1", nameservers=["10.0.0.1"], privateDirs=[("/etc/resolv.conf", "/tmp/fake-resolv")])
            mock_seed.assert_not_called()

    def test_add_router_seeds_resolv_conf_and_hostname_exactly_once(self) -> None:
        with unstarted_lab("resolv") as net:
            with patch.object(mnIPLabCLI, "seed_resolv_conf_file", wraps=mnIPLabCLI.seed_resolv_conf_file) as mock_seed_resolv, \
                 patch.object(mnIPLabCLI, "seed_hostname_file", wraps=mnIPLabCLI.seed_hostname_file) as mock_seed_hostname:
                net.addRouter("r1")
                self.assertEqual(mock_seed_resolv.call_count, 1)
                self.assertEqual(
                    mock_seed_resolv.call_args,
                    call("r1", resolv_conf_file=net.resolv_conf_file("r1"), nameservers=None)
                )
                self.assertEqual(mock_seed_hostname.call_count, 1)
                self.assertEqual(
                    mock_seed_hostname.call_args,
                    call("r1", hostname_file=net.hostname_file("r1"))
                )


class SeedResolvConfFileTests(unittest.TestCase):
    """seed_resolv_conf_file copies the host's resolver configuration."""

    def test_copies_the_host_resolver_configuration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = os.path.join(tmpdir, 'host-resolv.conf')
            with open(source, 'w') as f:
                f.write('nameserver 192.0.2.1\n')
            template = os.path.join(tmpdir, '%(name)s')

            mnIPLabCLI.seed_resolv_conf_file(
                'h1', resolv_conf_file=template, source=source
            )

            with open(os.path.join(tmpdir, 'h1')) as f:
                self.assertEqual(f.read(), 'nameserver 192.0.2.1\n')

    def test_creates_missing_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = os.path.join(tmpdir, 'host-resolv.conf')
            with open(source, 'w') as f:
                f.write('nameserver 192.0.2.1\n')
            template = os.path.join(tmpdir, 'nested', '%(name)s')

            mnIPLabCLI.seed_resolv_conf_file(
                'h1', resolv_conf_file=template, source=source
            )

            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, 'nested', 'h1'))
            )

    def test_missing_source_seeds_an_empty_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            template = os.path.join(tmpdir, '%(name)s')

            mnIPLabCLI.seed_resolv_conf_file(
                'h1', resolv_conf_file=template,
                source=os.path.join(tmpdir, 'does-not-exist'),
            )

            with open(os.path.join(tmpdir, 'h1')) as f:
                self.assertEqual(f.read(), '')

    def test_seeds_file_with_configured_nameservers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            template = os.path.join(tmpdir, '%(name)s')

            mnIPLabCLI.seed_resolv_conf_file(
                'h1', resolv_conf_file=template, nameservers=['10.0.0.1', '10.0.0.2']
            )

            with open(os.path.join(tmpdir, 'h1')) as f:
                self.assertEqual(f.read(), 'nameserver 10.0.0.1\nnameserver 10.0.0.2\n')

    def test_seeds_file_with_single_string_nameserver(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            template = os.path.join(tmpdir, '%(name)s')

            mnIPLabCLI.seed_resolv_conf_file(
                'h1', resolv_conf_file=template, nameservers='10.0.0.1'
            )

            with open(os.path.join(tmpdir, 'h1')) as f:
                self.assertEqual(f.read(), 'nameserver 10.0.0.1\n')


class DhcpcdRunDirTests(unittest.TestCase):
    """dhcpcd's control socket and pid file must be per-Node.

    Both paths are compiled into dhcpcd under /run/dhcpcd, so without this
    bind every Node shares one directory with every other Node and with the
    machine the Lab runs on: `ls /run/dhcpcd` inside h1 listed h2's socket.
    """

    def setUp(self):
        self.net = FakeLab()

    def test_host_run_dhcpcd_is_bound_per_node(self):
        self.assertEqual(
            _mapping(self.net.host_private_dirs('h1'), '/run/dhcpcd'),
            self.net.dhcpcd_run_dir('h1'),
        )

    def test_router_run_dhcpcd_is_bound_per_node(self):
        self.assertEqual(
            _mapping(self.net.router_private_dirs('r1'), '/run/dhcpcd'),
            self.net.dhcpcd_run_dir('r1'),
        )

    def test_run_dir_is_keyed_by_node_name(self):
        self.assertNotEqual(self.net.dhcpcd_run_dir('h1'), self.net.dhcpcd_run_dir('h2'))

    def test_run_dir_is_ephemeral(self):
        # Inside the Lab directory, so it goes when the Lab stops.
        self.assertTrue(self.net.dhcpcd_run_dir('h1').startswith(self.net.lab_dir + '/'))


if __name__ == '__main__':
    unittest.main()
