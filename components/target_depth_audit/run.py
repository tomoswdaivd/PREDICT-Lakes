"""CLI entry point for the 1 m versus 2 m target-depth audit."""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from predict_lakes.target_audit import audit_target, read_target_series, write_audit_outputs, write_audit_plot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical", nargs="+", help="Canonical observation CSV files")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--plot", required=True)
    parser.add_argument("--start", default="2008-01-01T01:00:00+00:00")
    parser.add_argument("--end", default="2016-01-01T00:00:00+00:00")
    parser.add_argument("--minimum-hours", type=int, default=18)
    args = parser.parse_args()
    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    series = read_target_series(args.canonical, start=start, end=end)
    result = audit_target(series, expected_start=start, expected_end=end, minimum_hours=args.minimum_hours)
    write_audit_outputs(result, args.output_dir)
    write_audit_plot(result, args.plot)
    print(result["summary"])


if __name__ == "__main__":
    main()
