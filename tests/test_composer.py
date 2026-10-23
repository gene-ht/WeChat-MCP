from __future__ import annotations

import pytest

from wechat_mcp import composer
from wechat_mcp.errors import InputNotEmpty


class FakeField:
    def __init__(self, value: str = "") -> None:
        self.value = value


class FakeUI:
    def __init__(self) -> None:
        self.returns = 0
        self.actions: list[tuple[object, str]] = []
        self.text_after_press: str | None = None


@pytest.fixture
def ui(monkeypatch):
    field = FakeField()
    fake = FakeUI()

    monkeypatch.setattr(composer, "ensure_frontmost", lambda **kwargs: None)
    monkeypatch.setattr(composer, "get_wechat_ax_app", lambda **kwargs: object())
    monkeypatch.setattr(composer, "is_wechat_frontmost", lambda: True)
    monkeypatch.setattr(composer, "find_input_field", lambda ax_app: field)
    monkeypatch.setattr(
        composer,
        "read_input_value",
        lambda input_field=None: (input_field or field).value,
    )
    monkeypatch.setattr(
        composer, "_write_input", lambda input_field, text: setattr(input_field, "value", text)
    )
    monkeypatch.setattr(
        composer,
        "AXUIElementPerformAction",
        lambda element, action: fake.actions.append((element, action)),
    )
    monkeypatch.setattr(composer, "press_return", lambda: setattr(fake, "returns", fake.returns + 1))
    monkeypatch.setattr(
        composer, "find_send_button", lambda ax_app, input_field=None: None
    )
    monkeypatch.setattr(composer, "_verify_sent", lambda text, timeout: True)
    return field, fake


def test_set_input_value_writes_and_reads_back(ui):
    field, _ = ui
    assert composer.set_input_value("你好") == "你好"
    assert field.value == "你好"


def test_set_input_value_refuses_to_clobber_a_draft(ui):
    field, _ = ui
    field.value = "我正在输入"
    with pytest.raises(InputNotEmpty):
        composer.set_input_value("你好")
    assert field.value == "我正在输入"


def test_set_input_value_can_replace_when_explicitly_allowed(ui):
    field, _ = ui
    field.value = "旧草稿"
    composer.set_input_value("你好", replace_existing=True)
    assert field.value == "你好"


def test_send_uses_return_when_no_send_button(ui):
    _, fake = ui
    result = composer.send_message("好的")

    assert result == {
        "sent": True,
        "verified": True,
        "method": "return",
        "text_length": 2,
    }
    assert fake.returns == 1


def test_send_uses_axpress_when_button_exists(ui, monkeypatch):
    _, fake = ui
    button = object()
    monkeypatch.setattr(composer, "find_send_button", lambda ax_app, input_field=None: button)

    result = composer.send_message("好的")
    assert result["method"] == "axpress"
    assert fake.returns == 0
    assert fake.actions[0][0] is button


def test_send_can_skip_verification(ui, monkeypatch):
    called = {"verify": False}

    def fail(text, timeout):
        called["verify"] = True
        return True

    monkeypatch.setattr(composer, "_verify_sent", fail)
    result = composer.send_message("好的", verify=False)

    assert result["verified"] is False
    assert called["verify"] is False


def test_verify_sent_polls_until_message_appears(monkeypatch):
    replies = iter([None, "旧的", "好的"])

    def fake_last_message_text():
        return next(replies)

    monkeypatch.setattr("wechat_mcp.messages.last_message_text", fake_last_message_text)
    assert composer._verify_sent("好的", timeout=2.0) is True


def test_verify_sent_gives_up(monkeypatch):
    monkeypatch.setattr("wechat_mcp.messages.last_message_text", lambda: "别的消息")
    assert composer._verify_sent("好的", timeout=0.3) is False
