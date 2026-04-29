#!/usr/bin/env python3
"""
build.py — One-command pipeline: KiCad PCB → STL files.

Usage:
    python3 build.py <path/to/file.kicad_pcb> [--config <path/to/config.json>]

Examples:
    # Without config (uses defaults — uniform 1u keys, no rotation, no tilt)
    python3 build.py kicad_input/reference-design.kicad_pcb

    # With config (per-key sizes, rotations, and case tilt)
    python3 build.py kicad_input/alice.kicad_pcb --config kicad_input/alice.json

This runs:
    1. parse_kicad.py  → output/params.json
    2. generate_case.py → output/bottom_case.stl + plate.stl
"""

import subprocess
import sys
import os


def run(cmd, description):
    print(f"\n{'='*50}")
    print(f"  {description}")
    print(f"{'='*50}")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"\nERROR: {description} failed (exit code {result.returncode})")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 build.py <path/to/file.kicad_pcb> [--config <path/to/config.json>]")
        print("")
        print("Examples:")
        print("  python3 build.py kicad_input/reference-design.kicad_pcb")
        print("  python3 build.py kicad_input/alice.kicad_pcb --config kicad_input/alice.json")
        sys.exit(1)

    pcb_path = sys.argv[1]
    if not os.path.exists(pcb_path):
        print(f"ERROR: File not found: {pcb_path}")
        sys.exit(1)

    # Optional config flag
    config_args = []
    if '--config' in sys.argv:
        idx = sys.argv.index('--config')
        if idx + 1 >= len(sys.argv):
            print("ERROR: --config flag requires a path argument")
            sys.exit(1)
        config_path = sys.argv[idx + 1]
        if not os.path.exists(config_path):
            print(f"ERROR: Config file not found: {config_path}")
            sys.exit(1)
        config_args = ['--config', config_path]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    params_json = os.path.join(script_dir, 'output', 'params.json')

    # Step 1: Parse KiCad PCB (+ optional config)
    run(
        [sys.executable, os.path.join(script_dir, 'parse_kicad.py'), pcb_path] + config_args,
        "Step 1: Parsing KiCad PCB" + (" + config" if config_args else "")
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