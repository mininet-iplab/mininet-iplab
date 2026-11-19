"""Unit tests for per-Lab hosts file operations."""

import os
import unittest
from mniplab.cli import mnIPLabCLI
from mniplab.constants import LABS_DIR
from mniplab.net import mnIPLab
from mniplab.tests.labs import unstarted_lab
from mniplab.utils import update_hosts_file


class TestLabHostsFile(unittest.TestCase):
    """Test suite for per-Lab hosts file operations."""

    def test_hosts_file_per_lab_distinct(self) -> None:
        """Each Lab resolves its own distinct hosts file path."""
        net1 = mnIPLab(topo=None, controller=None, lab_key="l1")
        net2 = mnIPLab(topo=None, controller=None, lab_key="l2")
        try:
            self.assertNotEqual(net1.hosts_file, net2.hosts_file)
            self.assertIn("/l1/", net1.hosts_file)
            self.assertIn("/l2/", net2.hosts_file)
        finally:
            net1.stop()
            net2.stop()

    def test_init_and_ensure_hosts_file_takes_lab(self) -> None:
        """init_hosts_file and ensure_hosts_file accept a Lab instance."""
        net1 = mnIPLab(topo=None, controller=None, lab_key="l1")
        net2 = mnIPLab(topo=None, controller=None, lab_key="l2")
        try:
            mnIPLabCLI.init_hosts_file(net1)
            mnIPLabCLI.init_hosts_file(net2)

            self.assertTrue(os.path.exists(net1.hosts_file))
            self.assertTrue(os.path.exists(net2.hosts_file))

            # Modify net1's hosts file
            with open(net1.hosts_file, "a") as f:
                f.write("192.168.1.10 r1\n")

            # Verify net2's hosts file was not touched
            with open(net2.hosts_file, "r") as f:
                content2 = f.read()
            self.assertNotIn("192.168.1.10 r1", content2)

            # Re-init net1 should reset net1 without touching net2
            mnIPLabCLI.init_hosts_file(net1)
            with open(net1.hosts_file, "r") as f:
                content1 = f.read()
            self.assertNotIn("192.168.1.10 r1", content1)
            self.assertIn("127.0.0.1 localhost", content1)

            # Ensure hosts file on lab
            mnIPLabCLI.ensure_hosts_file(net1)
            self.assertTrue(os.path.exists(net1.hosts_file))
        finally:
            net1.stop()
            net2.stop()

    def test_nodes_bind_lab_hosts_file(self) -> None:
        """Hosts and Routers added to a Lab bind that Lab's hosts file."""
        net = mnIPLab(topo=None, controller=None, lab_key="l1")
        try:
            h1 = net.addHost("h1")
            r1 = net.addRouter("r1")

            # Check h1's bound /etc/hosts
            h1_hosts_binding = None
            for entry in h1.params.get("privateDirs", []):
                if isinstance(entry, tuple) and entry[0] == "/etc/hosts":
                    h1_hosts_binding = entry[1]
                    break
            self.assertEqual(h1_hosts_binding, net.hosts_file)

            # Check r1's bound /etc/hosts
            r1_hosts_binding = None
            for entry in r1.params.get("privateDirs", []):
                if isinstance(entry, tuple) and entry[0] == "/etc/hosts":
                    r1_hosts_binding = entry[1]
                    break
            self.assertEqual(r1_hosts_binding, net.hosts_file)
        finally:
            net.stop()

    def test_update_hosts_file_writes_to_lab_hosts_file(self) -> None:
        """update_hosts_file writes to the Lab's own hosts file."""
        net = mnIPLab(topo=None, controller=None, lab_key="l3")
        try:
            h1 = net.addHost("h1")
            h2 = net.addHost("h2")
            net.addLink(h1, h2, params1={"ip": "10.0.0.1/24"}, params2={"ip": "10.0.0.2/24"})
            net.start()
            net.update_hosts_file()
            self.assertTrue(os.path.exists(net.hosts_file))
            with open(net.hosts_file, "r") as f:
                content = f.read()
            self.assertIn("10.0.0.1 h1", content)
            self.assertIn("10.0.0.2 h2", content)
        finally:
            net.stop()


class TestLabResetsItsOwnHostsFile(unittest.TestCase):
    """A Lab starts from a clean hosts file with nothing for a Lab Example to call first."""

    def test_constructing_a_lab_resets_a_stale_hosts_file_to_loopback(self) -> None:
        """Entries a previous Lab left under the same key never reach the new one."""
        stale = os.path.join(LABS_DIR, "l7", "etc", "hosts")
        os.makedirs(os.path.dirname(stale), exist_ok=True)
        with open(stale, "w") as f:
            f.write("127.0.0.1 localhost\n10.0.0.1 r1\n10.0.0.2 h1\n")

        net = mnIPLab(topo=None, controller=None, lab_key="l7")
        try:
            self.assertEqual(net.hosts_file, stale)
            with open(net.hosts_file) as f:
                self.assertEqual(f.read(), "127.0.0.1 localhost\n")
        finally:
            net.stop()

    def test_no_argument_init_hosts_file_creates_no_file(self) -> None:
        """The pre-key form is a no-op kept for scripts written against the old docs."""
        before = set(os.listdir(LABS_DIR)) if os.path.isdir(LABS_DIR) else None

        mnIPLabCLI.init_hosts_file()

        after = set(os.listdir(LABS_DIR)) if os.path.isdir(LABS_DIR) else None
        self.assertEqual(after, before)

    def test_host_with_nameservers_seeds_resolv_conf_at_the_lab_path(self) -> None:
        """The seed lands where the bind is: the Node's file in the Lab directory."""
        with unstarted_lab() as net:
            h7 = net.addHost("h7", nameservers=["10.0.0.53"])
            self.assertEqual(h7.nameservers, ["10.0.0.53"])
            self.assertTrue(net.resolv_conf_file("h7").startswith(net.lab_dir + "/"))
            with open(net.resolv_conf_file("h7")) as f:
                self.assertEqual(f.read(), "nameserver 10.0.0.53\n")


if __name__ == "__main__":
    unittest.main()
