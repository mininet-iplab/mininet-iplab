"""Unit tests verifying provisioners resolve paths through the Lab."""

import unittest
from mniplab.net import mnIPLab
from mniplab.provisioners import DHCPProvisioner, DHCPRelayProvisioner
from mniplab.dns.provisioner import DNSProvisioner
from mniplab.dns.models import Zone
from mniplab.resolver.provisioner import ResolverProvisioner


class TestProvisionersLabPaths(unittest.TestCase):
    """Test that all provisioners resolve paths through the provided Lab."""

    def test_dhcp_provisioner_resolves_through_lab(self) -> None:
        """DHCPProvisioner queries net for host_dir and control_socket_host_path."""
        net = mnIPLab(topo=None, controller=None, lab_key="p1")
        try:
            h_dir = DHCPProvisioner.host_dir("dhcp1", "run", net=net)
            self.assertEqual(h_dir, net.dhcp_dir("dhcp1", "run"))
            self.assertIn("/p1/", h_dir)

            sock = DHCPProvisioner.control_socket_host_path("dhcp1", family=4, net=net)
            self.assertEqual(sock, net.dhcp_control_socket_path("dhcp1", 4))
            self.assertIn("/p1/", sock)
        finally:
            net.stop()

    def test_dhcp_relay_provisioner_resolves_through_lab(self) -> None:
        """DHCPRelayProvisioner queries net for host_dir and log_path."""
        net = mnIPLab(topo=None, controller=None, lab_key="p1")
        try:
            r_dir = DHCPRelayProvisioner.host_dir("r1", net=net)
            self.assertEqual(r_dir, net.dhcp_relay_dir("r1"))
            self.assertIn("/p1/", r_dir)

            log_p = DHCPRelayProvisioner.log_path("r1", net=net)
            self.assertEqual(log_p, net.dhcp_relay_log_path("r1"))
            self.assertIn("/p1/", log_p)
        finally:
            net.stop()

    def test_dns_provisioner_resolves_through_lab(self) -> None:
        """DNSProvisioner queries net for host_dir, control_socket, and zone keys."""
        net = mnIPLab(topo=None, controller=None, lab_key="p1")
        try:
            d_dir = DNSProvisioner.host_dir("dns1", "run", net=net)
            self.assertEqual(d_dir, net.dns_dir("dns1", "run"))
            self.assertIn("/p1/", d_dir)

            sock = DNSProvisioner.control_socket_host_path("dns1", net=net)
            self.assertEqual(sock, net.dns_control_socket_path("dns1"))
            self.assertIn("/p1/", sock)

            # Fallback zone keys for signed zone
            zone = Zone(name="example.com.", dnssec=True)
            keys_dir = DNSProvisioner.ensure_zone_keys(zone, net=net)
            self.assertEqual(keys_dir, net.dns_zone_keys_dir("example.com."))
            self.assertIn("/p1/", keys_dir)
        finally:
            net.stop()

    def test_resolver_provisioner_resolves_through_lab(self) -> None:
        """ResolverProvisioner queries net for host_dir and control_socket."""
        net = mnIPLab(topo=None, controller=None, lab_key="p1")
        try:
            r_dir = ResolverProvisioner.host_dir("res1", "run", net=net)
            self.assertEqual(r_dir, net.resolver_dir("res1", "run"))
            self.assertIn("/p1/", r_dir)

            sock = ResolverProvisioner.control_socket_host_path("res1", net=net)
            self.assertEqual(sock, net.resolver_control_socket_path("res1"))
            self.assertIn("/p1/", sock)
        finally:
            net.stop()

    def test_exabgp_provisioner_resolves_through_lab(self) -> None:
        """ExaBGP speaker added to a Lab binds Lab-scoped directories."""
        net = mnIPLab(topo=None, controller=None, lab_key="p1")
        try:
            ex1 = net.addExaBGP("ex1")
            for container_p, host_p in ex1.params.get("privateDirs", []):
                self.assertIn("/p1/", host_p)
        finally:
            net.stop()

    def test_container_host_private_dirs_resolves_through_lab(self) -> None:
        """ContainerHost resolves bare tmpfs string privateDirs through net.container_dir."""
        from unittest.mock import MagicMock
        from mniplab.container import ContainerHost
        net = mnIPLab(topo=None, controller=None, lab_key="p1")
        try:
            host = MagicMock(spec=ContainerHost)
            host.net = net
            host.volumes = []
            ContainerHost._convert_private_dirs_to_volumes(host, ["/var/tmp"], "c1")
            self.assertEqual(len(host.volumes), 1)
            self.assertIn("/p1/", host.volumes[0])
            self.assertTrue(host.volumes[0].startswith(net.container_dir("c1", "/var/tmp")))
        finally:
            net.stop()


if __name__ == "__main__":
    unittest.main()
