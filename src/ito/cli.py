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
from pathlib import Path
from typing import Optional

import yaml

from . import __version__
from .audit import AuditLog, read_jsonl, render_record
from .config import Config, ConfigError, load
from .pipeline import Pipeline
from .report import format_summary
from .sinks import ConsoleSink, MqttSink, MultiSink, NullSink
from .sources import SourceError, camera_features, replay_features, synthetic_features

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(args) -> Config:
    path = Path(args.config) if getattr(args, "config", None) else None
    return load(path)


def _make_sink(args, config: Config):
    sinks = []
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
    source = camera_features(
        camera_index=int(args.camera if args.camera is not None else cam.get("index", 0)),
        width=int(cam.get("width", 640)),
        height=int(cam.get("height", 480)),
        duration_s=args.duration,
        show=args.show,
    )
    pipe = Pipeline(config, _make_sink(args, config), _audit(args, config), on_event=_printer)
    print(
        f"intent-to-object {__version__}  |  actions: "
        f"{', '.join(a.name for a in config.actions)}  |  ctrl-c to stop"
    )
    if args.mqtt:
        print(f"publishing to {config.mqtt.get('host')}:{config.mqtt.get('port')}")
    else:
        print("dry run: intents are printed and logged but not published (add --mqtt)")
    print()
    try:
        stats = pipe.run(source)
    except KeyboardInterrupt:
        stats = pipe.stats
        print()
        print("stopped.")
    print()
    print(format_summary(stats, config, label="live session"))
    return 0


def cmd_replay(args) -> int:
    config = _load(args)
    pipe = Pipeline(
        config, _make_sink(args, config), _audit(args, config), on_event=_printer
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
    print(f"{len(records)} record(s) in {path}\n")
    for record in records:
        print(render_record(record))
    declined = [r for r in records if not r.get("allowed")]
    if declined:
        print("\ndeclined, by reason:")
        counts: dict = {}
        for r in declined:
            counts[r.get("reason", "?")] = counts.get(r.get("reason", "?"), 0) + 1
        for reason, n in sorted(counts.items()):
            print(f"  {reason:<24}{n}")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    config: Optional[Config] = None
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
            print(f"                 warning: mediapipe rarely ships wheels for {py.major}.{py.minor}")
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
        print(f"config           ok, {len(config.actions)} action(s): "
              + ", ".join(f"{a.name}({a.risk})" for a in config.actions))
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
        p.add_argument("-c", "--config", help="path to a YAML config (default: built-in single action)")
        p.add_argument("--audit", help="write the decision log here")
        p.add_argument("--no-audit", action="store_true", help="do not write a decision log")
        p.add_argument("--mqtt", action="store_true", help="publish to the MQTT broker in the config")
        p.add_argument("-q", "--quiet", action="store_true", help="do not echo intents to the terminal")

    p = sub.add_parser("demo", help="run the full chain on a scripted session (no camera)")
    common(p)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--duration", type=float, default=None, help="stop after N seconds of session time")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("run", help="live: camera -> intent -> MQTT")
    common(p)
    p.add_argument("--camera", type=int, default=None, help="camera index (default from config)")
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--show", action="store_true", help="show the camera window")
    p.set_defaults(func=cmd_run)

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


def main(argv: Optional[list] = None) -> int:
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
