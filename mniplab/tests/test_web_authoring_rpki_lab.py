"""Real Krill and Routinator execution of the RPKI Test Lab described as an authored definition."""

from mniplab.tests.labs import RPKI_LEGIT_PREFIX, LabCase, authored_rpki_test_lab, rpki_states
from mniplab.utils import wait_until


class AuthoredRPKILabTests(LabCase):
    """A Canvas definition of the RPKI Test Lab starts the RPKI chain, and r1 marks the hijack invalid."""

    def test_the_rpki_test_lab_from_a_definition_marks_the_hijack_invalid(self) -> None:
        network = self.start_draft(authored_rpki_test_lab(), address_family="ipv4")
        r1 = network.get("r1")

        def settled() -> bool:
            states = rpki_states(r1, RPKI_LEGIT_PREFIX)
            return states.get(65003) == "invalid" and states.get(65002) == "valid"

        self.assertTrue(
            wait_until(settled, 60.0),
            f"r1 never marked AS65003 invalid and AS65002 valid: {rpki_states(r1, RPKI_LEGIT_PREFIX)}",
        )
