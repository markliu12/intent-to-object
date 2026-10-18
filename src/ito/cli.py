"""Command line entry point.

Design rule for this file: every command must either work on the machine you
are on, or tell you exactly what is missing and how to install it. A first run
that dies with an ImportError stack trace is how assistive projects lose their
only two potential contributors.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import platform
import socket
import sys
import time
from pathlib import Path

import yaml

from . import __version__
from .audit import AuditLog, is_session, read_jsonl, render_record, render_session
from .calibrate import NOT_SEPARABLE, fit, verify, write_config, write_session
from .config import DEFAULT_PAYLOAD, Config, ConfigError, default_dict, from_dict, load
from .pipeline import Pipeline
from .policy import StdinConfirmer
from .report import format_summary
from .sinks import ConsoleSink, MqttSink, MultiSink, NullSink, Sink
from .sources import SourceError, camera_features, replay_features, synthetic_features

REPO_ROOT = Path(__file__).resolve().parents[2]

# `ito setup` writes the first of these. Discovering it means the file the
# setup wizard just wrote is the file `ito run` reads, without anybody having to
# remember a flag -- which is the difference between a working first run and a
# person running the built-in defaults while believing they calibrated.
CONFIG_NAMES = ("config.yaml", "ito.yaml")


def _discover_config() -> Path | None:
    for name in CONFIG_NAMES:
        candidate = Path(name)
        if candidate.is_file():
            return candidate
    return None


# One movement, chosen in words rather than in a scorer name. The default config
# teaches the vocabulary; `ito setup` should not require learning it first.
_SETUP_CHOICES = {
    "1": ("tilt", "left", "tilt_left", "head tilts to your left"),
    "2": ("tilt", "right", "tilt_right", "head tilts to your right"),
    "3": ("blink", None, "hold_blink", "eyes held closed"),
}


def _load(args) -> Config:
    explicit = getattr(args, "config", None)
    if explicit:
        return load(Path(explicit))
    found = _discover_config()
    if found is not None:
        print(f"using {found} (found in this directory)", file=sys.stderr)
    return load(found)


def _mqtt_credentials(config: Config) -> tuple[str | None, str | None]:
    """MQTT username and password, preferring environment over the config file.

    The config is plain text and gets pasted into issues and bug reports, so a
    password that lives in the file is one copy-paste away from leaking. The
    environment is the safer home: set ITO_MQTT_USERNAME / ITO_MQTT_PASSWORD and
    leave the file fields empty.
    """
    import os

    username = os.environ.get("ITO_MQTT_USERNAME")
    password = os.environ.get("ITO_MQTT_PASSWORD")
    if username is not None or password is not None:
        # Either env var is set: use the environment, falling back to the file
        # only for whichever side the environment did not provide.
        return (
            username if username is not None else config.mqtt.get("username"),
            password if password is not None else config.mqtt.get("password"),
        )
    return config.mqtt.get("username"), config.mqtt.get("password")


def _make_sink(args, config: Config):
    sinks: list[Sink] = []
    if not getattr(args, "quiet", False):
        sinks.append(ConsoleSink())
    if getattr(args, "mqtt", False):
        m = config.mqtt
        username, password = _mqtt_credentials(config)
        sinks.append(
            MqttSink(
                host=str(m.get("host", "localhost")),
                port=int(m.get("port", 1883)),
                client_id=str(m.get("client_id", "intent-to-object")),
                username=username,
                password=password,
                qos=int(m.get("qos", 0)),
                retain=bool(m.get("retain", False)),
            )
        )
    if not sinks:
        return NullSink()
    return MultiSink(*sinks) if len(sinks) > 1 else sinks[0]


def _audit(args, config: Config) -> AuditLog:
    if getattr(args, "no_audit", False):
        return AuditLog(None)
    path = getattr(args, "audit", None) or config.output.get("audit_path")
    return AuditLog(Path(path) if path else None)


def _printer(record: dict) -> None:
    print("  " + render_record(record))


def build_confirmer(args):
    """Only an explicit --confirm puts a person in the loop.

    The config says which actions are medium risk; the flag says a person is
    present to answer. Requiring both means a config copied from somewhere else
    cannot start interrogating an empty room, and a forgotten flag declines the
    command instead of publishing it.
    """
    return StdinConfirmer() if getattr(args, "confirm", False) else None


def _countdown(seconds: int = 3) -> None:
    for i in range(seconds, 0, -1):
        print(f"  {i}...", end="\r", flush=True)
        time.sleep(1)
    print("      ", end="\r", flush=True)


def _camera_source(config, args, duration_s: float):
    cam = config.camera
    return camera_features(
        camera_index=int(
            args.camera if getattr(args, "camera", None) is not None else cam.get("index", 0)
        ),
        width=int(cam.get("width", 640)),
        height=int(cam.get("height", 480)),
        duration_s=duration_s,
        show=getattr(args, "show", False),
    )


_MEASUREMENT_LABELS = {
    "rest_median_deg": ("rest, median", "deg"),
    "rest_band_deg": ("rest, 95% band", "deg"),
    "rest_noise_deg": ("rest, noise", "deg"),
    "hold_deg": ("movement reached", "deg above rest"),
    "hold_ms": ("held above threshold", "ms"),
    "rest_ear_median": ("open-eye EAR", ""),
    "rest_ear_noise": ("EAR noise", ""),
    "move_ear_median": ("EAR while closed", ""),
    "hold_closure": ("closure while held", "of baseline"),
    "samples_rest": ("rest frames", ""),
    "samples_move": ("movement frames", ""),
}


def _default_out(args) -> Path:
    return (
        Path(args.out)
        if args.out
        else Path((args.config or "configs/example.yaml").replace(".yaml", "") + ".calibrated.yaml")
    )


def _capture_pair(config, args, scorer: str) -> tuple[list, list]:
    """Collect the rest and movement phases a fit needs.

    Shared by ``calibrate`` and ``setup`` so the sentence a person hears before
    each phase is written once, and so the scripted path stays testable without
    a camera.
    """
    if args.source == "synthetic":
        print("no camera, no model: this exercises the fit and the verification, not a person")
        rest = list(synthetic_features(script=(("idle", args.rest),), fps=30.0, seed=args.seed))
        phase = "blink" if scorer == "blink" else "tilt"
        move = list(synthetic_features(script=((phase, args.move),), fps=30.0, seed=args.seed + 1))
        return rest, move

    print()
    print("  rest   sit as you normally would. Head resting naturally, face the camera.")
    print(f"         recording {args.rest:.0f}s -- do not perform the movement yet")
    _countdown()
    rest = list(_camera_source(config, args, args.rest))
    print(f"         {len(rest)} frames")
    print()
    print(f"  move   now perform the movement and keep holding it for {args.move:.0f}s")
    _countdown()
    move = list(_camera_source(config, args, args.move))
    print(f"         {len(move)} frames")
    return rest, move


def cmd_calibrate(args) -> int:
    started = time.perf_counter()
    config = _load(args)
    name = args.action or (config.actions[0].name if config.actions else None)
    if name is None:
        raise ConfigError("no action in this config to calibrate")

    scorer = next(a.scorer for a in config.actions if a.name == name)
    origin = "a scripted session" if args.source == "synthetic" else "the camera"
    print(f"fitting `{name}` ({scorer}) from {origin}")

    rest, move = _capture_pair(config, args, scorer)

    if len(rest) < 10 or len(move) < 10:
        raise SourceError(
            "not enough usable frames. Was a face visible the whole time? "
            "Try a brighter room or a smaller distance to the camera."
        )

    # Keep what was captured. Without it, checking a threshold change later means
    # asking the person to perform the movement again -- which is exactly what
    # replay exists to avoid, and the handover story in GOVERNANCE.md assumes.
    session_path = None
    if not args.no_session and (args.session or args.source != "synthetic"):
        session_path = (
            Path(args.session) if args.session else _default_out(args).with_suffix(".session.jsonl")
        )
        write_session(session_path, rest, move)
        print(f"          session saved to {session_path}")
        print(f"          replay it later with:  ito replay {session_path}")

    suggestion = fit(config, name, rest, move)

    print()
    print("measured")
    for key, (label, unit) in _MEASUREMENT_LABELS.items():
        if key in suggestion.measurements:
            unit_txt = f" {unit}" if unit else ""
            print(f"  {label:<22}{suggestion.measurements[key]}{unit_txt}")

    action = next(a for a in config.actions if a.name == name)
    print()
    print("thresholds")
    print(f"  {'':<22}{'current':>10}{'fitted':>10}")
    for key, value in suggestion.params.items():
        old = action.scorer_params.get(key, "-")
        print(f"  {key:<22}{str(old):>10}{str(value):>10}")
    for key, value in suggestion.gate.items():
        old = getattr(action.gate, key, "-")
        print(f"  {key:<22}{str(old):>10}{str(value):>10}")

    print()
    print(f"verdict              {suggestion.verdict}")
    for note in suggestion.notes:
        print(f"  {note}")

    if suggestion.verdict != NOT_SEPARABLE:
        ok, dwell = verify(config, name, suggestion.params, rest, move, suggestion.gate)
        if ok:
            print(f"verified             a command fired after {dwell} ms of holding")
        else:
            print("verified             FAILED: the recorded movement does not fire under these")
            print("                     thresholds. Do not adopt them.")
            suggestion.verdict = NOT_SEPARABLE
    else:
        ok = False
        print("not verified         thresholds were not written")

    out = _default_out(args)
    if suggestion.verdict != NOT_SEPARABLE:
        written = write_config(
            config,
            name,
            suggestion.params,
            out,
            args.config if args.config else None,
            gate=suggestion.gate,
        )
        print(f"wrote                {written}")

    elapsed = time.perf_counter() - started
    print()
    if args.source == "synthetic":
        print(
            f"re-calibration time  {elapsed:.1f} s from a scripted source,"
            " which is not a measurement"
        )
        print("                     run it against the camera for the number that counts")
    else:
        print(f"re-calibration time  {elapsed:.1f} s (command start to a verified configuration)")
    if suggestion.verdict == NOT_SEPARABLE:
        print("                     this movement cannot be separated from rest -- try a larger")
        print("                     movement, a different direction, or a different body part")
        return 3
    return 0


def cmd_review(args) -> int:
    from .review import ask_missed_count, review

    if not sys.stdin or not sys.stdin.isatty():
        print(
            "`ito review` asks one question per command, so it needs a real terminal.\n"
            "Run it on the machine that holds the log, with the user or their carer present.",
            file=sys.stderr,
        )
        return 2
    try:
        review(Path(args.file), ask_missed=ask_missed_count)
    except KeyboardInterrupt:
        print("\nstopped early; nothing was written")
        return 130
    return 0


def _warner(message: str) -> None:
    print(f"! {message}", file=sys.stderr)


def cmd_demo(args) -> int:
    config = _load(args)
    for w in config.warnings:
        print(f"! {w}", file=sys.stderr)

    source = synthetic_features(seed=args.seed, fps=args.fps)
    if args.duration:
        source = _limit(source, args.duration)

    pipe = Pipeline(
        config,
        _make_sink(args, config),
        _audit(args, config),
        on_event=_printer,
        on_warning=_warner,
    )
    print(
        f"intent-to-object {__version__}  |  scripted session, "
        f"{len(config.actions)} action(s): {', '.join(a.name for a in config.actions)}"
    )
    print("(no camera, no model -- this exercises the gate/policy/publish chain only)")
    print()
    stats = pipe.run(source)
    print()
    print(format_summary(stats, config, label=f"synthetic session, seed {args.seed}", fps=args.fps))
    return 0


def _limit(source, seconds: float):
    for frame in source:
        if frame.t_ms > seconds * 1000:
            return
        yield frame


def cmd_run(args) -> int:
    config = _load(args)
    for w in config.warnings:
        print(f"! {w}", file=sys.stderr)
    cam = config.camera
    record_to = Path(args.record) if getattr(args, "record", None) else None
    source = camera_features(
        camera_index=int(args.camera if args.camera is not None else cam.get("index", 0)),
        width=int(cam.get("width", 640)),
        height=int(cam.get("height", 480)),
        duration_s=args.duration,
        show=args.show,
        landmarks_out=record_to,
    )
    pipe = Pipeline(
        config,
        _make_sink(args, config),
        _audit(args, config),
        confirmer=build_confirmer(args),
        on_event=_printer,
        on_warning=_warner,
    )
    print(
        f"intent-to-object {__version__}  |  actions: "
        f"{', '.join(a.name for a in config.actions)}  |  ctrl-c to stop"
    )
    medium = [a.name for a in config.actions if a.risk == "medium"]
    if medium:
        state = (
            "confirmation prompts are ON"
            if args.confirm
            else "they will be declined (add --confirm)"
        )
        print(f"medium-risk actions: {', '.join(medium)} -- {state}")
    if args.mqtt:
        print(f"publishing to {config.mqtt.get('host')}:{config.mqtt.get('port')}")
    else:
        print("dry run: intents are printed and logged but not published (add --mqtt)")
    if record_to:
        print(
            f"recording face landmarks to {record_to} -- contains no picture, but it is "
            "a measurement of one face; treat it like a photo"
        )
    print()
    try:
        stats = pipe.run(source)
    except KeyboardInterrupt:
        stats = pipe.stats
        print()
        print("stopped.")
    print()
    print(format_summary(stats, config, label="live session", user_session=True))
    if record_to:
        print()
        print(f"replay this session with:  ito replay {record_to}")
        print("it reproduces the same decisions only while the thresholds stay unchanged")
    return 0


def cmd_replay(args) -> int:
    config = _load(args)
    pipe = Pipeline(
        config,
        _make_sink(args, config),
        _audit(args, config),
        on_event=_printer,
        on_warning=_warner,
    )
    stats = pipe.run(replay_features(Path(args.file)))
    print()
    print(format_summary(stats, config, label=f"replay {args.file}"))
    return 0


def cmd_record(args) -> int:
    path = Path(args.out)
    # Landmarks are a measurement of a specific face, not an anonymous log line.
    # Say what is about to be written *before* it is written, and make a person
    # in a terminal confirm; a script (no tty) proceeds so automation still works.
    print(
        f"this writes face geometry (landmark coordinates) for {args.seconds:.0f}s to {path}.\n"
        "it contains no picture, but it is a measurement of one person's face and\n"
        "should be treated like a photo: do not commit it or send it to anyone."
    )
    if sys.stdin and sys.stdin.isatty():
        print("continue? [y/N] ", end="", flush=True)
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("cancelled; nothing was written")
            return 0
    print(f"recording face landmarks to {path} for {args.seconds:.0f}s ...")
    from .sources import record_landmarks

    frames, fps = record_landmarks(
        path,
        seconds=args.seconds,
        camera_index=args.camera,
        width=args.width,
        height=args.height,
    )
    if frames == 0:
        print("no frames recorded -- was a face visible?", file=sys.stderr)
        return 1
    print(f"wrote {frames} frames at ~{fps:.1f} fps")
    print(f"replay it with:  ito replay {path}")
    return 0


def cmd_explain(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"no such log: {path}", file=sys.stderr)
        return 1
    records = list(read_jsonl(path))
    if not records:
        print("log is empty")
        return 0
    # The end-of-session record is a record but not an event. Rendering it with
    # the event renderer used to produce a line that read like a declined
    # command called "unknown" -- on the one surface a carer is meant to read.
    sessions = [r for r in records if is_session(r)]
    events = [r for r in records if not is_session(r)]
    print(f"{len(events)} record(s) in {path}\n")
    for record in events:
        print(render_record(record))
    declined = [r for r in events if not r.get("allowed")]
    if declined:
        print("\ndeclined, by reason:")
        counts: dict = {}
        for r in declined:
            counts[r.get("reason", "?")] = counts.get(r.get("reason", "?"), 0) + 1
        for reason, n in sorted(counts.items()):
            print(f"  {reason:<24}{n}")
    if sessions:
        print()
        for record in sessions:
            print(render_session(record))
    return 0


def cmd_doctor(args) -> int:
    ok = True
    config: Config | None = None
    print(f"intent-to-object {__version__}")
    print(f"python           {platform.python_version()} ({platform.system()})")
    try:
        import numpy

        print(f"numpy            {numpy.__version__}")
    except ImportError:
        print("numpy            MISSING  -- pip install numpy")
        ok = False
    try:
        import yaml as _yaml  # noqa: F401

        print("pyyaml           ok")
    except ImportError:
        print("pyyaml           MISSING  -- pip install pyyaml")
        ok = False

    try:
        import cv2

        print(f"opencv           {cv2.__version__}")
    except ImportError:
        print("opencv           missing (needed for camera) -- pip install '.[camera]'")

    try:
        import mediapipe

        print(f"mediapipe        {mediapipe.__version__}")
        has_face_api = False
        try:
            from mediapipe.tasks.python.vision import FaceLandmarker  # noqa: F401

            has_face_api = True
        except ImportError:
            has_face_api = hasattr(mediapipe, "solutions")
        if not has_face_api:
            print("                 no face API in this build; pip install 'mediapipe>=0.10'")
            ok = False
    except ImportError:
        print("mediapipe        missing (needed for camera) -- pip install '.[camera]'")

    try:
        import paho.mqtt.client  # noqa: F401

        print("paho-mqtt        ok")
    except ImportError:
        print("paho-mqtt        missing (needed for --mqtt) -- pip install '.[mqtt]'")

    config_path = getattr(args, "config", None)
    try:
        config = load(Path(config_path) if config_path else _discover_config())
        print(
            f"config           ok, {len(config.actions)} action(s): "
            + ", ".join(f"{a.name}({a.risk})" for a in config.actions)
        )
        for w in config.warnings:
            print(f"                 ! {w}")
    except ConfigError as exc:
        print(f"config           INVALID -- {exc}")
        ok = False

    host = str(config.mqtt.get("host", "localhost")) if config else "localhost"
    port = int(config.mqtt.get("port", 1883)) if config else 1883
    try:
        with socket.create_connection((host, port), timeout=1.0):
            print(f"broker           {host}:{port} reachable")
    except OSError:
        print(f"broker           {host}:{port} not reachable (fine unless you pass --mqtt)")

    print()
    print("core is usable" if ok else "core is not usable until the items above are fixed")
    return 0 if ok else 1


def _module_missing(name: str) -> bool:
    """True when a module cannot be imported, without importing it."""
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ValueError):
        return True


def _setup_action(choice: str) -> dict:
    scorer, direction, name, _ = _SETUP_CHOICES[choice]
    if scorer == "tilt":
        params = {"start_deg": 5.0, "full_deg": 13.0, "direction": direction, "neutral_deg": 0.0}
    else:
        params = {"start_ratio": 0.30, "full_ratio": 0.62}
    return {
        "name": name,
        "scorer": scorer,
        "scorer_params": params,
        "gate": {
            "on": 0.5,
            "off": 0.35,
            "dwell_ms": 450,
            "cooldown_ms": 1800,
            "release_ms": 150,
        },
        # Risk is about what this switches, not about which movement triggers it.
        # A lamp on a blink is still a lamp.
        "risk": "low",
        "topic": f"ito/intent/{name}",
        "payload": DEFAULT_PAYLOAD,
    }


def _ask_choice() -> str:
    if not sys.stdin or not sys.stdin.isatty():
        raise ConfigError(
            "`ito setup` asks which movement to use, so it needs a terminal. "
            "Pass --choice 1|2|3 to answer that without one."
        )
    print("       > ", end="", flush=True)
    return input().strip()


def cmd_setup(args) -> int:
    """One command from a fresh checkout to a calibrated config.

    The chain it replaces was: read the README, install an extra, run doctor,
    edit two numbers in YAML, run calibrate, copy the fitted file over the one
    you just edited. Each step is defensible on its own and the sequence is not
    something to ask of the person who will actually use this.
    """
    need_camera = args.source != "synthetic"
    print(f"intent-to-object {__version__}  |  first run")
    print()
    print("  1/3  this machine")

    required = [("numpy", "pip install numpy"), ("yaml", "pip install pyyaml")]
    if need_camera:
        required += [
            ("cv2", "pip install '.[camera]'"),
            ("mediapipe", "pip install '.[camera]'"),
        ]
    missing = [(module, fix) for module, fix in required if _module_missing(module)]
    if missing:
        for module, fix in missing:
            print(f"       {module:<10} missing -- {fix}")
        print()
        print("       install those, then run `ito setup` again.")
        print("       `ito doctor` reports the same thing with more detail.")
        return 2
    print("       ok")

    print()
    print("  2/3  which movement will you use?")
    for key, (_, _, name, plain) in _SETUP_CHOICES.items():
        print(f"       {key}  {plain}  -> {name}")
    choice = args.choice if args.choice is not None else _ask_choice()
    if choice not in _SETUP_CHOICES:
        raise ConfigError("choose 1, 2 or 3: tilt left, tilt right, or eyes held closed")
    action = _setup_action(choice)
    name = str(action["name"])
    print(f"       using {name}")

    raw = default_dict()
    raw["actions"] = [action]
    config = from_dict(raw)

    print()
    print("  3/3  the camera" if need_camera else "  3/3  the scripted session")
    rest, move = _capture_pair(config, args, str(action["scorer"]))
    if len(rest) < 10 or len(move) < 10:
        raise SourceError(
            "not enough usable frames. Was a face visible the whole time? "
            "Try a brighter room or a smaller distance to the camera."
        )

    suggestion = fit(config, name, rest, move)
    print()
    print(f"  verdict    {suggestion.verdict}")
    for note in suggestion.notes:
        print(f"             {note}")

    if suggestion.verdict == NOT_SEPARABLE:
        print()
        print("  nothing was written: thresholds that cannot be told apart from rest")
        print("  would fire on their own. Try a larger movement, or a different body part.")
        return 3

    ok, dwell = verify(config, name, suggestion.params, rest, move, suggestion.gate)
    if not ok:
        print("  verified   FAILED: the recorded movement does not fire under these thresholds")
        print("  nothing was written. Try again with a larger movement.")
        return 3
    print(f"  verified   a command fires after {dwell} ms of holding")

    raw["actions"][0]["scorer_params"].update(suggestion.params)
    raw["actions"][0]["gate"].update(suggestion.gate)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# written by `ito setup` from one session\n"
        "#\n"
        "#   start_deg    score leaves zero here -- raise it if it fires on its own\n"
        "#   full_deg     score reaches 1.0 here -- lower it if it never fires\n"
        "#   dwell_ms     how long the movement must be held (lowered to what you can hold)\n"
        "#   cooldown_ms  the earliest it can fire again\n"
        "#   release_ms   how long you must be released before it can re-arm\n"
        "#\n"
        "# `ito run` in this directory reads this file without a flag.\n"
        + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    if not args.no_session:
        session = write_session(out.with_suffix(".session.jsonl"), rest, move)
        print(f"  session    {session}  (replay it with `ito replay {session}`)")

    print(f"  saved      {out}")
    print()
    print("  two commands from here:")
    print("    ito run --show     watch it decide, nothing is published")
    print("    ito run --mqtt     publish to the broker in this file")
    return 0


def cmd_config(args) -> int:
    from .config import default_dict

    if args.action == "init":
        sys.stdout.write(
            "# intent-to-object configuration\n"
            "# one action, one movement. add a second only when the first is reliable.\n"
            + yaml.safe_dump(default_dict(), sort_keys=False, allow_unicode=True)
        )
        return 0
    if args.action == "check":
        try:
            config = load(Path(args.file) if args.file else _discover_config())
        except ConfigError as exc:
            print(f"INVALID: {exc}", file=sys.stderr)
            return 1
        print(f"ok: {len(config.actions)} action(s)")
        for w in config.warnings:
            print(f"! {w}")
        return 0
    return 2


def cmd_selftest(args) -> int:
    """Smoke-test the whole chain without a camera."""
    config = load(None)
    pipe = Pipeline(config, NullSink(), AuditLog(None))
    stats = pipe.run(synthetic_features(fps=30.0, seed=1))
    failures = []
    if stats.published < 1:
        failures.append("expected at least one published intent from the scripted session")
    if stats.frames < 300:
        failures.append(f"expected >=300 frames, got {stats.frames}")
    if stats.frames_scored < 1:
        failures.append("ear baseline never established")
    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1
    print(
        f"ok: {stats.frames} frames, {stats.fired} fired, "
        f"{stats.published} published, {stats.cancelled_dwells} cancelled"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ito",
        description="Turn small voluntary movements into MQTT intents for ordinary objects.",
    )
    parser.add_argument("--version", action="version", version=f"intent-to-object {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, config_required=False):
        p.add_argument(
            "-c", "--config", help="path to a YAML config (default: built-in single action)"
        )
        p.add_argument("--audit", help="write the decision log here")
        p.add_argument("--no-audit", action="store_true", help="do not write a decision log")
        p.add_argument(
            "--mqtt", action="store_true", help="publish to the MQTT broker in the config"
        )
        p.add_argument(
            "-q", "--quiet", action="store_true", help="do not echo intents to the terminal"
        )

    p = sub.add_parser("demo", help="run the full chain on a scripted session (no camera)")
    common(p)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument(
        "--duration", type=float, default=None, help="stop after N seconds of session time"
    )
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("run", help="live: camera -> intent -> MQTT")
    common(p)
    p.add_argument("--camera", type=int, default=None, help="camera index (default from config)")
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--show", action="store_true", help="show the camera window")
    p.add_argument(
        "--record",
        help="save the landmarks of this session, so a false trigger can be replayed "
        "with `ito replay` instead of waiting for it to happen again",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="ask a human before publishing medium-risk actions (without this they are declined)",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "setup",
        help="first run: check the machine, choose one movement, calibrate, write a config",
    )
    p.add_argument(
        "-o",
        "--out",
        default="config.yaml",
        help="where to write the config (default ./config.yaml)",
    )
    p.add_argument("--choice", help="answer the movement question up front: 1, 2 or 3")
    p.add_argument("--source", choices=["camera", "synthetic"], default="camera")
    p.add_argument("--camera", type=int, default=None)
    p.add_argument("--rest", type=float, default=8.0, help="seconds of resting posture")
    p.add_argument("--move", type=float, default=6.0, help="seconds of holding the movement")
    p.add_argument("--seed", type=int, default=7, help="only with --source synthetic")
    p.add_argument("--show", action="store_true", help="show the camera while calibrating")
    p.add_argument("--no-session", action="store_true", help="do not save the captured session")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("calibrate", help="fit thresholds to this person in about 20 seconds")
    p.add_argument("-c", "--config")
    p.add_argument("-o", "--out", help="where to write the fitted config")
    p.add_argument("--action", help="which action to fit (default: the first)")
    p.add_argument("--source", choices=["camera", "synthetic"], default="camera")
    p.add_argument("--rest", type=float, default=8.0, help="seconds of resting posture")
    p.add_argument("--move", type=float, default=6.0, help="seconds of holding the movement")
    p.add_argument("--camera", type=int, default=None)
    p.add_argument("--seed", type=int, default=7, help="only with --source synthetic")
    p.add_argument(
        "--session",
        help="where to save the captured session for later `ito replay` "
        "(default: next to the fitted config; on by default for the camera)",
    )
    p.add_argument("--no-session", action="store_true", help="do not save the captured session")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("review", help="mark each command as intended, and get a rate per hour")
    p.add_argument("file")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("replay", help="re-run a recorded session through the current config")
    common(p)
    p.add_argument("file")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("record", help="record face landmarks for later replay")
    p.add_argument("out")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("explain", help="render a decision log in plain language")
    p.add_argument("file")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("doctor", help="check optional dependencies, config and broker")
    p.add_argument("-c", "--config")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("config", help="print or check configuration")
    p.add_argument("action", choices=["init", "check"])
    p.add_argument("file", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("selftest", help="smoke-test the chain without a camera")
    p.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list | None = None) -> int:
    # When the output is piped or redirected it is block-buffered, so the
    # banner can appear *after* an error that was printed to stderr -- which
    # makes a pasted transcript unreadable at exactly the moment someone is
    # asking for help with it.
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):  # depends on the stream
            sys.stdout.reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, SourceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
