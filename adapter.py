"""Octer.ai platform adapter for Hermes Agent.

Plugin-based gateway adapter that bridges Octer.ai cloud requests to the
local Hermes agent over a WebSocket connection.

Configuration (env vars take precedence over config.yaml):
    OCTER_API_KEY            - API key from octer.ai/workspace (starts with `evo_`)
    OCTER_ALLOWED_USERS      - comma-separated user IDs allowed to talk to the bot
    OCTER_ALLOW_ALL_USERS    - "true" to disable the allowlist

Or via ~/.hermes/config.yaml::

    gateway:
      platforms:
        octer:
          enabled: true
          extra:
            api_key: evo_...
            account_id: default
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import time
from typing import Any, Optional

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.config import Platform, PlatformConfig

from .client import OcterClient
from .dedup import RequestDedup

logger = logging.getLogger(__name__)

MAX_TOOL_RESPONSE_BYTES = 50_000

# All Octer cloud requests share this stable user_id so the gateway's
# allowlist (OCTER_ALLOWED_USERS) can authorize Octer as a single peer
# instead of having to whitelist every per-request UUID.
OCTER_USER_ID = "octer-cloud"


class OcterAdapter(BasePlatformAdapter):
    """Bridges Octer.ai cloud tool_request messages to the local Hermes agent."""

    def __init__(self, config: PlatformConfig, **_: Any) -> None:
        super().__init__(config=config, platform=Platform("octer"))
        extra = getattr(config, "extra", {}) or {}
        self.api_key: str = os.getenv("OCTER_API_KEY") or extra.get("api_key", "") or ""
        self.account_id: str = extra.get("account_id", "default")

        self._client: Optional[OcterClient] = None
        self._client_task: Optional[asyncio.Task] = None
        self._dedup = RequestDedup()
        self._pending: dict[str, list[str]] = {}
        self._req_locks: dict[str, asyncio.Lock] = {}

    @property
    def name(self) -> str:
        return "Octer"

    # ── lifecycle ────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        if not self.api_key:
            logger.error("[octer] OCTER_API_KEY required")
            self._set_fatal_error(
                "config_missing", "OCTER_API_KEY required", retryable=False
            )
            return False
        if not self.api_key.startswith("evo_"):
            logger.error("[octer] OCTER_API_KEY must start with 'evo_'")
            self._set_fatal_error(
                "invalid_apikey",
                "OCTER_API_KEY must start with evo_",
                retryable=False,
            )
            return False

        self._client = OcterClient(
            api_key=self.api_key,
            account_id=self.account_id,
            on_message=self._on_ws_message,
            on_connected=lambda: logger.info("[octer] WebSocket ready"),
            log=logger.info,
            error=logger.error,
        )
        self._client_task = asyncio.create_task(self._client.start())
        self._mark_connected()
        logger.info("[octer] adapter connected (account=%s)", self.account_id)
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()
        if self._client is not None:
            try:
                await self._client.stop()
            except Exception:  # noqa: BLE001
                logger.exception("[octer] error stopping client")
        if self._client_task is not None and not self._client_task.done():
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("[octer] error awaiting client task")
        self._client = None
        self._client_task = None

    # ── inbound from Octer cloud ─────────────────────────────────────────

    async def _on_ws_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "tool_request":
            asyncio.create_task(self._handle_tool_request(msg))
        elif msg_type == "pong":
            return
        else:
            logger.debug("[octer] unknown WS message type: %s", msg_type)

    async def _handle_tool_request(self, msg: dict) -> None:
        request_id = str(msg.get("request_id") or "unknown")
        if not self._dedup.try_record(request_id):
            logger.info("[octer] duplicate request %s, skipping", request_id)
            return

        lock = self._req_locks.setdefault(request_id, asyncio.Lock())
        async with lock:
            try:
                await self._dispatch_to_agent(request_id, msg)
            finally:
                self._req_locks.pop(request_id, None)

    async def _dispatch_to_agent(self, request_id: str, msg: dict) -> None:
        query = (msg.get("arguments") or {}).get("query") or ""
        tool_name = msg.get("tool_name", "")
        logger.info(
            "[octer] tool_request id=%s tool=%s query_len=%d",
            request_id,
            tool_name,
            len(query),
        )

        if not self._message_handler:
            logger.warning("[octer] no message handler bound for request %s", request_id)
            await self._send_tool_response(
                request_id, error="No handler bound", success=False
            )
            return

        source = self.build_source(
            chat_id=request_id,
            chat_name=f"octer:{request_id[:12]}",
            chat_type="dm",
            user_id=OCTER_USER_ID,
            user_name="Octer",
        )
        event = MessageEvent(
            text=query,
            message_type=MessageType.TEXT,
            source=source,
            message_id=f"octer_{int(time.time() * 1000)}",
            timestamp=datetime.datetime.now(),
        )

        self._pending[request_id] = []
        response: Any = None
        try:
            response = await self._message_handler(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[octer] handler error for %s", request_id)
            await self._send_tool_response(
                request_id, error=str(exc), success=False
            )
            self._pending.pop(request_id, None)
            return
        buffered = self._pending.pop(request_id, [])

        if isinstance(response, str) and response.strip():
            text = response
        elif buffered:
            text = "\n".join(chunk for chunk in buffered if chunk)
        else:
            text = ""
        text = text or "(no response)"

        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TOOL_RESPONSE_BYTES:
            text = encoded[:MAX_TOOL_RESPONSE_BYTES].decode(
                "utf-8", errors="ignore"
            ) + "\n…(truncated)"

        await self._send_tool_response(request_id, result=text, success=True)

    async def _send_tool_response(
        self,
        request_id: str,
        *,
        result: Optional[str] = None,
        error: Optional[str] = None,
        success: bool = True,
    ) -> None:
        if self._client is None:
            logger.warning(
                "[octer] tool_response dropped (no client) id=%s", request_id
            )
            return
        await self._client.send(
            {
                "type": "tool_response",
                "request_id": request_id,
                "result": result,
                "error": error,
                "success": success,
            }
        )
        logger.info(
            "[octer] tool_response id=%s success=%s len=%d",
            request_id,
            success,
            len(result or ""),
        )

    # ── outbound (BasePlatformAdapter abstract methods) ──────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        if chat_id in self._pending:
            self._pending[chat_id].append(content)
            return SendResult(
                success=True, message_id=f"octer_buf_{int(time.time() * 1000)}"
            )

        if self._client is None:
            return SendResult(success=False, error="not connected")
        msg_id = f"octer_{int(time.time() * 1000)}"
        payload: dict[str, Any] = {
            "type": "message",
            "to": chat_id,
            "text": content,
            "message_id": msg_id,
        }
        if reply_to:
            payload["reply_to"] = reply_to
        ok = await self._client.send(payload)
        return SendResult(
            success=ok,
            message_id=msg_id if ok else None,
            error=None if ok else "send failed",
        )

    async def send_typing(
        self, chat_id: str, metadata: Optional[dict[str, Any]] = None
    ) -> None:
        return  # Octer protocol has no typing indicator

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": chat_id, "type": "dm", "chat_id": chat_id}


# ── module-level functions required by the Hermes plugin protocol ────────


def check_requirements() -> bool:
    """Return True iff the python deps and required env are available."""
    try:
        import websockets  # noqa: F401
    except ImportError:
        logger.warning("[octer] missing dependency: pip install websockets")
        return False
    return bool(os.getenv("OCTER_API_KEY"))


def validate_config(config: Any) -> bool:
    extra = getattr(config, "extra", {}) or {}
    return bool(os.getenv("OCTER_API_KEY") or extra.get("api_key"))


def is_connected(config: Any) -> bool:
    return validate_config(config)


def interactive_setup() -> None:
    """`hermes gateway setup` flow for the Octer platform."""
    from hermes_cli.setup import (  # type: ignore[import-not-found]
        get_env_value,
        print_header,
        print_info,
        print_success,
        print_warning,
        prompt,
        prompt_yes_no,
        save_env_value,
    )

    print_header("Octer.ai")
    existing = get_env_value("OCTER_API_KEY")
    if existing and not prompt_yes_no("Reconfigure Octer.ai?", False):
        return

    print_info("Get your API key at https://octer.ai/workspace")
    print_info("  → Me → Settings → API Keys → Create Key (starts with `evo_`)")
    print()
    key = prompt(
        "Octer API Key",
        default=existing or "",
        password=True,
    )
    if not key:
        print_warning("API Key is required — skipping Octer setup")
        return
    if not key.startswith("evo_"):
        print_warning("Octer API keys usually start with `evo_` — saving anyway.")
    save_env_value("OCTER_API_KEY", key.strip())

    print()
    print_info("🔒 Access control")
    if prompt_yes_no("Allow all incoming Octer requests?", True):
        save_env_value("OCTER_ALLOW_ALL_USERS", "true")
        save_env_value("OCTER_ALLOWED_USERS", "")
    else:
        save_env_value("OCTER_ALLOW_ALL_USERS", "false")
        allowed = prompt(
            "Allowed user IDs (comma-separated request IDs or peers)",
            default=get_env_value("OCTER_ALLOWED_USERS") or "",
        )
        save_env_value("OCTER_ALLOWED_USERS", allowed.replace(" ", ""))

    print()
    print_success("Octer.ai configuration saved to ~/.hermes/.env")
    print_info("Restart the gateway: hermes gateway restart")


def register(ctx: Any) -> None:
    """Plugin entry point — called by the Hermes plugin loader."""
    ctx.register_platform(
        name="octer",
        label="Octer",
        adapter_factory=lambda cfg: OcterAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["OCTER_API_KEY"],
        install_hint="pip install websockets",
        setup_fn=interactive_setup,
        allowed_users_env="OCTER_ALLOWED_USERS",
        allow_all_env="OCTER_ALLOW_ALL_USERS",
        max_message_length=MAX_TOOL_RESPONSE_BYTES,
        emoji="🌐",
        pii_safe=False,
        allow_update_command=False,
        platform_hint=(
            "You are responding via the Octer.ai cloud bridge. Each request "
            "is a single-turn RPC tool call — keep responses self-contained "
            "and avoid relying on prior conversation context. Markdown is "
            "supported in the result text. The reply is delivered back to "
            "the user as a tool_response over WebSocket."
        ),
    )
