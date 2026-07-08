"""M1 CLI: run one source file in a fresh sandbox and print the JSON result.

Usage: python3 -m app.cli <language> <source-file> [--stdin TEXT] [--timeout S]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from dataclasses import replace

from .executor.limits import DEFAULT_LIMITS
from .executor.runner import ExecutionRequest, execute, sweep_orphans
from .languages.registry import get_language, resolve


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("language")
    p.add_argument("source")
    p.add_argument("--stdin", default="")
    p.add_argument("--timeout", type=float, default=None)
    args = p.parse_args()

    lang = get_language(args.language)
    code = Path(args.source).read_text()
    base = replace(DEFAULT_LIMITS, wall_timeout_s=args.timeout) if args.timeout \
        else DEFAULT_LIMITS
    p = resolve(lang, base)
    req = ExecutionRequest(
        args=p.run_args,
        files={lang.main_file: code},
        stdin=args.stdin,
        timeout_s=args.timeout,
        limits=p.run_limits,
        compile_args=p.compile_args,
        compile_limits=p.compile_limits,
        env=p.run_env or None,
        compile_env=p.compile_env or None,
        compile_cache=p.compile_cache,
    )
    sweep_orphans()
    result = asyncio.run(execute(req))
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.error is None else 1


if __name__ == "__main__":
    sys.exit(main())
