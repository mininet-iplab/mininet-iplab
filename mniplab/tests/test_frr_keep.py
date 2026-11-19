"""FRR running-configuration normalization for Keep."""

import unittest

import pytest

from mniplab.webserver.authoring import frr_config_body

RUNNING = """Building configuration...

Current configuration:
!
frr version 10.1
frr defaults traditional
hostname r1
service integrated-vtysh-config
!
router ospf
 network 10.0.0.0/30 area 0
exit
!
end
"""


@pytest.mark.unit
class FrrKeepTests(unittest.TestCase):
    def test_body_drops_generated_header_and_end(self):
        self.assertEqual(
            frr_config_body(RUNNING),
            "router ospf\n network 10.0.0.0/30 area 0\nexit\n!\n",
        )
