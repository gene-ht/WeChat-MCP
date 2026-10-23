"""MCP tool surface for WeChat on macOS.

Tools are intentionally thin: every entry point resolves the runtime service,
calls one use case and converts typed errors into structured payloads. Policy,
state and auditing live in :mod:`wechat_mcp.services` / :mod:`wechat_mcp.policy`.
"""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import control
from .activity import HumanActivityMonitor
from .contacts import add_contact_by_wechat_id as ax_add_contact_by_wechat_id
from .errors import ChatNotFound, PolicyBlocked, WeChatMCPError
from .logging_config import logger
from .models import try_parse_mode
from .moments import publish_moment_without_media as ax_publish_moment
from .outreach.scripts import ChannelScripts
from .outreach.service import OutreachService
from .outreach.store import LeadStore
from .services import get_service

mcp = FastMCP("WeChat Helper MCP Server")

_outreach_service: OutreachService | None = None


def get_outreach_service() -> OutreachService:
    """Build the outreach service from the current settings on first use."""
    global _outreach_service
    if _outreach_service is None:
        settings = get_service().settings
        _outreach_service = OutreachService(
            store=LeadStore(settings.resolved_leads_csv),
            scripts=ChannelScripts.load(settings.resolved_outreach_config),
            dry_run=settings.outreach_dry_run,
            policy=get_service().policy,
        )
    return _outreach_service


def configure_outreach_service(service: OutreachService | None) -> None:
    """Replace the outreach service (tests)."""
    global _outreach_service
    _outreach_service = service


def _tool_error(exc: Exception, chat_name: str | None = None) -> dict[str, Any]:
    if isinstance(exc, ChatNotFound):
        payload = exc.to_dict()
        payload.setdefault("chat_name", chat_name)
        return payload
    if isinstance(exc, PolicyBlocked):
        payload = exc.to_dict()
        payload.update({"ok": False, "blocked_reason": exc.reason})
        if chat_name is not None:
            payload["chat_name"] = chat_name
        return payload
    if isinstance(exc, WeChatMCPError):
        return exc.to_dict()
    return {"error": str(exc), "chat_name": chat_name}


@mcp.tool()
def get_wechat_status() -> dict[str, Any]:
    """
    Report the current runtime state without touching WeChat.

    Returns the mode (active / draft_only / paused / takeover), whether WeChat
    is running, the open chat title, the chat allow-list, how many messages
    were sent in the last hour and how many drafts are pending.
    """
    try:
        return get_service().status()
    except Exception as exc:
        logger.exception("Error in get_wechat_status: %s", exc)
        return _tool_error(exc)


@mcp.tool()
def set_wechat_mode(mode: str) -> dict[str, Any]:
    """
    Switch the server between autonomy levels.

    - active: read, draft and send.
    - draft_only: read and write drafts, never send.
    - paused: read only; the agent stays idle.
    - takeover: the human owns WeChat; the server performs no UI actions.

    Use this, not prose, when the user says "take over", "resume", "pause" or
    "stop auto-replying".
    """
    try:
        parsed = try_parse_mode(mode)
        if parsed is None:
            return {
                "ok": False,
                "error": f"Unknown mode {mode!r}",
                "allowed": ["active", "draft_only", "paused", "takeover"],
            }
        return get_service().set_mode(parsed.value)
    except Exception as exc:
        logger.exception("Error in set_wechat_mode: %s", exc)
        return _tool_error(exc)


@mcp.tool()
def list_wechat_chats() -> list[str]:
    """List chat names currently visible in WeChat's session list."""
    try:
        return get_service().list_chats()
    except Exception as exc:
        logger.exception("Error in list_wechat_chats: %s", exc)
        return []


@mcp.tool()
def fetch_messages_by_chat(
    chat_name: str,
    last_n: int = 50,
    sender_names: bool = False,
) -> list[dict[str, Any]]:
    """
    Fetch recent messages for a specific chat (contact or group).

    This will:
    - Look for the chat in the left sidebar session list
    - If found, click it to open the chat
    - If not found, search for the chat via the search box
    - Once the chat is open, retrieve recent messages from that chat
    """
    try:
        logger.info("Tool fetch_messages_by_chat called for chat=%s", chat_name)
        messages = get_service().fetch_messages(
            chat_name, last_n=last_n, sender_names=sender_names
        )
        result = [msg.to_dict() for msg in messages]
        logger.info("Returning %d messages for chat=%s", len(result), chat_name)
        return result
    except ChatNotFound as exc:
        payload = exc.to_dict()
        payload["tool"] = "fetch_messages_by_chat"
        return [payload]
    except Exception as exc:
        logger.exception(
            "Error in fetch_messages_by_chat for chat=%s: %s", chat_name, exc
        )
        return [{"error": str(exc), "chat_name": chat_name}]


