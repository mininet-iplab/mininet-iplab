"""Unit tests for the web SessionRegistry facade."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from mniplab.tests.fakes import FAKE_LAB_KEY, FakeIntf, FakeLab, FakeLink, FakeNode
from mniplab.webserver.session import SessionRegistry


class SessionRegistryLabCommandTests(unittest.IsolatedAsyncioTestCase):
    """What the facade does itself: the no-Lab guard, link condition changes,
    and the broadcast that follows a successful write.

    Which service a command is forwarded to is not asserted here; a wrong
    forward shows up where the result is used, against a Lab.
    """

    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.lab = Mock(lab_key="fake")
        self.registry.lab_service.lab = self.lab
        self.key = "fake"

    async def test_run_ping_raises_when_no_lab_running(self) -> None:
        self.registry.lab_service.lab = None

        with self.assertRaises(ValueError) as ctx:
            await self.registry.run_ping(self.key, "h1", "h2")

        self.assertEqual(str(ctx.exception), "No lab running")

    async def test_link_configure_broadcasts_after_success(self) -> None:
        self.registry.broadcast_topology_update = AsyncMock()  # type: ignore[method-assign]

        with patch(
            "mniplab.labhandle.NetworkTestService.configure_link",
            return_value={"success": True, "link": "r1 <-> r2"},
        ):
            result = await self.registry.link_configure(
                self.key,
                "r1",
                "r2",
                delay="10ms",
                jitter=None,
                loss="1%",
                bandwidth="100",
                scheduler="hfsc",
            )

        self.assertEqual(result, {"success": True, "link": "r1 <-> r2"})
        self.registry.broadcast_topology_update.assert_awaited_once_with(self.key)

    async def test_disconnect_link_wraps_mininet_errors(self) -> None:
        self.registry.broadcast_topology_update = AsyncMock()  # type: ignore[method-assign]
        self.lab.configLinkStatus.side_effect = KeyError("missing")

        with self.assertRaises(ValueError) as ctx:
            await self.registry.disconnect_link(self.key, "r1", "r2")

        self.assertIn("Failed to down link", str(ctx.exception))
        self.registry.broadcast_topology_update.assert_not_awaited()

    async def test_disconnect_link_broadcasts_after_success(self) -> None:
        self.registry.broadcast_topology_update = AsyncMock()  # type: ignore[method-assign]

        result = await self.registry.disconnect_link(self.key, "r1", "r2")

        self.assertEqual(
            result,
            {"success": True, "status": "disconnected", "link": "r1 <-> r2"},
        )
        self.lab.configLinkStatus.assert_called_once_with("r1", "r2", "down")
        self.registry.broadcast_topology_update.assert_awaited_once_with(self.key)

    async def test_set_node_pool_broadcasts_after_success(self) -> None:
        broadcast = AsyncMock()
        self.registry.feed(self.key).broadcast = broadcast  # type: ignore[method-assign]
        pool = {"cidr": "10.50.0.0/24", "start": "10.50.0.150", "end": "10.50.0.220"}
        pools = [pool]

        with patch(
            "mniplab.labhandle.PoolService.set_pool",
            return_value={"success": True, "pool": pool, "pools": pools},
        ):
            result = await self.registry.set_node_pool(
                self.key, "r1", "10.50.0.0/24", "10.50.0.150", "10.50.0.220"
            )

        self.assertEqual(result, {"success": True, "pool": pool, "pools": pools})
        broadcast.assert_awaited_once_with({
            "type": "dhcp_pool_update",
            "data": {"node": "r1", "pool": pool, "pools": pools},
        })

    async def test_set_node_pool_rejection_does_not_broadcast(self) -> None:
        broadcast = AsyncMock()
        self.registry.feed(self.key).broadcast = broadcast  # type: ignore[method-assign]

        with patch(
            "mniplab.labhandle.PoolService.set_pool",
            side_effect=ValueError("Start address cannot be after end address"),
        ):
            with self.assertRaises(ValueError) as ctx:
                await self.registry.set_node_pool(
                    self.key, "r1", "10.50.0.0/24", "10.50.0.200", "10.50.0.100"
                )

        self.assertIn("cannot be after", str(ctx.exception))
        broadcast.assert_not_awaited()


class SessionRegistryInternetGatewayLinkTests(unittest.IsolatedAsyncioTestCase):
    """Link Config and Link status on an Internet Gateway's Link are refused through the facade."""

    def _registry(self) -> SessionRegistry:
        registry = SessionRegistry()
        router = FakeNode("r1", display_type="router")
        gateway = FakeNode("nat0", hidden=True)
        gateway.inNamespace = False  # type: ignore[attr-defined]
        router_end, gateway_end = FakeIntf("r1-eth1", router), FakeIntf("nat0-eth0", gateway)
        router.addIntf(router_end)
        gateway.addIntf(gateway_end)
        registry.lab_service.lab = FakeLab(
            router, gateway, links=[FakeLink(router_end, gateway_end)]
        )
        registry.broadcast_topology_update = AsyncMock()  # type: ignore[method-assign]
        return registry

    async def test_disconnect_and_reconnect_refuse_the_gateway_link_and_broadcast_nothing(self) -> None:
        registry = self._registry()

        for action in (registry.disconnect_link, registry.reconnect_link):
            with self.subTest(action=action.__name__):
                with self.assertRaises(ValueError) as ctx:
                    await action(FAKE_LAB_KEY, "r1", "nat0")

                self.assertEqual(str(ctx.exception), "nat0 is the Internet, which is outside the Lab")
        assert isinstance(registry.broadcast_topology_update, AsyncMock)
        registry.broadcast_topology_update.assert_not_awaited()

    async def test_link_configure_refuses_the_gateway_and_broadcasts_nothing(self) -> None:
        registry = self._registry()

        with self.assertRaises(ValueError) as ctx:
            await registry.link_configure(FAKE_LAB_KEY, "r1", "nat0", delay="10ms")

        self.assertEqual(str(ctx.exception), "nat0 is the Internet, which is outside the Lab")
        assert isinstance(registry.broadcast_topology_update, AsyncMock)
        registry.broadcast_topology_update.assert_not_awaited()


