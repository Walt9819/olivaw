"""
Provider adapter interface.

A "provider" is the model backend Hermes uses as its brain (via the bridge).
Claude Code is the default; Codex is fully supported too. Antigravity is still a
`coming_soon` stub that slots into the SAME interface, so adding it later is one
adapter file plus an engine module — no wizard rework.

Each adapter is a plain object exposing:
  id            short slug ("claude-code")
  label         human name shown on the card
  status        "ready" | "coming_soon"
  tagline       one-line pitch for the card
  paid_note     what subscription/account the user must have (shown explicitly)
  download_url  where to get the app/CLI
  help_url      official docs / support link
  login_hint    what "logged in" looks like, in plain words
  cli_key       key under which this CLI's path travels in state/requests ("claude"/"codex")
  cli_label     the CLI's own name, for buttons and labels ("Claude Code"/"Codex")
  engine        which bridge engine runs it ("claude"/"codex") — sets OLIVAW_ENGINE
  check()       -> dict: is the CLI present & runnable? {ok, found, path, version, detail}
  install()     -> dict: best-effort auto-install {ok, detail}   (may be a no-op)
  login()       -> dict: open the interactive sign-in for this CLI
  login_status() -> dict: {ok, signed_in, detail} without any interaction
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
    cli_key: str = "claude"
    cli_label: str = ""
    engine: str = "claude"
    steps: List[Dict[str, str]] = field(default_factory=list)  # [{title, body, link?}]
    # runtime hooks (set by concrete adapters); stubs may leave them None
    check_fn: Optional[Callable[[dict], dict]] = None
    install_fn: Optional[Callable[[dict], dict]] = None
    login_fn: Optional[Callable[[dict], dict]] = None
    login_status_fn: Optional[Callable[[dict], dict]] = None
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
            "cli_key": self.cli_key,
            "cli_label": self.cli_label or self.label,
            "engine": self.engine,
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

    def login(self, paths: dict) -> dict:
        if self.status != "ready" or not self.login_fn:
            return {"ok": False, "detail": f"{self.label} aún no está disponible."}
        return self.login_fn(paths)

    def login_status(self, paths: dict) -> dict:
        if self.status != "ready" or not self.login_status_fn:
            return {"ok": False, "signed_in": False,
                    "detail": f"{self.label} aún no está disponible."}
        return self.login_status_fn(paths)

    def bridge_env(self, paths: dict) -> dict:
        if self.bridge_env_fn:
            return self.bridge_env_fn(paths)
        return {}
