"""CLI entry point for the leakage-safe build_forecast_state component."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from predict_lakes.forecast_state import build_forecast_state, write_forecast_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical", nargs="+", help="Canonical observation CSV files")
    parser.add_argument("--forecast-issue-time", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--availability-mode", choices=["observation_time_proxy", "data_available_time"], default="observation_time_proxy")
    parser.add_argument("--minimum-hours", type=int, default=18)
    args = parser.parse_args()
    state, history = build_forecast_state(args.canonical, args.forecast_issue_time, availability_mode=args.availability_mode, minimum_hours=args.minimum_hours)
    write_forecast_state(state, history, args.output_dir)
    print(state)


if __name__ == "__main__":
    main()
