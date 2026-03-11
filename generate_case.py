#!/usr/bin/env python3
"""
generate_case.py — Generate bottom case + positioning plate using CadQuery.

Reads params.json (produced by parse_kicad.py) and outputs:
  - output/bottom_case.stl
  - output/plate.stl
  - output/bottom_case.step  (optional, for CAD import)
  - output/plate.step

Usage:
    python3 generate_case.py output/params.json

Prerequisites:
    pip install cadquery
    
    For visualization (optional):
    pip install cadquery[ocp-vscode]   # VS Code viewer
    # or
    pip install jupyter-cadquery       # Jupyter notebook viewer
"""

import json
import sys
import os

try:
    import cadquery as cq
except ImportError:
    print("ERROR: CadQuery not installed.")
    print("  Install it with:  pip install cadquery")
    print("  For VS Code viz:  pip install cadquery ocp-vscode")
    sys.exit(1)


def load_params(json_path):
    """Load parameters from params.json."""
    with open(json_path, 'r') as f:
        return json.load(f)


# ──────────────────────────────────────────────
# Bottom Case
# ──────────────────────────────────────────────

def make_bottom_case(p):
    """
    Build the bottom case as a CadQuery solid.
    
    The case is a rectangular box with:
      - Walls on all 4 sides
      - A floor at the bottom
      - An open top
      - A USB-C cutout on the appropriate wall
      - Internal ledges to support the PCB
    """
    pcb_len = p['pcb_len'] + p['pcb_clearance']
    pcb_wid = p['pcb_wid'] + p['pcb_clearance']
    wall = p['wall_thick']
    bottom = p['bottom_thick']
    
    outer_len = pcb_len + 2 * wall
    outer_wid = pcb_wid + 2 * wall
    case_inner_z = bottom + p['pcb_thick'] + p['clear_above_pcb']
    case_total_z = case_inner_z + wall

    # ---- Main shell ----
    # Outer box minus inner cavity
    case = (
        cq.Workplane("XY")
        .box(outer_len, outer_wid, case_total_z, centered=False)
    )
    
    # Cut inner cavity (leave bottom floor intact)
    case = (
        case
        .faces(">Z")
        .workplane()
        .transformed(offset=cq.Vector(
            outer_len / 2,
            outer_wid / 2,
            0
        ))
        .rect(pcb_len, pcb_wid)
        .cutBlind(-(case_inner_z + wall))
    )
    
    # Actually, let's use a more explicit approach for clarity:
    # Rebuild using boolean operations
    outer = cq.Workplane("XY").box(outer_len, outer_wid, case_total_z, centered=False)
    
    inner = (
        cq.Workplane("XY")
        .transformed(offset=cq.Vector(wall, wall, bottom))
        .box(pcb_len, pcb_wid, case_inner_z + wall + 1, centered=False)
    )
    
    case = outer.cut(inner)

    # ---- USB-C cutout ----
    usb_w = p['usb_width']
    usb_h = p['usb_height']
    usb_x = wall + p['usb_x']  # USB center X in case coords
    usb_y = wall + p['usb_y']  # USB center Y in case coords
    usb_z = bottom + p['usb_bottom_clear']  # bottom of USB opening
    edge = p['usb_edge']
    
    # Create USB cutout block depending on which wall
    if edge == 'top':
        # USB on min-Y wall (Y=0 face)
        usb_cut = (
            cq.Workplane("XY")
            .transformed(offset=cq.Vector(usb_x - usb_w/2, -0.5, usb_z))
            .box(usb_w, wall + 1, usb_h, centered=False)
        )
    elif edge == 'bottom':
        # USB on max-Y wall
        usb_cut = (
            cq.Workplane("XY")
            .transformed(offset=cq.Vector(usb_x - usb_w/2, outer_wid - wall - 0.5, usb_z))
            .box(usb_w, wall + 1, usb_h, centered=False)
        )
    elif edge == 'left':
        # USB on min-X wall
        usb_cut = (
            cq.Workplane("XY")
            .transformed(offset=cq.Vector(-0.5, usb_y - usb_w/2, usb_z))
            .box(wall + 1, usb_w, usb_h, centered=False)
        )
    elif edge == 'right':
        # USB on max-X wall
        usb_cut = (
            cq.Workplane("XY")
            .transformed(offset=cq.Vector(outer_len - wall - 0.5, usb_y - usb_w/2, usb_z))
            .box(wall + 1, usb_w, usb_h, centered=False)
        )
    
    case = case.cut(usb_cut)

    # ---- PCB support ledges ----
    ledge_h = p['ledge_height']
    ledge_t = p['ledge_thick']
    ledge_z = bottom + p['pcb_floor_gap']
    
    # Helper: create a ledge block at given position
    def make_ledge(x, y, lx, ly):
        return (
            cq.Workplane("XY")
            .transformed(offset=cq.Vector(x, y, ledge_z))
            .box(lx, ly, ledge_h, centered=False)
        )
    
    # Front ledge (Y = wall, full width)
    case = case.union(make_ledge(wall, wall, pcb_len, ledge_t))
    
    # Back ledge (Y = outer_wid - wall - ledge_t)
    # Split around USB if USB is on the back wall (min-Y = "top" in KiCad)
    back_y = outer_wid - wall - ledge_t
    
    if edge == 'bottom':
        # Split the back ledge around USB
        usb_x0 = usb_x - usb_w / 2
        usb_x1 = usb_x + usb_w / 2
        # Left segment
        if usb_x0 > wall:
            case = case.union(make_ledge(wall, back_y, usb_x0 - wall, ledge_t))
        # Right segment
        if usb_x1 < outer_len - wall:
            case = case.union(make_ledge(usb_x1, back_y, outer_len - wall - usb_x1, ledge_t))
    else:
        case = case.union(make_ledge(wall, back_y, pcb_len, ledge_t))
    
    # Front ledge split if USB is on top (min-Y)
    if edge == 'top':
        # We already added full front ledge above — remove it and split
        # Actually let's redo: remove the full front ledge and add split
        case = case.cut(make_ledge(wall, wall, pcb_len, ledge_t))
        usb_x0 = usb_x - usb_w / 2
        usb_x1 = usb_x + usb_w / 2
        if usb_x0 > wall:
            case = case.union(make_ledge(wall, wall, usb_x0 - wall, ledge_t))
        if usb_x1 < outer_len - wall:
            case = case.union(make_ledge(usb_x1, wall, outer_len - wall - usb_x1, ledge_t))
    
    # Left side ledge
    if edge == 'left':
        # Split around USB
        usb_y0 = usb_y - usb_w / 2
        usb_y1 = usb_y + usb_w / 2
        if usb_y0 > wall:
            case = case.union(make_ledge(wall, wall, ledge_t, usb_y0 - wall))
        if usb_y1 < outer_wid - wall:
            case = case.union(make_ledge(wall, usb_y1, ledge_t, outer_wid - wall - usb_y1))
    else:
        case = case.union(make_ledge(wall, wall, ledge_t, pcb_wid))
    
    # Right side ledge
    right_x = outer_len - wall - ledge_t
    if edge == 'right':
        usb_y0 = usb_y - usb_w / 2
        usb_y1 = usb_y + usb_w / 2
        if usb_y0 > wall:
            case = case.union(make_ledge(right_x, wall, ledge_t, usb_y0 - wall))
        if usb_y1 < outer_wid - wall:
            case = case.union(make_ledge(right_x, usb_y1, ledge_t, outer_wid - wall - usb_y1))
    else:
        case = case.union(make_ledge(right_x, wall, ledge_t, pcb_wid))

    return case


