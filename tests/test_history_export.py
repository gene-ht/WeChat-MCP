import json

from wechat_mcp.history_export import _norm, render_document


def test_norm_strips_whitespace():
    assert _norm("  a\n b\tc ") == "abc"


def test_render_document_groups_and_orders(tmp_path):
    payload = {
        "chat": "测试会话",
        "date": "2026-10-23",
        "collected_at": "2026-10-23T18:00:00+08:00",
        "complete": True,
        "boundary": {"type": "top"},
        "messages": [
            {"sender": "OTHER", "text": "🦄海淀小区保安\n工资 4000", "at": None},
            {"sender": "OTHER", "text": "🦄朝阳写字楼保安\n工资 5000", "at": None},
            {"sender": "OTHER", "text": "10月23日在招项目更新完毕", "at": None},
        ],
        "cards": [
            {
                "preview": "Chat History…",
                "title": "群聊的聊天记录",
                "texts": ["🦄丰台小区保安\n工资 4500"],
            }
        ],
    }
    path = tmp_path / "2026-10-23.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out = render_document(path)
    text = out.read_text(encoding="utf-8")

    assert "岗位数：3" in text
    assert text.index("海淀") < text.index("朝阳") < text.index("丰台")
    assert "◆ 通知" in text
    assert "测试会话" in text
