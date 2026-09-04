#!/usr/bin/env python3
"""
release.py — cut a new Hermes Bridge release (run by the maintainer only).

Bumps VERSION, builds the release zip (src/ + templates/ + VERSION + manifest.json),
writes its SHA-256, and — if the GitHub CLI `gh` is installed — creates the GitHub
Release with both assets. Every friend's supervisor then picks it up and applies it
when idle.

Usage:
  python tools/release.py patch  -m "Ve imágenes que le mandes"          # 1.0.0 -> 1.0.1
  python tools/release.py minor  -m "Nuevo comando hqctl today"          # 1.0.1 -> 1.1.0
  python tools/release.py 2.0.0  -m "Rediseño" --publish                 # explicit + publish
Flags:
  -m/--message  changelog shown to users in the Telegram "updated" note
  --publish     create the GitHub Release via `gh` (else prints manual steps)
  --migration   optional JSON migration step, repeatable (advanced)
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "VERSION")
DIST = os.path.join(ROOT, "dist")
INCLUDE = ("src", "templates")           # shipped trees
EXCLUDE_DIRS = {"__pycache__", ".git", ".backup", ".staging", "img_cache",
                "models", ".venv", "dist", "node_modules"}
EXCLUDE_FILES = {"updater.config.json", ".env"}
EXCLUDE_EXT = {".log", ".pyc", ".db"}


def read_version():
    try:
        return open(VERSION_FILE, encoding="utf-8").read().strip()
    except Exception:
        return "0.0.0"


def bump(cur, spec):
    if all(c.isdigit() or c == "." for c in spec) and spec.count(".") == 2:
        return spec
    a, b, c = (list(int(x) for x in cur.split(".")) + [0, 0, 0])[:3]
    if spec == "major":
        return f"{a+1}.0.0"
    if spec == "minor":
        return f"{a}.{b+1}.0"
    if spec == "patch":
        return f"{a}.{b}.{c+1}"
    sys.exit(f"bad version spec: {spec} (use patch|minor|major|X.Y.Z)")


def _keep(path):
    parts = set(path.replace("\\", "/").split("/"))
    if parts & EXCLUDE_DIRS:
        return False
    base = os.path.basename(path)
    if base in EXCLUDE_FILES:
        return False
    return os.path.splitext(base)[1] not in EXCLUDE_EXT


def build_zip(version, changelog, migrations):
    os.makedirs(DIST, exist_ok=True)
    manifest = {
        "version": version,
        "released_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "changelog": changelog,
        "migrations": migrations,
        # files the updater must NOT overwrite on a friend's machine (belt-and-suspenders;
        # the updater only swaps src/ + templates anyway):
        "user_owned": ["updater.config.json", ".env", "VERSION.user", "config.yaml"],
    }
    man_path = os.path.join(ROOT, "manifest.json")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    zip_path = os.path.join(DIST, f"hermes-bridge-{version}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for tree in INCLUDE:
            base = os.path.join(ROOT, tree)
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, ROOT)
                    if _keep(rel):
                        z.write(full, rel)
        z.write(VERSION_FILE, "VERSION")
        z.write(man_path, "manifest.json")

    digest = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
    with open(zip_path + ".sha256", "w", encoding="utf-8") as fh:
        fh.write(f"{digest}  {os.path.basename(zip_path)}\n")
    return zip_path, digest


def build_launchers():
    """The two files a non-technical person downloads and double-clicks.

    Both are thin wrappers around the same installers the one-liner fetches, so they never
    go stale: the logic stays in install/, downloaded from main at run time.

    The Mac one has to be a ZIP, not a bare .command. A script downloaded straight from a
    browser arrives WITHOUT the execute bit, so double-clicking it opens a text editor
    instead of running it - which is worse than the command it was meant to replace. A zip
    entry carries its mode, so the extracted file is runnable. (Gatekeeper still asks once,
    because none of this is signed: right-click -> Open.)

    Returns the asset paths.
    """
    out = []
    cmd_src = os.path.join(ROOT, "install", "Olivaw-Instalar.cmd")
    if os.path.isfile(cmd_src):
        cmd_dst = os.path.join(DIST, "Olivaw-Instalar.cmd")
        shutil.copy2(cmd_src, cmd_dst)
        out.append(cmd_dst)

    mac_src = os.path.join(ROOT, "install", "install-macos.command")
    if os.path.isfile(mac_src):
        mac_zip = os.path.join(DIST, "Olivaw-Instalar-Mac.zip")
        info = zipfile.ZipInfo("Olivaw-Instalar.command")
        info.date_time = datetime.datetime.now().timetuple()[:6]
        info.compress_type = zipfile.ZIP_DEFLATED
        # 0o755 in the high half is what makes the extracted file executable. Without it a
        # downloaded installer cannot be double-clicked at all.
        info.external_attr = (0o755 << 16) | 0o600
        info.create_system = 3                      # Unix, so the mode is honoured
        with zipfile.ZipFile(mac_zip, "w") as z:
            with open(mac_src, "rb") as fh:
                z.writestr(info, fh.read())
        out.append(mac_zip)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", help="patch | minor | major | X.Y.Z")
    ap.add_argument("-m", "--message", default="", help="changelog for users")
    ap.add_argument("--publish", action="store_true", help="create the GitHub release via gh")
    ap.add_argument("--migration", action="append", default=[],
                    help='JSON step, e.g. \'{"type":"note","text":"..."}\'')
    a = ap.parse_args()

    cur = read_version()
    new = bump(cur, a.spec)
    migrations = []
    for m in a.migration:
        try:
            migrations.append(json.loads(m))
        except Exception as e:
            sys.exit(f"bad --migration JSON: {e}")

    with open(VERSION_FILE, "w", encoding="utf-8") as fh:
        fh.write(new + "\n")
    zip_path, digest = build_zip(new, a.message, migrations)
    launchers = build_launchers()
    print(f"built {os.path.basename(zip_path)}  (v{cur} -> v{new})")
    print(f"  sha256: {digest}")
    print(f"  assets: {zip_path}  and  {zip_path}.sha256")
    for p in launchers:
        print(f"  double-click: {p}")

    if a.publish:
        if not shutil.which("gh"):
            sys.exit("`gh` CLI not found. Install it, or create the release manually (below).")
        subprocess.run(["gh", "release", "create", f"v{new}", zip_path, zip_path + ".sha256",
                        *launchers,
                        "-t", f"v{new}", "-n", a.message or f"Release v{new}"], check=True)
        print(f"published GitHub release v{new}")
    else:
        print("\nNext steps (manual publish):")
        print(f"  1) git add -A && git commit -m \"release v{new}\" && git push")
        print(f"  2) gh release create v{new} \"{zip_path}\" \"{zip_path}.sha256\" "
              f"-t \"v{new}\" -n \"{a.message or 'Release v'+new}\"")
        print("  (or upload both assets to a new GitHub Release named v" + new + ")")


if __name__ == "__main__":
    main()
