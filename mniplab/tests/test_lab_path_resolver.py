"""Unit tests for Lab ephemeral path resolution.

Every ephemeral path a Lab writes -- Node backing files, homes, Service
trees, the Speaker's, the Container Host's backing, zone keys, the hosts
file and the handle socket -- lies under that Lab's ``labs/<key>/``, no
path of one Lab coincides with another's, and a backing path ends in the
guest path it is bound to.
"""

import os
import unittest
from typing import List

from mniplab.net import mnIPLab
from mniplab.tests.fakes import FakeLab

NODE_NAMES = ["r1", "h1", "ex1", "dhcp1", "dns1", "res1", "c1"]
ZONE_NAME = "example.com."


def backing_paths(net: FakeLab, name: str) -> List[tuple]:
    """Return each backing path of a Node with the guest path it is bound to."""
    return [
        (net.frr_config_dir(name), "etc/frr"),
        (net.hostname_file(name), "etc/hostname"),
        (net.resolv_conf_file(name), "etc/resolv.conf"),
        (net.dhcpcd_lib_dir(name), "var/lib/dhcpcd"),
        (net.dhcpcd_run_dir(name), "run/dhcpcd"),
        (net.exabgp_dir(name), "etc/exabgp"),
        (net.exabgp_dir(name, "logs"), "var/log/exabgp"),
        (net.exabgp_dir(name, "run"), "var/run/exabgp"),
        (net.dhcp_dir(name, "etc"), "etc/kea"),
        (net.dhcp_dir(name, "run"), "var/run/kea"),
        (net.dhcp_dir(name, "lib"), "var/lib/kea"),
        (net.dhcp_relay_dir(name), "var/log/dnsmasq"),
        (net.dns_dir(name, "etc"), "etc/knot"),
        (net.dns_dir(name, "run"), "run/knot"),
        (net.dns_dir(name, "lib"), "var/lib/knot"),
        (net.resolver_dir(name, "etc"), "etc/unbound"),
        (net.resolver_dir(name, "run"), "run/unbound"),
        (net.container_dir(name, "/var/log"), "var/log"),
        (net.hosts_file, "etc/hosts"),
    ]


def every_ephemeral_path(net: FakeLab, name: str) -> List[str]:
    """Return every ephemeral path the Lab resolves for a Node, and the Lab's own."""
    return [backing for backing, _guest in backing_paths(net, name)] + [
        net.node_dir(name),
        net.node_home_dir(name),
        net.dhcp_control_socket_path(name, 4),
        net.dhcp_control_socket_path(name, 6),
        net.dhcp_relay_log_path(name),
        net.dns_control_socket_path(name),
        net.dns_log_path(name),
        net.resolver_control_socket_path(name),
        net.container_dir(name),
        net.dns_zone_keys_dir(ZONE_NAME),
        net.rpc_socket_path,
    ]


