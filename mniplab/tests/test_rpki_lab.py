"""Integration tests for the RPKI CA Service on the RPKI Test Lab.

Each test boots a Lab with an RPKI CA on a Node, so Krill runs in that Node's
namespaces. They prove the starting ROA is listed at its Hosted CA, the CA's
hostname resolves from another Node, a Hosted CA refuses a prefix it does not
hold, ``krillc`` works in a Terminal with no environment set, nothing of Krill
survives the Lab stopping, and two Labs can each run an RPKI CA at once.

The Validator on the same Lab proves the chain end to end: r1 marks the
hijack invalid and the legitimate path valid, the Validator does not hold the
Lab up waiting for its first VRPs, and a Lab declaring a Validator without the
RPKI module fails at start.

The ROA and ASPA changes at a Hosted CA go through the RPKI object service and
the query service, and r1's validation state follows each change.

The query service reads the Validator back: its VRPs and ASPAs, when it last
refreshed, and which Routers hold an RTR session to it, named as Routers.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

from mniplab import mnIPLab
from mniplab.errors import ConfigurationError, KrillError
from mniplab.exabgp.models import BGPRoute
from mniplab.exabgp.routes import SpeakerRouteService
from mniplab.rpki.krill_client import KrillClient
from mniplab.rpki.models import ROA, HostedCA
from mniplab.rpki.objects import RPKIObjectService
from mniplab.rpki.query import RPKIQueryService
from mniplab.rpki.service import RPKICAService
from mniplab.tests.labs import (
    RPKI_LEGIT_PREFIX,
    LabCase,
    build_rpki_test_lab,
    rpki_states,
    rpki_vrp_count,
    unstarted_lab,
)
from mniplab.utils import wait_until


def _processes_mentioning(path: str) -> List[str]:
    """Return the command lines of every live process that names ``path``.

    Args:
        path: The host path to look for in each process's command line.

    Returns:
        The matching command lines, empty when nothing names the path.
    """
    found: List[str] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                command_line = handle.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if path in command_line:
            found.append(command_line.strip())
    return found


class RPKIHostedCALabTests(LabCase):
    """The RPKI CA on the RPKI Test Lab: its Hosted CAs, hostname and Terminal tools."""

    def _lab(self) -> Tuple[mnIPLab, Dict[str, Any]]:
        """Build and start the RPKI Test Lab.

        Returns:
            The started Lab and its Nodes, keyed by name.
        """
        net = self.lab()
        nodes = build_rpki_test_lab(net)
        self.start(net)
        return net, nodes

    def test_the_starting_roa_is_listed_at_its_hosted_ca(self) -> None:
        net, _nodes = self._lab()
        client = KrillClient(net.rpki_ca_socket_path("ca"))
        self.assertEqual(client.list_roas("as65002"), [ROA.parse("10.1.0.0/24-24 => 65002")])

    def test_the_ca_hostname_resolves_from_another_node(self) -> None:
        _net, nodes = self._lab()
        self.assertIn("10.0.14.2", nodes["r1"].cmd("getent hosts ca"))

    def test_a_hosted_ca_refuses_an_unheld_prefix(self) -> None:
        net, _nodes = self._lab()
        client = KrillClient(net.rpki_ca_socket_path("ca"))
        with self.assertRaises(KrillError) as ctx:
            client.update_roas(
                "as65003", added=[ROA.parse("10.1.0.0/24-24 => 65003")], removed=[]
            )
        self.assertEqual(ctx.exception.label, "ca-roa-delta-error")
        body = ctx.exception.body
        assert isinstance(body, dict)
        refused = [entry["prefix"] for entry in body["delta_error"]["notheld"]]
        self.assertIn("10.1.0.0/24", refused)

    def test_krillc_works_with_no_environment(self) -> None:
        _net, nodes = self._lab()
        output = nodes["ca"].cmd(
            "env -i PATH=/usr/local/bin:/usr/bin:/bin krillc roas list --ca as65002"
        )
        self.assertIn("10.1.0.0/24-24 => 65002", output)

    def test_nothing_of_krill_survives_the_lab_stopping(self) -> None:
        net, _nodes = self._lab()
        self.assertTrue(KrillClient(net.rpki_ca_socket_path("ca")).is_running())
        lab_dir = net.lab_dir

        net.stop()

        self.assertFalse(os.path.exists(lab_dir))
        self.assertEqual(_processes_mentioning(lab_dir), [])

    def test_two_labs_with_an_rpki_ca_run_at_once(self) -> None:
        first = self._ca_lab("rpkia", 65002)
        second = self._ca_lab("rpkib", 65003)

        first_handles = KrillClient(first.rpki_ca_socket_path("ca")).list_cas()
        second_handles = KrillClient(second.rpki_ca_socket_path("ca")).list_cas()

        self.assertIn("as65002", first_handles)
        self.assertNotIn("as65003", first_handles)
        self.assertIn("as65003", second_handles)
        self.assertNotIn("as65002", second_handles)

    def _ca_lab(self, lab_key: str, asn: int) -> mnIPLab:
        """Build and start a Lab holding one RPKI CA with one Hosted CA.

        Args:
            lab_key: The Lab's key, so two Labs can be told apart.
            asn: The Hosted CA's ASN.

        Returns:
            The started Lab.
        """
        net = mnIPLab(topo=None, controller=None, lab_key=lab_key)
        ca = net.addHost("ca")
        ca.addService(RPKICAService([HostedCA(asn, ["10.1.0.0/24"])]))
        return self.start(net)


class RPKIValidatorLabTests(LabCase):
    """The Validator on the RPKI Test Lab: the VRPs it serves mark r1's paths."""

    VALIDATION_TIMEOUT = 60.0
    HIJACK_ORIGIN = 65003
    LEGIT_ORIGIN = 65002

    def _lab(self) -> Tuple[mnIPLab, Dict[str, Any]]:
        """Build and start the RPKI Test Lab, Validator included.

        Returns:
            The started Lab and its Nodes, keyed by name.
        """
        net = self.lab()
        nodes = build_rpki_test_lab(net)
        self.start(net)
        return net, nodes

    def test_r1_marks_the_hijack_invalid_and_the_legitimate_path_valid(self) -> None:
        _net, nodes = self._lab()
        r1 = nodes["r1"]

        def settled() -> bool:
            states = rpki_states(r1, RPKI_LEGIT_PREFIX)
            return (
                states.get(self.HIJACK_ORIGIN) == "invalid"
                and states.get(self.LEGIT_ORIGIN) == "valid"
            )

        self.assertTrue(
            wait_until(settled, self.VALIDATION_TIMEOUT),
            f"r1 never settled; it holds {rpki_states(r1, RPKI_LEGIT_PREFIX)}",
        )
        self.assertEqual(
            rpki_states(r1, RPKI_LEGIT_PREFIX),
            {self.LEGIT_ORIGIN: "valid", self.HIJACK_ORIGIN: "invalid"},
        )

    def test_start_does_not_wait_for_the_first_vrps(self) -> None:
        _net, nodes = self._lab()
        self.assertEqual(rpki_vrp_count(nodes["r1"]), 0)

    def test_a_validator_without_the_rpki_module_fails_at_start(self) -> None:
        with unstarted_lab() as net:
            build_rpki_test_lab(net)
            with patch("mniplab.frrouter.config.rpki_module_installed", return_value=False):
                with self.assertRaises(ConfigurationError) as ctx:
                    net.start()
        self.assertIn("docs/install.md", str(ctx.exception))


