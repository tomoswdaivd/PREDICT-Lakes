"""CLI entry point for verification-side observed forecast targets."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from predict_lakes.forecast_targets import build_forecast_targets, write_forecast_targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical", nargs="+", help="Canonical observation CSV files")
    parser.add_argument("--forecast-state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--daily-minimum-hours", type=int, default=18)
    parser.add_argument("--window-minimum-valid-days", type=int, default=27)
    args = parser.parse_args()
    result = build_forecast_targets(args.canonical, args.forecast_state, daily_minimum_hours=args.daily_minimum_hours, window_minimum_valid_days=args.window_minimum_valid_days)
    write_forecast_targets(result, args.output)
    print(result)


if __name__ == "__main__":
    main()
