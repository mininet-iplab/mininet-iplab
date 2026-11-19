"""RSTP chooses the configured root and discards one port in a triangle."""

import time

import pytest

from mniplab.tests.labs import LabCase


@pytest.mark.requires_ovs
class SwitchSpanningTreeTests(LabCase):
    """Read the live spanning tree through the ManagedSwitch view."""

    def test_rstp_triangle_elects_the_chosen_root_and_one_alternate(self) -> None:
        net = self.lab()
        core = net.addSwitch("s0", stp="rstp", priority=4096)
        access1 = net.addSwitch("s1", stp="rstp")
        access2 = net.addSwitch("s2", stp="rstp")
        net.addLink(core, access1)
        net.addLink(core, access2)
        net.addLink(access1, access2)
        self.start(net)
        deadline = time.monotonic() + 15
        while True:
            views = [switch.get_spanning_tree() for switch in (core, access1, access2)]
            alternate = [port for view in views for port in view["ports"]
                         if port["role"] == "Alternate"]
            forwarding = sum(port["state"] == "Forwarding" for view in views
                             for port in view["ports"])
            if (len(alternate) == 1 and forwarding == 5
                    and all(view["root_bridge"] == views[0]["bridge_id"] for view in views)):
                break
            if time.monotonic() >= deadline:
                self.fail(f"RSTP did not converge: {views}")
            time.sleep(0.25)
        self.assertEqual(views[0]["protocol"], "rstp")
        self.assertTrue(views[0]["bridge_id"].startswith("1.000."), views)
        self.assertEqual(alternate[0]["state"], "Discarding")
        self.assertEqual(sum(port["state"] == "Forwarding" for view in views
                             for port in view["ports"]), 5)
