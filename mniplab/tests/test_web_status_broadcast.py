"""Status fan-out budgets and visibility for shared classroom browser Sessions."""

import asyncio
import json
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from mniplab.tests.test_web_multi_lab import FakeLauncher
from mniplab.webserver.config import settings
from mniplab.webserver.session import SessionRegistry
from mniplab.webserver.websocket import LabsWebSocketHandler


class StatusSubscriber:
    """Record the status messages delivered to one browser."""

    def __init__(self, disconnected: bool = False) -> None:
        """Initialize a browser.

        Args:
            disconnected: Whether delivery should fail for this browser.
        """
        self.messages: list[dict[str, Any]] = []
        self.disconnected = disconnected

    async def send_json(self, message: dict[str, Any]) -> None:
        """Record a serialized message or report a disconnected browser.

        Args:
            message: The payload sent by the broadcaster.
        """
        if self.disconnected:
            raise RuntimeError("Browser disconnected")
        self.messages.append(json.loads(json.dumps(message)))


@pytest.mark.unit
class StatusBroadcastTests(unittest.IsolatedAsyncioTestCase):
    """A status update inspects each audience's Lab once and preserves isolation."""

    async def asyncSetUp(self) -> None:
        """Create a private registry with the existing fake child launcher."""
        self.registry = SessionRegistry()
        self.launcher = FakeLauncher()
        self.registry.lab_service.launcher = self.launcher
        self.addAsyncCleanup(self.registry.stop_all)
        self.addCleanup(self.registry.lab_service.executor.shutdown)

    async def subscribe(self, viewer: str, role: str = "learner") -> StatusSubscriber:
        """Subscribe an independent browser as a named classroom account.

        Args:
            viewer: The account behind the browser Session.
            role: The account's classroom role.

        Returns:
            The subscribed browser recording its messages.
        """
        subscriber = StatusSubscriber()
        await self.registry.add_status_subscriber(cast(WebSocket, subscriber), viewer, role)
        return subscriber

    async def test_single_lab_inspection_budget_does_not_grow_with_learner_count(
        self,
    ) -> None:
        """Forty viewers receive one fresh Lab snapshot for each status update."""
        with patch.object(settings, "lab_mode", "single"):
            await self.registry.lab_service.start_lab_from_builder(
                "static-lab", "lab.py"
            )
            handle = self.launcher.children[0].handle
            viewers = [await self.subscribe(f"learner{i}") for i in range(40)]
            with patch.object(handle, "status", wraps=handle.status) as inspections:
                await self.registry.broadcast_status_update()
                self.assertEqual(inspections.call_count, 1)
            self.assertTrue(
                all(viewer.messages == viewers[0].messages for viewer in viewers)
            )
            self.assertTrue(viewers[0].messages[0]["data"]["running"])
            # A later update must inspect the Lab again and publish fresh state.
            self.launcher.children[0].handle.net.hosts.clear()
            await self.registry.broadcast_status_update()
            self.assertEqual(viewers[0].messages[-1]["data"]["nodes"], 0)

    async def test_multi_lab_inspection_budget_and_group_isolation(self) -> None:
        """Five Sessions sharing each Group's login receive only their own Lab."""
        with patch.object(settings, "lab_mode", "multi"):
            for group in ("group-a", "group-b"):
                await self.registry.lab_service.start_lab_from_builder(
                    "static-lab", "lab.py", group
                )
            audiences = {
                group: [await self.subscribe(group) for _ in range(5)]
                for group in ("group-a", "group-b")
            }
            with patch.object(
                self.launcher.children[0].handle,
                "status",
                wraps=self.launcher.children[0].handle.status,
            ) as first:
                with patch.object(
                    self.launcher.children[1].handle,
                    "status",
                    wraps=self.launcher.children[1].handle.status,
                ) as second:
                    await self.registry.broadcast_status_update()
                    self.assertEqual((first.call_count, second.call_count), (1, 1))
            for index, viewers in enumerate(audiences.values()):
                self.assertTrue(
                    all(
                        viewer.messages[-1]["data"]["key"]
                        == self.launcher.children[index].key
                        for viewer in viewers
                    )
                )

    async def test_shared_lab_preview_remains_hidden_from_learners(self) -> None:
        """Instructor and Learner visibility cannot share a Preview snapshot."""
        with patch.object(settings, "lab_mode", "single"):
            await self.registry.lab_service.start_lab_from_builder(
                "static-lab", "lab.py", preview=True
            )
            instructor = await self.subscribe("teacher", "instructor")
            learner = await self.subscribe("student")
            await self.registry.broadcast_status_update()
            self.assertTrue(instructor.messages[-1]["data"]["running"])
            self.assertFalse(learner.messages[-1]["data"]["running"])

    async def test_disconnected_viewer_does_not_prevent_status_delivery(self) -> None:
        """A failed browser is removed while another still receives the update."""
        disconnected = StatusSubscriber(disconnected=True)
        await self.registry.add_status_subscriber(cast(WebSocket, disconnected), "group-a", "learner")
        live = await self.subscribe("group-a")
        await self.registry.broadcast_status_update()
        self.assertEqual(
            live.messages, [{"type": "status_update", "data": {"running": False}}]
        )
        disconnected.disconnected = False
        await self.registry.broadcast_status_update()
        self.assertEqual(disconnected.messages, [])

    async def test_websocket_disconnect_does_not_abort_terminal_cleanup_status(
        self,
    ) -> None:
        """A departed learner cannot prevent the rest seeing the stopped Lab."""
        disconnected = await self.subscribe("group-a")
        live = await self.subscribe("group-a")
        with patch.object(
            disconnected, "send_json", side_effect=WebSocketDisconnect(code=1006)
        ) as send:
            await self.registry.broadcast_status_update()
            await self.registry.broadcast_status_update()
            self.assertEqual(send.await_count, 1)
        self.assertEqual(len(live.messages), 2)
        self.assertTrue(all(not message["data"]["running"] for message in live.messages))

    async def test_new_status_cannot_overtake_an_update_already_being_delivered(
        self,
    ) -> None:
        """A paused browser must finish with the newest Lab state."""
        with patch.object(settings, "lab_mode", "single"):
            await self.registry.lab_service.start_lab_from_builder(
                "static-lab", "lab.py"
            )
            viewer = await self.subscribe("student")
            paused = asyncio.Event()
            resume = asyncio.Event()
            send = viewer.send_json

            async def pause_first_message(message: dict[str, Any]) -> None:
                if not paused.is_set():
                    paused.set()
                    await resume.wait()
                await send(message)

            with patch.object(viewer, "send_json", side_effect=pause_first_message):
                old = asyncio.create_task(self.registry.broadcast_status_update())
                await asyncio.wait_for(paused.wait(), 1)
                self.launcher.children[0].handle.net.hosts.clear()
                new = asyncio.create_task(self.registry.broadcast_status_update())
                try:
                    done, _ = await asyncio.wait({new}, timeout=0.05)
                    self.assertFalse(done, "The new update overtook the paused one")
                finally:
                    resume.set()
                    await asyncio.gather(old, new)
            self.assertEqual(viewer.messages[-1]["data"]["nodes"], 0)

    async def test_initial_status_cannot_overwrite_a_newer_broadcast(self) -> None:
        """A newly joining browser finishes with the newest status snapshot."""
        with patch.object(settings, "lab_mode", "single"):
            await self.registry.lab_service.start_lab_from_builder(
                "static-lab", "lab.py"
            )
            viewer = StatusSubscriber()
            handler = LabsWebSocketHandler(cast(WebSocket, viewer))
            paused = asyncio.Event()
            resume = asyncio.Event()
            leave = asyncio.Event()
            send = viewer.send_json

            async def authenticate() -> bool:
                handler._username = "student"
                handler._role = "learner"
                return True

            async def pause_first_message(message: dict[str, Any]) -> None:
                if not paused.is_set():
                    paused.set()
                    await resume.wait()
                await send(message)

            with (
                patch("mniplab.webserver.websocket.registry", self.registry),
                patch.object(viewer, "accept", AsyncMock(), create=True),
                patch.object(viewer, "send_json", side_effect=pause_first_message),
                patch.object(handler, "_authenticate", side_effect=authenticate),
                patch.object(handler, "_message_loop", side_effect=leave.wait),
            ):
                joining = asyncio.create_task(handler.run())
                await asyncio.wait_for(paused.wait(), 1)
                self.launcher.children[0].handle.net.hosts.clear()
                new = asyncio.create_task(self.registry.broadcast_status_update())
                try:
                    done, _ = await asyncio.wait({new}, timeout=0.05)
                    self.assertFalse(done, "A broadcast overtook the initial status")
                finally:
                    resume.set()
                    await new
                    leave.set()
                    await joining
            self.assertEqual(viewer.messages[-1]["data"]["nodes"], 0)


@pytest.mark.unit
class StatusBroadcastLoopTests(unittest.TestCase):
    """One registry serves broadcasts from several event loops in turn."""

    def test_a_lock_left_held_by_an_earlier_loop_does_not_block_the_next(self) -> None:
        registry = SessionRegistry()

        async def hold_then_leave() -> None:
            await registry.broadcast_status_update()
            await registry._broadcast_lock().acquire()

        asyncio.run(hold_then_leave())
        asyncio.run(asyncio.wait_for(registry.broadcast_status_update(), 1))
