"""python-telegram-bot adapter around the approval bus. Runs on the VPS as its own service; also serves as the
headless credential prompter for goldbot.ops.accounts. Requires `pip install -e ".[live]"`."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Callable

import pandas as pd

from goldbot.config import ROOT, TelegramSettings
from goldbot.ops import accounts
from goldbot.ops.account_view import proposal_budget_from_state
from goldbot.ops.deadman import DeadMan
from goldbot.ops.deploy import DeployWatch
from goldbot.ops.health import HealthContext, HealthWatch, run_checks
from goldbot.telegram import automode
from goldbot.telegram.approvals import Proposal, decision_channel
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.digest import DigestSchedule, digest_from_runtime
from goldbot.telegram.outbox import Outbox, ReportRetry, SendGuard

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
AUTOMODE_EVERY_S = 900           # auto-mode evidence (A10): offered once per mode epoch when it holds
DIGEST_EVERY_S = 60              # daily owner digest due check (telegram.digest_at; once a day, late <= 6 h)
HEALTH_EVERY_S = 60               # health checks + alert dedupe: a silent service is reported within ~6 min (S5)
AUDIT_DRAIN_S = 10.0              # at shutdown, wait this long at most for audit rows still being written
OUTCOME_TEXT = {"APPROVED": "✅ APPROVED", "REJECTED": "❌ REJECTED", "EXPIRED_UNAPPROVED": "⌛ EXPIRED"}


def outcome_text(outcome: str) -> str:
    if outcome.startswith("GATE_REFUSED"):
        return "🛑 NOT PLACED: approved, but the risk check refused it at approval (" + outcome.partition(": ")[2] + ")"
    return OUTCOME_TEXT.get(outcome, outcome)


class OffLoopAudit:
    """Who acted on Telegram (decision, halt, deploy tap), in the VPS audit log the dashboard and /mode write
    (automode.audit_to: state/aux.db), never in approvals/, control.json or approved.json (A15). Review M1: aux.db can
    be locked for seconds, so `write` runs on a worker thread and returns at once. Callers write as soon as the action
    stands, before answering the owner, so an answer that fails (a timeout, "query is too old") never loses the row
    (A15 review). The row's `ts` is the moment of the action, not of the write. One audit store, opened on the first
    write in the worker thread, never on the event loop. A locked database is retried once; a write that still fails is
    logged (event, channel, proposal or sha; never who) and never undoes or hides the action, which already stands.
    The service drains pending rows at shutdown, for at most AUDIT_DRAIN_S."""

    def __init__(self, state_dir: str, opener: Callable[[str], Callable[..., None]] | None = None):
        self.state_dir = state_dir
        self._opener = opener
        self._audit: Callable[..., None] | None = None
        self._lock = threading.Lock()
        self._tasks: set[asyncio.Task[None]] = set()       # held until done: an unreferenced task can be collected

    RETRY_S = 0.5                               # pause before the one retry of a locked aux.db

    def write_now(self, event: str, **kw: Any) -> None:
        """Blocking: call it only off the event loop (a worker thread)."""
        for attempt in (1, 2):
            try:
                with self._lock:
                    if self._audit is None:
                        self._audit = (self._opener or automode.audit_to)(self.state_dir)
                    audit = self._audit
                audit(event, **kw)
                return
            except sqlite3.OperationalError:    # "database is locked" past the busy timeout: once more
                if attempt == 1:
                    time.sleep(self.RETRY_S)
                    continue
                self._failed(event, kw, "OperationalError")
            except Exception as exc:            # noqa: BLE001 - the action already stands
                self._failed(event, kw, type(exc).__name__)
            return

    @staticmethod
    def _failed(event: str, kw: dict[str, Any], why: str) -> None:
        ref = kw.get("proposal") or kw.get("sha") or "-"
        log.warning("audit of %s via %s (%s) failed: %s", event, decision_channel(str(kw.get("by", "-"))), ref, why)

    def write(self, event: str, uid: int, **kw: Any) -> None:
        """From a handler on the event loop: the row is written on a worker thread; this returns at once. The row's
        time is taken now, when the action stands, not when the worker gets the database."""
        task = asyncio.get_running_loop().create_task(
            asyncio.to_thread(self.write_now, event, ts=time.time(), by=f"telegram:{uid}", telegram_id=uid, **kw))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self, timeout: float | None = None) -> bool:
        """Wait for the rows still being written (a clean shutdown; tests), for at most `timeout` seconds. False, and
        a warning, when rows were still being written at the deadline."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while self._tasks:
            left = None if deadline is None else deadline - loop.time()
            if left is not None and left <= 0:
                log.warning("shutdown: %d audit row(s) still being written", len(self._tasks))
                return False
            await asyncio.wait(set(self._tasks), timeout=left)
        return True


