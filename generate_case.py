#!/usr/bin/env python3
"""
generate_case.py — Generate bottom case + positioning plate using CadQuery.

Reads params.json (produced by parse_kicad.py) and outputs:
  - output/bottom_case.stl  +  bottom_case.step
  - output/plate.stl        +  plate.step

Features:
  - Per-key switch cutouts with individual size and rotation
  - Tilt wedge under the bottom case (driven by `tilt_deg` in params)

Usage:
    python3 generate_case.py output/params.json
"""

import json
import math
import sys
import os

try:
    import cadquery as cq
except ImportError:
    print("ERROR: CadQuery not installed.")
    print("  Install it with:  pip install cadquery")
    sys.exit(1)


def load_params(json_path):
    with open(json_path, 'r') as f:
        return json.load(f)


def make_box(x, y, z, sx, sy, sz):
    """Box with min corner at (x, y, z) and size (sx, sy, sz)."""
    return (
        cq.Workplane("XY")
        .box(sx, sy, sz)
        .translate((x + sx/2, y + sy/2, z + sz/2))
    )


def get_bbox(wp):
    bb = wp.val().BoundingBox()
    return (round(bb.xmin, 2), round(bb.ymin, 2), round(bb.zmin, 2),
            round(bb.xmax, 2), round(bb.ymax, 2), round(bb.zmax, 2))


# ──────────────────────────────────────────────
# Bottom Case
# ──────────────────────────────────────────────

def make_flat_case(p):
    """Build the bottom case as a flat box with cavity, USB cutout, and ledges."""
    pcb_len = p['pcb_len'] + p['pcb_clearance']
    pcb_wid = p['pcb_wid'] + p['pcb_clearance']
    wall = p['wall_thick']
    bottom = p['bottom_thick']

    outer_len = pcb_len + 2 * wall
    outer_wid = pcb_wid + 2 * wall
    case_inner_z = bottom + p['pcb_thick'] + p['clear_above_pcb']
    case_total_z = case_inner_z + wall

    print(f"    Case outer: {outer_len:.2f} x {outer_wid:.2f} x {case_total_z:.2f}")

    # Main shell: outer minus cavity
    outer = make_box(0, 0, 0, outer_len, outer_wid, case_total_z)
    inner = make_box(wall, wall, bottom, pcb_len, pcb_wid, case_inner_z + wall + 1)
    case = outer.cut(inner)

    # USB cutout
    usb_w = p['usb_width']
    usb_h = p['usb_height']
    usb_x = wall + p['usb_x']
    usb_y = wall + p['usb_y']
    usb_z = bottom + p['usb_bottom_clear']
    edge = p['usb_edge']

    if edge == 'top':
        usb_cut = make_box(usb_x - usb_w/2, -0.5, usb_z, usb_w, wall + 1, usb_h)
    elif edge == 'bottom':
        usb_cut = make_box(usb_x - usb_w/2, outer_wid - wall - 0.5, usb_z, usb_w, wall + 1, usb_h)
    elif edge == 'left':
        usb_cut = make_box(-0.5, usb_y - usb_w/2, usb_z, wall + 1, usb_w, usb_h)
    else:  # 'right'
        usb_cut = make_box(outer_len - wall - 0.5, usb_y - usb_w/2, usb_z, wall + 1, usb_w, usb_h)
    case = case.cut(usb_cut)

    # PCB support ledges
    ledge_h = p['ledge_height']
    ledge_t = p['ledge_thick']
    ledge_z = bottom + p['pcb_floor_gap']
    usb_x0 = usb_x - usb_w / 2
    usb_x1 = usb_x + usb_w / 2

    # Front ledge (min-Y inner wall)
    if edge == 'top':
        if usb_x0 > wall:
            case = case.union(make_box(wall, wall, ledge_z, usb_x0 - wall, ledge_t, ledge_h))
        if usb_x1 < outer_len - wall:
            case = case.union(make_box(usb_x1, wall, ledge_z, outer_len - wall - usb_x1, ledge_t, ledge_h))
    else:
        case = case.union(make_box(wall, wall, ledge_z, pcb_len, ledge_t, ledge_h))

    # Back ledge (max-Y inner wall)
    back_y = outer_wid - wall - ledge_t
    if edge == 'bottom':
        if usb_x0 > wall:
            case = case.union(make_box(wall, back_y, ledge_z, usb_x0 - wall, ledge_t, ledge_h))
        if usb_x1 < outer_len - wall:
            case = case.union(make_box(usb_x1, back_y, ledge_z, outer_len - wall - usb_x1, ledge_t, ledge_h))
    else:
        case = case.union(make_box(wall, back_y, ledge_z, pcb_len, ledge_t, ledge_h))

    # Left side ledge (min-X)
    if edge == 'left':
        usb_y0 = usb_y - usb_w / 2
        usb_y1 = usb_y + usb_w / 2
        if usb_y0 > wall:
            case = case.union(make_box(wall, wall, ledge_z, ledge_t, usb_y0 - wall, ledge_h))
        if usb_y1 < outer_wid - wall:
            case = case.union(make_box(wall, usb_y1, ledge_z, ledge_t, outer_wid - wall - usb_y1, ledge_h))
    else:
        case = case.union(make_box(wall, wall, ledge_z, ledge_t, pcb_wid, ledge_h))

    # Right side ledge (max-X)
    right_x = outer_len - wall - ledge_t
    if edge == 'right':
        usb_y0 = usb_y - usb_w / 2
        usb_y1 = usb_y + usb_w / 2
        if usb_y0 > wall:
            case = case.union(make_box(right_x, wall, ledge_z, ledge_t, usb_y0 - wall, ledge_h))
        if usb_y1 < outer_wid - wall:
            case = case.union(make_box(right_x, usb_y1, ledge_z, ledge_t, outer_wid - wall - usb_y1, ledge_h))
    else:
        case = case.union(make_box(right_x, wall, ledge_z, ledge_t, pcb_wid, ledge_h))

    return case


