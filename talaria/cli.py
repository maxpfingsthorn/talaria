from __future__ import annotations

import argparse

from talaria import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="talaria")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "version":
        print(__version__)
        return 0
    return 1
