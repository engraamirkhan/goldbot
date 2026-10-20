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

from goldbot.api.entry_status import refusal_words
from goldbot.config import ROOT, TelegramSettings
from goldbot.execution.not_confirmed import card_text as nc_card_text
from goldbot.execution.not_placed import card_text
from goldbot.ops import accounts
from goldbot.ops.account_view import proposal_budget_from_state
from goldbot.ops.deadman import DeadMan
from goldbot.ops.deploy import DeployWatch
from goldbot.ops.health import HealthContext, HealthWatch, run_checks
from goldbot.telegram import automode
from goldbot.telegram.approvals import Proposal, decision_callbacks, decision_channel, parse_decision_callback
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.digest import DigestSchedule, digest_from_runtime
from goldbot.telegram.limits_notices import LimitsNotices
from goldbot.telegram.outbox import NOT_CONFIRMED_OUTCOME, Outbox, ReportRetry, SendGuard

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
LIMITS_EVERY_S = 30              # risk-limit change FYI (goldbot/telegram/limits_notices.py)
DIGEST_EVERY_S = 60              # daily owner digest due check (telegram.digest_at; once a day, late <= 6 h)
HEALTH_EVERY_S = 60               # health checks + alert dedupe: a silent service is reported within ~6 min (S5)
AUDIT_DRAIN_S = 10.0              # at shutdown, wait this long at most for audit rows still being written
OUTCOME_TEXT = {"APPROVED": "✅ APPROVED", "REJECTED": "❌ REJECTED", "EXPIRED_UNAPPROVED": "⌛ EXPIRED"}


def outcome_text(outcome: str) -> str:
    """The card's outcome line. An approved order the MT5 box or the broker refused reads "NOT PLACED" in the words of
    `goldbot.execution.not_placed` (docs/ux/order-not-placed.md: one table for the card, the notice and the API); one
    the broker neither filled nor refused reads "NOT CONFIRMED" (`not_confirmed`, BACKLOG 72)."""
    code = outcome.partition(": ")[2]
    if outcome.startswith("GATE_REFUSED") and (text := card_text(code)) is not None:
        return text
    if outcome.startswith("GATE_REFUSED"):          # the RiskGate's codes in words (BACKLOG 72), never the raw code
        why = ", ".join(refusal_words(c.strip()) for c in code.split(",") if c.strip())
        return f"🛑 NOT PLACED: approved, but the risk check refused it at approval ({why})"
    return OUTCOME_TEXT.get(outcome, outcome)


FINAL_EDIT_ERRORS = frozenset({"BadRequest", "Forbidden"})    # python-telegram-bot's telegram.error classes


