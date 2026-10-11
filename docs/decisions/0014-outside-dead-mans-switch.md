# 0014: An outside dead man's switch on Healthchecks.io, pinged by the Telegram service's health pass

Status: accepted (SRE, 2026-10-11; first-deploy follow-up 4)

## Context
Every alert came from the brain's own Telegram service. A dead brain sent nothing: host down, out of memory, a
crashed Telegram service, a revoked bot token. The owner's only signal was the 06:45 digest not arriving, which can
be up to 24 h late. The design's alert objective (S5: a silent service is reported within about 6 minutes) held only
while the brain itself was alive. Exits are automatic, so a dead engine or bridge during a fast market costs money.
The owner must learn about it within minutes.

Free options compared (late 2026 free tiers; check them again when choosing):

| Service | Free tier | Fail signal | Alert channels | Notes |
| --- | --- | --- | --- | --- |
| Healthchecks.io | 20 checks | `<url>/fail` | email, Telegram, many more | open source (can be self-hosted later), period down to 1 min |
| Better Stack heartbeats | 10 | `<url>/fail` | email (phone/SMS paid) | larger product, more account setup |
| Cronitor | 5 monitors | `?state=fail` | email, Slack | small free tier |
| Dead Man's Snitch | 1 snitch | none | email | one check, hourly at best |
| UptimeRobot heartbeat | paid plans only | - | - | not free |

## Decision
- **Healthchecks.io**, free plan, one check: period 2 min, grace 3 min, email and optionally its own Telegram bot.
  The code takes any https URL. A service without `/fail` still works as a plain dead man's switch, because no ok
  ping is sent while health fails.
- **Pinged from the Telegram service's health pass** (`goldbot/ops/deadman.py`, called after each pass in
  `goldbot/telegram/bot.py`). That pass already runs every minute and is the alert path. Its death is exactly what
  needs an outside alert, so pinging from there covers the host, the service, the checks crashing (no ping) and
  undelivered Telegram alerts (fail ping). A separate systemd timer was rejected. It would need a new root-installed
  unit, and it would keep pinging while the Telegram service, which is the alert path, is dead.
- **Signal:** healthy (no FAIL, alert delivered) -> GET at most every `telegram.deadman_every_min` (2) minutes.
  Unhealthy -> POST `<url>/fail` whose body holds only the failing check names (account ids carry no login: accounts
  refuses 5+ digit names). A state change pings at once. The cadence is held in memory, with no new state file
  (no new JSON state files): whether pings arrive is judged by the outside service, which is the whole point.
- **The URL is a secret** (whoever holds it can keep the check green). It is stored only in the keyring via
  `goldbot accounts set deadman-url` (hidden prompt). It never appears in config, the repo, a log or a health
  reason. Only https is accepted. Errors are logged as exception type or HTTP status. The stdlib `urllib` is used
  because urllib3 logs request paths at debug level.
- **Never in the way of a trade** (security review M1, L1): the ping runs as its own task off the Telegram pump
  that sends proposals (the owner's 90 s window). One ping is in flight at a time, and a pass that finds one running
  skips. The socket timeout is 5 s and the whole attempt has an 8 s deadline (DNS included). A failed attempt backs
  off for one period, like a successful one; a change of state is still tried at once. Redirects are refused, and any
  3xx is a failed ping. Check names in the fail body are reduced to `[A-Za-z0-9_.:-]`.
- **Unset:** no-op plus a health `deadman` WARN naming the command. It is not a Telegram notice: it shows in
  `/status` and the digest.

## Consequences
- A dead brain is reported within about 6 minutes (2 min period + 3 min grace + Healthchecks.io's check cycle),
  independently of Oracle, Telegram's bot and the brain.
- The owner holds one more external account (free). If Healthchecks.io changes its free tier, any https heartbeat
  URL can be stored instead, with no code change.
- A Healthchecks.io outage gives a false "down" or a missed alert. Accepted: it is a second channel, Telegram stays
  the first.
- The check names in a fail body reach a third party. They are operational names, not secrets.
- What would change this: a second always-on host (watch each other), or a paid uptime service the owner chooses.

## Accepted risk recorded here: the MT5 installer on the Wine box (security review L2)
`mt5_bootstrap.sh` checks the Windows Python installer against a pinned sha256 before Wine runs it. python.org
publishes no SHA-256 for 3.11.9, so the pin was derived from a download whose md5 matched python.org's published one
and whose Sigstore bundle verified for the 3.11 release manager. `mt5setup.exe` cannot be pinned the same way.
MetaQuotes serves one unversioned URL that changes with every terminal build, publishes no hash, and the installer
then downloads the terminal itself. Accepted, because:
- it comes over https from MetaQuotes' own CDN, and runs only as the unprivileged `mt5` user inside its Wine prefix;
- the terminal holds no goldbot secret. The bridge token is in the bridge's own store, and the terminal's saved demo
  login is visible to it in any case;
- the alternative is no MT5 at all. Option A (Windows VM) uses a signed installer whose Authenticode signature is
  checked.

What would change this: MetaQuotes publishing versioned installers or hashes, or a live account on this box. Before
going live, re-check the terminal build by hand, or move to option A.
