"""The version drifted once: the code said 0.1.0 while the README showed 0.2.0.

This is the cheapest possible test for a mistake that makes a repository look
unmaintained in its own first command, so it is worth the file.
"""

import re
from pathlib import Path

import ito

REPO = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version"
    return match.group(1)


def test_fallback_version_matches_pyproject():
    assert _pyproject_version() == ito._FALLBACK_VERSION


def test_readme_shows_the_version_the_tool_prints():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert f"intent-to-object {_pyproject_version()}" in readme
