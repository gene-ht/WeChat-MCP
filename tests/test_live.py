"""Live smoke tests that drive the real WeChat client.

They are skipped unless ``WECHAT_MCP_LIVE=1`` is set, and every write is
opt-in via ``WECHAT_MCP_LIVE_CHAT`` + ``WECHAT_MCP_LIVE_SEND=1`` so a plain
pytest run can never message anyone.

Run manually:

    WECHAT_MCP_LIVE=1 .venv/bin/pytest tests/test_live.py -m live -s
"""

from __future__ import annotations

import os

import pytest

from wechat_mcp import mcp_server

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("WECHAT_MCP_LIVE") != "1",
        reason="set WECHAT_MCP_LIVE=1 to exercise the real WeChat client",
    ),
]

CHAT = os.getenv("WECHAT_MCP_LIVE_CHAT", "文件传输助手")


def test_status():
    print(mcp_server.get_wechat_status())


def test_fetch_messages():
    result = mcp_server.fetch_messages_by_chat(CHAT, last_n=5)
    print(result)
    assert isinstance(result, list)


def test_draft_reply():
    if os.getenv("WECHAT_MCP_LIVE_DRAFT") != "1":
        pytest.skip("set WECHAT_MCP_LIVE_DRAFT=1 to write into the composer")
    print(mcp_server.draft_reply_to_chat(CHAT, "MCP 草稿测试"))


def test_send_reply():
    if os.getenv("WECHAT_MCP_LIVE_SEND") != "1":
        pytest.skip("set WECHAT_MCP_LIVE_SEND=1 to actually send a message")
    print(mcp_server.reply_to_messages_by_chat(CHAT, "MCP 发送测试"))
