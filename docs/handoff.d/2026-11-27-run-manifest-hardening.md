- 2026-11-27 run manifest hardening (security follow-up to #494): a manifest's `builder` must be
  `goldbot.<module>:<function>` or `tests.rebuild_builders:<function>` (public name, defined in that module); anything else (`os:system`, `json:loads`)
  fails validation and `build` refuses it before any import. `ModelEntry.manifest_id` must be 64 lowercase hex.
  `rebuild` refuses a `data_files` key that resolves outside the data root (`../x`) before hashing or fitting.
