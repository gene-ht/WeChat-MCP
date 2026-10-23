"""Outreach workflow: import leads, send the channel greeting, follow up.

The service never talks to WeChat directly. It renders copy from
:mod:`wechat_mcp.outreach.scripts`, records state in
:mod:`wechat_mcp.outreach.store`, and delegates the actual UI work to an
adapter — which makes the whole flow testable with a fake adapter and keeps
dry-run as the default.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .models import Lead, LeadStage
from .scripts import ChannelScripts, ScriptError
from .store import LeadStore


class WeChatOutreachAdapter:
    """Real adapter; every call still goes through the policy gate."""

    def send_friend_request(self, phone: str, greeting: str) -> dict[str, Any]:
        from ..contacts import add_contact_by_wechat_id
        from ..services import get_service

        get_service().ensure_ui_writable()
        return add_contact_by_wechat_id(
            wechat_id=phone, friending_msg=greeting
        )

    def send_followup(self, chat_name: str, messages: list[str]) -> list[dict[str, Any]]:
        from ..services import get_service

        service = get_service()
        results: list[dict[str, Any]] = []
        for text in messages:
            results.append(service.send_reply(chat_name, text))
        return results


@dataclass
class OutreachService:
    store: LeadStore
    scripts: ChannelScripts
    wechat: Any = None
    dry_run: bool = True
    policy: Any = None

    def __post_init__(self) -> None:
        if self.wechat is None:
            self.wechat = WeChatOutreachAdapter()

    # ---------------------------------------------------------------- leads
    def import_leads(
        self, rows: Iterable[dict[str, Any]], default_channel: str = ""
    ) -> dict[str, Any]:
        created = 0
        updated = 0
        errors: list[str] = []

        for index, row in enumerate(rows):
            phone = str(
                row.get("phone") or row.get("identifier") or row.get("wechat_id") or ""
            ).strip()
            channel = str(row.get("channel") or default_channel or "").strip()
            if not phone:
                errors.append(f"row {index}: missing phone")
                continue
            if not channel:
                errors.append(f"row {index} ({phone}): missing channel")
                continue
            try:
                self.scripts.get(channel)
                existed = self.store.exists(phone)
                self.store.upsert(
                    phone,
                    channel,
                    name=row.get("name"),
                    wechat_name=row.get("wechat_name"),
                    user_state=row.get("user_state") or row.get("state"),
                    note=row.get("note"),
                )
                created += 0 if existed else 1
                updated += 1 if existed else 0
            except (ScriptError, ValueError) as exc:
                errors.append(f"row {index} ({phone}): {exc}")

        return {
            "ok": not errors,
            "action": "import_leads",
            "created": created,
            "updated": updated,
            "errors": errors,
            "total": len(self.store.list(limit=1000)),
        }

    def list_leads(
        self,
        *,
        stage: str | None = None,
        channel: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        parsed = LeadStage(stage) if stage else None
        leads = self.store.list(stage=parsed, channel=channel, limit=limit)
        return {
            "ok": True,
            "count": len(leads),
            "leads": [lead.to_dict() for lead in leads],
        }

    def get_lead(self, phone: str) -> dict[str, Any]:
        lead = self.store.get(phone)
        return {"ok": True, "lead": lead.to_dict()}

    # ------------------------------------------------------------- outreach
    def send_friend_request(
        self,
        phone: str,
        *,
        channel: str | None = None,
        name: str | None = None,
        wechat_name: str | None = None,
        user_state: str | None = None,
    ) -> dict[str, Any]:
        lead = self._ensure_lead(phone, channel, name, user_state, wechat_name)
        if lead.stage is not LeadStage.NEW:
            return {
                "ok": False,
                "reason": f"lead is already at stage {lead.stage.value}",
                "lead": lead.to_dict(),
            }

        if not self.dry_run and self.policy is not None:
            decision = self.policy.check_friend_request()
            if not decision.allowed:
                return {
                    "ok": False,
                    "blocked_reason": decision.reason,
                    "detail": decision.detail,
                    "lead": lead.to_dict(),
                }

        greeting = self.scripts.greeting_for(
            lead.channel,
            phone=lead.phone,
            identifier=lead.phone,
            name=lead.name,
            user_state=lead.user_state,
        )

        if self.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "action": "send_friend_request",
                "lead": lead.to_dict(),
                "would_send": greeting,
            }

        result = self.wechat.send_friend_request(lead.phone, greeting)
        if isinstance(result, dict) and result.get("error"):
            detail = str(result["error"])[:500]
            self.store.update_stage(
                lead.phone, LeadStage.FAILED, note=detail
            )
            return {
                "ok": False,
                "error": result["error"],
                "lead": self.store.get(lead.phone).to_dict(),
            }

        if self.policy is not None:
            self.policy.state.record_friend_request()
        lead = self.store.update_stage(lead.phone, LeadStage.REQUEST_SENT)
        return {
            "ok": True,
            "action": "send_friend_request",
            "sent": True,
            "greeting": greeting,
            "lead": lead.to_dict(),
        }

    def send_followup(self, phone: str) -> dict[str, Any]:
        lead = self.store.get(phone)
        if lead.stage is not LeadStage.ACCEPTED:
            return {
                "ok": False,
                "reason": (
                    "follow-up requires stage 'accepted' "
                    f"(current: {lead.stage.value})"
                ),
                "lead": lead.to_dict(),
            }

        messages = self.scripts.followup_for(
            lead.channel,
            phone=lead.phone,
            identifier=lead.phone,
            name=lead.name,
            user_state=lead.user_state,
        )
        if not messages:
            return {"ok": False, "reason": f"no follow-up copy for {lead.channel!r}"}

        chat_name = lead.wechat_name or lead.name or lead.phone
        if self.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "action": "send_followup",
                "chat_name": chat_name,
                "would_send": messages,
                "lead": lead.to_dict(),
            }

        results = self.wechat.send_followup(chat_name, messages)
        blocked = [r for r in results if isinstance(r, dict) and not r.get("sent")]
        if blocked:
            return {
                "ok": False,
                "error": "one or more follow-up messages were not sent",
                "results": results,
                "lead": lead.to_dict(),
            }

        lead = self.store.update_stage(phone, LeadStage.GREETED)
        return {
            "ok": True,
            "action": "send_followup",
            "chat_name": chat_name,
            "sent": len(messages),
            "lead": lead.to_dict(),
        }

    def mark_stage(
        self, phone: str, stage: str, note: str | None = None
    ) -> dict[str, Any]:
        target = LeadStage(stage)
        lead = self.store.update_stage(phone, target, note=note)
        return {"ok": True, "action": "mark_stage", "lead": lead.to_dict()}

    # --------------------------------------------------------------- helpers
    def _ensure_lead(
        self,
        phone: str,
        channel: str | None,
        name: str | None,
        user_state: str | None,
        wechat_name: str | None = None,
    ) -> Lead:
        if self.store.exists(phone):
            lead = self.store.get(phone)
            if channel and channel != lead.channel:
                self.scripts.get(channel)
                lead = self.store.update_fields(phone, channel=channel)
            updates = {
                key: value
                for key, value in {
                    "name": name,
                    "wechat_name": wechat_name,
                    "user_state": user_state,
                }.items()
                if value
            }
            if updates:
                lead = self.store.update_fields(phone, **updates)
            return lead

        if not channel:
            raise ValueError(
                f"Lead {phone!r} does not exist yet; channel is required"
            )
        self.scripts.get(channel)
        return self.store.upsert(
            phone,
            channel,
            name=name,
            wechat_name=wechat_name,
            user_state=user_state,
        )
