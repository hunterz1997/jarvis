"""WebSocket server — bridges the browser client to the Jarvis agent."""

import json
import logging
from fastapi import WebSocket, WebSocketDisconnect
from core.agent import agent

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[session_id] = ws
        logger.info("WebSocket connected: %s", session_id)

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)
        logger.info("WebSocket disconnected: %s", session_id)

    async def send(self, session_id: str, data: dict) -> bool:
        ws = self._connections.get(session_id)
        if not ws:
            return False
        try:
            await ws.send_text(json.dumps(data))
            return True
        except Exception:
            self.disconnect(session_id)
            return False

    async def broadcast(self, data: dict) -> int:
        """Send data to ALL connected WebSocket clients. Returns count delivered."""
        if not self._connections:
            return 0
        payload = json.dumps(data)
        delivered = 0
        # Snapshot keys so we can prune disconnected sockets safely during iteration
        for sid in list(self._connections.keys()):
            ws = self._connections.get(sid)
            if not ws:
                continue
            try:
                await ws.send_text(payload)
                delivered += 1
            except Exception:
                self.disconnect(sid)
        return delivered

    @property
    def active_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """
    Handle a WebSocket connection for a given session.
    Receives user messages and streams Jarvis responses token by token.
    """
    await manager.connect(session_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            msg_type = data.get("type")
            if msg_type != "message":
                continue

            content = data.get("content", "").strip()
            if not content:
                continue

            # Optional: voice-mode camera frames or other vision attachments.
            # Expected shape: {"images": ["<base64-jpeg>", ...]} (data: prefix OK)
            images = data.get("images") if isinstance(data.get("images"), list) else None

            # Stream agent response directly to WebSocket
            async for event in agent.run(session_id=session_id, user_message=content, images=images):
                sent = await manager.send(session_id, event)
                if not sent:
                    break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket error for session %s: %s", session_id, e)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
    finally:
        manager.disconnect(session_id)
