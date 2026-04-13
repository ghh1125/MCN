from __future__ import annotations

import argparse
import os
from services.config import get_settings
from workflow.interactive import run_interactive_cli


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCN interactive workflow entrypoint")
    parser.add_argument("--raw-input", default=None)
    parser.add_argument("--creator-id", default=None)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--debug-search", action="store_true")
    return parser


def _apply_runtime_flags(*, debug_search: bool) -> None:
    if debug_search:
        os.environ["SEARCH_DEBUG_SAVE_RAW"] = "true"
    get_settings.cache_clear()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _apply_runtime_flags(debug_search=args.debug_search)
    run_interactive_cli(
        raw_input=args.raw_input,
        creator_id=args.creator_id,
        platform=args.platform,
    )


if __name__ == "__main__":
    main()
