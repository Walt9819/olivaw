"""Provider registry. Add a provider = drop an adapter module + register it here."""

from . import antigravity, claude_code, codex

# Order = display order in the wizard. Ready ones first.
_PROVIDERS = [claude_code.INFO, codex.INFO, antigravity.INFO]
_BY_ID = {p.id: p for p in _PROVIDERS}

DEFAULT_ID = "claude-code"


def all_providers():
    return list(_PROVIDERS)


def get(provider_id):
    return _BY_ID.get(provider_id)


def public_list():
    return [p.to_public() for p in _PROVIDERS]