def edit_is_final(exc: BaseException) -> bool:
    """A message edit Telegram refused for good, so retrying cannot help: a BadRequest (the text is already shown,
    the message was deleted or is too old to edit) or Forbidden (the bot was blocked). RetryAfter, TimedOut and other
    network errors may pass and are retried (BACKLOG 87 review). Matched by class name along the MRO, so this module
    imports without the [live] extra; BadRequest subclasses NetworkError there, so it is checked by its own name."""
    return any(c.__name__ in FINAL_EDIT_ERRORS for c in type(exc).__mro__)


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
    automode.py), which this service checks and offers once; `/mode propose` and `/mode account` need no code."""

    def __init__(self, token: str, state_dir: str, owner_ids: set[int], poll_s: float = 2.0,
                 settings: TelegramSettings | None = None):
        if Application is None:
            raise RuntimeError("python-telegram-bot not installed; pip install -e '.[live]'")
        self.bus = ApprovalBus(state_dir)
        # opened lazily inside the pump: a broken aux.db must not stop the service (halt, health alerts, decisions)
        self.outbox: Outbox | None = None
        self.limits_notices: LimitsNotices | None = None   # opened lazily in the pump, like the outbox
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
                await self._outbox_pass(self.outbox, n)
            except Exception:
                log.exception("telegram pump")
            if n % max(int(HEALTH_EVERY_S / self.poll_s), 1) == 0:
                await self._health_pass()
            if n % max(int(AUTOMODE_EVERY_S / self.poll_s), 1) == 0:
                await self._automode_pass()
            if n % max(int(DIGEST_EVERY_S / self.poll_s), 1) == 0:     # n == 0: a missed slot is sent on start
                await self._digest_pass()
            if n % max(int(LIMITS_EVERY_S / self.poll_s), 1) == 0:
                await self._limits_pass()
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

    async def _limits_pass(self) -> None:
        """FYI to the owner when an account's risk limits changed (docs/ux/limits-and-tradingview.md): no buttons; a
        raise is silent, a tighten or a safety revert has sound. The cursor moves only after every message went out."""
        try:
            if self.limits_notices is None:
                self.limits_notices = await asyncio.to_thread(LimitsNotices, self.state_dir, self.settings.owner_tz)
            msgs, ack = await asyncio.to_thread(self.limits_notices.pending)
            for m in msgs:
                for uid in self.owner_ids:
                    await self.app.bot.send_message(uid, m.text, disable_notification=m.silent)
            if ack is not None:
                await asyncio.to_thread(self.limits_notices.ack, ack)    # only after delivery: retried next pass
        except Exception:
            log.exception("limits pass")

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

    async def _outbox_pass(self, outbox: Outbox, n: int) -> None:
        """One pump pass over the outbox: new proposals, finished ones, NOT CONFIRMED cards that resolved, and every
        ~30 s the staff-agent reports and owner notices."""
        await asyncio.to_thread(self.send_guard.retry, outbox, time.monotonic())
        await self._send_new_proposals(outbox)
        for pid, outcome, msgs in await asyncio.to_thread(outbox.finished):
            replies = []
            for chat, mid in msgs:
                try:
                    await self.app.bot.edit_message_reply_markup(chat, mid, reply_markup=None)
                    m = await self.app.bot.send_message(chat, f"{pid}: {outcome_text(outcome)}",
                                                        reply_parameters=ReplyParameters(message_id=mid))
                    replies.append((chat, m.message_id))
                except Exception as exc:
                    log.warning("could not update %s: %s", pid, exc)
            if outcome == NOT_CONFIRMED_OUTCOME:
                await self._watch(outbox, pid, replies)
        await self._edit_resolved_cards(outbox)
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

    @staticmethod
    async def _watch(outbox: Outbox, pid: str, replies: list[tuple[int, int]]) -> None:
        """Keep the NOT CONFIRMED replies that went out, for the edit at resolution (BACKLOG 87). Best effort: a
        failure only loses that edit, never the pass's reports."""
        try:
            await asyncio.to_thread(outbox.watch, pid, replies)
        except Exception as exc:
            log.warning("NOT CONFIRMED card %s will not be edited at resolution: %s", pid, exc)

    async def _edit_resolved_cards(self, outbox: Outbox) -> None:
        """BACKLOG 87: each watched NOT CONFIRMED reply is edited to its new outcome's words (`card_text`), once per
        outcome; an edit that failed for a reason that may pass is retried with backoff (`Outbox.card_edit_failed`),
        one Telegram refused for good is not (`edit_is_final`). Best effort, logged, never raised: the owner's notice
        for the outcome is a separate message."""
        try:
            edits = await asyncio.to_thread(outbox.card_edits)
        except Exception as exc:
            log.warning("NOT CONFIRMED cards not checked for edits: %s", exc)
            return
        for e in edits:
            text = f"{e.proposal_id}: {nc_card_text(e.confirmation)}"
            done = True
            for chat, mid in e.messages:
                try:
                    await self.app.bot.edit_message_text(text, chat_id=chat, message_id=mid)
                except Exception as exc:
                    done = done and edit_is_final(exc)
                    log.warning("could not edit NOT CONFIRMED card %s: %s", e.proposal_id, exc)
            # an edit that may still go through keeps the card watched (retried with backoff, `card_edit_failed`)
            record = outbox.card_edited if done else outbox.card_edit_failed
            try:
                await asyncio.to_thread(record, e.proposal_id, e.outcome)
            except Exception as exc:
                log.warning("NOT CONFIRMED card %s edit not recorded: %s", e.proposal_id, exc)

    async def _send_new_proposals(self, outbox: Outbox) -> None:
        """Each proposal not yet sent goes out once; one that went out but whose mark is being retried is skipped."""
        for p in await asyncio.to_thread(outbox.unsent_proposals):
            if not self.send_guard.should_send(p.proposal_id):
                continue
            msgs = await self.send_proposal(p)
            await asyncio.to_thread(self.send_guard.record, outbox, p.proposal_id, msgs, time.monotonic())

    async def send_proposal(self, p: Proposal) -> list[tuple[int, int]]:
        """The card to each owner; [] (recorded as sent, so the pump moves on) when no button can carry its id: the
        owner is told and the proposal expires undecided (trading-safety review LOW-1 of 2c6e805)."""
        # each button carries the card's `created`, so a tap decides only this card (BACKLOG 63)
        try:
            cb = decision_callbacks(p)
        except ValueError as exc:
            log.error("proposal %s: card not sent (%s)", p.proposal_id, exc)
            self.bus.notify_owner(f"A proposal card could not be sent: {exc}. Proposal {p.proposal_id} cannot be "
                                  f"approved and will expire; no order is placed from it.")
            return []
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=cb[None])],
                                   [InlineKeyboardButton(f"Reject: {r}", callback_data=cb[r])
                                    for r in ("news", "cost", "discretion")],
                                   [InlineKeyboardButton(f"Reject: {r}", callback_data=cb[r])
                                    for r in ("duplicate", "other")]])
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
        if parts[0] in ("dep", "skp"):
            if self.deploy is None:         # deploys off: still a deploy tap, never read as an approval (INFO-1)
                await q.answer("no longer on offer", show_alert=True)
                return
            ok = self.deploy.approve(parts[1]) if parts[0] == "dep" else self.deploy.skip(parts[1])
            if ok and parts[0] == "dep":    # who tapped: the audit log only (A15); before the answer, which can fail
                self.audit.write("deploy_approve", uid, sha=parts[1])
            await q.answer("ok" if ok else "no longer on offer", show_alert=not ok)
            if ok:
                await q.edit_message_text(q.message.text + ("\n\n→ deploying within a minute; you will get the result"
                                                            if parts[0] == "dep" else "\n\n→ skipped"))
            return
        try:
            tap = parse_decision_callback(q.data)        # bound to the card tapped; an unstamped one is refused (63)
            approve, reason = tap.approve, tap.reason_code
            self.bus.submit(tap.proposal_id, approve, reason, by="telegram",     # the channel only on disk (A15)
                            card_created=tap.card_created)
            # who decided, before the answer (which can time out): write() only schedules the row, so the answer,
            # which Telegram drops when late, is not delayed
            self.audit.write("decision", uid, proposal=tap.proposal_id, action="approve" if approve else "reject",
                             reason=reason)
            await q.answer("sent to the engine")
            await q.edit_message_text(q.message.text + "\n\n→ " + ("approve" if approve else f"reject ({reason})")
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
