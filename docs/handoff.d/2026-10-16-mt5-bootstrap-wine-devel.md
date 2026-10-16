- **MT5 bootstrap reproduces the hand-fixed box (sre; postmortem `docs/incidents/2026-10-16-mt5-debugger-found-under-wine.md`, first entry in the new `docs/incidents/`).**
  `goldbot/ops/linux/mt5_bootstrap.sh` now installs `winehq-devel` (wine-stable 11.0 trips MT5's "A debugger has
  been found", WineHQ bug 58043, fixed in 11.6), runs `wineboot` with `WINEDLLOVERRIDES=mscoree=` (no Mono dialog),
  saves the installer as `python-installer.exe`, treats mt5setup's exit code as meaningless and instead fails unless
  `C:\MT5\ICMarkets\terminal64.exe` exists. The Windows Python is called by one absolute path, `WINPY`
  (`/home/mt5/.wine/drive_c/users/mt5/AppData/Local/Programs/Python/Python311/python.exe`), in the bootstrap,
  `goldbot-bridge@.service`, the `goldbot-mt5` wrapper, `goldbot-deploy` and the RUNBOOK: a bare `wine python` runs a
  `python.exe` from the current directory first (`/opt/goldbot`, writable by mt5). The bootstrap refuses a `WINPY`
  that does not match `PYVER`; `tests/test_bootstrap_sh.py` checks every copy. **Wine is not pinned: that is the
  owner's decision, ADR 0019 (proposed, sre recommends holding the installed version).**
  **The box already set up by hand** keeps its old `goldbot-bridge@.service` (bare `wine python`) and the old
  `goldbot-deploy` until updated. With the book flat or at the weekend, from root's own clone at the merged commit
  (as RUNBOOK 0.5 "Installing a new goldbot-deploy", hash checked on the Mac): install `goldbot-bridge@.service` into
  `/etc/systemd/system/` and `goldbot-deploy.sh` as `/usr/local/sbin/goldbot-deploy`, rewrite `/usr/local/bin/goldbot-mt5`
  from the bootstrap's heredoc, `sudo systemctl daemon-reload && sudo systemctl restart goldbot-bridge@icm-demo`, then
  check `systemctl is-active goldbot-bridge@icm-demo` and `test -f` on the `WINPY` path as mt5. Not run on any host yet:
  the bootstrap change is checked by shellcheck and static tests only.
