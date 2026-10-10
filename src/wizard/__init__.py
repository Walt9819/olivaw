"""Hermes Bridge onboarding wizard — a local, browser-based setup assistant.

This package must not be on sys.path itself, and it takes itself off.

Why: ``numbers.py`` lives here, and Python puts a script's own directory first on
sys.path — which for ``python src/wizard/wizard_server.py`` (how the shortcut starts the
console) is exactly this directory. From that moment stdlib ``numbers`` resolves to our
module, which cannot be imported outside its package, so every library that imports
``numbers`` fails. The failures are silent where they hurt: Pillow catches a plugin that
will not import, registers no PNG decoder and simply stops being able to open images.
The only symptom was an uploaded avatar stored byte-for-byte as it arrived instead of
being decoded and re-encoded by us — the one thing that re-encode exists to prevent. The
same shadow is why importing the console used to print a warning about ``_decimal`` being
initialised twice.

Doing it here rather than only in the console's own bootstrap means it holds for every
entry point: the shortcut, ``python -m``, a test that imports one module, and the
``cron_patch``/``wa_patch`` command lines. Nothing imports a module from this package
bare — they are all ``from . import x`` or ``from wizard import x`` — so removing the
entry costs nothing and the package stays reachable as ``wizard.*``.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
# "" means the current directory, which is this one when the console is started from
# inside src/wizard - so it is resolved rather than compared as a string.
_sys.path[:] = [_d for _d in _sys.path if _os.path.abspath(_d or ".") != _HERE]
