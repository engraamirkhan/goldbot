# Free / trial hosting options for goldbot (verified 2026-10-11, revision 2)

Scope: MT5 terminal + MetaTrader5 Python bridge (Windows, or x86_64 Wine; ~1-2 GB) and the "brain" (Python 3.11,
~1-2 GB peak, 24/7). Private link between them; dashboard never on an open port. Requirement now: permanently free
OR a 12-month trial, after which the owner pays (trading should cover it) or moves. No secrets, accounts or hosts here.
"unverified" = not confirmed from a primary source. Most vendor facts come from web-search summaries: re-check at sign-up.

## 1. Key findings
- **Azure free VMs are only 1 GiB.** The free offer is 750 h each of B2pts v2 (Arm) and B2ats v2 (AMD x64), 2 vCPU / 1 GiB
  (Bsv2 docs list the B2ts v2 sibling at 2 vCPU / 1 GiB; B2ats v2 / B2pts v2 sizes are from third-party listings, unverified),
  plus 2 x 64 GB P6 SSD, 15 GB outbound/month, for 12 months from account creation (new customers). B1s is no longer on the page.
  https://azure.microsoft.com/en-gb/pricing/free-services/ , https://learn.microsoft.com/en-au/azure/virtual-machines/sizes/general-purpose/bsv2-series
  Windows Server + MT5 + Python in 1 GiB: earlier measurement was ~1 GB for Windows + MT5 alone, so it is borderline (pagefile
  on the 64 GB disk would carry it, slowly). Not tested; assume "might run the bridge only, no headroom". Arm B2pts v2 is
  useless for MT5 (x64 only). UK South availability of B2ats v2 is unverified (check in the portal size picker).
- **Azure terms:** $200 credit for 30 days, then upgrade to pay-as-you-go to keep the free services (spending limit is removed
  on upgrade); card + phone required (temporary auth hold only). https://azure.microsoft.com/pricing/offers/ms-azr-0044p/
  After month 12 the VM is not free; it keeps running and bills at PAYG rates (unverified wording): deallocate or delete before
  the anniversary, and set a budget alert.
- **The repo's Windows path is all-in-one:** goldbot/ops/vps_bootstrap.ps1 (NSSM) installs Python, git, NSSM, Tailscale, Node 22 and
  `npm ci && npm run build`, TWO MT5 terminals (IC Markets + Vantage), and 8 services (supervisor, 2 engines, api, webhook, scheduler,
  telegram, news) plus cloudflared. It fits a normal Windows box (>=4 GB) but will NOT fit 1 GiB. vps_update.ps1 (ordered stop/start,
  rollback) assumes the same service list. GAP: a bridge-only mode is needed (skip Node/web build, Vantage terminal and brain
  services; run only the MT5 bridge, goldbot/execution/bridge.py, under NSSM, bound to the Tailscale address).
