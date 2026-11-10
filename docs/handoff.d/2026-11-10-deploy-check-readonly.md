- Deploy offer works again under the telegram sandbox (R120): `DeployWatch.check()` no longer runs `git fetch` (it
  could not write /opt/goldbot/.git under ProtectSystem=strict and exited 255 on every pass, so no version was ever
  offered). It reads origin main with `git ls-remote` and asks GitHub's compare API for the fast-forward, the commit
  subjects (newest 15, count from `ahead_by`) and the MT5-box ancestry. If git or GitHub gives no answer, nothing is
  offered and one warning is logged per outage, with no traceback. Deploys themselves are unchanged
  (`sudo goldbot-deploy`, root, outside the sandbox). Unauthenticated API use is at most 2 calls a pass and only while
  a new commit is waiting, well under 60/h.
