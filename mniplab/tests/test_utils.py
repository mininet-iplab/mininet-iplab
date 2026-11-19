"""Unit tests for the helpers in mniplab/utils.py."""

import unittest

from mniplab.utils import wait_until


class WaitUntilTests(unittest.TestCase):
    """wait_until polls a probe until it answers or the budget runs out."""

    def test_returns_true_as_soon_as_the_probe_does(self):
        answers = iter([False, False, True])
        calls = []

        def probe() -> bool:
            calls.append(1)
            return next(answers)

        self.assertTrue(wait_until(probe, timeout=1.0, interval=0))
        self.assertEqual(len(calls), 3)

    def test_returns_false_once_the_budget_is_spent(self):
        self.assertFalse(wait_until(lambda: False, timeout=0.05, interval=0.01))

    def test_probes_at_least_once_even_with_no_budget(self):
        calls = []

        def probe() -> bool:
            calls.append(1)
            return True

        self.assertTrue(wait_until(probe, timeout=0))
        self.assertEqual(len(calls), 1)
