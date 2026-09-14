"""CLI entry point for the unfitted full anomaly-persistence baseline."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from predict_lakes.anomaly_persistence import forecast_anomaly_persistence, write_anomaly_persistence_forecast


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical", nargs="+", help="Canonical observation CSV files")
    parser.add_argument("--forecast-state", required=True, help="Leakage-safe forecast-state JSON")
    parser.add_argument("--output", required=True, help="Forecast JSON output path")
    parser.add_argument("--minimum-training-years", type=int, default=3)
    parser.add_argument("--daily-minimum-hours", type=int, default=18)
    parser.add_argument("--window-minimum-valid-days", type=int, default=27)
    args = parser.parse_args()
    result = forecast_anomaly_persistence(
        args.canonical, args.forecast_state,
        minimum_training_years=args.minimum_training_years,
        daily_minimum_hours=args.daily_minimum_hours,
        window_minimum_valid_days=args.window_minimum_valid_days,
    )
    write_anomaly_persistence_forecast(result, args.output)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
