"""The DNS and Resolver behaviour behind the Web UI modals, driven from the CLI.

Three integration tests drive those modals in a real browser. They need a
privileged Lab *and* a browser at once, which the lab image cannot give
them: it carries no chromium. Run them where both exist, the dev container,
with ``pytest -m "integration and requires_browser"``.

These tests carry the daemon-level half of what that group covers, through the same commands a Learner types at
``mininet-iplab>``. What is left to the browser group is the UI itself —
that the Secondary's panel renders read-only, that the form is hidden —
which has no CLI equivalent and should not pretend to one.

One Test Lab, not a Lab Example: these are mechanism tests for the DNS
Service family.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import pytest

from mniplab.resolver.service import ResolverService
from mniplab.tests.fakes import cli_output
from mniplab.tests.labs import LabCase, build_dns_test_lab

AUTH = "10.0.3.2"
RESOLVER = "10.0.4.2"


@pytest.mark.integration
@pytest.mark.requires_root
class CLIDNSRecordIntegrationTests(LabCase):
    """A record authored at the prompt is served by the real daemon."""

    def test_a_record_added_from_the_cli_resolves_for_a_client(self) -> None:
        """``dns_records ... add`` reaches knotd, not just the config on disk.

        The Web UI's DNS modal is one caller of this; the prompt is the
        other, and the interesting part is common to both: the Zone is
        edited while the daemon is running and the next query sees it.
        """
        net = self.lab()
        nodes = build_dns_test_lab(net)
        self.start(net)

        printed = cli_output(
            "dns_records", f"auth1 example.com. add cli A 10.0.9.9", net=net
        )
        self.assertIn("Added record cli A 10.0.9.9", printed)

        answer = nodes["client1"].cmd(f"dig @{AUTH} cli.example.com A +tries=1 +time=5")
        self.assertIn("status: NOERROR", answer)
        self.assertIn("10.0.9.9", answer)

    def test_a_record_deleted_from_the_cli_stops_resolving(self) -> None:
        """The delete half of the same command, proven the same way."""
        net = self.lab()
        nodes = build_dns_test_lab(net)
        self.start(net)

        cli_output("dns_records", "auth1 example.com. add gone A 10.0.9.8", net=net)
        self.assertIn(
            "10.0.9.8",
            nodes["client1"].cmd(f"dig @{AUTH} gone.example.com A +tries=1 +time=5"),
        )

        printed = cli_output("dns_records", "auth1 example.com. del gone", net=net)
        self.assertIn("Deleted record gone", printed)

        answer = nodes["client1"].cmd(f"dig @{AUTH} gone.example.com A +tries=1 +time=5")
        self.assertIn("status: NXDOMAIN", answer)


@pytest.mark.integration
@pytest.mark.requires_root
class CLIResolverIntegrationTests(LabCase):
    """The Resolver modal's two actions — flush the cache, refuse a subnet."""

    def _resolving_lab(self) -> Tuple[Any, Dict[str, Any]]:
        """The DNS Test Lab with ``res1`` seeded from the in-lab Root."""
        net = self.lab()
        nodes = build_dns_test_lab(net)
        nodes["res1"].addService(ResolverService(root_hints=[nodes["root1"]]))
        return net, nodes

    def test_flush_cache_drops_what_a_query_cached(self) -> None:
        """``flush_cache`` makes the next query walk the hierarchy again."""
        net, nodes = self._resolving_lab()
        self.start(net)
        client = nodes["client1"]

        warmed = client.cmd(f"dig @{RESOLVER} web.example.com A +tries=1 +time=5")
        self.assertIn("status: NOERROR", warmed)
        self.assertIn("web.example.com", cli_output("show_cache", "res1", net=net))

        cli_output("flush_cache", "res1", net=net)

        self.assertNotIn("web.example.com", cli_output("show_cache", "res1", net=net))

    def test_a_refuse_rule_added_from_the_cli_stops_a_client_resolving(self) -> None:
        """``resolver_access ... refuse`` is applied hot, to a live daemon.

        The rule has to reach unbound without a restart — that is what
        "applied hot" in the command's own help means, and the only way to
        see it is a client that resolved a moment ago and now cannot.
        """
        net, nodes = self._resolving_lab()
        self.start(net)
        client = nodes["client1"]

        self.assertIn(
            "status: NOERROR",
            client.cmd(f"dig @{RESOLVER} web.example.com A +tries=1 +time=5"),
        )

        printed = cli_output("resolver_access", "res1 add 10.0.5.0/24 refuse", net=net)
        self.assertIn("10.0.5.0/24", printed)
        self.assertIn("refuse", printed)

        refused = client.cmd(f"dig @{RESOLVER} other.example.com A +tries=1 +time=5")
        self.assertIn("status: REFUSED", refused)
