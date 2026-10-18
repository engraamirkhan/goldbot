# MT5 box: the terminal shows the gold chart by default

Owner, 18 Oct 2026: "by default it should show the gold chart". The dashboard's MT5 picture (goldbot-chart-capture,
every 60 s, display :99) showed MT5's stock layout: four tiled H1 charts (EURUSD, GBPUSD, USDCHF, USDJPY), each
with a one-click trading panel; an "Updates have been downloaded: Restart / Later" dialog over them; and the
Toolbox's Mailbox, which shows the owner's full name, filling the lower half of the screen.

## 1. What was found (read-only, 18 Oct 13:40 UTC)

| Item | Finding |
|---|---|
| Data directory | Portable: `C:\MT5\ICMarkets` = `/home/mt5/.wine/drive_c/MT5/ICMarkets` (`Config/`, `Profiles/`, `logs/`). The AppData hash directory only holds `origin.txt`, `portable.txt` and an empty `liveupdate/`. |
| Profile in use | `Config/common.ini` `[Charts] ProfileLast=Default`; `Profiles/Charts/Default/chart01-04.chr` (4 H1 charts, `one_click=1`). These files have not changed since install (16 Oct), because the terminal only saves them when it exits. |
| Gold symbol | `XAUUSD`, the broker's exact name (symbol history folder and Market Watch; it matches `config/accounts.yaml`). |
| How the terminal starts | The transient unit `goldbot-mt5-terminal-check.service` (`systemd-run`, `User=mt5`, `DISPLAY=:99`, `WINEDEBUG=-all`, `ExecStart=/usr/bin/wine "C:\MT5\ICMarkets\terminal64.exe"`, no `Restart=`), running since **16 Oct 19:01 UTC**. Its cgroup also holds `wineserver`. `goldbot-bridge@icm-demo` has its own unit and attaches to the terminal with `initialize(path=...)`. The watchdog timer is **active** (every 2 min). |
| Saved login | `common.ini` has `Login=`/`Server=`, and `accounts.dat` was rewritten today at 11:49. **Whether the login survives a restart has never been tested.** The only start so far (16 Oct 19:01) was followed by a manual login at 19:25, and the terminal has not been restarted since. The "Cold-restart test of the saved login" is planned for the attended upgrade on Sat 24 Oct (RUNBOOK 0.5). |
| Book | Last broker sync in the terminal log (18 Oct 00:36 UTC): `0 positions, 0 orders`. The market is closed until Sun 22:00 UTC. |
| LiveUpdate | Downloaded on 17 Oct 18:13 (`new version ... available`, `downloaded successfully`). MT5 has no documented setting to turn LiveUpdate off. Brokers also eventually refuse old builds, so turning it off is not wanted. **Any restart will probably install the pending build.** |
| Screen | Xvfb is 1280x800. The terminal's top-level window is at `1280x774+4+30` (there is no window manager). The hidden window of the dialog is "Welcome to LiveUpdate". `xdotool`, `xwininfo` and ImageMagick are installed. |
| Box | RAM 954 MB (482 MB available). The terminal's cgroup uses 298 MB. Disk: 33 GB free. |

## 2. Design (repo: `goldbot/ops/linux/mt5/`)

* `profile/goldbot/chart01.chr` + `order.wnd`: one chart, `symbol=XAUUSD`, M15 (`period_type=0`, `period_size=15`),
  maximised (`window_type=3`), candles on black, no grid, bid and ask lines, trade levels shown, `one_click=0` (no
  one-click panel).
* `goldbot.tpl`: the same look as a template, so it can be applied to any chart over VNC.
* `mt5-gold-profile.sh apply | rollback <dir>`: runs **as user mt5** and refuses to run as root or while
  `terminal64.exe` runs (the terminal rewrites `ProfileLast` and its profile when it exits). Before writing anything it
  backs up `common.ini`, `terminal.ini` and the whole `Profiles/` to `~/mt5-profile-backup-<UTC>`. It then writes the
  profile and the template (UTF-16LE + BOM + CRLF), sets `ProfileLast=goldbot` and `[Window] Fullscreen=1` (the chart
  only: no toolbars, Market Watch, Navigator or Toolbox, which also takes the owner's name off the picture; F11 over
  VNC toggles it). It prints states only.
