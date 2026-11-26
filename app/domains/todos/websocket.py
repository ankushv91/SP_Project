import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)


class TodoConnectionManager:
    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)

    async def broadcast(self, payload: dict) -> None:
        for connection in tuple(self.active_connections):
            try:
                await connection.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                self.disconnect(connection)
                logger.info("Removed disconnected Todo WebSocket client")


todo_connection_manager = TodoConnectionManager()
