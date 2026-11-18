"""Command line entry point. Modules register subcommands with @command(name).

Whatever goes wrong, the person sees one plain line and exit code 1, never a traceback; with
ADEDITOR_DEBUG=1 set, the traceback is printed instead, for whoever is fixing the tool."""
import argparse
import importlib
import os
import sys
import traceback

COMMANDS: dict[str, dict] = {}
# Modules imported by main() so their @command decorators run. Later tasks append here.
MODULES: list[str] = ["adeditor.doctor", "adeditor.retakes", "adeditor.cutplan", "adeditor.speed", "adeditor.brand", "adeditor.broll", "adeditor.plan", "adeditor.render", "adeditor.checks"]


def command(name: str, help: str = "", args=None):
    """Register a subcommand. The function receives the parsed argparse namespace and
    returns an exit code (or None for 0). `args(parser)` adds its arguments."""

    def deco(fn):
        COMMANDS[name] = {"fn": fn, "help": help, "args": args}
        return fn

    return deco


def build_parser() -> argparse.ArgumentParser:
    for module in MODULES:
        importlib.import_module(module)
    parser = argparse.ArgumentParser(prog="adeditor", description="Turn raw talking-head video into finished ads.")
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, spec in sorted(COMMANDS.items()):
        p = sub.add_parser(name, help=spec["help"], description=spec["help"])
        if spec["args"]:
            spec["args"](p)
        p.set_defaults(_fn=spec["fn"])
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if not getattr(ns, "_fn", None):
        parser.print_help()
        return 0
    try:
        return int(ns._fn(ns) or 0)
    except Exception as exc:  # friendly one-line errors; keys never appear in messages
        from adeditor import keys, media, paths

        if os.environ.get("ADEDITOR_DEBUG", "").strip() not in ("", "0"):
            traceback.print_exc()
            return 1
        if isinstance(exc, paths.VideoNotFound):
            print(str(exc), file=sys.stderr)
            return 1
        if isinstance(exc, (keys.MissingKey, media.ToolError)):
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Error: {_one_line(exc)}. Set ADEDITOR_DEBUG=1 and run it again to see the details.", file=sys.stderr)
        return 1


def _one_line(exc: Exception) -> str:
    """The error on one line: our own ValueErrors are written for a person, so they read as they are;
    anything else is named as unexpected, with its type."""
    text = " ".join(str(exc).split())
    if len(text) > 300:
        text = text[:300] + "..."
    if isinstance(exc, ValueError) and text:
        return text.rstrip(".")
    return f"something we didn't expect stopped it ({type(exc).__name__}{': ' + text if text else ''})"
