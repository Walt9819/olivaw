"""
Provider adapter interface.

A "provider" is the model backend Hermes uses as its brain (via the bridge).
Claude Code is the default and only fully-supported provider today; Codex and
Antigravity are declared as `coming_soon` stubs that slot into the SAME interface,
so adding them later is one adapter file each — no wizard rework.

Each adapter is a plain object exposing:
  id            short slug ("claude-code")
  label         human name shown on the card
  status        "ready" | "coming_soon"
  tagline       one-line pitch for the card
  paid_note     what subscription/account the user must have (shown explicitly)
  download_url  where to get the app/CLI
  help_url      official docs / support link
  login_hint    what "logged in" looks like, in plain words
  check()       -> dict: is the CLI present & runnable? {ok, found, path, version, detail}
  install()     -> dict: best-effort auto-install {ok, detail}   (may be a no-op)
  bridge_env(paths) -> dict: env vars the bridge needs for THIS provider

The wizard only calls check()/install() for the selected provider, and renders
download_url / help_url / paid_note in the guide.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class ProviderInfo:
    id: str
    label: str
    status: str                       # "ready" | "coming_soon"
    tagline: str
    paid_note: str
    download_url: str
    help_url: str
    login_hint: str = ""
    steps: List[Dict[str, str]] = field(default_factory=list)  # [{title, body, link?}]
    # runtime hooks (set by concrete adapters); stubs may leave them None
    check_fn: Optional[Callable[[dict], dict]] = None
    install_fn: Optional[Callable[[dict], dict]] = None
    bridge_env_fn: Optional[Callable[[dict], dict]] = None

    def to_public(self) -> dict:
        """JSON-safe view for the front-end (no callables)."""
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "tagline": self.tagline,
            "paid_note": self.paid_note,
            "download_url": self.download_url,
            "help_url": self.help_url,
            "login_hint": self.login_hint,
            "steps": self.steps,
        }

    def check(self, paths: dict) -> dict:
        if self.status != "ready" or not self.check_fn:
            return {"ok": False, "found": False,
                    "detail": f"{self.label} aún no está disponible."}
        return self.check_fn(paths)

    def install(self, paths: dict) -> dict:
        if self.status != "ready" or not self.install_fn:
            return {"ok": False, "detail": "Instalación automática no disponible."}
        return self.install_fn(paths)

    def bridge_env(self, paths: dict) -> dict:
        if self.bridge_env_fn:
            return self.bridge_env_fn(paths)
        return {}