class RPKIObjectLabTests(LabCase):
    """ROA and ASPA changes at a Hosted CA, made through the object service, reach r1's validation state."""

    VALIDATION_TIMEOUT = 60.0
    LEGIT_ORIGIN = 65002
    SUB_PREFIX = "10.1.0.0/25"

    def _lab(self) -> Tuple[mnIPLab, Dict[str, Any]]:
        """Build and start the RPKI Test Lab, Validator included.

        Returns:
            The started Lab and its Nodes, keyed by name.
        """
        net = self.lab()
        nodes = build_rpki_test_lab(net)
        self.start(net)
        return net, nodes

    def _settles_at(self, router: Any, prefix: str, origin: int, state: str) -> None:
        """Poll until a Router holds a path for a prefix from an origin in a state.

        Args:
            router: The Router to ask.
            prefix: The prefix, as ``show bgp ipv4 unicast`` takes it.
            origin: The origin AS of the path.
            state: The validation state the path must reach.
        """
        def settled() -> bool:
            return rpki_states(router, prefix).get(origin) == state

        self.assertTrue(
            wait_until(settled, self.VALIDATION_TIMEOUT),
            f"{router.name} never showed {prefix} from AS{origin} as {state}; "
            f"it holds {rpki_states(router, prefix)}",
        )

    def test_removing_the_roa_turns_r2s_path_not_found(self) -> None:
        net, nodes = self._lab()
        RPKIObjectService.remove_roa(net, "ca", 65002, "10.1.0.0/24-24 => 65002")
        self._settles_at(nodes["r1"], RPKI_LEGIT_PREFIX, self.LEGIT_ORIGIN, "not found")

    def test_re_adding_the_roa_makes_r2s_path_valid_again(self) -> None:
        net, nodes = self._lab()
        RPKIObjectService.remove_roa(net, "ca", 65002, "10.1.0.0/24-24 => 65002")
        self._settles_at(nodes["r1"], RPKI_LEGIT_PREFIX, self.LEGIT_ORIGIN, "not found")
        RPKIObjectService.add_roa(net, "ca", 65002, "10.1.0.0/24-24 => 65002")
        self._settles_at(nodes["r1"], RPKI_LEGIT_PREFIX, self.LEGIT_ORIGIN, "valid")

    def test_a_forged_origin_sub_prefix_is_invalid_at_maxlength_24_and_valid_at_25(self) -> None:
        net, nodes = self._lab()
        SpeakerRouteService.announce(
            net,
            "sp1",
            BGPRoute(self.SUB_PREFIX, "10.0.13.2", as_path=[65003, 65002]),
        )
        self._settles_at(nodes["r1"], self.SUB_PREFIX, self.LEGIT_ORIGIN, "invalid")

        RPKIObjectService.replace_roa(
            net, "ca", 65002, "10.1.0.0/24-24 => 65002", "10.1.0.0/24-25 => 65002"
        )

        self._settles_at(nodes["r1"], self.SUB_PREFIX, self.LEGIT_ORIGIN, "valid")

    def test_an_aspa_is_accepted_at_its_hosted_ca(self) -> None:
        net, _nodes = self._lab()
        RPKIObjectService.add_aspa(net, "ca", 65002, "AS65002 => AS65001")

        listing = {ca["asn"]: ca for ca in RPKIQueryService.list_cas(net)}

        self.assertEqual(listing[65002]["aspas"], [{"customer": 65002, "providers": [65001]}])


