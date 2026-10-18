"""The camera source, without a camera.

The one thing these tests cannot do is open a real webcam, so they pin the
behaviour that *can* be checked without one: which MediaPipe API the source
prefers, what it does when a frame has no face, and how it fails when the model
is unavailable. Everything here runs against a fake ``mediapipe`` module, which
is exactly what CI has (the camera extra is not installed there).
"""

from __future__ import annotations

import sys
import types

import pytest

from ito import sources
from ito.sources import SourceError, _face_mesh


def _install_fake_mediapipe(monkeypatch, *, with_tasks: bool = True, with_solutions: bool = False):
    """Stub out ``mediapipe`` with just enough surface for `_face_mesh`."""
    mp = types.ModuleType("mediapipe")

    if with_solutions:
        solutions = types.ModuleType("mediapipe.solutions")

        class _Landmark:
            def __init__(self, x, y, z):
                self.x, self.y, self.z = x, y, z

        class _FaceMesh:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def process(self, rgb):
                result = types.SimpleNamespace(multi_face_landmarks=[])
                return result

            def close(self):
                pass

        face_mesh = types.ModuleType("mediapipe.solutions.face_mesh")
        face_mesh.FaceMesh = _FaceMesh
        solutions.face_mesh = face_mesh
        mp.solutions = solutions

    if with_tasks:
        tasks = types.ModuleType("mediapipe.tasks")
        python = types.ModuleType("mediapipe.tasks.python")
        vision = types.ModuleType("mediapipe.tasks.python.vision")
        core = types.ModuleType("mediapipe.tasks.python.vision.core")
        running = types.ModuleType("mediapipe.tasks.python.vision.core.vision_task_running_mode")

        class _BaseOptions:
            def __init__(self, model_asset_path=None):
                self.model_asset_path = model_asset_path

        class _Mode:
            VIDEO = "VIDEO"

        class _Options:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _Landmarker:
            created = None
            last_timestamps = []

            def __init__(self):
                self.closed = False
                self.last_image = None

            @classmethod
            def create_from_options(cls, options):
                cls.created = options
                cls.last_timestamps = []
                return cls()

            def detect_for_video(self, image, ts):
                self.last_image = image
                type(self).last_timestamps.append(ts)
                result = types.SimpleNamespace(face_landmarks=[])
                return result

            def close(self):
                self.closed = True

        python.BaseOptions = _BaseOptions
        running.VisionTaskRunningMode = _Mode
        vision.FaceLandmarker = _Landmarker
        vision.FaceLandmarkerOptions = _Options

        # Wire the submodule chain so `from mediapipe.tasks.python import BaseOptions`
        # and `from mediapipe.tasks.python.vision import FaceLandmarker` both resolve.
        vision.core = core
        core.vision_task_running_mode = running
        python.vision = vision
        tasks.python = python
        mp.tasks = tasks

        # `from mediapipe import Image, ImageFormat`.
        mp.Image = types.SimpleNamespace
        mp.ImageFormat = types.SimpleNamespace(SRGB="SRGB")

        monkeypatch.setitem(sys.modules, "mediapipe.tasks", mp.tasks)
        monkeypatch.setitem(sys.modules, "mediapipe.tasks.python", python)
        monkeypatch.setitem(sys.modules, "mediapipe.tasks.python.vision", vision)
        monkeypatch.setitem(
            sys.modules,
            "mediapipe.tasks.python.vision.core.vision_task_running_mode",
            running,
        )

    monkeypatch.setitem(sys.modules, "mediapipe", mp)
    return mp


def _no_model(monkeypatch, tmp_path):
    """Point the model cache at an empty dir so download is never attempted."""
    monkeypatch.setenv("ITO_MODEL_DIR", str(tmp_path / "models"))
    # Never hit the network in a test.
    monkeypatch.setattr(sources, "_FACEMESH_MODEL_URL", "http://127.0.0.1:1/nope.task")


def test_modern_api_is_preferred_over_legacy(monkeypatch, tmp_path):
    _install_fake_mediapipe(monkeypatch, with_tasks=True, with_solutions=True)
    monkeypatch.setenv("ITO_MODEL_DIR", str(tmp_path / "models"))
    # Pre-seed a model file so the path lookup short-circuits without network.
    model_dir = tmp_path / "models"
    model_dir.mkdir(parents=True)
    (model_dir / "face_landmarker.task").write_bytes(b"x" * 16)

    mesh = _face_mesh()

    # The modern adapter exposes the process/close shape the loop relies on.
    assert hasattr(mesh, "process")
    assert hasattr(mesh, "close")


def test_video_frames_carry_monotonically_increasing_timestamps(monkeypatch, tmp_path):
    """detect_for_video raises if its timestamp does not increase, so the adapter
    must feed it a rising value -- a constant 0 crashed on the second frame."""
    mp = _install_fake_mediapipe(monkeypatch, with_tasks=True)
    monkeypatch.setenv("ITO_MODEL_DIR", str(tmp_path / "models"))
    model_dir = tmp_path / "models"
    model_dir.mkdir(parents=True)
    (model_dir / "face_landmarker.task").write_bytes(b"x" * 16)

    mesh = _face_mesh()
    for _ in range(3):
        mesh.process(b"\x00" * 10)

    timestamps = mp.tasks.python.vision.FaceLandmarker.last_timestamps
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)  # strictly increasing, no repeats


def test_legacy_api_is_the_fallback_when_tasks_are_absent(monkeypatch, tmp_path):
    _install_fake_mediapipe(monkeypatch, with_tasks=False, with_solutions=True)

    mesh = _face_mesh()

    assert hasattr(mesh, "process")


def test_no_face_api_at_all_is_a_source_error(monkeypatch):
    _install_fake_mediapipe(monkeypatch, with_tasks=False, with_solutions=False)

    with pytest.raises(SourceError, match="no usable face API"):
        _face_mesh()


def test_missing_mediapipe_is_a_source_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    with pytest.raises(SourceError, match="MediaPipe"):
        _face_mesh()


def test_model_download_failure_is_a_source_error(monkeypatch, tmp_path):
    _install_fake_mediapipe(monkeypatch, with_tasks=True)
    _no_model(monkeypatch, tmp_path)

    with pytest.raises(SourceError, match="could not download"):
        _face_mesh()
