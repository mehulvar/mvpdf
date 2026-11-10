"""CLI: python -m mvpdf input.pdf [-o output.mvpdf]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compile import DEFAULT_BROWSER_MODEL, DEFAULT_MODEL, compile_file, load_embedder
from .container import ContainerError
from .insights import Embedder


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mvpdf", description="Convert a PDF (or .txt) into .mvpdf")
    parser.add_argument("input", type=Path, help="PDF or UTF-8 text file to convert")
    parser.add_argument("-o", "--output", type=Path, help="Output path (default: <input>.mvpdf)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="sentence-transformers model")
    parser.add_argument(
        "--browser-model",
        default=DEFAULT_BROWSER_MODEL,
        help="Matching Transformers.js model id the reader should load",
    )
    return parser.parse_args(argv)


def lazy_embedder(model_name: str) -> Embedder:
    """Only load the (slow) model if the document actually has text to embed."""
    loaded: list[Embedder] = []

    def embed(texts):
        if not loaded:
            print(f"Loading model {model_name} ...", file=sys.stderr)
            loaded.append(load_embedder(model_name))
        return loaded[0](texts)

    return embed


def _progress(done: int, total: int) -> None:
    print(f"\rReading pages {done}/{total}", end="" if done < total else "\n", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output or args.input.with_suffix(".mvpdf")
    try:
        result = compile_file(
            args.input,
            output=output,
            embed=lazy_embedder(args.model),
            model=args.model,
            browser_model=args.browser_model,
            progress=_progress,
        )
    except (FileNotFoundError, ValueError, ContainerError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    pages = f"{result.page_count} pages, " if result.page_count else ""
    print(f"Wrote {result.output} ({pages}{result.chunk_count} searchable paragraphs, "
          f"{result.size_bytes / 1024:.0f} KB)")
    if result.tags:
        print(f"Tags: {', '.join(result.tags)}")
    if result.summary:
        print(f"Summary: {result.summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
