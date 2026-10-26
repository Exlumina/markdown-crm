#!/usr/bin/env python3
"""Copy the bundled synthetic workspace into a new directory; never overwrite."""
import argparse
import shutil
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.destination.exists() or args.destination.is_symlink():
        parser.error("Destination already exists; choose a new directory.")
    shutil.copytree(Path(__file__).parent / "examples" / "demo", args.destination)
    print("Synthetic workspace created.")

if __name__ == "__main__":
    main()
