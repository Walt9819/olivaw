"""
iGalenus local TTS — turn a text reply into a Telegram-ready voice note.

Runs Kokoro-82M on the local GPU (RTX A2000) and writes an OGG/Opus file that
Hermes delivers as a native Telegram voice message (via a `MEDIA:<path>` line).
Falls back to CPU if CUDA can't be used, so it always produces audio.

Languages (--lang): default **mx** = Mexican / Latin-American Spanish. Also:
  es = Spain (Castilian) Spanish · en/us = US English · uk/gb = British English
The mx↔es difference is real at the phoneme level: Mexican uses *seseo*
(es-419 espeak variant, "gracias" -> ...ɾˈasjas), Spain uses *ceceo* (the
Castilian "th", ...ɾˈaθjas). Non-Spanish text just needs the matching --lang.

Usage:
  python synthesize.py --in reply.txt --out reply.ogg              # Mexican Spanish
  python synthesize.py "Hi Walt." --lang en --out reply.ogg        # US English
  python synthesize.py "Hola Walt." --lang mx --gender female --out reply.ogg

Prints the output path to stdout (and a one-line meta report to stderr / tts.log).
"""
import sys
import os
import re
import time
import argparse
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from winspawn import quiet          # noqa: E402  (needs the path above)

HERE = os.path.dirname(os.path.abspath(__file__))
# Keep model weights local & self-contained (mirrors stt/models), instead of the
# global HF cache — so the whole tts/ folder is portable and predictable.
os.environ.setdefault("HF_HOME", os.path.join(HERE, "models"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

RUN_LOG = os.path.join(HERE, "tts.log")
# Pin the Kokoro weights repo so the pipeline doesn't print a "Defaulting
# repo_id" warning to stdout (which must stay clean = just the output path).
KOKORO_REPO = "hexgrad/Kokoro-82M"

# Friendly --lang -> (kokoro lang_code, espeak language override or None, label).
# Kokoro hard-codes espeak 'es' (Spain) for lang_code 'e'; we override the g2p
# to 'es-419' (Latin American) for Mexican Spanish — see _build_pipeline().
LANGS = {
    "mx":    ("e", "es-419", "Mexican / Latin-American Spanish"),
    "mex":   ("e", "es-419", "Mexican / Latin-American Spanish"),
    "latam": ("e", "es-419", "Latin-American Spanish"),
    "es":    ("e", "es",     "Spain (Castilian) Spanish"),
    "spain": ("e", "es",     "Spain (Castilian) Spanish"),
    "en":    ("a", None,     "US English"),
    "us":    ("a", None,     "US English"),
    "uk":    ("b", None,     "British English"),
    "gb":    ("b", None,     "British English"),
}

# Default voice per Kokoro lang_code + gender. Voice name must start with the
# lang_code letter, or Kokoro reloads/mismatches. Override with --voice.
VOICES = {
    "e": {"male": "em_alex",   "female": "ef_dora"},   # Spanish
    "a": {"male": "am_adam",   "female": "af_heart"},  # US English
    "b": {"male": "bm_george", "female": "bf_emma"},   # British English
}

MAX_CHUNK = 250  # espeak g2p isn't chunked by Kokoro; split long text ourselves.


def _log(msg):
    """Write a diagnostic line to stderr and append it to tts.log."""
    sys.stderr.write(msg + "\n")
    try:
        with open(RUN_LOG, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


def _register_espeak():
    """Point phonemizer/espeak at the espeak-ng library bundled by espeakng_loader.

    espeak-ng isn't installed system-wide on this box; espeakng_loader ships the
    shared lib + data (incl. the es-419 Latin-American variant) as a pip wheel.
    """
    try:
        import espeakng_loader
        os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY",
                              espeakng_loader.get_library_path())
        os.environ.setdefault("PHONEMIZER_ESPEAK_PATH",
                              espeakng_loader.get_data_path())
        if hasattr(espeakng_loader, "make_library_available"):
            espeakng_loader.make_library_available()
    except Exception as e:  # noqa: BLE001 - non-fatal; Kokoro may still find espeak
        _log(f"WARN espeak register failed: {type(e).__name__}: {e}")


def split_text(text):
    """Split into sentence-ish chunks under MAX_CHUNK chars.

    Kokoro doesn't chunk espeak (non-English) languages, so long text can be
    truncated unless we feed it in pieces. Split on sentence enders / newlines,
    pack greedily, and hard-split any single oversized sentence.
    """
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text.strip())
    chunks, cur = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(cur) + len(p) + 1 <= MAX_CHUNK:
            cur = (cur + " " + p).strip()
        else:
            if cur:
                chunks.append(cur)
            while len(p) > MAX_CHUNK:
                chunks.append(p[:MAX_CHUNK])
                p = p[MAX_CHUNK:]
            cur = p
    if cur:
        chunks.append(cur)
    return chunks or [text.strip()]


def _build_pipeline(kokoro_lang, espeak_override, device):
    """Construct a KPipeline on `device`, overriding the g2p for es-419 if asked."""
    from kokoro import KPipeline
    pipeline = KPipeline(lang_code=kokoro_lang, repo_id=KOKORO_REPO, device=device)
    if espeak_override:
        # Kokoro built EspeakG2P(language='es'); swap in the requested variant
        # (es-419) so Mexican/Latin-American pronunciation is used.
        from misaki import espeak as _espeak
        pipeline.g2p = _espeak.EspeakG2P(language=espeak_override)
    return pipeline


def synthesize(text, kokoro_lang, espeak_override, voice, speed, device):
    """Run Kokoro over chunked text; return (float32 mono 24kHz samples, device)."""
    import numpy as np
    import torch

    chunks = split_text(text)
    order = [device] if device != "auto" else ["cuda", "cpu"]
    last_err = None
    for dev in order:
        if dev == "cuda" and not torch.cuda.is_available():
            _log("WARN cuda requested but torch.cuda.is_available()=False")
            continue
        try:
            t0 = time.time()
            pipeline = _build_pipeline(kokoro_lang, espeak_override, dev)
            audio_parts = []
            for chunk in chunks:
                for _, _, audio in pipeline(chunk, voice=voice, speed=speed):
                    audio_parts.append(audio.detach().cpu().numpy()
                                       if hasattr(audio, "detach")
                                       else np.asarray(audio))
            if not audio_parts:
                raise RuntimeError("Kokoro produced no audio (empty text?)")
            samples = np.concatenate(audio_parts).astype("float32")
            _log(f"kokoro synth on {dev}: {len(chunks)} chunk(s) in "
                 f"{time.time()-t0:.1f}s")
            return samples, dev
        except Exception as e:  # noqa: BLE001 - fall back to CPU on any CUDA failure
            last_err = e
            _log(f"WARN synth on {dev} failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"synthesis failed on all devices ({last_err})")


def to_voice_note(samples, sr, out_path):
    """Write 24kHz float samples to an OGG/Opus voice note via a temp WAV + ffmpeg.

    Telegram shows a proper voice-message bubble (waveform) only for OGG/Opus.
    """
    import soundfile as sf
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        sf.write(wav_path, samples, sr)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", wav_path,
            "-c:a", "libopus", "-b:a", "32k", "-ac", "1",
            "-application", "voip",
            out_path,
        ], **quiet(check=True))
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass
    return out_path


