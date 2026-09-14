"""CLI entry point for the ingest_observations component."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from predict_lakes.ingest import ingest_observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Local archive/file or URL")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--manifest", dest="manifest_path", required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--licence")
    args = parser.parse_args()
    print(ingest_observations(**vars(args)))


if __name__ == "__main__":
    main()
