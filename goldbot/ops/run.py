"""Service entry points used by NSSM on the VPS and by hand in development.

  python -m goldbot.ops.run supervisor
  python -m goldbot.ops.run engine <account_id>
  python -m goldbot.ops.run api
  python -m goldbot.ops.run webhook
  python -m goldbot.ops.run scheduler
"""
from __future__ import annotations

import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("goldbot.run")


def run_supervisor() -> None:
    from goldbot.risk.supervisor import Supervisor
    sup = Supervisor("state")
    while True:
        st = sup.evaluate()
        if st["halt"]:
            log.warning("HALT %s", st["reasons"])
        time.sleep(5)


def run_engine(account_id: str) -> None:
    from goldbot.config import load_settings
    from goldbot.engine import ConstantModel, Engine, EngineConfig
    from goldbot.ops import accounts
    from goldbot.specialists import SPECIALISTS
    from goldbot.telegram.approvals import ApprovalCenter
    acc = accounts.load_accounts()[account_id]
    settings = load_settings()
    if acc.is_live and acc not in accounts.enabled_accounts("live"):
        raise SystemExit("live account not unlocked by the phase gate")
    if sys.platform == "win32":
        from goldbot.execution.mt5_adapter import MT5Broker
        acc = accounts.ensure_login(acc)
        broker = MT5Broker(terminal_path=acc.terminal_path, login=acc.login, password=accounts.account_password(acc),
                           server=acc.server, server_tz=acc.server_tz, symbol=acc.symbol, account_label=account_id)
    else:
        from goldbot.execution.paper import PaperBroker
        broker = PaperBroker(symbol=acc.symbol)
        log.warning("not on Windows: running %s against the paper broker", account_id)
    center = ApprovalCenter(set(settings.get("telegram", {}).get("allowed_user_ids", [])))
    agents = [SPECIALISTS["session_open"]()]
    eng = Engine(EngineConfig(account_id, acc.broker, mode=acc.mode, approval_mode="propose", symbol=acc.symbol,
                              magic_base=acc.magic_base, state_dir="state"), broker, agents,
                 {"session_open": ConstantModel(0.0)}, center)  # p=0 until a trained model is loaded by the scheduler
    log.info("engine %s started (%s)", account_id, type(broker).__name__)
    while True:
        try:
            t = broker.last_tick(acc.symbol)
            eng.on_tick(t)
        except AssertionError:
            pass
        time.sleep(0.25)


def run_api() -> None:
    import uvicorn
    from goldbot.api.app import create_app
    uvicorn.run(create_app("state"), host="127.0.0.1", port=8787)


def run_webhook() -> None:
    import uvicorn
    from goldbot.ops import accounts
    from goldbot.webhook.app import create_app
    secret = accounts.get_credential("tradingview-webhook-secret", "TradingView webhook shared secret")
    uvicorn.run(create_app(secret), host="0.0.0.0", port=8443)


def run_scheduler() -> None:
    log.info("scheduler: nightly cost tables, Saturday retrain, monthly research — wired in Phase 1")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "supervisor":
        run_supervisor()
    elif cmd == "engine":
        run_engine(sys.argv[2])
    elif cmd == "api":
        run_api()
    elif cmd == "webhook":
        run_webhook()
    elif cmd == "scheduler":
        run_scheduler()
    else:
        print(__doc__)
        sys.exit(1)