class RPKIVRPLabTests(LabCase):
    """The Validator's VRPs, refresh times and connected Routers, read through the query service."""

    VALIDATION_TIMEOUT = 60.0
    ASPA_CUSTOMER = 65002
    ASPA_PROVIDER = 65001

    def _lab(self, **kwargs: Any) -> Tuple[mnIPLab, Dict[str, Any]]:
        """Build and start the RPKI Test Lab, Validator included.

        Args:
            **kwargs: Passed to :func:`build_rpki_test_lab`.

        Returns:
            The started Lab and its Nodes, keyed by name.
        """
        net = self.lab()
        nodes = build_rpki_test_lab(net, **kwargs)
        self.start(net)
        return net, nodes

    def test_r1_is_listed_by_name_as_connected(self) -> None:
        net, _nodes = self._lab()
        view: Dict[str, Any] = {}

        def connected() -> bool:
            view.update(RPKIQueryService.get_vrps(net))
            return "r1" in view["routers"]

        self.assertTrue(
            wait_until(connected, self.VALIDATION_TIMEOUT),
            f"the Validator never listed r1 as connected; it lists {view.get('routers')}",
        )

    def test_a_starting_aspa_is_listed_at_the_validator(self) -> None:
        net, _nodes = self._lab(aspas=[f"AS{self.ASPA_CUSTOMER} => AS{self.ASPA_PROVIDER}"])
        view: Dict[str, Any] = {}
        expected = {"customer": self.ASPA_CUSTOMER, "providers": [self.ASPA_PROVIDER]}

        def listed() -> bool:
            view.update(RPKIQueryService.get_vrps(net))
            return expected in view["aspas"]

        self.assertTrue(
            wait_until(listed, self.VALIDATION_TIMEOUT),
            f"the Validator never listed {expected}; it lists {view.get('aspas')}",
        )
