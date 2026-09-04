"""Tiny stdlib process/HTTP helpers shared by the wizard backend. No deps."""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from winspawn import quiet


IS_WIN = os.name == "nt"


def which(name: str):
    """Locate an executable on PATH, trying Windows suffixes too."""
    p = shutil.which(name)
    if p:
        return p
    if IS_WIN:
        for ext in (".cmd", ".exe", ".bat"):
            p = shutil.which(name + ext)
            if p:
                return p
    return None


def run(cmd, timeout=25, cwd=None, env=None):
    """Run a command, capture output. Returns {ok, code, out, err}.

    `cmd` is a list. On Windows, .cmd/.bat shims need shell resolution, which
    subprocess handles when given the full path; we pass the list as-is.

    Always windowless: the callers are `pythonw` daemons, and every hermes_ctl call
    lands here, including the supervisor's once-a-minute gateway check. Without
    quiet() that check opens a terminal window on the owner's desktop (see winspawn).
    """
    try:
        merged = None
        if env:
            merged = os.environ.copy()
            merged.update({k: str(v) for k, v in env.items()})
        proc = subprocess.run(cmd, **quiet(
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd, env=merged, encoding="utf-8", errors="replace",
        ))
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return {"ok": proc.returncode == 0, "code": proc.returncode,
                "out": out, "err": err}
    except FileNotFoundError:
        return {"ok": False, "code": -1, "out": "", "err": "not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "out": "", "err": "timeout"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": -1, "out": "", "err": str(e)}


def http_json(url, data=None, method=None, timeout=25, headers=None):
    """Minimal JSON HTTP client. Returns (ok, parsed_or_text, status)."""
    hdrs = {"User-Agent": "hermes-wizard", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return True, json.loads(raw), r.status
            except json.JSONDecodeError:
                return True, raw, r.status
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        try:
            return False, json.loads(raw), e.code
        except Exception:  # noqa: BLE001
            return False, raw or str(e), e.code
    except Exception as e:  # noqa: BLE001
        return False, str(e), 0
