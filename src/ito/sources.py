"""Feature sources.

Every source yields ``(t_ms, Features)``. The rest of the pipeline cannot tell
whether those features came from a webcam, a recorded file, or a script -- which
is what lets the gate, the policy and the report path be tested on a machine
with no camera, and lets a recorded session be replayed after a threshold change
instead of asking a user to perform the movement again.

If you are here to add support for a different perception library, this file is
the only one you need to touch.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np

from .geometry import DEFAULT_ASPECT, extract_features
from .types import Features


class SourceError(RuntimeError):
    pass


def _t(seconds: float) -> int:
    return int(round(seconds * 1000.0))


def synthetic_features(
    script: Sequence[tuple[str, float]] | None = None,
    fps: float = 30.0,
    seed: int = 7,
) -> Iterator[Features]:
    """A scripted, deterministic session. No camera, no model, no data.

    This is not a stand-in for a user study and must never be reported as one.
    It exists so that a new contributor can watch the whole chain -- score,
    dwell, policy, payload, audit line -- work on their first run, and so that
    regressions in that chain are caught in CI.

    The script intentionally contains two things that must *not* fire: ordinary
    blinks (~120 ms) and a half-hearted tilt held for ~300 ms. Both should end
    up in the cancelled counter, not in the published count.
    """
    if script is None:
        script = (
            ("idle", 3.0),
            ("tilt", 1.2),
            ("idle", 2.0),
            ("blink", 1.2),
            ("idle", 2.0),
            ("tilt_short", 0.35),
            ("idle", 1.5),
            ("tilt", 1.0),
            ("idle", 2.5),
        )

    rng = np.random.default_rng(seed)
    dt = 1.0 / fps
    t = 0.0

    roll = 0.0
    ear = 0.30
    t_next_blink = 1.5
    blink_until = -1.0

    for phase, duration in script:
        end = t + duration
        while t < end:
            noise = float(rng.normal(0.0, 0.8))

            target_roll = 14.0 if phase in ("tilt", "tilt_short") else 0.0
            roll += (target_roll - roll) * min(1.0, dt * 6.0)
            current_roll = roll + noise

            if phase == "blink":
                target_ear = 0.03
            elif t < blink_until:
                target_ear = 0.05
            else:
                target_ear = 0.30
            ear += (target_ear - ear) * min(1.0, dt * 18.0)

            if t >= t_next_blink and phase != "blink":
                blink_until = t + 0.12
                t_next_blink = t + float(rng.uniform(3.0, 6.0))

            yield Features(
                t_ms=_t(t),
                roll_deg=current_roll,
                yaw=float(rng.normal(0.0, 0.01)),
                pitch=float(rng.normal(0.0, 0.01)),
                ear=max(0.0, ear),
                brow_raise=0.28 + float(rng.normal(0.0, 0.01)),
                ear_baseline=0.0,
            )
            t += dt


def replay_features(path: Path) -> Iterator[Features]:
    """Replay a recorded JSONL session frame by frame.

    Accepts either raw landmark frames (``{"t_ms":..,"landmarks":[[x,y,z]..]}``)
    or pre-extracted features (``{"t_ms":..,"features":{..}}``).
    """
    path = Path(path)
    if not path.exists():
        raise SourceError(f"recording not found: {path}")
    aspect = DEFAULT_ASPECT
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            # A recording can end mid-write (the process was interrupted, the
            # disk filled). Say which line and why, instead of a JSON traceback.
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SourceError(
                    f"{path}:{lineno}: this line is not valid JSON; the recording was "
                    "probably cut off mid-write. Re-record, or delete the truncated "
                    "last line and replay the rest."
                ) from exc
            t_ms = int(row.get("t_ms", 0))
            if "features" in row:
                f = row["features"]
                yield Features(
                    t_ms=t_ms,
                    roll_deg=float(f["roll_deg"]),
                    yaw=float(f.get("yaw", 0.0)),
                    pitch=float(f.get("pitch", 0.0)),
                    ear=float(f["ear"]),
                    brow_raise=float(f.get("brow_raise", 0.0)),
                    ear_baseline=0.0,
                )
            elif "landmarks" in row:
                yield extract_features(
                    np.asarray(row["landmarks"], dtype=float),
                    t_ms=t_ms,
                    aspect=float(row.get("aspect", aspect)),
                )
            else:
                raise SourceError(f"row has neither 'features' nor 'landmarks': {t_ms}")


def _open_camera(index: int, width: int, height: int):
    try:
        import cv2
    except ImportError as exc:
        raise SourceError("camera input needs OpenCV: pip install '.[camera]'") from exc

    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SourceError(
            f"could not open camera index {index}. Close other apps using it, or try --camera 1."
        )
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cv2, cap


_FACEMESH_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)


def _face_landmarker_model_path() -> Path:
    """Locate (and, on first use, download) the FaceLandmarker model.

    MediaPipe's legacy ``solutions.face_mesh`` shipped its model inside the
    wheel. The modern ``FaceLandmarker`` API does not: the model is a separate
    ``.task`` file the caller must supply. We keep one copy in the user cache
    and download it once, so a person never has to fetch a model by hand.
    """
    import os

    cache = Path(os.environ.get("ITO_MODEL_DIR") or Path.home() / ".cache" / "intent-to-object")
    target = cache / "face_landmarker.task"
    if target.is_file() and target.stat().st_size > 0:
        return target

    cache.mkdir(parents=True, exist_ok=True)
    import urllib.request

    tmp = target.with_suffix(".task.part")
    try:
        with urllib.request.urlopen(_FACEMESH_MODEL_URL, timeout=30) as resp, tmp.open("wb") as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
        tmp.replace(target)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise SourceError(
            "could not download the FaceLandmarker model. If this machine has no "
            f"internet, copy it to {target} by hand, or set ITO_MODEL_DIR. "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    return target


def _face_mesh():
    """Return a face landmarker in whichever API this MediaPipe build offers.

    MediaPipe 0.10.x removed the ``solutions.face_mesh`` API this project used
    to depend on, and now ships ``FaceLandmarker`` (which needs an explicit
    model file). Prefer the modern API; fall back to the legacy one so people
    still on an older install keep working.
    """
    try:
        import mediapipe as mp
    except ImportError as exc:
        raise SourceError("perception needs MediaPipe: pip install '.[camera]'") from exc

    # Modern API (mediapipe >= 0.10): FaceLandmarker + an explicit model file.
    try:
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions
        from mediapipe.tasks.python.vision.core.vision_task_running_mode import (
            VisionTaskRunningMode,
        )

        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(_face_landmarker_model_path())),
            running_mode=VisionTaskRunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        landmarker = FaceLandmarker.create_from_options(options)

        class _ModernFaceMesh:
            """Adapt the modern FaceLandmarker to the legacy shape the loop uses."""

            def __init__(self):
                self._ts_ms = 0

            def process(self, rgb):
                from mediapipe import Image as MpImage
                from mediapipe import ImageFormat

                mp_image = MpImage(image_format=ImageFormat.SRGB, data=rgb)
                # detect_for_video needs a monotonically increasing timestamp in
                # milliseconds; passing a constant (0) makes the second frame
                # raise "Input timestamp must be monotonically increasing."
                result = landmarker.detect_for_video(mp_image, self._ts_ms)
                self._ts_ms += 33  # ~30 fps; the exact step does not matter
                return result

            def close(self):
                landmarker.close()

        return _ModernFaceMesh()
    except SourceError:
        raise
    except Exception:
        # Fall through to the legacy API below.
        pass

    solutions = getattr(mp, "solutions", None)
    if solutions is None:
        raise SourceError(
            "this MediaPipe build has no usable face API. Install one with "
            "'pip install mediapipe>=0.10' (both the legacy solutions.face_mesh "
            "and the modern FaceLandmarker are supported)."
        )
    return solutions.face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )


def camera_features(
    camera_index: int = 0,
    width: int = 640,
    height: int = 480,
    duration_s: float | None = None,
    landmarks_out: Path | None = None,
    show: bool = False,
) -> Iterator[Features]:
    """Live features from a webcam. The only place MediaPipe is imported."""
    cv2, cap = _open_camera(camera_index, width, height)
    mesh = _face_mesh()
    aspect = width / float(height) if height else DEFAULT_ASPECT
    started = time.monotonic()
    out = None
    if landmarks_out:
        landmarks_out = Path(landmarks_out)
        landmarks_out.parent.mkdir(parents=True, exist_ok=True)
        out = landmarks_out.open("w", encoding="utf-8")

    try:
        while True:
            elapsed = time.monotonic() - started
            if duration_s is not None and elapsed >= duration_s:
                break
            ok, frame = cap.read()
            if not ok:
                break
            # Stamp the frame the moment it exists: everything after this is
            # latency this project is responsible for.
            captured_at = time.perf_counter()
            rgb = cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)
            result = mesh.process(rgb)
            t_ms = _t(elapsed)
            landmarks = getattr(result, "face_landmarks", None)
            if landmarks is None:
                # Legacy solutions.face_mesh result.
                landmarks = getattr(result, "multi_face_landmarks", None)
            if not landmarks:
                # Nothing is yielded, and the pipeline reads the resulting gap in
                # t_ms as "no usable frame" -- counting it here as well used to
                # produce a number nothing ever read.
                continue
            face = landmarks[0]
            if hasattr(face, "landmark"):
                # Legacy: each face is a container with a .landmark list.
                face = face.landmark
            pts = np.array([[p.x, p.y, p.z] for p in face], dtype=float)
            if out:
                out.write(
                    json.dumps({"t_ms": t_ms, "aspect": aspect, "landmarks": pts.round(6).tolist()})
                    + "\n"
                )
            yield extract_features(pts, t_ms=t_ms, aspect=aspect, captured_at=captured_at)
            if show:
                cv2.imshow("intent-to-object", frame)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
    finally:
        cap.release()
        mesh.close()
        if out:
            out.close()
        if show:
            cv2.destroyAllWindows()


def record_landmarks(
    path: Path,
    seconds: float = 60.0,
    camera_index: int = 0,
    width: int = 640,
    height: int = 480,
) -> tuple[int, float]:
    """Record a session for later replay. Returns (frames, effective_fps)."""
    frames = 0
    first = last = None
    for features in camera_features(
        camera_index=camera_index,
        width=width,
        height=height,
        duration_s=seconds,
        landmarks_out=Path(path),
    ):
        frames += 1
        first = features.t_ms if first is None else first
        last = features.t_ms
    fps = 0.0
    if frames > 1 and first is not None and last is not None and last > first:
        fps = (frames - 1) * 1000.0 / (last - first)
    return frames, fps
