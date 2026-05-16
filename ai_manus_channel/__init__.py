# -*- coding: utf-8 -*-
"""
AiManus WebSocket Channel for QwenPaw.

This custom channel allows ai-manus backend to interact with QwenPaw
via WebSocket instead of SSE. All events, approvals, and push messages
flow through a single bidirectional WebSocket connection per session.

To enable: add "ai_manus": {"enabled": true} to channels in config.json or
set the environment variable QWENPAW_ENABLED_CHANNELS=ai_manus,console.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from agentscope_runtime.engine.schemas.agent_schemas import (
    TextContent,
    ContentType,
    AgentRequest,
)

from qwenpaw.app.channels.base import BaseChannel
from qwenpaw.app.channels.schema import ChannelType
from qwenpaw.app.approvals import get_approval_service
from qwenpaw.security.tool_guard.approval import ApprovalDecision

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — shared between the channel instance (created by
# ChannelManager) and the WebSocket route (registered by register_app_routes).
# ---------------------------------------------------------------------------
_active_ws: Dict[str, WebSocket] = {}   # session_id → WebSocket
_channel_instance: Optional["AiManusChannel"] = None


def _serialize_event(event: Any) -> dict:
    """Serialize an AgentScope Event to a JSON-safe dict."""
    if hasattr(event, "model_dump"):
        return event.model_dump()
    elif hasattr(event, "dict"):
        return event.dict()
    elif hasattr(event, "json"):
        import json as _json
        return _json.loads(event.json())
    else:
        return {"text": str(event)}


# ===================================================================
# Channel class
# ===================================================================

class AiManusChannel(BaseChannel):
    """Bidirectional WebSocket channel for ai-manus integration.

    Replaces the SSE + polling approach with a single persistent
    WebSocket per session.  Tool-guard approvals are detected via
    background polling and delivered inline; approve/deny commands
    arrive on the same socket.
    """

    channel: ChannelType = "ai_manus"

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        process,
        enabled: bool = True,
        bot_prefix: str = "",
        on_reply_sent=None,
        show_tool_details: bool = True,
        filter_tool_messages: bool = False,
        filter_thinking: bool = False,
        **kwargs,
    ):
        super().__init__(
            process,
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
            filter_tool_messages=filter_tool_messages,
            filter_thinking=filter_thinking,
        )
        self.enabled = enabled
        self.bot_prefix = bot_prefix

        global _channel_instance
        _channel_instance = self

    # ------------------------------------------------------------------
    # Factories (required by BaseChannel)
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        process,
        config,
        on_reply_sent=None,
        show_tool_details=True,
        filter_tool_messages=False,
        filter_thinking=False,
        **kwargs,
    ) -> "AiManusChannel":
        return cls(
            process=process,
            enabled=getattr(config, "enabled", True),
            bot_prefix=getattr(config, "bot_prefix", "") or "",
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
            filter_tool_messages=filter_tool_messages,
            filter_thinking=filter_thinking,
            **kwargs,
        )

    @classmethod
    def from_env(cls, process, on_reply_sent=None) -> "AiManusChannel":
        return cls(process=process, on_reply_sent=on_reply_sent)

    # ------------------------------------------------------------------
    # build_agent_request_from_native  (required)
    # ------------------------------------------------------------------

    def build_agent_request_from_native(self, native_payload: Any) -> "AgentRequest":
        payload = native_payload if isinstance(native_payload, dict) else {}
        channel_id = payload.get("channel_id") or self.channel
        sender_id = payload.get("sender_id") or payload.get("user_id") or ""
        meta = payload.get("meta") or {}
        session_id = payload.get("session_id") or self.resolve_session_id(
            sender_id, meta
        )

        content_parts = payload.get("content_parts") or payload.get("content") or []
        if not content_parts and payload.get("text"):
            content_parts = [TextContent(type=ContentType.TEXT, text=payload["text"])]
        if not content_parts:
            content_parts = [TextContent(type=ContentType.TEXT, text=" ")]

        request = self.build_agent_request_from_user_content(
            channel_id=channel_id,
            sender_id=sender_id,
            session_id=session_id,
            content_parts=content_parts,
            channel_meta=meta,
        )
        request.channel_meta = meta
        return request

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.enabled:
            logger.debug("AiManusChannel disabled")
            return
        logger.info("AiManusChannel started (WebSocket endpoint: /api/ai-manus/ws/{session_id})")

    async def stop(self) -> None:
        # Close all active WS connections
        for sid, ws in list(_active_ws.items()):
            try:
                await ws.close(code=1001, reason="Channel shutting down")
            except Exception:
                pass
        _active_ws.clear()
        logger.info("AiManusChannel stopped")

    # ------------------------------------------------------------------
    # send  — push messages (cron jobs, scheduled reminders, etc.)
    # ------------------------------------------------------------------

    async def send(
        self,
        to_handle: str,
        text: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send a proactive text message to the connected ai-manus session."""
        session_id = (meta or {}).get("session_id", "") or to_handle
        ws = _active_ws.get(session_id)
        if ws is None:
            logger.debug(
                "AiManusChannel.send: no WS for session %s, dropping message",
                session_id,
            )
            return
        try:
            await ws.send_text(json.dumps({
                "ws_type": "push_message",
                "role": "assistant",
                "message": text,
            }, ensure_ascii=False))
        except Exception:
            logger.warning(
                "AiManusChannel.send: failed to send to session %s", session_id
            )


# ===================================================================
# WebSocket route registration
# ===================================================================

