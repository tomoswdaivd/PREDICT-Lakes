"""CLI entry point for standardising one EIDC observation CSV."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from predict_lakes.standardise import read_observations, write_canonical, write_inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_csv")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--canonical-output", required=True)
    parser.add_argument("--inventory-output", required=True)
    parser.add_argument("--data-available-time")
    args = parser.parse_args()
    records, inventory = read_observations(args.source_csv, args.dataset_id, data_available_time=args.data_available_time)
    write_canonical(records, args.canonical_output)
    write_inventory(inventory, args.inventory_output)
    print(inventory)


if __name__ == "__main__":
    main()
