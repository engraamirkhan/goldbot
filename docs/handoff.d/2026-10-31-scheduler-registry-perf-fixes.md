- Brain fixes (2026-10-31): (1) `registry_sync` reads the `research-v1` release with the keyring `github-token` but its
  upload was refused with a bare HTTP 403: the token lacks the fine-grained permission `Contents: Read and write` on the
  repo (reads need only `Contents: Read-only`). The code is right (REST release assets, Bearer auth); the error now
  names the permission (`RegistryForbidden`). Owner step: re-issue the token with Contents read and write and store it
  with `python -m goldbot.ops.accounts set github-token`, then restart `goldbot-scheduler`. (2) `perf-report`: Days,
  North star and Costs of the demo learning book (exploration, control, probe closes; voided rows left out, censored
  not in money) now print under the learning tables, labelled; the record sections and `gates_phase` are unchanged.
  (3) `cpcv_quarterly` and `monthly_research` had not failed: they had never run (`runs` 0). A job first seen after its
  slot is anchored to it and waits a period; cpcv_quarterly would have waited until 2027-01-03. New schedule flag
  `run_if_never_run` (set for cpcv_quarterly in settings.yaml) runs a never-started job once for its latest slot;
  monthly_research runs on its own slot 2026-11-01 08:00 UTC.