def make_bottom_case(p):
    """
    Build the bottom case, optionally with a tilt wedge underneath.
    
    Tilt approach:
      1. Build the flat case at Z=[0, case_total_z]
      2. Rotate around the front-bottom edge (X axis at Y=0, Z=0) by +tilt_deg.
         This raises the back edge (max-Y) upward.
      3. Add a triangular wedge that fills under the rotated case so the
         result has a flat bottom at Z=0.
    """
    flat = make_flat_case(p)

    tilt_deg = float(p.get('tilt_deg', 0))
    if abs(tilt_deg) < 0.01:
        print(f"    Final case bbox: {get_bbox(flat)}")
        return flat

    pcb_len = p['pcb_len'] + p['pcb_clearance']
    pcb_wid = p['pcb_wid'] + p['pcb_clearance']
    wall = p['wall_thick']
    outer_len = pcb_len + 2 * wall
    outer_wid = pcb_wid + 2 * wall

    tilt_rad = math.radians(tilt_deg)
    wedge_h = outer_wid * math.tan(tilt_rad)
    print(f"    Tilt: {tilt_deg}° → back raised by {wedge_h:.2f} mm")

    # Rotate case around X axis through (0, 0, 0)
    # Positive rotation around +X axis: +Y rotates toward +Z (back goes up)
    tilted = flat.rotate((0, 0, 0), (1, 0, 0), tilt_deg)

    # Build wedge under the case
    # Profile in YZ plane: triangle (0,0) → (outer_wid, 0) → (outer_wid, wedge_h)
    # Extruded along X by outer_len
    wedge = (
        cq.Workplane("YZ")
        .polyline([(0, 0), (outer_wid, 0), (outer_wid, wedge_h)])
        .close()
        .extrude(outer_len)
    )

    full = tilted.union(wedge)
    print(f"    Final case bbox: {get_bbox(full)}")
    return full


# ──────────────────────────────────────────────
# Positioning Plate
# ──────────────────────────────────────────────

def make_plate(p):
    pcb_len = p['pcb_len'] + p['pcb_clearance']
    pcb_wid = p['pcb_wid'] + p['pcb_clearance']
    plate_t = p['plate_thick']

    plate = make_box(0, 0, 0, pcb_len, pcb_wid, plate_t)

    switches = p.get('switches')
    if not switches:
        # Legacy fallback
        key_sz = p.get('key_size', 13.97)
        for pos in p['switch_positions']:
            sx, sy = pos[0], pos[1]
            cut = make_box(sx - key_sz/2, sy - key_sz/2, -0.5,
                           key_sz, key_sz, plate_t + 1)
            plate = plate.cut(cut)
        return plate

    print(f"    Cutting {len(switches)} key holes (with per-key size + rotation)...")
    for i, sw in enumerate(switches):
        sx = sw['x']
        sy = sw['y']
        cw = sw['cutout_w']
        ch = sw['cutout_h']
        # Negate the rotation: PCB is viewed from the bottom (components face
        # down), so when we look at the top of the plate, all rotations are
        # mirrored. Flipping the sign restores the intended visual rotation.
        rot = -sw.get('rotation', 0)

        # Build a centered box, rotate around Z axis, then translate to (sx, sy)
        cut = cq.Workplane("XY").box(cw, ch, plate_t + 1)
        if abs(rot) > 0.01:
            cut = cut.rotate((0, 0, 0), (0, 0, 1), rot)
        cut = cut.translate((sx, sy, plate_t / 2))

        plate = plate.cut(cut)

        if (i + 1) % 20 == 0:
            print(f"      ... {i + 1}/{len(switches)} cutouts done")

    return plate


# ──────────────────────────────────────────────
# Export
# ──────────────────────────────────────────────

def export_model(model, name, out_dir):
    stl_path = os.path.join(out_dir, f"{name}.stl")
    step_path = os.path.join(out_dir, f"{name}.step")
    cq.exporters.export(model, stl_path, exportType="STL")
    print(f"  Exported: {stl_path}")
    cq.exporters.export(model, step_path, exportType="STEP")
    print(f"  Exported: {step_path}")


def export_svg_preview(model, name, out_dir):
    svg_path = os.path.join(out_dir, f"{name}_preview.svg")
    try:
        cq.exporters.export(model, svg_path, exportType="SVG")
        print(f"  Preview:  {svg_path}")
    except Exception as e:
        print(f"  SVG preview skipped: {e}")


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
    print("To preview in VS Code: python3 view_models.py")


if __name__ == '__main__':
    main()