* `goldbot-mt5-terminal@.service` (enabled by the 24 Oct upgrade): `ExecStart ... terminal64.exe /profile:goldbot`.
  Every start of the terminal unit then opens gold, even after someone switches profiles over VNC.
* `goldbot-chart-capture.sh`: optional `CHART_CROP=WxH+X+Y` (checked against the geometry pattern; anything else means
  the full screen is captured). It is not needed in full-screen mode. If full screen does not hold under Wine, the
  chart area of the current layout is `918x358+362+76`; measure it again from the first capture.
* LiveUpdate stays on. A downloaded build is installed in an attended window, never by accident.

Tests: `tests/test_mt5_gold_profile.py` (profile contents; apply then rollback round trip on a fake UTF-16 data
directory; refusals; the crop check). shellcheck is clean.

## 3. Trading-safety assessment

* Market closed until **Sun 18 Oct 22:00 UTC**; the broker reports a flat book.
* Option A (restart, below) drops the bridge for about 2-5 min. If the brain's engine is running, it sees the bridge
  drop and Telegram alerts. Stop the engine first; with the market closed nothing can be missed.
* Main risk of A: the **login may not survive** (never tested), and a restart probably **installs the pending MT5
  build**. If the login is lost, only the owner can log in again (VNC, password, tick "Save password"); the
  password is not on the box. Until then the bridge cannot serve trading. Positions: none, so no exit is at risk.
* Option B (no restart) cannot lose the login or the build. Risks: clicks land on a one-click trading panel, and
  keys go to the wrong window. To avoid them, every click is outside the panels (panel boxes are at x 418-512 and
  878-972, y 128-146 and 308-326). The keys sent are only typed symbol/timeframe text, Ctrl+F4, Alt+R, Alt+T and F11.
  No F9 (new order), and no hotkeys are set (`hotkeys.ini` is empty). The account is a demo. Check after each step.
  Alt+T removes the panel for good.

**Recommendation: B today, then A on Sat 24 Oct in the attended upgrade.** At 24 Oct, run the terminal unit with
`/profile:goldbot` and the cold-restart login test. B already makes gold the default: the terminal saves the
changed Default profile when it exits, so later restarts reopen it. Choose A today only if the owner is on hand for a
VNC login before 21:00 UTC.

## 4. Option B: gold chart now, without a restart (MT5 box, `ssh -F ~/.ssh/goldbot.conf goldbot-mt5`)

After each step, take a picture and look at it:
`sudo systemctl start goldbot-chart-capture.service` on the box, then on the Mac:
`ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-mt5 'sudo cat /home/mt5/chart/mt5.png' > ~/tmp/mt5.png`.

```bash
X() { sudo -u mt5 env DISPLAY=:99 xdotool "$@"; }
```
1. Close the update dialog with "Later". The click is relative to the dialog and fails if it is gone:
   `W=$(X search --name '^Welcome to LiveUpdate$' | head -1) && X mousemove --window "$W" 280 113 click 1`
2. Close the GBPUSD, USDCHF and USDJPY charts (click an empty spot in each chart, then Ctrl+F4):
   `X mousemove 1150 165 click 1 key ctrl+F4 && sleep 1 && X mousemove 650 400 click 1 key ctrl+F4 && sleep 1 && X mousemove 1150 360 click 1 key ctrl+F4`
3. Switch the EURUSD chart to XAUUSD M15 with quick navigation:
   `X mousemove 480 215 click 1 && sleep 1 && X type --delay 120 XAUUSD && X key Return && sleep 2 && X type --delay 120 M15 && X key Return`