class TestLabPathResolver(unittest.TestCase):
    """Test suite for Lab-scoped ephemeral path resolution."""

    def test_every_ephemeral_path_lies_under_the_lab_directory(self) -> None:
        net = FakeLab(lab_key="k1")

        for name in NODE_NAMES:
            for path in every_ephemeral_path(net, name):
                self.assertTrue(
                    path.startswith(net.lab_dir + os.sep),
                    f"{path} is not under the Lab directory {net.lab_dir}",
                )
                self.assertIn("/labs/k1/", path)

    def test_backing_paths_end_in_the_guest_path_they_are_bound_to(self) -> None:
        net = FakeLab(lab_key="k1")

        for name in NODE_NAMES:
            for backing, guest in backing_paths(net, name):
                self.assertTrue(
                    backing.endswith("/" + guest),
                    f"{backing} does not end in the guest path {guest}",
                )
                if guest != "etc/hosts":
                    self.assertIn(f"/nodes/{name}/", backing)

    def test_ephemeral_paths_diverge_across_labs(self) -> None:
        """Two Labs with different keys and one Node-name set share no path."""
        net1 = FakeLab(lab_key="lab1")
        net2 = FakeLab(lab_key="lab2")

        paths1 = {p for name in NODE_NAMES for p in every_ephemeral_path(net1, name)}
        paths2 = {p for name in NODE_NAMES for p in every_ephemeral_path(net2, name)}

        self.assertTrue(all("/lab1/" in p and "/lab2/" not in p for p in paths1))
        self.assertTrue(all("/lab2/" in p and "/lab1/" not in p for p in paths2))
        self.assertEqual(paths1 & paths2, set())

    def test_provisioners_resolve_through_the_lab(self) -> None:
        """Every provisioner's host path is the Lab's, never a bare <root>/<node>."""
        from mniplab.dns.models import Zone
        from mniplab.dns.provisioner import DNSProvisioner
        from mniplab.provisioners import DHCPProvisioner, DHCPRelayProvisioner
        from mniplab.resolver.provisioner import ResolverProvisioner

        net = FakeLab(lab_key="key99")
        node = "srv1"

        resolved = {
            DHCPProvisioner.host_dir(node, "run", net=net): net.dhcp_dir(node, "run"),
            DHCPProvisioner.control_socket_host_path(node, family=4, net=net):
                net.dhcp_control_socket_path(node, family=4),
            DHCPRelayProvisioner.host_dir(node, net=net): net.dhcp_relay_dir(node),
            DHCPRelayProvisioner.log_path(node, net=net): net.dhcp_relay_log_path(node),
            DNSProvisioner.host_dir(node, "run", net=net): net.dns_dir(node, "run"),
            DNSProvisioner.control_socket_host_path(node, net=net):
                net.dns_control_socket_path(node),
            DNSProvisioner.log_host_path(node, net=net): net.dns_log_path(node),
            DNSProvisioner.ensure_zone_keys(Zone(name=ZONE_NAME, dnssec=True), net=net):
                net.dns_zone_keys_dir(ZONE_NAME),
            ResolverProvisioner.host_dir(node, "run", net=net): net.resolver_dir(node, "run"),
            ResolverProvisioner.control_socket_host_path(node, net=net):
                net.resolver_control_socket_path(node),
        }
        for provisioner_path, lab_path in resolved.items():
            self.assertEqual(provisioner_path, lab_path)
            self.assertIn("/labs/key99/", provisioner_path)

    def test_root_namespace_names_diverge_across_labs(self) -> None:
        """Two Labs with different keys render disjoint root-namespace object names.

        The bridge, its veths, the NAT Gateway's veths, the container name and
        the namespace link all live in the host's one namespace, so each must
        carry the Lab key.
        """
        net1 = mnIPLab(topo=None, controller=None, lab_key="lab1")
        net2 = mnIPLab(topo=None, controller=None, lab_key="lab2")

        try:
            names1 = set()
            names2 = set()
            for name in ["s1", "sw1", "nat1", "c1", "d1"]:
                n1 = net1.resolve_all_root_names(name)
                n2 = net2.resolve_all_root_names(name)
                self.assertTrue(len(n1) > 0)
                self.assertEqual(len(n1), len(n2))
                for rendered in n1:
                    self.assertIn("lab1", rendered)
                    self.assertNotIn("lab2", rendered)
                for rendered in n2:
                    self.assertIn("lab2", rendered)
                    self.assertNotIn("lab1", rendered)
                names1.update(n1)
                names2.update(n2)

            self.assertEqual(names1.intersection(names2), set())

            # The kernel-facing names must fit an interface name.
            for name in ["s1", "sw1", "nat1"]:
                self.assertLessEqual(len(net1.root_intf_name(name, 0)), 15)
                self.assertLessEqual(len(net1.bridge_name(name)), 15)

            # Concrete shapes a host operator will see in ip link, ovs-vsctl
            # show and docker ps.
            self.assertEqual(net1.root_intf_name("s1", 1), "lab1-s1-eth1")
            self.assertEqual(net1.bridge_name("s1"), "lab1-s1")
            self.assertEqual(net1.container_name("d1"), "mn.lab1.d1")
            self.assertEqual(net1.netns_link_path("d1"), "/var/run/netns/mn.lab1.d1")
        finally:
            net1.stop()
            net2.stop()


if __name__ == "__main__":
    unittest.main()
