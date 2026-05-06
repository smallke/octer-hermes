"""WebSocket client for the Octer.ai cloud bridge.

Mirrors the Node.js implementation at octer-channel/src/core/octer-client.js:
- Connects to wss://octer.ai/ws/bridge?api_key=<key>
- Sends a `status` message on connect (hostname, machine_id, client_version, online)
- Pings every 30s
- Auto-reconnects 3s after any disconnect until stop() is called
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import socket
from typing import Any, Awaitable, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

logger = logging.getLogger(__name__)

BACKEND_WS_URL = "wss://octer.ai/ws/bridge"
RECONNECT_INTERVAL = 3.0
PING_INTERVAL = 30.0
CLIENT_VERSION = "2.1.0"

OnMessage = Callable[[dict], Awaitable[None]]
OnConnected = Callable[[], Any]


class OcterClient:
    def __init__(
        self,
        *,
        api_key: str,
        account_id: str = "default",
        on_message: OnMessage,
        on_connected: Optional[OnConnected] = None,
        log: Optional[Callable[..., None]] = None,
        error: Optional[Callable[..., None]] = None,
    ) -> None:
        self.api_key = api_key
        self.account_id = account_id
        self._on_message = on_message
        self._on_connected = on_connected
        self._log = log or logger.info
        self._error = error or logger.error

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._stopped = False
        self._ping_task: Optional[asyncio.Task] = None

    @staticmethod
    def get_machine_id() -> tuple[str, str]:
        hostname = socket.gethostname()
        try:
            user = getpass.getuser()
        except Exception:
            user = "unknown"
        return hostname, f"{hostname}-{user}"

    @staticmethod
    def _is_open(ws) -> bool:
        return ws is not None and getattr(ws, "state", None) == State.OPEN

    @property
    def is_connected(self) -> bool:
        return self._is_open(self._ws)

    async def start(self) -> None:
        """Run the connect/receive/reconnect loop until stop() is called."""
        while not self._stopped:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._error(f"[octer-channel][{self.account_id}] connection error: {exc}")
            if self._stopped:
                break
            self._log(
                f"[octer-channel][{self.account_id}] reconnecting in "
                f"{RECONNECT_INTERVAL:g}s ..."
            )
            try:
                await asyncio.sleep(RECONNECT_INTERVAL)
            except asyncio.CancelledError:
                raise

    async def _connect_once(self) -> None:
        url = f"{BACKEND_WS_URL}?api_key={self.api_key}&client_type=hermes"
        self._log(f"[octer-channel][{self.account_id}] connecting to {BACKEND_WS_URL} ...")
        try:
            async with websockets.connect(url, open_timeout=30, close_timeout=5) as ws:
                self._ws = ws
                self._log(f"[octer-channel][{self.account_id}] connected to Octer.ai")
                await self._send_status()
                if self._on_connected is not None:
                    try:
                        result = self._on_connected()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:  # noqa: BLE001
                        self._error(f"[octer-channel][{self.account_id}] on_connected error: {exc}")

                self._ping_task = asyncio.create_task(self._ping_loop(ws))
                try:
                    async for raw in ws:
                        await self._dispatch(raw)
                except ConnectionClosed as exc:
                    self._log(
                        f"[octer-channel][{self.account_id}] disconnected "
                        f"(code={exc.code}, reason={exc.reason or ''})"
                    )
                finally:
                    self._cancel_ping()
                    self._ws = None
        finally:
            self._cancel_ping()
            self._ws = None

    async def _dispatch(self, raw: Any) -> None:
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                self._error(f"[octer-channel][{self.account_id}] non-utf8 frame, skipping")
                return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            self._error(f"[octer-channel][{self.account_id}] bad JSON from backend")
            return
        try:
            await self._on_message(msg)
        except Exception as exc:  # noqa: BLE001
            self._error(f"[octer-channel][{self.account_id}] on_message error: {exc}")

    async def _send_status(self) -> None:
        hostname, machine_id = self.get_machine_id()
        await self.send(
            {
                "type": "status",
                "hostname": hostname,
                "machine_id": machine_id,
                "client_version": CLIENT_VERSION,
                "openclaw_status": "online",
            }
        )

    async def _ping_loop(self, ws) -> None:
        try:
            while self._is_open(ws):
                await asyncio.sleep(PING_INTERVAL)
                if not self._is_open(ws):
                    return
                try:
                    await ws.send(json.dumps({"type": "ping"}))
                except (ConnectionClosed, RuntimeError):
                    return
        except asyncio.CancelledError:
            raise

    def _cancel_ping(self) -> None:
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
        self._ping_task = None

    async def send(self, msg: dict) -> bool:
        ws = self._ws
        if not self._is_open(ws):
            self._error(f"[octer-channel][{self.account_id}] cannot send, not connected")
            return False
        try:
            await ws.send(json.dumps(msg))
            return True
        except (ConnectionClosed, RuntimeError) as exc:
            self._error(f"[octer-channel][{self.account_id}] send failed: {exc}")
            return False

    async def stop(self) -> None:
        self._stopped = True
        self._cancel_ping()
        ws = self._ws
        if self._is_open(ws):
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws = None
