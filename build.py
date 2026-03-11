#!/usr/bin/env python3
"""
build.py — One-command pipeline: KiCad PCB → STL files.

Usage:
    python3 build.py kicad_input/reference-design_unrouted.kicad_pcb

This runs:
    1. parse_kicad.py  → output/params.json + params.scad
    2. generate_case.py → output/bottom_case.stl + plate.stl

All output goes to the output/ directory.
"""

import subprocess
import sys
import os


def run(cmd, description):
    """Run a command, print what's happening, and exit on failure."""
    print(f"\n{'='*50}")
    print(f"  {description}")
    print(f"{'='*50}")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"\nERROR: {description} failed (exit code {result.returncode})")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 build.py <path/to/file.kicad_pcb>")
        print("")
        print("Example:")
        print("  python3 build.py kicad_input/reference-design_unrouted.kicad_pcb")
        sys.exit(1)

    pcb_path = sys.argv[1]
    if not os.path.exists(pcb_path):
        print(f"ERROR: File not found: {pcb_path}")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    params_json = os.path.join(script_dir, 'output', 'params.json')

    # Step 1: Parse KiCad PCB
    run(
        [sys.executable, os.path.join(script_dir, 'parse_kicad.py'), pcb_path],
        "Step 1: Parsing KiCad PCB file"
    )

    # Step 2: Generate case + plate
    run(
        [sys.executable, os.path.join(script_dir, 'generate_case.py'), params_json],
        "Step 2: Generating 3D models (STL + STEP)"
    )

    print(f"\n{'='*50}")
    print(f"  BUILD COMPLETE")
    print(f"{'='*50}")
    print(f"\nOutput files:")
    out_dir = os.path.join(script_dir, 'output')
    for f in sorted(os.listdir(out_dir)):
        size = os.path.getsize(os.path.join(out_dir, f))
        print(f"  {f:30s}  {size:>8,d} bytes")
    print(f"\nTo preview in VS Code: python3 view_models.py")
    print(f"To view STLs: open output/*.stl in any 3D viewer")


if __name__ == '__main__':
    main()