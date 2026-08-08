"""Codex provider — declared, not yet wired (coming soon)."""

from .base import ProviderInfo

INFO = ProviderInfo(
    id="codex",
    label="Codex",
    status="coming_soon",
    tagline="Próximamente: usa Codex de OpenAI como cerebro del agente.",
    paid_note="Requerirá una cuenta de pago de ChatGPT/OpenAI.",
    download_url="https://openai.com/codex",
    help_url="https://openai.com/codex",
    login_hint="",
    steps=[],
)