def read_text(args):
    """Resolve the text to speak from --in file, positional arg, or stdin."""
    if args.infile:
        with open(args.infile, encoding="utf-8") as fh:
            return fh.read().strip()
    if args.text:
        return args.text.strip()
    return sys.stdin.read().strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default=None,
                    help="text to speak (or use --in / stdin)")
    ap.add_argument("--in", dest="infile", default=None,
                    help="read text from this file (for Hermes command-TTS)")
    ap.add_argument("--out", default=None,
                    help="output .ogg path (Opus voice note). "
                         "Defaults to out/voice_<pid>.ogg to avoid collisions.")
    ap.add_argument("--lang", default="mx", help=f"one of: {', '.join(LANGS)}")
    ap.add_argument("--gender", default="male", choices=["male", "female"])
    ap.add_argument("--voice", default=None,
                    help="explicit Kokoro voice id (overrides --lang/--gender default)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    args = ap.parse_args()

    lang_key = args.lang.lower()
    if lang_key not in LANGS:
        sys.stderr.write(f"unknown --lang '{args.lang}'; use one of: "
                         f"{', '.join(LANGS)}\n")
        sys.exit(2)
    kokoro_lang, espeak_override, label = LANGS[lang_key]
    voice = args.voice or VOICES[kokoro_lang][args.gender]

    text = read_text(args)
    if not text:
        sys.stderr.write("no text to synthesize\n")
        sys.exit(2)

    out_path = args.out or os.path.join(HERE, "out", f"voice_{os.getpid()}.ogg")

    _register_espeak()
    t0 = time.time()
    samples, dev = synthesize(text, kokoro_lang, espeak_override,
                              voice, args.speed, args.device)
    out = to_voice_note(samples, 24000, out_path)
    dur = len(samples) / 24000.0
    _log(f"device={dev} lang={lang_key}({label}) voice={voice} "
         f"chars={len(text)} audio={dur:.1f}s total={time.time()-t0:.1f}s -> {out}")
    print(out)


if __name__ == "__main__":
    main()