# ──────────────────────────────────────────────
# Positioning Plate
# ──────────────────────────────────────────────

def make_plate(p):
    """
    Build the positioning plate as a CadQuery solid.
    
    The plate sits above the PCB and has cutouts for each key switch.
    """
    pcb_len = p['pcb_len'] + p['pcb_clearance']
    pcb_wid = p['pcb_wid'] + p['pcb_clearance']
    wall = p['wall_thick']
    plate_t = p['plate_thick']
    key_sz = p['key_size']
    
    # Plate Z position (for reference — we build it at Z=0 for simplicity,
    # it drops into the case at the right height)
    plate_z = p['bottom_thick'] + p['pcb_thick'] + p['gap_above_pcb']
    
    # Start with solid plate
    plate = (
        cq.Workplane("XY")
        .box(pcb_len, pcb_wid, plate_t, centered=False)
    )
    
    # Cut key switch holes
    for pos in p['switch_positions']:
        sx, sy = pos[0], pos[1]
        # Center the cutout on the switch position
        # switch positions are board-relative, plate matches board footprint
        cx = sx - key_sz / 2
        cy = sy - key_sz / 2
        
        key_cut = (
            cq.Workplane("XY")
            .transformed(offset=cq.Vector(cx, cy, -0.5))
            .box(key_sz, key_sz, plate_t + 1, centered=False)
        )
        plate = plate.cut(key_cut)
    
    return plate


# ──────────────────────────────────────────────
# Export & Preview helpers
# ──────────────────────────────────────────────

def export_model(model, name, out_dir):
    """Export a CadQuery model to STL and STEP."""
    stl_path = os.path.join(out_dir, f"{name}.stl")
    step_path = os.path.join(out_dir, f"{name}.step")
    
    cq.exporters.export(model, stl_path, exportType="STL")
    print(f"  Exported: {stl_path}")
    
    cq.exporters.export(model, step_path, exportType="STEP")
    print(f"  Exported: {step_path}")


def export_svg_preview(model, name, out_dir):
    """Export an SVG preview (isometric view)."""
    svg_path = os.path.join(out_dir, f"{name}_preview.svg")
    try:
        svg = cq.exporters.export(model, svg_path, exportType="SVG")
        print(f"  Preview:  {svg_path}")
    except Exception as e:
        print(f"  SVG preview skipped: {e}")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_case.py output/params.json")
        sys.exit(1)
    
    json_path = sys.argv[1]
    if not os.path.exists(json_path):
        print(f"ERROR: {json_path} not found. Run parse_kicad.py first.")
        sys.exit(1)
    
    p = load_params(json_path)
    out_dir = os.path.dirname(json_path) or '.'
    os.makedirs(out_dir, exist_ok=True)
    
    print("Building bottom case...")
    case = make_bottom_case(p)
    export_model(case, "bottom_case", out_dir)
    export_svg_preview(case, "bottom_case", out_dir)
    
    print("Building positioning plate...")
    plate = make_plate(p)
    export_model(plate, "plate", out_dir)
    export_svg_preview(plate, "plate", out_dir)
    
    print("\nDone! Files in:", out_dir)
    print("\nTo visualize in VS Code, install ocp-vscode:")
    print("  pip install ocp-vscode")
    print("Then see view_models.py for interactive preview.")


if __name__ == '__main__':
    main()
