"""Tests for the Recursive Caching Resolver Web API and its Topology badge.

Unit coverage (unprivileged) exercises the routes under
``/api/labs/fake/nodes/{node}/resolver`` against a mocked SessionRegistry, the registry's
own resolver methods, and the ``RESOLVER`` badge the Topology payload carries.

Privileged integration coverage (@pytest.mark.integration) stands up the DNS
Test Lab with a Resolver on ``res1``, drives the endpoints exactly as the Web
UI does, and asserts the cache transitions a Learner is meant to watch: a cold
walk filling the cache, a warm query counted as a hit, a cached denial, a
surgical flush, a total flush, and an Access Control rule applied without
dropping the cache.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import unittest
from typing import Any, List
from unittest.mock import AsyncMock, patch

import pytest
import uvicorn
from mniplab.tests.browser import sync_playwright

from mniplab.tests.fakes import FAKE_LAB_KEY, FakeLab, FakeLabAliveMixin, FakeTerminalCloserMixin, LoginClientMixin
from mniplab.resolver import ResolverService
from mniplab.tests.labs import LabCase, build_dns_test_lab
from mniplab.webserver import create_app, registry


CACHE_PAYLOAD = {
    "node": "res1",
    "entries": [
        {
            "name": "web.example.com.",
            "type": "A",
            "ttl": 118,
            "data": "10.0.5.2",
            "status": "positive",
            "negative": False,
            "soa": "",
        },
        {
            "name": "nosuch.example.com.",
            "type": "A",
            "ttl": 25,
            "data": "ns.example.com. hostmaster.example.com. 1 3600 1800 604800 30",
            "status": "NXDOMAIN",
            "negative": True,
            "soa": "example.com.",
        },
    ],
    "metrics": {
        "queries": 4,
        "hits": 2,
        "misses": 2,
        "hit_ratio": 50.0,
        "recursion_time_ms": 12.5,
    },
}


@pytest.mark.unit
class ResolverCacheRouteTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Cache reads and flushes under /api/nodes/{node}/resolver/cache."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_returns_entries_and_metrics_together(self) -> None:
        with patch(
            'mniplab.webserver.registry.get_resolver_cache',
            new=AsyncMock(return_value=CACHE_PAYLOAD),
        ) as mock_cache:
            response = self.client.get('/api/labs/fake/nodes/res1/resolver/cache')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["node"], "res1")
        self.assertEqual(len(payload["entries"]), 2)
        self.assertEqual(payload["metrics"]["hit_ratio"], 50.0)
        mock_cache.assert_called_once_with(FAKE_LAB_KEY, "res1")

    def test_negative_entry_carries_its_bounding_soa(self) -> None:
        """A cached denial must say which Zone denied it, not just that it did."""
        with patch(
            'mniplab.webserver.registry.get_resolver_cache',
            new=AsyncMock(return_value=CACHE_PAYLOAD),
        ):
            response = self.client.get('/api/labs/fake/nodes/res1/resolver/cache')

        negative = [e for e in response.json()["entries"] if e["negative"]]
        self.assertEqual([e["status"] for e in negative], ["NXDOMAIN"])
        self.assertEqual(negative[0]["soa"], "example.com.")

    def test_flush_without_a_target_clears_everything(self) -> None:
        flushed = {"node": "res1", "scope": "zone", "target": ".", "response": "ok"}
        with patch(
            'mniplab.webserver.registry.flush_resolver_cache',
            new=AsyncMock(return_value=flushed),
        ) as mock_flush:
            response = self.client.post(
                '/api/labs/fake/nodes/res1/resolver/cache/flush', json={},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), flushed)
        mock_flush.assert_called_once_with(FAKE_LAB_KEY, "res1", None, "name")

    def test_flush_narrows_to_one_name(self) -> None:
        flushed = {
            "node": "res1", "scope": "name",
            "target": "web.example.com", "response": "ok",
        }
        with patch(
            'mniplab.webserver.registry.flush_resolver_cache',
            new=AsyncMock(return_value=flushed),
        ) as mock_flush:
            response = self.client.post(
                '/api/labs/fake/nodes/res1/resolver/cache/flush',
                json={"target": "web.example.com", "scope": "name"},
            )

        self.assertEqual(response.status_code, 200)
        mock_flush.assert_called_once_with(FAKE_LAB_KEY, "res1", "web.example.com", "name")

    def test_flush_accepts_zone_scope(self) -> None:
        flushed = {
            "node": "res1", "scope": "zone",
            "target": "example.com", "response": "ok",
        }
        with patch(
            'mniplab.webserver.registry.flush_resolver_cache',
            new=AsyncMock(return_value=flushed),
        ) as mock_flush:
            response = self.client.post(
                '/api/labs/fake/nodes/res1/resolver/cache/flush',
                json={"target": "example.com", "scope": "zone"},
            )

        self.assertEqual(response.status_code, 200)
        mock_flush.assert_called_once_with(FAKE_LAB_KEY, "res1", "example.com", "zone")

    def test_unknown_flush_scope_returns_400(self) -> None:
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'flush_resolver_cache',
            side_effect=ValueError("Invalid flush scope 'subtree'"),
        ):
            response = self.client.post(
                '/api/labs/fake/nodes/res1/resolver/cache/flush',
                json={"target": "example.com", "scope": "subtree"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid flush scope", response.json()["detail"])

    def test_node_without_a_resolver_returns_400(self) -> None:
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'get_resolver_cache',
            side_effect=ValueError("h1 has no Resolver Service attached"),
        ):
            response = self.client.get('/api/labs/fake/nodes/h1/resolver/cache')

        self.assertEqual(response.status_code, 400)
        self.assertIn("no Resolver Service", response.json()["detail"])

    def test_requires_authentication(self) -> None:
        self.client.cookies.clear()
        response = self.client.get('/api/labs/fake/nodes/res1/resolver/cache')
        self.assertEqual(response.status_code, 401)


@pytest.mark.unit
class ResolverAccessRouteTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Access Control reads and replacements under /resolver/access."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_lists_rules_in_match_order(self) -> None:
        rules = [
            {"subnet": "0.0.0.0/0", "action": "allow", "family": 4},
            {"subnet": "10.0.5.0/24", "action": "refuse", "family": 4},
        ]
        with patch(
            'mniplab.webserver.registry.get_resolver_access',
            new=AsyncMock(return_value={"node": "res1", "rules": rules}),
        ):
            response = self.client.get('/api/labs/fake/nodes/res1/resolver/access')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rules"], rules)

    def test_replaces_the_whole_policy(self) -> None:
        applied = {
            "node": "res1",
            "rules": [{"subnet": "10.0.5.0/24", "action": "refuse", "family": 4}],
        }
        with patch(
            'mniplab.webserver.registry.set_resolver_access',
            new=AsyncMock(return_value=applied),
        ) as mock_set:
            response = self.client.put(
                '/api/labs/fake/nodes/res1/resolver/access',
                json={"rules": [{"subnet": "10.0.5.0/24", "action": "refuse"}]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), applied)
        mock_set.assert_called_once_with(
            FAKE_LAB_KEY, "res1", [{"subnet": "10.0.5.0/24", "action": "refuse"}],
        )

    def test_an_empty_policy_is_accepted(self) -> None:
        """Deleting the last rule is a legal edit, not a malformed request."""
        with patch(
            'mniplab.webserver.registry.set_resolver_access',
            new=AsyncMock(return_value={"node": "res1", "rules": []}),
        ) as mock_set:
            response = self.client.put(
                '/api/labs/fake/nodes/res1/resolver/access', json={"rules": []},
            )

        self.assertEqual(response.status_code, 200)
        mock_set.assert_called_once_with(FAKE_LAB_KEY, "res1", [])

    def test_invalid_action_returns_400(self) -> None:
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'set_resolver_access', return_value=[],
        ):
            response = self.client.put(
                '/api/labs/fake/nodes/res1/resolver/access',
                json={"rules": [{"subnet": "10.0.5.0/24", "action": "ignore"}]},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid Access Control action", response.json()["detail"])

    def test_invalid_subnet_returns_400(self) -> None:
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'set_resolver_access', return_value=[],
        ):
            response = self.client.put(
                '/api/labs/fake/nodes/res1/resolver/access',
                json={"rules": [{"subnet": "not-a-subnet", "action": "allow"}]},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid Access Control subnet", response.json()["detail"])

    def test_empty_subnet_is_rejected_by_request_validation(self) -> None:
        response = self.client.put(
            '/api/labs/fake/nodes/res1/resolver/access',
            json={"rules": [{"subnet": "", "action": "allow"}]},
        )
        self.assertEqual(response.status_code, 422)

    def test_requires_authentication(self) -> None:
        self.client.cookies.clear()
        response = self.client.get('/api/labs/fake/nodes/res1/resolver/access')
        self.assertEqual(response.status_code, 401)


@pytest.mark.unit
class ResolverAccessBroadcastTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """An applied policy reaches every open browser session."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_applied_policy_is_broadcast(self) -> None:
        applied = [{"subnet": "10.0.5.0/24", "action": "refuse", "family": 4}]
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'set_resolver_access', return_value=applied,
        ):
            with patch.object(
                registry.feed(FAKE_LAB_KEY), 'broadcast', new=AsyncMock(),
            ) as mock_broadcast:
                response = self.client.put(
                    '/api/labs/fake/nodes/res1/resolver/access',
                    json={"rules": [{"subnet": "10.0.5.0/24", "action": "refuse"}]},
                )

        self.assertEqual(response.status_code, 200)
        mock_broadcast.assert_called_once_with({
            "type": "RESOLVER_ACCESS_UPDATED",
            "data": {"node": "res1", "rules": applied},
        })

    def test_a_rejected_reload_does_not_broadcast(self) -> None:
        """A policy the daemon refused is not in force, so nobody is told it is."""
        with patch.object(
            registry.lab(FAKE_LAB_KEY).handle, 'set_resolver_access',
            side_effect=ValueError("Failed to apply Access Control rules"),
        ):
            with patch.object(
                registry.feed(FAKE_LAB_KEY), 'broadcast', new=AsyncMock(),
            ) as mock_broadcast:
                response = self.client.put(
                    '/api/labs/fake/nodes/res1/resolver/access',
                    json={"rules": [{"subnet": "10.0.5.0/24", "action": "refuse"}]},
                )

        self.assertEqual(response.status_code, 400)
        mock_broadcast.assert_not_called()

    def test_an_invalid_rule_does_not_broadcast(self) -> None:
        with patch.object(registry.lab(FAKE_LAB_KEY).handle, 'set_resolver_access', return_value=[]):
            with patch.object(
                registry.feed(FAKE_LAB_KEY), 'broadcast', new=AsyncMock(),
            ) as mock_broadcast:
                response = self.client.put(
                    '/api/labs/fake/nodes/res1/resolver/access',
                    json={"rules": [{"subnet": "10.0.5.0/24", "action": "ignore"}]},
                )

        self.assertEqual(response.status_code, 400)
        mock_broadcast.assert_not_called()


