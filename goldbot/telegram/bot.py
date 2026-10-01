"""python-telegram-bot adapter around ApprovalCenter. Runs on the VPS; also serves as the headless
credential prompter for goldbot.ops.accounts. Requires `pip install -e ".[live]"`."""
from __future__ import annotations

import asyncio
import logging

from goldbot.ops import accounts
from goldbot.telegram.approvals import ApprovalCenter, Proposal

log = logging.getLogger(__name__)

try:  # pragma: no cover
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
except ImportError:  # pragma: no cover
    Application = None


class TelegramBot:  # pragma: no cover - needs network + token
    def __init__(self, token: str, center: ApprovalCenter, owner_ids: set[int]):
        if Application is None:
            raise RuntimeError("python-telegram-bot not installed; pip install -e '.[live]'")
        self.center = center
        self.owner_ids = owner_ids
        self.app = Application.builder().token(token).build()
        self.app.add_handler(CallbackQueryHandler(self._on_button))
        for c in ("status", "halt", "rearm", "mode", "set"):
            self.app.add_handler(CommandHandler(c, self._on_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self._pending_prompt: asyncio.Future | None = None
        accounts.register_prompter(self.prompt_sync)

    # ------------------------------------------------------------ proposals out
    async def send_proposal(self, p: Proposal) -> None:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"ok:{p.proposal_id}")],
                                   [InlineKeyboardButton(f"Reject: {r}", callback_data=f"no:{p.proposal_id}:{r}")
                                    for r in ("news", "cost", "discretion")],
                                   [InlineKeyboardButton("Reject: duplicate", callback_data=f"no:{p.proposal_id}:duplicate"),
                                    InlineKeyboardButton("Reject: other", callback_data=f"no:{p.proposal_id}:other")]])
        for uid in self.owner_ids:
            await self.app.bot.send_message(uid, p.text(), reply_markup=kb)

    async def _on_button(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
        q = update.callback_query
        uid = q.from_user.id
        parts = q.data.split(":")
        try:
            if parts[0] == "ok":
                p = self.center.decide(parts[1], uid, True)
            else:
                p = self.center.decide(parts[1], uid, False, parts[2])
            await q.answer(p.outcome.value)
            await q.edit_message_text(q.message.text + f"\n\n→ {p.outcome.value}" + (f" ({p.reason_code})" if p.reason_code else ""))
        except (PermissionError, KeyError, ValueError) as exc:
            await q.answer(str(exc), show_alert=True)

    async def _on_command(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
        uid = update.effective_user.id
        parts = update.message.text.split()
        cmd, arg, totp = parts[0], (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else None)
        try:
            await update.message.reply_text(self.center.command(uid, cmd, arg, totp))
        except PermissionError:
            log.warning("command from non-owner %s ignored", uid)

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
