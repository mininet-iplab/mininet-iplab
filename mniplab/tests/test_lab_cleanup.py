"""Unit tests for Lab-scoped cleanup and emergency fallback."""

import os
import shutil
import tempfile
import unittest
from typing import Optional
from unittest.mock import MagicMock, patch

from mniplab.cleanup import emergency_cleanup
from mniplab.lab_key import DEFAULT_ALLOCATOR
from mniplab.net import mnIPLab
from mniplab.tests.labs import discard_unstarted_lab


class TestLabScopedCleanup(unittest.TestCase):
    """Verify cleanup operations are strictly scoped by Lab key."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.key1 = "cl1"
        self.key2 = "cl2"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _lab(self, lab_key: Optional[str] = None) -> mnIPLab:
        """Construct an unstarted Lab the test takes apart when it ends."""
        net = mnIPLab(topo=None, controller=None, lab_key=lab_key)
        self.addCleanup(discard_unstarted_lab, net)
        return net

    def test_net_cleanup_ephemeral_dirs_removes_only_own_key(self) -> None:
        """net._cleanup_ephemeral_config_dirs() removes only its own lab_key tree."""
        lab1 = self._lab(self.key1)
        lab2 = self._lab(self.key2)

        # Create dummy directories under ephemeral roots for both labs
        lab1_dir = lab1.frr_config_dir("r1")
        lab2_dir = lab2.frr_config_dir("r1")
        os.makedirs(lab1_dir, exist_ok=True)
        os.makedirs(lab2_dir, exist_ok=True)
        self.assertTrue(os.path.exists(lab1_dir))
        self.assertTrue(os.path.exists(lab2_dir))

        # Perform cleanup on lab1
        lab1._cleanup_ephemeral_config_dirs()

        # lab1's directory must be gone, lab2's must be untouched
        self.assertFalse(os.path.exists(lab1_dir))
        self.assertTrue(os.path.exists(lab2_dir))

        # Clean up lab2
        lab2._cleanup_ephemeral_config_dirs()
        self.assertFalse(os.path.exists(lab2_dir))

    def test_emergency_cleanup_scoped_leaves_other_lab_intact(self) -> None:
        """emergency_cleanup(net=lab1) deletes only lab1's files and leaves lab2 intact."""
        lab1 = self._lab(self.key1)
        lab2 = self._lab(self.key2)

        p1_dns = lab1.dns_dir("ns1")
        p2_dns = lab2.dns_dir("ns1")
        os.makedirs(p1_dns, exist_ok=True)
        os.makedirs(p2_dns, exist_ok=True)

        emergency_cleanup(net=lab1)

        self.assertFalse(os.path.exists(p1_dns))
        self.assertTrue(os.path.exists(p2_dns))

        emergency_cleanup(net=lab2)
        self.assertFalse(os.path.exists(p2_dns))

    def test_stop_releases_allocated_lab_key(self) -> None:
        """net.stop() releases its lab_key back to the allocator."""
        net = self._lab()
        key = net.lab_key
        self.assertIn(key, DEFAULT_ALLOCATOR._active_keys)

        with patch.object(net, "_cleanup_ephemeral_config_dirs"), \
             patch.object(net, "_cleanup_tmp_parent_dir"), \
             patch("mininet.net.Mininet.stop"):
            net.stop()

        self.assertNotIn(key, DEFAULT_ALLOCATOR._active_keys)

    def test_emergency_cleanup_releases_key_and_cleans_etc(self) -> None:
        """emergency_cleanup releases the allocated key and removes the Lab directory."""
        net = self._lab()
        key = net.lab_key
        self.assertIn(key, DEFAULT_ALLOCATOR._active_keys)

        # Create hosts file under /tmp/etc/<key>/hosts
        hosts_path = net.hosts_file
        os.makedirs(os.path.dirname(hosts_path), exist_ok=True)
        with open(hosts_path, "w", encoding="utf-8") as f:
            f.write("127.0.0.1 localhost\n")

        self.assertTrue(os.path.exists(hosts_path))

        emergency_cleanup(net=net)

        self.assertFalse(os.path.exists(hosts_path))
        self.assertFalse(os.path.exists(os.path.dirname(hosts_path)))
        self.assertNotIn(key, DEFAULT_ALLOCATOR._active_keys)


if __name__ == "__main__":
    unittest.main()