- **Oracle:** A1 was cut to 2 OCPU / 12 GB total (docs: https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm ;
  https://linuxiac.com/oracle-quietly-cuts-free-tier-ampere-a1-resources-in-half/); micro still 1 GB, 1/8 OCPU, x2. Idle = CPU p95 and
  network <20% over 7 days (+ memory <20% on A1). PAYG upgrade: no charge within free limits and can fix "out of capacity"; that it
  stops reclamation is reported, not stated (unverified). London A1 capacity: no 2026 reports (unverified). Wine MT5 on 1 GB: no
  source found (unverified); needs a test boot with 4 GB swap.
- **Not free:** AWS (accounts after 15 Jul 2025: $100-200 credits, plan ends at 6 months or credit exhaustion, then 90-day grace
  and closure; no always-free EC2) https://aws.amazon.com/about-aws/whats-new/2025/07/aws-free-tier-credits-month-free-plan/ .
  GCP: $300 / 90 days with a card (Windows licence cost vs credit unverified), plus a permanent US-only e2-micro (1 GB, 1 GB egress)
  https://docs.cloud.google.com/free . IBM: no free VM found. Fly.io/Render/Railway: no free always-on service with a disk.
  MetaQuotes hosting: EAs only, no DLLs, no Python: unusable. https://www.mql5.com/en/vps/rules
- **IC Markets VPS:** free only at 15 (Standard: 1 CPU, 2 GB, 60 GB Windows) / 30 / 75 real forex+metals lots a month, cancelled after
  2 months below; demo volume almost certainly does not count (unverified). https://ic.com/sc/en/forex-trading-tools/virtual-private-server

## 2. Comparison (ranked for the new rule)
| # | Option | Runs | Free for | RAM / CPU | MT5 feasible | Risk | Setup | After free |
|---|---|---|---|---|---|---|---|---|
| 1 | Oracle A1 Flex (London) | Brain | Permanent | 12 GB / 2 OCPU | n/a (ARM) | Capacity at create; idle rule; limits cut once already | 1-2 h | Free |
| 2 | Azure B2ats v2 Windows | Bridge (native MT5) | 12 months | 1 GiB / 2 vCPU burst, 2x64 GB | Borderline; native, repo path fits with a bridge-only mode | RAM; trial lapses; needs card | 2-3 h | PAYG about $8-10/mo for this size (third-party, unverified) or resize |
| 3 | Oracle E2.1.Micro Wine | Bridge | Permanent | 1 GB / 1/8 OCPU | Unproven | Wine, idle reclaim | 2 h | Free |
| 4 | GCP e2-micro (US) | Bridge or brain (Linux) | Permanent | 1 GB | Wine, unproven | US only, egress | 1 h | Free |
| 5 | GCP $300 / 90 d | Windows 2-4 GB bridge | 90 days | any | Yes | Short; Windows licence eats credit (unverified) | 2 h | PAYG |
| 6 | AWS 6-month credits | Windows t3.small class | about 6 months | 2 GB | Yes | Closes at end; credits run out sooner | 2 h | PAYG |
| 7 | Owner's Mac | Both | Own hardware | any | Official macOS MT5 (Wine+Rosetta) | Must stay awake; stopgap only | 2-3 h | Free |
| 8 | Paid VPS, see 3 | Both | No | 2-4+ GB | Windows only | Cost | 1 h | below |
Supporting free: Tailscale Personal (3 users, 100 devices) https://tailscale.com/pricing/faq ; Cloudflare Tunnel + Access free to 50 users
https://costbench.com/software/business-vpn/cloudflare-zero-trust/free-plan/ (secondary).

## 3. Cost of staying on after the trial (prices unverified; vendor pages move)
- Hetzner Cloud: Linux only (no Windows image found). CX23 2 vCPU/4 GB about EUR 4-5.5/mo, CPX22 about EUR 8/mo after an April 2026
  price rise; cost-optimized tier reported sold out in Sep 2026. Fine for the brain (about GBP 4-7) and for Wine, not native MT5.
  https://kimmo.suominen.com/stuff/cpc-2026-08.txt , https://www.vincentschmalbach.com/hetzner-cheap-cloud-unavailable-price-increases/
- Contabo: Cloud VPS 10 about $5 plus a Windows licence about $5 = about $10 (GBP 8) a month, 3 vCPU / 8 GB (one secondary source;
  licence price unverified). https://contabo.com/blog/windows-vps-rdp-affordable-guide/ . Latency and neighbour noise worse than hyperscalers.
- Generic Windows RDP VPS with a legal licence: $15-25/mo (about GBP 12-20) https://valebyte.com/en/blog/the-cheapest-windows-vps-in-2026-where-to-get-rdp-inexpensively/
- Azure after month 12: 2 vCPU / 1 GiB burstable about $8-10/mo; a 2-4 GB size (B2s v2 class) roughly $30+/mo (rough, unverified).
- Budget to plan on: about GBP 10-15/mo for a 2-4 GB Windows box, brain still free on Oracle (about GBP 0). Against a GBP 50/day target this is small.

## 4. Recommended first deploy (48 h)
Your expectation is half right: brain on Oracle A1 is confirmed; "Windows VM on Azure for MT5 natively" is right in principle but the
free size is 1 GiB, not enough for the repo's current all-in-one bootstrap, so it must be bridge-only (and might still be too tight).
1. Brain: Oracle A1 2 OCPU / 12 GB, London, PAYG + compartment quota + budget alert; systemd units in goldbot/ops/linux.
2. Bridge, run two candidates in parallel on day 1 (2 h time-box each; keep the one that is stable 24 h, RSS < 85% RAM):
   A. Azure B2ats v2 Windows Server 2022 (or Windows 11 if offered), pagefile 4 GB on the 64 GB disk, MT5 portable + Windows Python
      + bridge-only NSSM service, Tailscale. Native, no Wine risk.
   B. Oracle E2.1.Micro Ubuntu, 4 GB swap, Wine MT5 + Windows Python + bridge (goldbot/ops/linux/mt5_bootstrap.sh).
3. Link: Tailscale (brain engine calls the bridge on its tailnet address). Dashboard: Cloudflare Tunnel + Access on the brain.
4. Fallback if neither 1 GB box is stable, or A1 has no capacity: a 2-4 GB Windows VPS (Contabo or similar, about GBP 8-15/mo) running
   the existing all-in-one vps_bootstrap.ps1 unchanged; or burn the Azure $200/30-day credit on a 4 GB size as a short bridge while
   deciding; Mac as day-1 stopgap only. If A1 is unavailable, brain can share the Windows box (needs 4 GB+) or GCP e2-micro (Linux, thin).

## 5. Migration effort after month 12 (or earlier)
- Brain on Oracle A1: nothing to move. Bridge: Azure Windows to any Windows VPS = about 2-3 h: run the bootstrap, restore MT5 terminal
  profile (re-login to the broker; demo state is on the broker), restore SQLite backup, `accounts add` keyring entries again (secrets
  are per Windows user), repoint the bridge address in Tailscale/config, run `ops.run health`, 24 h soak. Rollback = leave the old VM up.
- Azure to Oracle-Wine (if it proves stable) = about 2 h. Keep one scripted path: bridge-only bootstrap plus a restore script, so it stays a rerun.

## 6. Owner sign-up checklist (no secrets in chat or repo)
1. Oracle: account, home region UK South (cannot change later), MFA; upgrade to PAYG; budget of 1 unit with email alerts; quota policy
   capping to free shapes; create A1 (2 OCPU/12 GB, Ubuntu 24.04 arm64) and optionally the micro (Ubuntu x86_64).
2. Azure: free account (card, phone, Microsoft account); within 30 days upgrade to PAYG (keeps the 12-month free services); region UK South,
   size B2ats v2 (if not listed, try UK West / North Europe: latency not critical on demo); budget alert at a few GBP; calendar a
   deallocate/decide reminder at month 11. RDP only through Tailscale (no public 3389; remove the default inbound rule).
3. Tailscale Personal and Cloudflare accounts (Tunnel plus Access limited to the owner email).
4. Store the MT5 demo password only via `python -m goldbot.ops.accounts add <account>` on the VM (Credential Manager / keyring).
5. Run the bootstrap for each box, log in to MT5 once, soak 24 h, check Telegram heartbeat and backup.
6. Monthly: re-read the Oracle and Azure free pages (Oracle already cut limits once without notice).

## 7. Risks
- 1 GiB Windows is the weak point; if both bridge candidates fail the plan costs about GBP 10-15/mo from day 1.
- Oracle can cut limits again or reclaim idle VMs; capacity at London unknown. Azure trial ends silently into billing unless acted on.
- bootstrap/update scripts assume an all-in-one box: bridge-only mode is unbuilt (small task before day 1, flagged for the implementer).
- Wine is less proven than Windows (acceptable on demo; re-evaluate before real money). One bridge box is a single point of failure;
  the engine must fail closed on entries while exits keep their server-side SL/TP (already the repo's design).
- Card needed for Azure, Oracle, GCP, AWS; every free tier depends on staying under limits: budgets everywhere.
- Unverified: B2ats/B2pts v2 UK availability and exact RAM, post-month-12 Azure wording, Wine on 1 GB, London A1 capacity, PAYG vs
  reclamation, all third-party post-trial prices, Hetzner Windows.
