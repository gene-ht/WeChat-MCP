"""Per-channel outreach scripts.

The copy lives in a TOML file (``config/outreach.toml``) so sales can edit
wording without touching code:

    [defaults]
    company = "某某科技"

    [channels.douyin_job]
    label = "抖音-求职"
    greeting = "你好，我是{company}的招聘负责人，看到你在抖音上说想找工作。"
    followup = ["你好呀，我是{recruiter}。", "方便发一份简历吗？"]

    [channels.douyin_job.vars]
    recruiter = "小王"
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ScriptError(ValueError):
    """Raised for unknown channels or missing template variables."""


class _SafeVars(dict):
    def __missing__(self, key: str) -> str:  # pragma: no cover - error path
        raise ScriptError(f"Missing template variable {{{key}}}")


@dataclass
class ChannelScript:
    name: str
    label: str
    greeting: str
    followup: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class ChannelScripts:
    defaults: dict[str, str] = field(default_factory=dict)
    channels: dict[str, ChannelScript] = field(default_factory=dict)
    source: Path | None = None

    @classmethod
    def load(cls, path: Path | str | None) -> ChannelScripts:
        if path is None:
            return cls()
        file_path = Path(path)
        if not file_path.exists():
            return cls()
        with file_path.open("rb") as handle:
            raw = tomllib.load(handle)

        defaults = {str(k): str(v) for k, v in (raw.get("defaults") or {}).items()}
        channels: dict[str, ChannelScript] = {}
        for name, config in (raw.get("channels") or {}).items():
            if not isinstance(config, dict) or "greeting" not in config:
                raise ScriptError(f"Channel {name!r} needs at least a greeting")
            channels[name] = ChannelScript(
                name=name,
                label=str(config.get("label", name)),
                greeting=str(config["greeting"]),
                followup=[str(item) for item in config.get("followup", [])],
                variables={
                    str(k): str(v) for k, v in (config.get("vars") or {}).items()
                },
            )
        return cls(defaults=defaults, channels=channels, source=file_path)

    # ------------------------------------------------------------------ api
    def get(self, channel: str) -> ChannelScript:
        if channel not in self.channels:
            available = ", ".join(sorted(self.channels))
            if not available:
                raise ScriptError(
                    f"No outreach scripts found at {self.source or 'the config path'}. "
                    "Copy config/outreach.example.toml to config/outreach.toml first."
                )
            raise ScriptError(f"Unknown channel {channel!r}; available: {available}")
        return self.channels[channel]

    def names(self) -> list[str]:
        return sorted(self.channels)

    def _render(self, template: str, channel: str, extra: dict[str, Any]) -> str:
        script = self.get(channel)
        values: dict[str, Any] = {}
        values.update(self.defaults)
        values.update(script.variables)
        values.update({"channel": channel, "channel_label": script.label})
        values.update({key: value for key, value in extra.items() if value is not None})
        try:
            return template.format_map(_SafeVars(values))
        except (KeyError, IndexError) as exc:  # pragma: no cover - defensive
            raise ScriptError(f"Bad template for {channel!r}: {exc}") from exc

    def greeting_for(self, channel: str, **extra: Any) -> str:
        return self._render(self.get(channel).greeting, channel, extra)

    def followup_for(self, channel: str, **extra: Any) -> list[str]:
        return [
            self._render(template, channel, extra)
            for template in self.get(channel).followup
        ]
