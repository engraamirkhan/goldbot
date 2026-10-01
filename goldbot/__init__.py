"""Goldbot: an evolving XAUUSD trading assistant.

Package layout mirrors the design document:
data/        point-in-time store, calendar, resampling, loaders, quality checks
features/    versioned feature registry, multi-timeframe merge without lookahead
labels/      spread-adjusted triple-barrier labels with uniqueness weights
specialists/ rule-triggered strategy specialists (session-open first)
allocator/   regime allocator (rule table first, learned later)
research/    purged walk-forward, metrics, trial registry
risk/        RiskGate: limits no model output can override
execution/   Broker protocol, paper broker, MT5 adapter (Windows only), account classifier
webhook/     TradingView alert receiver
telegram/    approvals and alerts
ops/         VPS bootstrap and service definitions
"""

__version__ = "0.1.0"
