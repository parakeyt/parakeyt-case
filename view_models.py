#!/usr/bin/env python3
"""
view_models.py — Interactive 3D preview in VS Code.

Prerequisites:
    pip install cadquery ocp-vscode

Usage:
    1. Open this file in VS Code
    2. Make sure the OCP CAD Viewer extension is installed
       (search "OCP CAD Viewer" in VS Code extensions)
    3. Open the OCP viewer panel: Ctrl+Shift+P → "OCP: Toggle Viewer"
    4. Run this script (right-click → "Run Python File" or F5)
    
The 3D models will appear in the viewer panel.
"""

import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate_case import load_params, make_bottom_case, make_plate

try:
    from ocp_vscode import show, show_object, set_defaults, Camera
    HAS_VIEWER = True
except ImportError:
    HAS_VIEWER = False
    print("ocp-vscode not installed. Install with: pip install ocp-vscode")
    print("Also install the 'OCP CAD Viewer' VS Code extension.")
    print("")
    print("Falling back to STL export only.")


def main():
    params_path = os.path.join(os.path.dirname(__file__), 'output', 'params.json')
    
    if not os.path.exists(params_path):
        print(f"ERROR: {params_path} not found.")
        print("Run: python3 parse_kicad.py kicad_input/<your_pcb>.kicad_pcb")
        sys.exit(1)
    
    p = load_params(params_path)
    
    print("Building models...")
    case = make_bottom_case(p)
    plate = make_plate(p)
    
    if HAS_VIEWER:
        set_defaults(reset_camera=Camera.RESET)

        # Show both models in the OCP viewer
        show_object(case, name="Bottom Case")

        # Position plate where it would sit in the case
        plate_z = p['bottom_thick'] + p['pcb_thick'] + p['gap_above_pcb']
        plate_positioned = plate.translate((p['wall_thick'], p['wall_thick'], plate_z))
        show_object(plate_positioned, name="Positioning Plate")
        
        print("Models displayed in OCP CAD Viewer.")
        print("Use mouse to rotate/zoom in the viewer panel.")
    else:
        print("No viewer available. Use generate_case.py to export STL/STEP files.")


if __name__ == '__main__':
    main()