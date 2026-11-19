"""Broadcast feeds for WebSocket subscribers.

Owns the subscriber set, locking, and message dispatch for one feed: the
registry keeps one for the Labs list (status updates, for every signed-in
browser) and one for the running Lab (topology and Service updates, for
the browsers on that Lab).
"""

import asyncio
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect, status

from mniplab.logging import get_logger

logger = get_logger(__name__)


class TopologyBroadcastService:
    """Manages WebSocket subscribers and broadcasts messages to them."""

    def __init__(self) -> None:
        self._subscribers: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add_subscriber(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers.add(websocket)
            logger.info(f"Added topology subscriber (total: {len(self._subscribers)})")

    async def remove_subscriber(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers.discard(websocket)
            logger.info(f"Removed topology subscriber (total: {len(self._subscribers)})")

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to all connected subscribers.

        Disconnected subscribers are silently removed.
        """
        async with self._lock:
            disconnected = []
            for ws in self._subscribers:
                try:
                    await ws.send_json(message)
                except (OSError, RuntimeError, ValueError, TypeError) as e:
                    logger.debug(f"Failed to send to subscriber: {e}")
                    disconnected.append(ws)

            for ws in disconnected:
                self._subscribers.discard(ws)

    async def close_all(self, reason: str) -> None:
        """Close every subscriber's connection, telling the browser why.

        Used when the Lab a feed belongs to has ended: the browsers must not
        keep a socket open on a Lab that no longer exists.

        Args:
            reason: The close reason sent to each browser.
        """
        async with self._lock:
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for ws in subscribers:
            try:
                await ws.close(code=status.WS_1000_NORMAL_CLOSURE, reason=reason)
            except (OSError, RuntimeError, ValueError, WebSocketDisconnect) as e:
                logger.debug(f"Subscriber was already gone: {e}")
