"""FastAPI backend for the React dashboard and the Telegram bot. Single owner auth (passkey/TOTP),
bound to the Tailscale address. The only process that touches the store and engine state."""