@mcp.tool()
def fetch_messages_since(
    chat_name: str,
    since: str,
    max_messages: int = 500,
    sender_names: bool = False,
) -> dict[str, Any]:
    """
    Collect every message in a chat sent after a given date.

    ``since`` accepts ``YYYY-MM-DD`` or an ISO datetime (local time). The tool
    scrolls back through history until it reaches a timestamp separator older
    than ``since``, then returns the messages after it, with ``at`` filled in
    from WeChat's separator rows (null when a message has no nearby marker).

    ``complete: false`` means the top of the loaded history was reached before
    the requested date, so the result may be truncated — ask for an earlier
    start or fetch the chat manually.
    """
    try:
        parsed = datetime.fromisoformat(since)
    except ValueError:
        return {
            "ok": False,
            "error": f"Invalid date {since!r}; use YYYY-MM-DD or an ISO datetime.",
            "chat_name": chat_name,
        }
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)

    try:
        return get_service().fetch_since(
            chat_name,
            parsed,
            max_messages=max_messages,
            sender_names=sender_names,
        )
    except Exception as exc:
        logger.exception("Error in fetch_messages_since for chat=%s: %s", chat_name, exc)
        return _tool_error(exc, chat_name)


@mcp.tool()
def reply_to_messages_by_chat(
    chat_name: str,
    reply_message: str | None = None,
    replace_existing: bool = False,
) -> dict[str, Any]:
    """
    Send a reply to a chat (contact or group), subject to runtime policy.

    Typical flow:
    - Call fetch_messages_by_chat first to inspect conversation history.
    - Compose the reply.
    - Call this tool with the reply text.

    The send is refused (with ``blocked_reason``) when the server is in
    draft_only / paused / takeover mode, when the chat is outside the allow-list,
    when the rate limit is hit, or when the exact same message was just sent.
    If so, do not retry blindly: use draft_reply_to_chat or ask the user.

    If reply_message is None or empty, no message is sent; the tool still
    ensures the chat is open.

    ``replace_existing`` allows overwriting text already in the composer. Keep
    it False unless you know the draft is yours; otherwise a human's draft
    would be destroyed.
    """
    logger.info(
        "Tool reply_to_messages_by_chat called for chat=%s (has_reply=%s)",
        chat_name,
        bool(reply_message),
    )
    try:
        service = get_service()
        if reply_message is None or not reply_message.strip():
            service.open_chat(chat_name)
            return {"chat_name": chat_name, "reply_message": reply_message, "sent": False}

        return service.send_reply(
            chat_name, reply_message, replace_existing=replace_existing
        )
    except Exception as exc:
        logger.exception(
            "Error in reply_to_messages_by_chat for chat=%s: %s", chat_name, exc
        )
        return _tool_error(exc, chat_name)


@mcp.tool()
def get_visible_messages(limit: int = 50) -> dict[str, Any]:
    """
    Read the messages currently visible in the open chat.

    Unlike fetch_messages_by_chat this never scrolls and never activates WeChat,
    so it cannot disturb whatever the human is looking at. Use it after the
    person scrolls to the part they want read.
    """
    try:
        return get_service().visible_messages(limit=limit)
    except Exception as exc:
        logger.exception("Error in get_visible_messages: %s", exc)
        return _tool_error(exc)


@mcp.tool()
def draft_reply_to_chat(chat_name: str, reply_message: str) -> dict[str, Any]:
    """
    Write a reply into the chat composer without sending it.

    This is the safe alternative when auto-send is blocked or when the user
    wants to review the wording. The text stays in WeChat's input box for a
    human to edit and send; the server records it as a pending draft.

    Refuses to overwrite text that is already in the composer, so it will not
    clobber a draft the human is typing.
    """
    logger.info(
        "Tool draft_reply_to_chat called for chat=%s (length=%d)",
        chat_name,
        len(reply_message or ""),
    )
    try:
        return get_service().draft_reply(chat_name, reply_message)
    except Exception as exc:
        logger.exception(
            "Error in draft_reply_to_chat for chat=%s: %s", chat_name, exc
        )
        return _tool_error(exc, chat_name)


