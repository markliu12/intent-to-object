"""Command line entry point.

Design rule for this file: every command must either work on the machine you
are on, or tell you exactly what is missing and how to install it. A first run
that dies with an ImportError stack trace is how assistive projects lose their
only two potential contributors.
"""

from __future__ import annotations

import argparse
import platform
import socket
import sys
import time
from pathlib import Path

import yaml

from . import __version__
from .audit import AuditLog, is_session, read_jsonl, render_record, render_session
from .calibrate import NOT_SEPARABLE, fit, verify, write_config, write_session
from .config import Config, ConfigError, load
from .pipeline import Pipeline
from .policy import StdinConfirmer
from .report import format_summary
from .sinks import ConsoleSink, MqttSink, MultiSink, NullSink, Sink
from .sources import SourceError, camera_features, replay_features, synthetic_features

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(args) -> Config:
    path = Path(args.config) if getattr(args, "config", None) else None
    return load(path)


def _make_sink(args, config: Config):
    sinks: list[Sink] = []
    if not getattr(args, "quiet", False):
        sinks.append(ConsoleSink())
    if getattr(args, "mqtt", False):
        m = config.mqtt
        sinks.append(
            MqttSink(
                host=str(m.get("host", "localhost")),
                port=int(m.get("port", 1883)),
                client_id=str(m.get("client_id", "intent-to-object")),
                username=m.get("username"),
                password=m.get("password"),
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


def cmd_calibrate(args) -> int:
    started = time.perf_counter()
    config = _load(args)
    name = args.action or (config.actions[0].name if config.actions else None)
    if name is None:
        raise ConfigError("no action in this config to calibrate")

    scorer = next(a.scorer for a in config.actions if a.name == name)
    origin = "a scripted session" if args.source == "synthetic" else "the camera"
    print(f"fitting `{name}` ({scorer}) from {origin}")

    if args.source == "synthetic":
        print("no camera, no model: this exercises the fit and the verification, not a person")
        rest = list(synthetic_features(script=(("idle", args.rest),), fps=30.0, seed=args.seed))
        phase = "blink" if scorer == "blink" else "tilt"
        move = list(synthetic_features(script=((phase, args.move),), fps=30.0, seed=args.seed + 1))
    else:
        print()
        print("  step 1  sit as you normally would. Head resting naturally, face the camera.")
        print(f"          recording {args.rest:.0f}s of rest -- do not perform the movement yet")
        _countdown()
        rest = list(_camera_source(config, args, args.rest))
        print(f"          {len(rest)} frames")
        print()
        print(f"  step 2  now perform the movement and keep holding it for {args.move:.0f}s")
        _countdown()
        move = list(_camera_source(config, args, args.move))
        print(f"          {len(move)} frames")

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
        print(f"recording landmarks to {record_to} -- treat the file as personal data")
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
    print("landmarks are face geometry, but treat the file as personal data.")
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

        py = sys.version_info
        print(f"mediapipe        {mediapipe.__version__}")
        if not hasattr(mediapipe, "solutions"):
            print("                 this build has no 'solutions' API; pin mediapipe<0.11")
            ok = False
        if py >= (3, 13):
            print(
                f"                 warning: mediapipe rarely ships wheels for {py.major}.{py.minor}"
            )
    except ImportError:
        print("mediapipe        missing (needed for camera) -- pip install '.[camera]'")
        print("                 note: mediapipe has no wheels for Python 3.13+; use 3.10-3.12")

    try:
        import paho.mqtt.client  # noqa: F401

        print("paho-mqtt        ok")
    except ImportError:
        print("paho-mqtt        missing (needed for --mqtt) -- pip install '.[mqtt]'")

    config_path = getattr(args, "config", None)
    try:
        config = load(Path(config_path) if config_path else None)
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
            config = load(Path(args.file) if args.file else None)
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
