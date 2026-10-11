- First demo deploy (SRE, 2026-10-11): `docs/deploy/first-deploy.md` is the owner's 60-90 min session (case A, fresh
  install). The brain runs on Oracle Ampere A1, now **2 OCPU / 12 GB**, the Always Free total since mid-2026 (it was
  4 / 24). MT5 runs on a bridge box. Day 1 tries both bridge options:
  - A: an Azure B2ats v2 Windows VM, set up with `vps_bootstrap.ps1 -BridgeOnly`. It runs one bridge service as the
    non-admin user `goldbot-bridge`, with admin-only ACLs, a signed MT5 installer and machine-wide Python. The brain
    reaches it over Tailscale through its SSH tunnel; the tunnel key has a forced no-op command.
  - B: an Oracle micro running MT5 under Wine (`mt5_bootstrap.sh`, 4 GB swap).

  Keep whichever bridge is stable for 24 h. Both bootstraps take an owner-checked 40-hex commit, run from a hash-checked
  download, and keep going if SSH drops. Python packages come from `uv.lock` (`uv sync --frozen`; `goldbot-deploy`
  too, after its root copy is reinstalled). The MT5 account must be a hedging demo (bridge v4). Paid fallback, the
  owner's decision: a 2-4 GB Windows VPS with the full install.

  Follow-ups: Cloudflare Tunnel with Access; an outside dead man's switch (alerts come only from the brain); a
  main-loop engine heartbeat (a hung engine shows as stale only after 45 min).