@mcp.tool()
def import_leads(
    leads: list[dict[str, Any]] | None = None,
    csv_path: str | None = None,
    default_channel: str = "",
) -> dict[str, Any]:
    """
    Import outreach leads into the local table (SQLite).

    Each lead needs a ``phone``; ``channel`` selects the script profile, and
    ``name`` / ``user_state`` are optional. Leads can be passed inline or read
    from a CSV with the same column names.

    Importing never contacts anyone: it only records rows as ``new``.
    """
    try:
        rows: list[dict[str, Any]] = list(leads or [])
        if csv_path:
            path = Path(csv_path).expanduser()
            if not path.exists():
                return {"ok": False, "error": f"CSV not found: {path}"}
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows.extend(dict(row) for row in csv.DictReader(handle))
        if not rows:
            return {"ok": False, "error": "No leads supplied (use leads or csv_path)"}
        return get_outreach_service().import_leads(rows, default_channel)
    except Exception as exc:
        logger.exception("Error in import_leads: %s", exc)
        return _tool_error(exc)


@mcp.tool()
def list_leads(
    stage: str | None = None,
    channel: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    List outreach leads, optionally filtered by stage or channel.

    Stages: new, request_sent, accepted, greeted, replied, failed, skipped.
    """
    try:
        return get_outreach_service().list_leads(
            stage=stage, channel=channel, limit=limit
        )
    except Exception as exc:
        logger.exception("Error in list_leads: %s", exc)
        return _tool_error(exc)


@mcp.tool()
def send_friend_request(
    phone: str,
    channel: str | None = None,
    name: str | None = None,
    user_state: str | None = None,
    wechat_name: str | None = None,
) -> dict[str, Any]:
    """
    Send the channel-specific friend request for one lead.

    The greeting comes from config/outreach.toml for the lead's channel. The
    lead moves to ``request_sent`` only after WeChat reports success. When the
    server runs in dry-run mode (default) nothing is sent and ``would_send``
    shows the exact text.

    Set WECHAT_MCP_OUTREACH_DRY_RUN=0 to send for real.
    """
    try:
        return get_outreach_service().send_friend_request(
            phone,
            channel=channel,
            name=name,
            user_state=user_state,
            wechat_name=wechat_name,
        )
    except Exception as exc:
        logger.exception("Error in send_friend_request for %s: %s", phone, exc)
        return _tool_error(exc)


@mcp.tool()
def send_lead_followup(phone: str) -> dict[str, Any]:
    """
    Send the follow-up messages for a lead whose friend request was accepted.

    Requires stage ``accepted``; after sending, the lead moves to ``greeted``.
    Mark acceptance with mark_lead_stage first when the human confirms it.
    """
    try:
        return get_outreach_service().send_followup(phone)
    except Exception as exc:
        logger.exception("Error in send_lead_followup for %s: %s", phone, exc)
        return _tool_error(exc)


@mcp.tool()
def mark_lead_stage(
    phone: str, stage: str, note: str | None = None
) -> dict[str, Any]:
    """
    Move a lead to a new stage (accepted / replied / failed / ...).

    Transitions are validated: new -> request_sent -> accepted -> greeted ->
    replied. Use this when a human confirms a friend request was accepted or a
    reply arrived.
    """
    try:
        return get_outreach_service().mark_stage(phone, stage, note)
    except Exception as exc:
        logger.exception("Error in mark_lead_stage for %s: %s", phone, exc)
        return _tool_error(exc)


@mcp.tool()
def add_contact_by_wechat_id(
    wechat_id: str,
    friending_msg: str | None = None,
    remark: str | None = None,
    tags: str | None = None,
    privacy: str | None = None,
    hide_my_posts: bool = False,
    hide_their_posts: bool = False,
) -> dict[str, Any]:
    """
    Add a new contact using a WeChat ID.

    This tool automates the WeChat flow:
    - Type the given WeChat ID into the global search box.
    - Click the "Search WeChat ID" card under the "More" section.
    - In the "Add Contacts" window, click "Add to Contacts".
    - In the "Send Friend Request" window, optionally customize the
      friending message, remark, and privacy options, then confirm.

    The `privacy` argument controls the "Privacy" section of the
    friend-request window:
    - "all" (default) selects "Chats, Moments, WeRun, etc." and applies
      the `hide_my_posts` / `hide_their_posts` flags.
    - "chats_only" selects "Chats Only" and ignores the hide flags.
    """
    logger.info(
        "Tool add_contact_by_wechat_id called for ID=%s (privacy=%r, "
        "hide_my_posts=%s, hide_their_posts=%s)",
        wechat_id,
        privacy,
        hide_my_posts,
        hide_their_posts,
    )
    try:
        get_service().ensure_ui_writable()
        return ax_add_contact_by_wechat_id(
            wechat_id=wechat_id,
            friending_msg=friending_msg,
            remark=remark,
            tags=tags,
            privacy=privacy,
            hide_my_posts=hide_my_posts,
            hide_their_posts=hide_their_posts,
        )
    except Exception as exc:
        logger.exception(
            "Error in add_contact_by_wechat_id for ID=%s: %s", wechat_id, exc
        )
        payload = _tool_error(exc)
        payload.setdefault("wechat_id", wechat_id)
        return payload


@mcp.tool()
def publish_moment_without_media(
    content: str,
    publish: bool = True,
) -> dict[str, Any]:
    """
    Publish a Moments post containing only text (no media).

    This will:
    - Open the main WeChat window and click the "Moments" button.
    - Long-press the "Post" button in the Moments window to open the
      composer sheet.
    - Fill the text entry area with the provided content.
    - If `publish` is True (default), click the "Post" button in the
      sheet to publish the moment; if False, leave the composer open
      without sending.
    """
    logger.info(
        "Tool publish_moment_without_media called (content_length=%d, publish=%s)",
        len(content) if isinstance(content, str) else -1,
        publish,
    )
    try:
        get_service().ensure_ui_writable()
        return ax_publish_moment(content=content, publish=publish)
    except Exception as exc:
        logger.exception("Error in publish_moment_without_media: %s", exc)
        payload = _tool_error(exc)
        payload.setdefault("content", content)
        return payload


def main() -> None:
    """Entry point for the WeChat MCP server."""
    parser = argparse.ArgumentParser(description="WeChat Helper MCP Server")
    parser.add_argument(
        "--mcp-debug",
        action="store_true",
        help="Enable detailed MCP protocol debugging logs",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol to use (default: stdio)",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Ask a running server to shut down and exit",
    )
    parser.add_argument(
        "--set-mode",
        choices=["active", "draft_only", "paused", "takeover"],
        default=None,
        help="Update the persisted runtime mode and exit",
    )

    args = parser.parse_args()

    if args.mcp_debug:
        logging.getLogger("mcp").setLevel(logging.DEBUG)
        logging.getLogger("anyio").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        debug_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - "
            "%(funcName)s:%(lineno)d - %(message)s"
        )
        for handler in logging.getLogger().handlers:
            handler.setFormatter(debug_formatter)

    service = get_service()

    if args.stop:
        response = control.request(
            control.socket_path(service.settings.state_dir), {"cmd": "shutdown"}
        )
        if response and response.get("ok"):
            print("Stopped the running server")
        else:
            print("No running server found")
        return

    if args.set_mode:
        # Prefer the running server so the switch takes effect immediately;
        # fall back to persisting it for the next start.
        response = control.request(
            control.socket_path(service.settings.state_dir),
            {"cmd": "set_mode", "mode": args.set_mode},
        )
        if response and response.get("ok"):
            print(f"Mode set to {response['status']['mode']} (running server)")
            return
        status = service.set_mode(args.set_mode)
        print(
            f"Mode persisted as {status['mode']}; "
            "it applies the next time the server starts"
        )
        return

    logger.info("Starting WeChat Helper MCP Server")
    logger.info("Transport: %s", args.transport)
    logger.info("MCP Debug mode: %s", args.mcp_debug)
    logger.info("Runtime mode: %s", service.state.mode.value)

    monitor = HumanActivityMonitor(service.presence)
    monitor.start()

    control_server = control.ControlServer(
        service, control.socket_path(service.settings.state_dir)
    )
    try:
        control_server.start()
    except Exception as exc:
        logger.warning("Control socket unavailable: %s", exc)

    try:
        if args.transport == "stdio":
            mcp.run()
        elif args.transport == "streamable-http":
            mcp.run(transport="streamable-http")
        elif args.transport == "sse":
            mcp.run(transport="sse")
    finally:
        control_server.stop()
        monitor.stop()


if __name__ == "__main__":
    main()
