"""CLI entry point for logging-claim-audit."""

import argparse
import sys
from pathlib import Path

from .audit import VERSION, audit, format_human, result_json


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print("error: %s" % message, file=sys.stderr)
        raise SystemExit(1)


def build_parser():
    parser = Parser(prog="logging-claim-audit")
    parser.add_argument("path", help="project root to audit")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--ignore", action="append", default=[],
                        help="glob matched against relative paths (repeatable)")
    parser.add_argument("--fail-on", choices=["HIGH", "MEDIUM"], default="MEDIUM")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = Path(args.path)
    if not root.is_dir():
        print("error: not a directory: %s" % args.path, file=sys.stderr)
        return 1
    result = audit(root, args.ignore)
    if args.json:
        sys.stdout.write(result_json(result))
    else:
        sys.stdout.write(format_human(result))
        if args.verbose:
            for claim in result["verified_claims"]:
                for phrase in claim["phrases"]:
                    print("  claim: %s" % phrase)
    if result["status"] == "PASS":
        return 0
    if args.fail_on == "HIGH":
        if all(f["severity"] != "HIGH" for f in result["findings"]):
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