def register_app_routes(app):
    """Register the WebSocket endpoint on the FastAPI app.

    Called by QwenPaw at startup when the channel module is loaded.
    """

    @app.websocket("/api/ai-manus/ws/{session_id}")
    async def ai_manus_ws_endpoint(websocket: WebSocket, session_id: str):
        await websocket.accept()
        logger.info("ai-manus WS connected: session=%s", session_id)
        _active_ws[session_id] = websocket

        ch = _channel_instance
        svc = get_approval_service()

        # Tracks that have already been sent to avoid duplicates.
        sent_approval_ids: Set[str] = set()

        # Background task for the current chat request.
        process_task: Optional[asyncio.Task] = None
        # Background task for approval polling.
        poll_task: Optional[asyncio.Task] = None
        # Signal to stop the poll task.
        poll_stop = asyncio.Event()

        # ------------------------------------------------------------------
        # Approval poller: runs concurrently with the agent process loop.
        # When tool-guard triggers, the agent pauses, but this poller keeps
        # running and can push approval cards to ai-manus.
        # ------------------------------------------------------------------
        async def poll_approvals():
            while not poll_stop.is_set():
                try:
                    pending = await svc.get_pending_by_root_session(session_id)
                    for p in pending:
                        if p.request_id not in sent_approval_ids:
                            sent_approval_ids.add(p.request_id)
                            await websocket.send_text(json.dumps({
                                "ws_type": "approval",
                                "request_id": p.request_id,
                                "session_id": p.session_id,
                                "root_session_id": p.root_session_id,
                                "agent_id": p.agent_id,
                                "tool_name": p.tool_name,
                                "severity": p.severity,
                                "findings_count": p.findings_count,
                                "result_summary": p.result_summary,
                                "timeout_seconds": p.timeout_seconds,
                                "created_at": p.created_at,
                            }, default=str, ensure_ascii=False))
                except Exception:
                    logger.debug("Approval poll error for session %s", session_id, exc_info=True)
                try:
                    await asyncio.wait_for(poll_stop.wait(), timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    pass

        # ------------------------------------------------------------------
        # Agent process runner: iterates the process generator and sends
        # every event back over the WebSocket.
        # ------------------------------------------------------------------
        async def run_agent(request: AgentRequest):
            try:
                async for event in ch._process(request):
                    ev_dict = _serialize_event(event)
                    ev_dict["ws_type"] = "event"
                    await websocket.send_text(
                        json.dumps(ev_dict, default=str, ensure_ascii=False)
                    )
            except Exception as exc:
                logger.exception(
                    "Agent process error for session %s", session_id
                )
                try:
                    await websocket.send_text(json.dumps({
                        "ws_type": "error",
                        "error": str(exc),
                    }, ensure_ascii=False))
                except Exception:
                    pass

        # ------------------------------------------------------------------
        # Main WS message loop
        # ------------------------------------------------------------------
        poll_task = asyncio.create_task(poll_approvals())

        try:
            while True:
                raw = await websocket.receive_text()

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({
                        "ws_type": "error",
                        "error": f"Invalid JSON: {raw[:200]}",
                    }))
                    continue

                msg_type = msg.get("type", "")

                # -- ping / heartbeat --
                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"ws_type": "pong"}))
                    continue

                # -- chat request --
                if msg_type == "chat":
                    # Cancel any still-running previous process for this session.
                    if process_task and not process_task.done():
                        process_task.cancel()
                        try:
                            await process_task
                        except asyncio.CancelledError:
                            pass

                    if ch is None:
                        await websocket.send_text(json.dumps({
                            "ws_type": "error",
                            "error": "AiManusChannel not initialized yet",
                        }))
                        continue

                    # Clear stale approval tracking for the new request.
                    sent_approval_ids.clear()

                    native = {
                        "channel_id": "ai_manus",
                        "sender_id": msg.get("user_id", ""),
                        "session_id": session_id,
                        "text": msg.get("text", ""),
                        "content": msg.get("content", []),
                        "meta": {
                            "session_id": session_id,
                            "user_id": msg.get("user_id", ""),
                        },
                    }
                    request = ch.build_agent_request_from_native(native)
                    process_task = asyncio.create_task(run_agent(request))
                    continue

                # -- approve --
                if msg_type == "approve":
                    request_id = msg.get("request_id", "")
                    if request_id:
                        resolved = await svc.resolve_request(
                            request_id, ApprovalDecision.APPROVED
                        )
                        sent_approval_ids.discard(request_id)
                        await websocket.send_text(json.dumps({
                            "ws_type": "approved",
                            "request_id": request_id,
                            "tool_name": resolved.tool_name,
                        }, ensure_ascii=False))
                    continue

                # -- deny --
                if msg_type == "deny":
                    request_id = msg.get("request_id", "")
                    if request_id:
                        resolved = await svc.resolve_request(
                            request_id, ApprovalDecision.DENIED
                        )
                        sent_approval_ids.discard(request_id)
                        reason = msg.get("reason", "User denied")
                        await websocket.send_text(json.dumps({
                            "ws_type": "denied",
                            "request_id": request_id,
                            "tool_name": resolved.tool_name,
                            "reason": reason,
                        }, ensure_ascii=False))
                    continue

                # Unknown message type
                await websocket.send_text(json.dumps({
                    "ws_type": "error",
                    "error": f"Unknown message type: {msg_type}",
                }))

        except WebSocketDisconnect:
            logger.info("ai-manus WS disconnected: session=%s", session_id)
        except Exception:
            logger.exception("ai-manus WS error for session %s", session_id)
        finally:
            # Cleanup
            poll_stop.set()
            if poll_task and not poll_task.done():
                poll_task.cancel()
                try:
                    await poll_task
                except asyncio.CancelledError:
                    pass
            if process_task and not process_task.done():
                process_task.cancel()
                try:
                    await process_task
                except asyncio.CancelledError:
                    pass
            _active_ws.pop(session_id, None)
            logger.info("ai-manus WS cleaned up: session=%s", session_id)