4. Fill the window (Alt+R tile), hide the one-click panel (Alt+T), go full screen (F11):
   `X key alt+r && sleep 1 && X key alt+t && sleep 1 && X key F11`

Done when the picture shows XAUUSD M15 alone, with no panel and no dialog. `curl -s 127.0.0.1:8765/health` on the box
still shows `"ok": true`. Undo over VNC: F11 again, or File > Profiles > Default. Nothing on disk changes until the
terminal exits.

## 5. Option A: persistent profile with a restart (market closed; owner on hand for VNC)

0. Brain: `ssh -F ~/.ssh/goldbot.conf goldbot-brain 'sudo systemctl stop goldbot-engine@icm-demo; systemctl is-active goldbot-engine@icm-demo'` (must print `inactive`)
1. Mac, copy the files as user mt5:
   `tar -C <worktree>/goldbot/ops/linux/mt5 -cf - . | ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-mt5 'sudo -u mt5 -H sh -c "rm -rf ~/gold-profile && mkdir -m 700 ~/gold-profile && tar -C ~/gold-profile -xf -"'`
2. Box, count the logins before the restart (keep the number):
   `sudo -u mt5 sh -c 'iconv -f UTF-16 -t UTF-8 /home/mt5/.wine/drive_c/MT5/ICMarkets/logs/$(date -u +%Y%m%d).log' | grep -c 'authorized on'`
3. Box, stop the watchdog and the bridge, then close the terminal cleanly (WM_CLOSE, so it saves its files):
   `sudo systemctl stop goldbot-bridge-watchdog@icm-demo.timer goldbot-bridge@icm-demo && sudo -u mt5 -H env DISPLAY=:99 WINEDEBUG=-all wine taskkill /IM terminal64.exe; for i in $(seq 90); do pgrep -u mt5 -f '[t]erminal64[.]exe' >/dev/null || break; sleep 1; done; sudo systemctl stop goldbot-mt5-terminal-check.service; sudo systemctl reset-failed goldbot-mt5-terminal-check.service 2>/dev/null; pgrep -u mt5 -f '[t]erminal64[.]exe' && echo STILL-RUNNING || echo stopped`
4. Box, apply (prints the backup directory; note it):
   `sudo -u mt5 -H bash /home/mt5/gold-profile/mt5-gold-profile.sh apply`
5. Box, start the terminal the same way as before, plus the profile:
   `sudo systemd-run --unit=goldbot-mt5-terminal-check -p User=mt5 -p Environment=DISPLAY=:99 -p Environment=WINEDEBUG=-all /usr/bin/wine /home/mt5/.wine/drive_c/MT5/ICMarkets/terminal64.exe /profile:goldbot`
6. Verify after 2 min:
   - the login count from step 2 has gone up (a new `authorized on` line);
   - `pgrep -c -u mt5 -f '[t]erminal64[.]exe'` prints `1`;
   - then `sudo systemctl start goldbot-bridge@icm-demo`, and 60 s later `curl -s 127.0.0.1:8765/health` shows `"ok": true`;
   - a capture shows XAUUSD M15, full screen;
   - then `sudo systemctl start goldbot-bridge-watchdog@icm-demo.timer`;
   - on the brain, `sudo systemctl start goldbot-engine@icm-demo` and preflight.
7. **No new login line within 3 min:** the login was not preserved. The owner logs in over VNC (RUNBOOK 0.3) with
   "Save password" ticked, by 21:00 UTC. Until then the engine stays stopped. Record it in `docs/incidents/` as
   evidence for the 24 Oct test.

**Rollback (about 3 min).** Repeat step 3, then
`sudo -u mt5 -H bash /home/mt5/gold-profile/mt5-gold-profile.sh rollback <backup-dir>`, then step 5 without
`/profile:goldbot`, then the step 6 checks. A build already installed by the restart is not rolled back by this.
The old build worked; reinstalling it would need the installer and an attended window.