class SessionRegistryLinkConditionTests(unittest.IsolatedAsyncioTestCase):
    """A Link Condition the Lab cannot apply is refused through the facade, and nothing is broadcast."""

    async def test_link_configure_refuses_a_quoted_delay_and_broadcasts_nothing(self) -> None:
        """A quoted delay raises ValueError here, and no topology update goes out."""
        registry = SessionRegistry()
        r1, r2 = FakeNode("r1", display_type="router"), FakeNode("r2", display_type="router")
        r1_end, r2_end = FakeIntf("r1-eth0", r1), FakeIntf("r2-eth0", r2)
        r1.addIntf(r1_end)
        r2.addIntf(r2_end)
        r1_end.config = Mock(return_value={"tcoutputs": ["", ""]})  # type: ignore[attr-defined]
        r2_end.config = Mock(return_value={"tcoutputs": ["", ""]})  # type: ignore[attr-defined]
        registry.lab_service.lab = FakeLab(r1, r2, links=[FakeLink(r1_end, r2_end)])
        registry.broadcast_topology_update = AsyncMock()  # type: ignore[method-assign]

        with self.assertRaises(ValueError) as ctx:
            await registry.link_configure(FAKE_LAB_KEY, "r1", "r2", delay="10ms'")

        self.assertIn("delay", str(ctx.exception))
        r1_end.config.assert_not_called()  # type: ignore[attr-defined]
        r2_end.config.assert_not_called()  # type: ignore[attr-defined]
        assert isinstance(registry.broadcast_topology_update, AsyncMock)
        registry.broadcast_topology_update.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
