from __future__ import annotations

import pytest
from ApplicationServices import kAXWindowsAttribute

from wechat_mcp import wechat_accessibility as wa
from wechat_mcp.errors import WeChatUIUnavailable


def test_ax_tree_stats_flags_window_only_tree(monkeypatch):
    window = object()
    monkeypatch.setattr(wa, "get_wechat_ax_app", lambda **kwargs: object())
    monkeypatch.setattr(
        wa,
        "ax_get",
        lambda element, attribute: [window] if attribute == kAXWindowsAttribute else [],
    )

    stats = wa.ax_tree_stats()
    assert stats["ok"] is False
    assert stats["window_count"] == 1
    assert stats["element_count"] == 1


def test_ensure_ax_tree_available_raises_with_diagnostics(monkeypatch):
    monkeypatch.setattr(
        wa,
        "ax_tree_stats",
        lambda ax_app=None: {"ok": False, "window_count": 1, "element_count": 6, "version": "4.1.4"},
    )

    with pytest.raises(WeChatUIUnavailable) as excinfo:
        wa.ensure_ax_tree_available(object())

    assert excinfo.value.context["wechat_version"] == "4.1.4"
    assert excinfo.value.code == "ax_tree_unavailable"