@pytest.mark.unit
class SessionRegistryResolverTests(unittest.TestCase):
    """SessionRegistry's resolver methods and per-Node reload lock."""

    def setUp(self) -> None:
        from mniplab.webserver.session import SessionRegistry
        self.reg = SessionRegistry()
        self.reg.lab_service.lab = FakeLab()
        self.key = FAKE_LAB_KEY

    def test_cache_returns_entries_and_metrics_as_dicts(self) -> None:
        from mniplab.resolver.models import CacheEntry, ResolverMetrics

        entries = [CacheEntry("web.example.com.", "A", 118, "10.0.5.2")]
        metrics = ResolverMetrics(queries=4, hits=2, misses=2, recursion_time_ms=12.5)

        with patch.object(
            self.reg.lab(self.key).handle, 'get_resolver_cache',
            return_value={
                "entries": [entry.to_dict() for entry in entries],
                "metrics": metrics.to_dict(),
            },
        ):
            payload = asyncio.run(self.reg.get_resolver_cache(self.key, "res1"))

        self.assertEqual(payload["node"], "res1")
        self.assertEqual(payload["entries"][0]["name"], "web.example.com.")
        self.assertEqual(payload["metrics"]["hit_ratio"], 50.0)

    def test_access_list_is_wrapped_with_the_node(self) -> None:
        with patch.object(
            self.reg.lab(self.key).handle, 'get_resolver_access',
            return_value=[{"subnet": "0.0.0.0/0", "action": "allow"}],
        ):
            payload = asyncio.run(self.reg.get_resolver_access(self.key, "res1"))
        self.assertEqual(payload["node"], "res1")
        self.assertEqual(len(payload["rules"]), 1)

    def test_an_invalid_rule_is_rejected_before_the_daemon_is_touched(self) -> None:
        with patch.object(self.reg.lab(self.key).handle, 'set_resolver_access') as run:
            with self.assertRaises(ValueError):
                asyncio.run(self.reg.set_resolver_access(
                    self.key, "res1", [{"subnet": "10.0.5.0/24", "action": "ignore"}],
                ))
            run.assert_not_called()

    def test_reload_lock_serializes_concurrent_policy_changes(self) -> None:
        """Two Learners saving at once must not interleave write with reload."""
        order: List[str] = []

        def fake_cmd(_node: str, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
            action = rules[0]["action"]
            order.append(f"start-{action}")
            import time
            time.sleep(0.05)
            order.append(f"end-{action}")
            return rules

        with patch.object(self.reg.lab(self.key).handle, 'set_resolver_access', side_effect=fake_cmd):
            setattr(self.reg.feed(self.key), 'broadcast', AsyncMock())

            async def run() -> None:
                await asyncio.gather(
                    self.reg.set_resolver_access(
                        self.key, "res1", [{"subnet": "10.0.5.0/24", "action": "refuse"}]),
                    self.reg.set_resolver_access(
                        self.key, "res1", [{"subnet": "10.0.5.0/24", "action": "deny"}]),
                )

            asyncio.run(run())
        self.assertEqual(
            order, ["start-refuse", "end-refuse", "start-deny", "end-deny"],
        )

    def test_reload_locks_are_independent_across_nodes(self) -> None:
        self.assertIsNot(
            self.reg._resolver_lock(self.key, "res1"), self.reg._resolver_lock(self.key, "res2"),
        )

    def test_stop_lab_clears_resolver_locks(self) -> None:
        """stop_lab lets the Lab go, and its per-node locks with it."""
        self.reg._resolver_lock(self.key, "res1")
        self.assertIn("res1", self.reg.lab(self.key).resolver_locks)
        with patch("mniplab.labhandle.LocalLabHandle._op_stop"):
            asyncio.run(self.reg.stop_lab(self.key))
        with self.assertRaises(ValueError):
            self.reg._resolver_lock(self.key, "res1")


class ResolverWebAPIIntegrationTests(LoginClientMixin, FakeTerminalCloserMixin, LabCase):
    """End-to-end: a Resolver on the DNS Test Lab driven through the endpoints the Web UI uses.

    ``res1`` walks the Lab's own hierarchy from ``root1``; ``client1`` names it
    as its only name server, so a plain ``dig`` goes through it.
    """

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

        self.net = self.lab()
        nodes = build_dns_test_lab(self.net)
        nodes["res1"].addService(ResolverService(root_hints=[nodes["root1"]]))
        self.start(self.net)
        result = asyncio.run(registry.start_lab(self.net, "dns-resolver-test"))
        self.assertNotIn("error", result)

        self.client_node = nodes["client1"]

    def tearDown(self) -> None:
        try:
            assert registry.lab_key is not None
            asyncio.run(registry.stop_lab(registry.lab_key))
        finally:
            super().tearDown()

    def _dig(self, name: str, rtype: str = "A") -> str:
        return self.client_node.cmd(f"dig {name} {rtype} +tries=1 +time=5")

    def _cache(self) -> dict:
        response = self.client.get(f'/api/labs/{registry.lab_key}/nodes/res1/resolver/cache')
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def _entries_for(payload: dict, name: str, rtype: str = "A") -> List[dict]:
        return [
            entry for entry in payload["entries"]
            if entry["name"] == name and entry["type"] == rtype
        ]

    def test_resolver_web_api_end_to_end_scenario(self) -> None:
        """Full scenario: resolution, cache hierarchy, metrics, flush, and access control."""
        # 1. Initial Policy, Topology Badges, Resolution & Cache Inspection
        response = self.client.get(f'/api/labs/{registry.lab_key}/nodes/res1/resolver/access')
        self.assertEqual(response.status_code, 200)
        subnets = {rule["subnet"] for rule in response.json()["rules"]}
        self.assertEqual(subnets, {"0.0.0.0/0", "::/0"})

        response = self.client.get(f'/api/labs/{registry.lab_key}/topology')
        self.assertEqual(response.status_code, 200)
        res1 = next(n for n in response.json()["nodes"] if n["id"] == "res1")
        badges = {d["name"]: d["badge"] for d in res1["service_details"]}
        self.assertEqual(badges.get("resolver"), "RESOLVER")

        answer = self._dig("web.example.com")
        self.assertIn("status: NOERROR", answer)
        self.assertIn("10.0.5.2", answer)

        cached = self._entries_for(self._cache(), "web.example.com.")
        self.assertEqual([entry["data"] for entry in cached], ["10.0.5.2"])
        self.assertEqual(cached[0]["status"], "positive")
        self.assertFalse(cached[0]["negative"])

        self._dig("web.example.com")
        metrics = self._cache()["metrics"]
        self.assertGreaterEqual(metrics["hits"], 1)
        self.assertGreater(metrics["hit_ratio"], 0.0)

        # 2. Hierarchy and negative caching
        names = {entry["name"] for entry in self._cache()["entries"]}
        self.assertIn("com.", names)
        self.assertIn("example.com.", names)

        self.assertIn("status: NXDOMAIN", self._dig("nosuch.example.com"))
        negative = [
            entry for entry in self._cache()["entries"]
            if entry["name"] == "nosuch.example.com."
        ]
        self.assertEqual([entry["status"] for entry in negative], ["NXDOMAIN"])
        self.assertTrue(negative[0]["negative"])
        self.assertEqual(negative[0]["soa"], "example.com.")

        # 3. Cached TTL decay
        self._dig("web.example.com")
        before_ttl = self._entries_for(self._cache(), "web.example.com.")[0]["ttl"]
        time.sleep(2)
        after_ttl = self._entries_for(self._cache(), "web.example.com.")[0]["ttl"]
        self.assertLess(after_ttl, before_ttl)

        # 4. Surgical and total flushing
        response = self.client.post(
            f'/api/labs/{registry.lab_key}/nodes/res1/resolver/cache/flush',
            json={"target": "web.example.com", "scope": "name"},
        )
        self.assertEqual(response.status_code, 200)
        payload = self._cache()
        self.assertEqual(self._entries_for(payload, "web.example.com."), [])
        self.assertIn("com.", {entry["name"] for entry in payload["entries"]})

        self._dig("web.example.com")
        self.assertNotEqual(self._cache()["entries"], [])
        response = self.client.post(
            f'/api/labs/{registry.lab_key}/nodes/res1/resolver/cache/flush', json={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._cache()["entries"], [])

        # 5. Access Control policy mutation & restoration
        self._dig("web.example.com")
        before_entries = {entry["name"] for entry in self._cache()["entries"]}
        self.assertIn("web.example.com.", before_entries)

        response = self.client.put(
            f'/api/labs/{registry.lab_key}/nodes/res1/resolver/access',
            json={"rules": [
                {"subnet": "0.0.0.0/0", "action": "allow"},
                {"subnet": "::/0", "action": "allow"},
                {"subnet": "10.0.5.0/24", "action": "refuse"},
            ]},
        )
        self.assertEqual(response.status_code, 200)
        subnet_actions = {rule["subnet"]: rule["action"] for rule in response.json()["rules"]}
        self.assertEqual(subnet_actions["10.0.5.0/24"], "refuse")

        self.assertIn("status: REFUSED", self._dig("web.example.com"))
        self.assertIn("web.example.com.", {e["name"] for e in self._cache()["entries"]})

        response = self.client.put(
            f'/api/labs/{registry.lab_key}/nodes/res1/resolver/access',
            json={"rules": [
                {"subnet": "0.0.0.0/0", "action": "allow"},
                {"subnet": "::/0", "action": "allow"},
            ]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("status: NOERROR", self._dig("web.example.com"))

    @pytest.mark.requires_browser
    def test_learner_flushes_the_cache_and_refuses_a_subnet_through_the_modal(self) -> None:
        """Drive the Resolver modal itself: the cache table, then a rule."""
        self._dig("web.example.com")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        server = uvicorn.Server(uvicorn.Config(
            create_app(), host="127.0.0.1", port=port, log_level="warning",
        ))
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("live browser server did not start within 10s")
                time.sleep(0.05)

            session_id = next(
                cookie.value for cookie in self.client.cookies.jar
                if cookie.name == "session"
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context()
                context.add_cookies([{
                    "name": "session",
                    "value": session_id,
                    "url": f"http://127.0.0.1:{port}",
                }])
                page = context.new_page()
                try:
                    page.goto(f"http://127.0.0.1:{port}/")
                    page.wait_for_selector("#app:not(.hidden)")
                    page.wait_for_function("""
                        () => document.querySelector('.node[aria-label*="res1"]')
                    """)
                    badge = page.locator(".node[aria-label*='res1'] .service-badge-resolver")
                    badge.wait_for()
                    badge.press("Enter")
                    page.wait_for_selector("#resolver-modal[style*='flex']")

                    # The cold walk's answer is in the table, badged as a HIT.
                    page.wait_for_function("""
                        () => document.querySelector('#resolver-cache-tbody')
                            .textContent.includes('web.example.com.')
                    """)
                    self.assertTrue(
                        page.locator("#resolver-cache-tbody .resolver-status-hit").count() > 0
                    )

                    page.locator("#resolver-flush-all").click()
                    page.wait_for_function("""
                        () => document.querySelector('#resolver-cache-tbody')
                            .children.length === 0
                    """)

                    page.locator("#resolver-tab-access").click()
                    page.wait_for_selector("#resolver-panel-access:not([hidden])")
                    page.locator("#resolver-access-subnet").fill("10.0.5.0/24")
                    page.locator("#resolver-access-action").select_option("refuse")
                    page.locator("#resolver-access-submit").click()
                    page.wait_for_function("""
                        () => document.querySelector('#resolver-access-tbody')
                            .textContent.includes('refuse')
                    """)
                finally:
                    context.close()
                    browser.close()
        finally:
            server.should_exit = True
            server_thread.join(timeout=5)

        self.assertIn("status: REFUSED", self._dig("web.example.com"))
