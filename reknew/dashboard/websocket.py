"""WebSocket manager for real-time dashboard updates."""

import datetime
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """Manages WebSocket connections and broadcasts events.

    Components emit events:
    - Agent monitor emits "agent_status" when an agent finishes
    - Reactions engine emits "ci_result" when CI completes
    - Task state machine emits "task_update" on every transition
    - Capacity router emits "capacity_update" on routing decisions

    The broadcaster relays these to all connected WebSocket clients.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a WebSocket connection.

        Args:
            websocket: incoming WebSocket connection
        """
        await websocket.accept()
        self._connections.add(websocket)
        logger.info("WebSocket connected (%d total)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection.

        Args:
            websocket: the connection to remove
        """
        self._connections.discard(websocket)
        logger.info(
            "WebSocket disconnected (%d remaining)", len(self._connections)
        )

    async def broadcast(self, event_type: str, data: dict) -> None:
        """Send event to all connected clients.

        Message format: {"type": event_type, "data": data, "timestamp": iso}
        Silently drops connections that have closed.

        Args:
            event_type: event type string
            data: payload dict
        """
        if not self._connections:
            return

        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
        })

        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._connections.discard(ws)

    async def emit(self, event_type: str, data: dict) -> None:
        """Alias for broadcast. Used by other components to emit events.

        Args:
            event_type: event type string
            data: payload dict
        """
        await self.broadcast(event_type, data)

    @property
    def connection_count(self) -> int:
        """Number of active WebSocket connections."""
        return len(self._connections)
