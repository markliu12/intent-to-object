"""Intent-to-Object: turn small voluntary movements into ordinary-object control signals.

The package is deliberately split so that the intent layer never imports a
perception library:

    sources  -> features -> scorer -> gate -> policy -> sink

Only ``ito.sources.camera`` touches MediaPipe and OpenCV, and only
``ito.sinks.mqtt`` touches paho. Everything else is numpy and the standard
library, which is what makes the core testable without a camera.
"""

from importlib.metadata import PackageNotFoundError, version

# pyproject.toml is the single source of truth for the release number. The
# fallback covers running straight from a checkout without installing, and
# tests/test_version.py fails if the two ever disagree -- which is how the code
# came to report 0.1.0 while the README showed 0.2.0.
_FALLBACK_VERSION = "0.2.1"

try:
    __version__ = version("intent-to-object")
except PackageNotFoundError:  # pragma: no cover - depends on how it was run
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__"]
