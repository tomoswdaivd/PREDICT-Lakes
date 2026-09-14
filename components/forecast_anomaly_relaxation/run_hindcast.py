"""Run regression audit and the paired weekly anomaly-relaxation hindcast."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from predict_lakes.anomaly_relaxation_hindcast import run_relaxation_hindcast, write_relaxation_hindcast_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical", nargs="+")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--minimum-training-years", type=int, default=3)
    parser.add_argument("--minimum-climatology-years", type=int, default=3)
    parser.add_argument("--daily-minimum-hours", type=int, default=18)
    parser.add_argument("--window-minimum-valid-days", type=int, default=27)
    args = parser.parse_args()
    result = run_relaxation_hindcast(
        args.canonical, minimum_training_years=args.minimum_training_years,
        minimum_climatology_years=args.minimum_climatology_years,
        daily_minimum_hours=args.daily_minimum_hours,
        window_minimum_valid_days=args.window_minimum_valid_days,
    )
    write_relaxation_hindcast_outputs(*result, args.output_dir)
    print(f"wrote anomaly-relaxation outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
