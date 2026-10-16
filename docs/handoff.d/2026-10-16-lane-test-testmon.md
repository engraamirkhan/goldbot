- Lane test loop (2026-10-16): lanes iterate on affected tests only with `scripts/lane_test.sh` (pytest-testmon):
  `seed` copies the integrator's `.testmondata` for the lane's base commit from `~/tmp/testmon/<sha>/` (`cp -c`;
  without one the first `iter` runs the full suite), `iter` runs `pytest --testmon -n ${GATES_WORKERS:-2} -q` (never
  with `-m`: testmon turns selection off under `-m`), `final` reminds the lane to run `scripts/gates.sh` once before
  handing back. The integrator runs `refresh` on a clean checkout at the integration tip to (re)build and publish the
  DB for that sha. Rule: lanes iterate with `scripts/lane_test.sh iter`; the full suite (`scripts/gates.sh`) runs once
  before handing back; the integration branch and release run the full gates. Selection is iteration only and
  misses non-Python inputs (config, data files) that testmon does not track, which is why the full gates stay.
