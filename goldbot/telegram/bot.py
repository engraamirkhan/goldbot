"""python-telegram-bot adapter around the approval bus. Runs on the VPS as its own service; also serves as the
headless credential prompter for goldbot.ops.accounts. Requires `pip install -e ".[live]"`."""
from __future__ import annotations

import asyncio
import logging
import os

from goldbot.config import ROOT
from goldbot.ops import accounts
from goldbot.ops.deploy import DeployWatch
from goldbot.ops.health import HealthContext, HealthWatch, run_checks
from goldbot.telegram.approvals import Proposal
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.outbox import Outbox

log = logging.getLogger(__name__)

try:  # pragma: no cover
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
except ImportError:  # pragma: no cover
    Application = None


DEPLOY_EVERY_S = 600             # new version on main with CI passed -> offered to the owner (one click)
HEALTH_EVERY_S = 300              # health checks + alert dedupe (the Scheduler's finest grain is daily)
OUTCOME_TEXT = {"APPROVED": "✅ APPROVED", "REJECTED": "❌ REJECTED", "EXPIRED_UNAPPROVED": "⌛ EXPIRED"}


class TelegramBot:  # pragma: no cover - needs network + token
    """The Telegram service (`python -m goldbot.ops.run telegram`): sends the engines' proposals from the approval
    bus with Approve/Reject buttons, writes the owner's decisions back to the bus, edits each message with the outcome,
    delivers the staff agents' reports, and answers /status and /halt. Re-arming needs an authenticator code, so it is
    done on the dashboard."""

    def __init__(self, token: str, state_dir: str, owner_ids: set[int], poll_s: float = 2.0):
        if Application is None:
            raise RuntimeError("python-telegram-bot not installed; pip install -e '.[live]'")
        self.bus = ApprovalBus(state_dir)
        self.outbox = Outbox(state_dir, self.bus)
        self.owner_ids = owner_ids
        self.poll_s = poll_s
        self.state_dir = state_dir
        self.health = HealthWatch(state_dir)
        # one-click deploys (Linux servers; the systemd unit sets GOLDBOT_DEPLOY=1): goldbot/ops/deploy.py
        self.deploy = DeployWatch(state_dir, ROOT) if os.environ.get("GOLDBOT_DEPLOY") == "1" else None
        self.app = Application.builder().token(token).post_init(self._start_pump).build()
        self.app.add_handler(CallbackQueryHandler(self._on_button))
        for c in ("status", "halt", "rearm"):
            self.app.add_handler(CommandHandler(c, self._on_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self._pending_prompt: asyncio.Future | None = None
        accounts.register_prompter(self.prompt_sync)

    # ------------------------------------------------------------ outbound: proposals, outcomes, reports
    async def _start_pump(self, app: "Application") -> None:
        app.create_task(self._pump())

    async def _pump(self) -> None:
        n = 0
        while True:
            try:
                for p in self.outbox.unsent_proposals():
                    self.outbox.mark_sent(p.proposal_id, await self.send_proposal(p))
                for pid, outcome, msgs in self.outbox.finished():
                    for chat, mid in msgs:
                        try:
                            await self.app.bot.edit_message_reply_markup(chat, mid, reply_markup=None)
                            await self.app.bot.send_message(chat, f"{pid}: {OUTCOME_TEXT.get(outcome, outcome)}",
                                                            reply_parameters=ReplyParameters(message_id=mid))
                        except Exception as exc:
                            log.warning("could not update %s: %s", pid, exc)
                if n % max(int(30 / self.poll_s), 1) == 0:          # reports every ~30 s
                    for text in self.outbox.new_reports():
                        for uid in self.owner_ids:
                            await self.app.bot.send_message(uid, text)
            except Exception:
                log.exception("telegram pump")
            if n % max(int(HEALTH_EVERY_S / self.poll_s), 1) == 0:
                await self._health_pass()
            if self.deploy is not None:
                await self._deploy_pass(offer=n % max(int(DEPLOY_EVERY_S / self.poll_s), 1) == 0,
                                        report=n % max(int(30 / self.poll_s), 1) == 0)
            n += 1
            await asyncio.sleep(self.poll_s)

    async def _health_pass(self) -> None:
        """Run the health checks and tell the owner about checks that turned fail or recovered (dedupe in
        state/health_last.json, see goldbot.ops.health.HealthWatch)."""
        try:
            report = await asyncio.to_thread(lambda: run_checks(HealthContext.from_runtime(self.state_dir)))
            text = self.health.alert(report)
            if text:
                for uid in self.owner_ids:
                    await self.app.bot.send_message(uid, text)
            self.health.record(report)          # only after delivery: a failed send is retried next pass
        except Exception:
            log.exception("health pass")

    async def _deploy_pass(self, *, offer: bool, report: bool) -> None:
        """Offer a new version that passed CI ([Deploy] [Skip]); report results of the root deploy script."""
        assert self.deploy is not None
        try:
            if offer:
                new = await asyncio.to_thread(self.deploy.check)
                if new:
                    more = f"\n… and {new['n_commits'] - len(new['subjects'])} more" if new["n_commits"] > len(new["subjects"]) else ""
                    text = (f"🚀 New version ready ({new['sha'][:8]}, CI passed):\n"
                            + "\n".join(f"• {s}" for s in new["subjects"]) + more
                            + "\n\nDeploy restarts the services (open positions keep their stops; the engine reconciles).")
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Deploy", callback_data=f"dep:{new['sha']}"),
                                                InlineKeyboardButton("Skip", callback_data=f"skp:{new['sha']}")]])
                    for uid in self.owner_ids:
                        await self.app.bot.send_message(uid, text, reply_markup=kb)
            if report:
                for r in self.deploy.new_results():
                    icon = {"deployed": "✅", "rolled_back": "↩️", "failed": "🛑", "refused": "⛔"}.get(r.get("result", ""), "ℹ️")
                    text = f"{icon} deploy {r.get('result')} on {r.get('role')}: {str(r.get('to', ''))[:8]} — {r.get('detail', '')}"
                    for uid in self.owner_ids:
                        await self.app.bot.send_message(uid, text)
        except Exception:
            log.exception("deploy pass")

    async def send_proposal(self, p: Proposal) -> list[tuple[int, int]]:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"ok:{p.proposal_id}")],
                                   [InlineKeyboardButton(f"Reject: {r}", callback_data=f"no:{p.proposal_id}:{r}")
                                    for r in ("news", "cost", "discretion")],
                                   [InlineKeyboardButton("Reject: duplicate", callback_data=f"no:{p.proposal_id}:duplicate"),
                                    InlineKeyboardButton("Reject: other", callback_data=f"no:{p.proposal_id}:other")]])
        sent = []
        for uid in self.owner_ids:
            m = await self.app.bot.send_message(uid, p.text(), reply_markup=kb)
            sent.append((uid, m.message_id))
        return sent

    # ------------------------------------------------------------ inbound: buttons and commands
    async def _on_button(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
        q = update.callback_query
        uid = q.from_user.id
        if uid not in self.owner_ids:
            await q.answer("not allowed", show_alert=True)
            return
        parts = q.data.split(":")
        if parts[0] in ("dep", "skp") and self.deploy is not None:
            ok = self.deploy.approve(parts[1], by=uid) if parts[0] == "dep" else self.deploy.skip(parts[1])
            await q.answer("ok" if ok else "no longer on offer", show_alert=not ok)
            if ok:
                await q.edit_message_text(q.message.text + ("\n\n→ deploying within a minute; you will get the result"
                                                            if parts[0] == "dep" else "\n\n→ skipped"))
            return
        try:
            approve = parts[0] == "ok"
            self.bus.submit(parts[1], approve, None if approve else parts[2], by=f"telegram:{uid}")
            await q.answer("sent to the engine")
            await q.edit_message_text(q.message.text + "\n\n→ " + ("approve" if approve else f"reject ({parts[2]})")
                                      + ": the engine re-checks risk and acts")
        except (KeyError, ValueError) as exc:
            await q.answer(str(exc).strip("'\""), show_alert=True)

    async def _on_command(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
        uid = update.effective_user.id
        if uid not in self.owner_ids:
            log.warning("command from non-owner %s ignored", uid)
            return
        parts = update.message.text.split(maxsplit=1)
        cmd, arg = parts[0].split("@")[0], (parts[1] if len(parts) > 1 else None)
        if cmd == "/halt":
            self.bus.set_halt(True, by=f"telegram:{uid}", reason=arg)
            text = "HALTED: no new entries on any engine. Open positions keep their stops and exits. Re-arm on the dashboard."
        elif cmd == "/rearm":
            text = "Re-arming needs your authenticator code: use the dashboard (Overview → Re-arm)."
        else:
            c = self.bus.control()
            text = (f"halted: {'yes, by ' + str(c.by) + (' (' + c.reason + ')' if c.reason else '') if c.halted else 'no'}\n"
                    f"pending proposals: {len(self.bus.pending())}")
        await update.message.reply_text(text)

    # ------------------------------------------------------------ credential prompts in
    def prompt_sync(self, message: str, secret: bool) -> str:
        loop = self.app.loop if hasattr(self.app, "loop") else asyncio.get_event_loop()
        return asyncio.run_coroutine_threadsafe(self._prompt(message, secret), loop).result(timeout=600)

    async def _prompt(self, message: str, secret: bool) -> str:
        self._pending_prompt = asyncio.get_event_loop().create_future()
        note = " (your reply will be deleted from the chat immediately)" if secret else ""
        for uid in self.owner_ids:
            await self.app.bot.send_message(uid, f"goldbot needs: {message}{note}")
        return await self._pending_prompt

    async def _on_text(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
        if update.effective_user.id not in self.owner_ids or self._pending_prompt is None or self._pending_prompt.done():
            return
        self._pending_prompt.set_result(update.message.text.strip())
        try:
            await update.message.delete()  # never leave a secret in the chat history
        except Exception:
            pass
        await self.app.bot.send_message(update.effective_user.id, "stored in the keyring, thank you")

    def run(self) -> None:
        self.app.run_polling()
