- **Outside dead man's switch and a hash-pinned MT5 box (SRE, 2026-10-11; first-deploy risks 1 and 2).**
  - Alerts no longer depend only on the brain. After each health pass, the Telegram service pings a Healthchecks.io
    check (free tier; ADR 0014, `goldbot/ops/deadman.py`). It sends an ok ping every `telegram.deadman_every_min`
    (2) min while health has no FAIL, and `<url>/fail` (body: failing check names only) on a FAIL or an undelivered
    Telegram alert. The URL is keyring-only (`goldbot accounts set deadman-url`) and never logged. When it is unset,
    nothing is sent and health `deadman` WARNs. Owner steps: first-deploy.md P7 and 8b, RUNBOOK section 6 "Outside
    alert". Tests: `tests/test_deadman.py`.
  - The Wine MT5 box's Windows-Python packages come from `goldbot/ops/linux/mt5-bridge-requirements.txt`. The file
    is rendered from uv.lock by `scripts/bridge_requirements.py` (CPython 3.11 win_amd64 wheels, sha256) and
    installed with `pip install --require-hashes --no-deps --only-binary :all:`. pip is no longer upgraded and goldbot
    is no longer built editable (a `.pth` instead). goldbot-deploy reinstalls the packages when uv.lock changes, with
    the bridges stopped. CI check: `tests/test_bridge_requirements.py`. An existing MT5 box moves onto the file once,
    by hand (RUNBOOK 0.5).
