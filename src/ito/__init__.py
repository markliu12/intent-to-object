"""Intent-to-Object: turn small voluntary movements into ordinary-object control signals.

The package is deliberately split so that the intent layer never imports a
perception library:

    sources  -> features -> scorer -> gate -> policy -> sink

Only ``ito.sources.camera`` touches MediaPipe and OpenCV, and only
``ito.sinks.mqtt`` touches paho. Everything else is numpy and the standard
library, which is what makes the core testable without a camera.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
