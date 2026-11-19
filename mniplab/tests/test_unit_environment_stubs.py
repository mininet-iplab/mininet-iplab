"""Unit tests verifying plain-runner environment stubs and seams."""

import os
import shutil
import unittest
from unittest.mock import patch

import pytest

from mniplab.net import mnIPLab
from mniplab.dns.models import Zone, DSRecord
from mniplab.dns.provisioner import DNSProvisioner


@pytest.mark.unit
class UnitEnvironmentStubsTests(unittest.TestCase):
    """Verify that unit tests can run on plain runners without daemons installed."""

    def test_mniplab_constructs_when_frr_init_script_missing(self) -> None:
        """mnIPLab should construct cleanly in unit tests even if FRR init script does not exist."""
        with patch("mniplab.router.FRR_INIT_SCRIPT", "/nonexistent/frrinit.sh"):
            # Inside a unit test, MNIPLAB_SKIP_DAEMON_PROBES should be active
            net = mnIPLab()
            self.assertIsNotNone(net)
            net.stop()

    def test_keymgr_operations_succeed_when_keymgr_missing_from_path(self) -> None:
        """DNSProvisioner key generation and DS querying should succeed on plain runners."""
        zone = Zone("example.com.", dnssec=True)
        ds = DNSProvisioner.get_zone_ds_record(zone)
        self.assertIsInstance(ds, DSRecord)
        self.assertEqual(ds.name, "example.com.")
        self.assertEqual(ds.algorithm, 13)
        self.assertEqual(ds.digest_type, 2)
        self.assertGreater(ds.key_tag, 0)
        self.assertEqual(len(ds.digest), 64)


if __name__ == "__main__":
    unittest.main()
