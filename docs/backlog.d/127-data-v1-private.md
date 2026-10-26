---
number: 127
rank: 47
title: Move Dukascopy bars off the public release (ADR 0048)
stage: Data · licence
size: S
status: 'proposed · accountable: program-director (owner decision) · reviews: security-reviewer'
---
- **127. Move Dukascopy bars off the public release (ADR 0048).** Value: Dukascopy's terms of use license downloads for personal, non-commercial use and forbid redistribution and third-party databases; release data-v1 is public (ADR 0048). Owner step (about 5 minutes): create a private repository and a fine-grained token (contents on that repository only) as secret DATA_PRIVATE_TOKEN and variable DATA_PRIVATE_REPO here. Then data-engineer: copy the 17+ yearly assets, point release.py, fetch_data_release.py and the brain's sync at it with a keyring token, verify sha256 per file, delete the public assets. Done = no Dukascopy-derived file on any public release, research and the brain read from the private one, digests pinned in pre-registrations unchanged.