class TelegramBot:  # pragma: no cover - needs network + token
    """The Telegram service (`python -m goldbot.ops.run telegram`): sends the engines' proposals from the approval
    bus with Approve/Reject buttons, writes the owner's decisions back to the bus, edits each message with the outcome,
    delivers the staff agents' reports, and answers /status, /halt and /mode. Re-arming needs an authenticator code, so
    it is done on the dashboard. `/mode auto <code>` is accepted only on the auto-mode evidence (goldbot/telegram/
    automode.py), which this service checks and offers once; `/mode propose` needs no code."""

    def __init__(self, token: str, state_dir: str, owner_ids: set[int], poll_s: float = 2.0,
                 settings: TelegramSettings | None = None):
        if Application is None:
            raise RuntimeError("python-telegram-bot not installed; pip install -e '.[live]'")
        self.bus = ApprovalBus(state_dir)
        # opened lazily inside the pump: a broken aux.db must not stop the service (halt, health alerts, decisions)
        self.outbox: Outbox | None = None
        self.send_guard = SendGuard()           # a sent proposal whose mark_sent failed is retried, never re-sent
        self.report_retry = ReportRetry()       # a report batch failing for one recipient is not resent forever
        self.owner_ids = owner_ids
        self.poll_s = poll_s
        self.state_dir = state_dir
        self.audit = OffLoopAudit(state_dir)        # who acted, off the event loop (A15, review M1)
        self.health = HealthWatch(state_dir)
        self.settings = settings or TelegramSettings()
        self.deadman = DeadMan(accounts.get_secret, self.settings.deadman_every_min)   # outside switch, ADR 0014
        self.offer = automode.AutoModeOffer(state_dir)
        self.digest = DigestSchedule(state_dir, self.settings.digest_at)     # goldbot/telegram/digest.py
        # one-click deploys (Linux servers; the systemd unit sets GOLDBOT_DEPLOY=1): goldbot/ops/deploy.py
        self.deploy = DeployWatch(state_dir, ROOT) if os.environ.get("GOLDBOT_DEPLOY") == "1" else None
        self.app = (Application.builder().token(token).post_init(self._start_pump)
                    .post_shutdown(self._on_shutdown).build())
        self.app.add_handler(CallbackQueryHandler(self._on_button))
        for c in ("status", "halt", "rearm", "mode"):
            self.app.add_handler(CommandHandler(c, self._on_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self._pending_prompt: asyncio.Future | None = None
        accounts.register_prompter(self.prompt_sync)

    # ------------------------------------------------------------ outbound: proposals, outcomes, reports
    async def _start_pump(self, app: "Application") -> None:
        app.create_task(self._pump())

    async def _on_shutdown(self, app: "Application") -> None:
        """After run_polling stops: let the audit rows still being written land, for at most AUDIT_DRAIN_S (A15)."""
        await self.audit.drain(timeout=AUDIT_DRAIN_S)

    async def _pump(self) -> None:
        n = 0
        while True:
            try:
                # every outbox call reads or writes aux.db (busy_timeout up to 5 s): it runs in a worker thread, as
                # the health pass does, so a busy database never delays the owner's Approve/Reject callbacks
                if self.outbox is None:
                    self.outbox = await asyncio.to_thread(Outbox, self.state_dir, self.bus)
                outbox = self.outbox
                await asyncio.to_thread(self.send_guard.retry, outbox, time.monotonic())
                for p in await asyncio.to_thread(outbox.unsent_proposals):
                    if not self.send_guard.should_send(p.proposal_id):
                        continue                    # went out already; its mark is being retried
                    msgs = await self.send_proposal(p)
                    await asyncio.to_thread(self.send_guard.record, outbox, p.proposal_id, msgs, time.monotonic())
                for pid, outcome, msgs in await asyncio.to_thread(outbox.finished):
                    for chat, mid in msgs:
                        try:
                            await self.app.bot.edit_message_reply_markup(chat, mid, reply_markup=None)
                            await self.app.bot.send_message(chat, f"{pid}: {outcome_text(outcome)}",
                                                            reply_parameters=ReplyParameters(message_id=mid))
                        except Exception as exc:
                            log.warning("could not update %s: %s", pid, exc)
                if n % max(int(30 / self.poll_s), 1) == 0:          # reports every ~30 s
                    texts, ack = await asyncio.to_thread(outbox.pending_reports)
                    delivered = True
                    for text in texts:
                        for uid in self.owner_ids:
                            try:
                                await self.app.bot.send_message(uid, text)
                            except Exception as exc:
                                delivered = False
                                log.warning("could not send a report to an owner: %s", exc)
                    # only after delivery: a failed batch is retried, at most REPORT_ATTEMPTS passes (ReportRetry)
                    if ack is not None and self.report_retry.should_ack(ack, delivered):
                        await asyncio.to_thread(outbox.ack_reports, ack)
            except Exception:
                log.exception("telegram pump")
            if n % max(int(HEALTH_EVERY_S / self.poll_s), 1) == 0:
                await self._health_pass()
            if n % max(int(AUTOMODE_EVERY_S / self.poll_s), 1) == 0:
                await self._automode_pass()
            if n % max(int(DIGEST_EVERY_S / self.poll_s), 1) == 0:     # n == 0: a missed slot is sent on start
                await self._digest_pass()
            if self.deploy is not None:
                await self._deploy_pass(offer=n % max(int(DEPLOY_EVERY_S / self.poll_s), 1) == 0,
                                        report=n % max(int(30 / self.poll_s), 1) == 0)
            n += 1
            await asyncio.sleep(self.poll_s)

    async def _health_pass(self) -> None:
        """Run the health checks and tell the owner about checks that turned fail or recovered (dedupe in
        state/health_last.json, see goldbot.ops.health.HealthWatch), then ping the outside dead man's switch: ok
        while healthy, /fail on a FAIL or an undelivered alert, nothing when the checks themselves did not run."""
        report, delivered = None, True
        try:
            report = await asyncio.to_thread(lambda: run_checks(HealthContext.from_runtime(self.state_dir)))
            text = self.health.alert(report)
            if text:
                try:
                    for uid in self.owner_ids:
                        await self.app.bot.send_message(uid, text)
                except Exception:
                    delivered = False
                    raise
            self.health.record(report)          # only after delivery: a failed send is retried next pass
        except Exception:
            log.exception("health pass")
        if report is not None:      # off the pump (security review M1): a hung ping never delays a proposal
            self.app.create_task(asyncio.to_thread(self.deadman.after_health, report, delivered=delivered))

    async def _digest_pass(self) -> None:
        """The daily owner digest, once per slot (state/telegram_digest.json); a slot missed while this service was
        down is sent on start when under 6 h late (goldbot/telegram/digest.py)."""
        try:
            now = pd.Timestamp.now("UTC")
            if not self.digest.due(now):
                return
            text = await asyncio.to_thread(digest_from_runtime, self.state_dir, now, self.digest.slot(now))
            for uid in self.owner_ids:
                await self.app.bot.send_message(uid, text)
            self.digest.record(now)             # only after delivery: a failed send is retried next pass
        except Exception:
            log.exception("digest pass")

    def _eligibility(self) -> automode.Eligibility:
        return automode.auto_mode_eligibility_from_state(self.state_dir, self.settings)

    async def _automode_pass(self) -> None:
        """Offer auto mode once when the evidence holds (A10). Never switches: the owner replies /mode auto <code>."""
        try:
            e = await asyncio.to_thread(self._eligibility)
            text = self.offer.check(e, self.bus.control().approval_mode)
            if text:
                for uid in self.owner_ids:
                    await self.app.bot.send_message(uid, text)
                self.offer.record(e)            # only after delivery: a failed send is offered again next pass
        except Exception:
            log.exception("auto-mode pass")

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
                            + "".join(f"\n⚠️ {n}" for n in new.get("mt5", []))
                            + "\n\nDeploy restarts the services (open positions keep their stops; the engine reconciles).")
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Deploy", callback_data=f"dep:{new['sha']}"),
                                                InlineKeyboardButton("Skip", callback_data=f"skp:{new['sha']}")]])
                    for uid in self.owner_ids:
                        await self.app.bot.send_message(uid, text, reply_markup=kb)
            if report:
                for r in self.deploy.new_results():
                    icon = {"deployed": "✅", "rolled_back": "↩️", "rolled_back_manual": "↩️", "rollback_reverted": "⚠️",
                            "deployed_degraded": "⚠️",
                            "failed": "🛑", "refused": "⛔"}.get(r.get("result", ""), "ℹ️")
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
        # the loss budget left if this loses, from the engine's and supervisor's state files (unknown if unreadable)
        budget = await asyncio.to_thread(proposal_budget_from_state, self.state_dir, p.account_id, p.risk_amount)
        text = p.text(budget)
        sent = []
        for uid in self.owner_ids:
            m = await self.app.bot.send_message(uid, text, reply_markup=kb)
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
            ok = self.deploy.approve(parts[1]) if parts[0] == "dep" else self.deploy.skip(parts[1])
            if ok and parts[0] == "dep":    # who tapped: the audit log only (A15); before the answer, which can fail
                self.audit.write("deploy_approve", uid, sha=parts[1])
            await q.answer("ok" if ok else "no longer on offer", show_alert=not ok)
            if ok:
                await q.edit_message_text(q.message.text + ("\n\n→ deploying within a minute; you will get the result"
                                                            if parts[0] == "dep" else "\n\n→ skipped"))
            return
        try:
            approve = parts[0] == "ok"
            reason = None if approve else parts[2]
            self.bus.submit(parts[1], approve, reason, by="telegram")     # the channel only on disk (A15)
            # who decided, before the answer (which can time out): write() only schedules the row, so the answer,
            # which Telegram drops when late, is not delayed
            self.audit.write("decision", uid, proposal=parts[1], action="approve" if approve else "reject",
                             reason=reason)
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
            self.bus.set_halt(True, by="telegram", reason=arg)             # the channel only on disk (A15)
            self.audit.write("halt", uid, reason=arg)   # who halted: the audit log only (A15); before the reply
            text = "HALTED: no new entries on any engine. Open positions keep their stops and exits. Re-arm on the dashboard."
        elif cmd == "/rearm":
            text = "Re-arming needs your authenticator code: use the dashboard (Overview → Re-arm)."
        elif cmd == "/mode":
            text = await asyncio.to_thread(
                automode.mode_command, self.bus, arg, f"telegram:{uid}",
                totp_ok=lambda code: automode.owner_totp_ok(self.state_dir, code),
                eligibility=self._eligibility, audit=self.audit.write_now)     # in the worker thread
            if arg and len(arg.split()) > 1:
                try:
                    await update.message.delete()      # keep the authenticator code out of the chat history
                except Exception:
                    pass
        else:
            # the dashboard's Entry status words (goldbot/telegram/entry_text.py), per account; plain text, no parse_mode
            text = await asyncio.to_thread(self._status_text)
        await update.message.reply_text(text)

    def _status_text(self) -> str:
        """Any failure (control file, pending list, settings, build) replies with entry_text.FAILED, never nothing."""
        from goldbot.config import load_settings
        from goldbot.telegram.entry_text import status_reply
        return status_reply(self.state_dir, pd.Timestamp.now("UTC"), tz=self.settings.owner_tz, bus=self.bus,
                            load=load_settings)

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
        self.app.run_polling()          # its shutdown runs _on_shutdown: pending audit rows are drained (bounded)
