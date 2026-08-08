"""
iGalenus local STT — transcribe a voice note with faster-whisper on the local GPU.

Loads the model on CUDA (RTX A2000) and falls back to CPU if the CUDA runtime
libraries (cuBLAS/cuDNN) aren't available, so it always produces a transcript.
Defaults to Spanish since Walt's notes are in es/spanglish.

Usage:
  python transcribe.py <audio_path> [--model medium] [--lang es] [--device auto]

Prints the transcript to stdout (and a one-line meta report to stderr).
"""
import sys
import os
import time
import argparse
import glob


# --- CUDA DLL discovery (must run BEFORE importing faster_whisper) ---------
# ctranslate2 bundles cuDNN in its own package dir (auto-registered), but
# cuBLAS (cublas64_*.dll) is NOT bundled — it lives in the system CUDA
# Toolkit bin dir and is normally found via PATH. When this script is spawned
# by the Hermes gateway (started from a Startup .vbs at logon), the parent's
# PATH may not include the CUDA bin dir, so cuBLAS can't be found and
# ctranslate2 SILENTLY falls back to CPU. To make GPU work regardless of how
# we're launched, register the CUDA bin dir(s) on the DLL search path here.
def _register_cuda_dlls():
    if not hasattr(os, "add_dll_directory"):  # non-Windows
        return []
    candidates = []
    # Explicit override wins.
    env_bin = os.environ.get("CUDA_DLL_DIR")
    if env_bin:
        candidates.append(env_bin)
    # Standard CUDA Toolkit install locations (newest version first).
    roots = [
        os.environ.get("CUDA_PATH", ""),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA",
    ]
    for root in roots:
        if root and os.path.isdir(os.path.join(root, "bin")):
            candidates.append(os.path.join(root, "bin"))
        elif root and os.path.isdir(root):
            for ver in sorted(glob.glob(os.path.join(root, "v*", "bin")), reverse=True):
                candidates.append(ver)
    registered, seen = [], set()
    for d in candidates:
        key = os.path.normcase(os.path.normpath(d)) if d else d
        if not d or key in seen:
            continue
        seen.add(key)
        try:
            if os.path.isdir(d) and glob.glob(os.path.join(d, "cublas64_*.dll")):
                os.add_dll_directory(d)
                registered.append(d)
        except OSError:
            pass
    return registered


_CUDA_DLL_DIRS = _register_cuda_dlls()

from faster_whisper import WhisperModel

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_CACHE = os.path.join(HERE, "models")
RUN_LOG = os.path.join(HERE, "stt.log")

# Prime Whisper with iGalenus proper nouns so it stops mangling them
# ("Igalinas", "parriba"...). Whisper weights the initial_prompt toward
# recognizing these exact spellings. The term list is loaded from glossary.txt
# (editable, one term per line) so it can grow without touching this code;
# FALLBACK_PROMPT is used only if that file is missing/empty.
GLOSSARY_FILE = os.path.join(HERE, "glossary.txt")
FALLBACK_PROMPT = (
    "Transcripcion para iGalenus. Terminos: iGalenus, Galenus, gbook, "
    "gbook-gcp, gbot, ece, patients, OAuth, CFDI, facturacion, oftalmologia, "
    "residentes R4 y R5, cold start, WhatsApp."
)


def build_prompt():
    """Build the initial_prompt from glossary.txt (skipping # comments / blanks)."""
    try:
        with open(GLOSSARY_FILE, encoding="utf-8") as fh:
            terms = [ln.strip() for ln in fh
                     if ln.strip() and not ln.lstrip().startswith("#")]
        if terms:
            return "Transcripcion para iGalenus. Terminos: " + ", ".join(terms) + "."
    except Exception:
        pass
    return FALLBACK_PROMPT


def _log(msg):
    """Write a diagnostic line to stderr and append it to stt.log."""
    sys.stderr.write(msg + "\n")
    try:
        with open(RUN_LOG, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


def load_model(size, device):
    """Try requested device; on CUDA failure fall back to CPU int8.

    Logs the registered CUDA DLL dirs and, on a CUDA failure, the FULL error
    (not just its type) so a silent CPU fallback can be diagnosed from stt.log.
    """
    _log("cuda_dll_dirs: " + (", ".join(_CUDA_DLL_DIRS) or "NONE (cuBLAS may be missing → CPU)"))
    tried = []
    order = [device] if device != "auto" else ["cuda", "cpu"]
    last_err = None
    for dev in order:
        compute = "float16" if dev == "cuda" else "int8"
        try:
            t0 = time.time()
            m = WhisperModel(size, device=dev, compute_type=compute,
                             download_root=MODEL_CACHE)
            tried.append(f"{dev}/{compute} loaded in {time.time()-t0:.1f}s")
            _log("model: " + "; ".join(tried))
            return m, dev
        except Exception as e:  # noqa: BLE001 - want any CUDA/lib failure to fall back
            last_err = e
            tried.append(f"{dev} failed: {type(e).__name__}: {e}")
            _log(f"WARN {dev} load failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"could not load model on any device: {tried} ({last_err})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--model", default="medium")
    ap.add_argument("--lang", default="es")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--prompt", default=None,
                    help="override the initial_prompt (defaults to glossary.txt)")
    ap.add_argument("--out", default=None,
                    help="write transcript to this file (for Hermes command-STT); "
                         "still printed to stdout too")
    args = ap.parse_args()

    prompt = args.prompt if args.prompt is not None else build_prompt()

    if not os.path.exists(args.audio):
        sys.stderr.write(f"audio not found: {args.audio}\n")
        sys.exit(2)

    model, dev = load_model(args.model, args.device)
    t0 = time.time()
    segments, info = model.transcribe(args.audio, language=args.lang,
                                      vad_filter=True, initial_prompt=prompt)
    text = "".join(seg.text for seg in segments).strip()
    dt = time.time() - t0
    _log(
        f"device={dev} model={args.model} lang={info.language} "
        f"audio={info.duration:.1f}s decode={dt:.1f}s"
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
