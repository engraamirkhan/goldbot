from goldbot.specialists import (  # noqa: F401  (registers specialists)
    asia_drift,
    box_break_close,
    box_course,
    breakout,
    eq_liquidity_run,
    intraday_momentum,
    mean_reversion,
    session_open,
    smc_structure,
    sweep_absorb,
    time_series_momentum,
    trend,
)
from goldbot.specialists.base import SPECIALISTS, AgentIdentity, Candidate, Specialist, register  # noqa: F401
