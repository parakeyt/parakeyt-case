#!/usr/bin/env python3
"""
parse_kicad.py — Extract PCB dimensions from a .kicad_pcb file
and write them to params.json for OpenSCAD consumption.

Usage:
    python3 parse_kicad.py kicad_input/reference-design.kicad_pcb

Output:
    output/params.json
"""

import re
import json
import sys
import os
from pathlib import Path


# ──────────────────────────────────────────────
# 1. Minimal S-expression tokenizer / parser
# ──────────────────────────────────────────────

def tokenize(text):
    """Split KiCad S-expression text into tokens."""
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in ' \t\n\r':
            i += 1
        elif c == '(':
            tokens.append('(')
            i += 1
        elif c == ')':
            tokens.append(')')
            i += 1
        elif c == '"':
            # quoted string
            j = i + 1
            while j < len(text) and text[j] != '"':
                if text[j] == '\\':
                    j += 1  # skip escaped char
                j += 1
            tokens.append(text[i+1:j])  # strip quotes
            i = j + 1
        else:
            # unquoted atom
            j = i
            while j < len(text) and text[j] not in ' \t\n\r()':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def parse_sexp(tokens, idx=0):
    """Parse tokens into nested lists. Returns (parsed, next_index)."""
    if tokens[idx] == '(':
        lst = []
        idx += 1
        while tokens[idx] != ')':
            elem, idx = parse_sexp(tokens, idx)
            lst.append(elem)
        return lst, idx + 1  # skip ')'
    else:
        return tokens[idx], idx + 1


def parse_file(filepath):
    """Read a .kicad_pcb file and return the top-level S-expression tree."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    tokens = tokenize(text)
    tree, _ = parse_sexp(tokens, 0)
    return tree


# ──────────────────────────────────────────────
# 2. Tree search helpers
# ──────────────────────────────────────────────

def find_all(tree, tag):
    """Recursively find all sub-lists whose first element == tag."""
    results = []
    if isinstance(tree, list):
        if len(tree) > 0 and tree[0] == tag:
            results.append(tree)
        for child in tree:
            results.extend(find_all(child, tag))
    return results


def find_first(tree, tag):
    """Return the first sub-list whose first element == tag, or None."""
    hits = find_all(tree, tag)
    return hits[0] if hits else None


def get_at(node):
    """Extract (x, y, angle) from an (at ...) node inside a list."""
    at = find_first(node, 'at')
    if at is None:
        return None
    x = float(at[1])
    y = float(at[2])
    angle = float(at[3]) if len(at) > 3 else 0.0
    return (x, y, angle)


# ──────────────────────────────────────────────
# 3. Extract board outline from Edge.Cuts layer
# ──────────────────────────────────────────────

def extract_board_outline(tree):
    """
    Find all gr_line / gr_rect / gr_arc on Edge.Cuts.
    Return bounding box: (min_x, min_y, max_x, max_y) and raw segments.
    """
    segments = []
    xs, ys = [], []

    for node in find_all(tree, 'gr_line'):
        layer = find_first(node, 'layer')
        if layer and layer[1] in ('Edge.Cuts', 'Edge_Cuts'):
            start = find_first(node, 'start')
            end = find_first(node, 'end')
            if start and end:
                x0, y0 = float(start[1]), float(start[2])
                x1, y1 = float(end[1]), float(end[2])
                segments.append({'type': 'line', 'start': [x0, y0], 'end': [x1, y1]})
                xs.extend([x0, x1])
                ys.extend([y0, y1])

    for node in find_all(tree, 'gr_rect'):
        layer = find_first(node, 'layer')
        if layer and layer[1] in ('Edge.Cuts', 'Edge_Cuts'):
            start = find_first(node, 'start')
            end = find_first(node, 'end')
            if start and end:
                x0, y0 = float(start[1]), float(start[2])
                x1, y1 = float(end[1]), float(end[2])
                segments.append({'type': 'rect', 'start': [x0, y0], 'end': [x1, y1]})
                xs.extend([x0, x1])
                ys.extend([y0, y1])

    for node in find_all(tree, 'gr_arc'):
        layer = find_first(node, 'layer')
        if layer and layer[1] in ('Edge.Cuts', 'Edge_Cuts'):
            start = find_first(node, 'start')
            mid = find_first(node, 'mid')
            end = find_first(node, 'end')
            if start and end:
                x0, y0 = float(start[1]), float(start[2])
                x1, y1 = float(end[1]), float(end[2])
                arc_data = {'type': 'arc', 'start': [x0, y0], 'end': [x1, y1]}
                if mid:
                    arc_data['mid'] = [float(mid[1]), float(mid[2])]
                    xs.append(float(mid[1]))
                    ys.append(float(mid[2]))
                segments.append(arc_data)
                xs.extend([x0, x1])
                ys.extend([y0, y1])

    if not xs:
        return None, segments

    bbox = {
        'min_x': min(xs),
        'min_y': min(ys),
        'max_x': max(xs),
        'max_y': max(ys),
        'width': max(xs) - min(xs),
        'height': max(ys) - min(ys),
    }
    return bbox, segments


# ──────────────────────────────────────────────
# 4. Extract footprints by reference or name
# ──────────────────────────────────────────────

def extract_footprints(tree):
    """
    Return a list of all footprint dicts:
      { reference, value, footprint_name, x, y, angle, layer }
    """
    fps = []
    for fp_node in find_all(tree, 'footprint'):
        fp_name = fp_node[1] if len(fp_node) > 1 else ''

        # Get position
        pos = get_at(fp_node)
        if pos is None:
            continue
        x, y, angle = pos

        # Get layer
        layer_node = find_first(fp_node, 'layer')
        layer = layer_node[1] if layer_node else ''

        # Get reference and value from (property ...) nodes
        reference = ''
        value = ''
        for prop in find_all(fp_node, 'property'):
            if len(prop) >= 3:
                if prop[1] == 'Reference':
                    reference = prop[2]
                elif prop[1] == 'Value':
                    value = prop[2]

        # Fallback: older KiCad uses (fp_text reference ...) instead of (property ...)
        if not reference:
            for fpt in find_all(fp_node, 'fp_text'):
                if len(fpt) >= 3 and fpt[1] == 'reference':
                    reference = fpt[2]
                elif len(fpt) >= 3 and fpt[1] == 'value':
                    if not value:
                        value = fpt[2]

        fps.append({
            'reference': reference,
            'value': value,
            'footprint_name': fp_name,
            'x': x,
            'y': y,
            'angle': angle,
            'layer': layer,
        })
    return fps


# ──────────────────────────────────────────────
# 5. Classify footprints for our keyboard use case
# ──────────────────────────────────────────────

def classify_footprints(footprints):
    """
    Separate footprints into categories:
      - switches (hall effect sensors / key switches)
      - usb_connector  (may be None if USB is on an MCU module)
      - mcu
      - other
    
    Adjust the matching patterns below to match YOUR footprint
    names / references. Run with --dump first to see what's there.
    """
    switches = []
    usb_connector = None
    mcu = None
    other = []

    for fp in footprints:
        ref = fp['reference'].upper()
        name = fp['footprint_name'].lower()
        val = fp['value'].lower()

        # ----- Hall effect switches -----
        # H1-H16 with AH49 hall sensors, or SW* references
        # Also: footprint contains 'keyswitch', 'switch', 'key', 'hall', 'mx', etc.
        is_switch = False
        if re.match(r'^H\d', ref) and ('key' in name or 'hall' in name or 'ah49' in name):
            is_switch = True
        elif re.match(r'^SW\d', ref):
            is_switch = True
        elif re.match(r'^S\d', ref) and ('switch' in name or 'key' in name or 'hall' in name
                                          or 'mx' in name or 'kailh' in name or 'cherry' in name
                                          or 'gateron' in name):
            is_switch = True
        elif 'keyswitch' in name or ('switch' in name.split(':')[-1] and 'sensor' not in name):
            is_switch = True

        if is_switch:
            switches.append(fp)
            continue

        # ----- USB connector (standalone) -----
        if 'usb' in name or 'usb' in val or (ref.startswith('J') and 'usb' in name):
            usb_connector = fp
            continue

        # ----- MCU / dev board -----
        # Matches: reference MCU*, U* with known MCU values
        # Also catches dev board modules like RP2040-Zero, Arduino, etc.
        if (ref.startswith('MCU') or ref.startswith('U')) and \
           ('mcu' in val or 'rp2040' in val or 'rp2040' in name
            or 'stm32' in val or 'atmega' in val or 'nrf' in val or 'esp32' in val
            or 'arduino' in val or 'teensy' in val
            or 'qfp' in name or 'qfn' in name or 'tqfp' in name or 'lqfp' in name):
            mcu = fp
            continue

        other.append(fp)

    return switches, usb_connector, mcu, other


# ──────────────────────────────────────────────
# 6. Compute derived parameters for case/plate
# ──────────────────────────────────────────────

def get_footprint_bounds(tree, footprints, reference):
    """
    For a given footprint reference, find its bounding box from 
    fp_rect/fp_line on silkscreen or courtyard layers.
    Returns (local_min_x, local_min_y, local_max_x, local_max_y) relative to footprint center.
    """
    for fp_node in find_all(tree, 'footprint'):
        # Check reference
        ref = ''
        for prop in find_all(fp_node, 'property'):
            if len(prop) >= 3 and prop[1] == 'Reference':
                ref = prop[2]
        if not ref:
            for fpt in find_all(fp_node, 'fp_text'):
                if len(fpt) >= 3 and fpt[1] == 'reference':
                    ref = fpt[2]
        
        if ref != reference:
            continue
        
        # Found the footprint — gather all rectangles and lines
        xs, ys = [], []
        for rect in find_all(fp_node, 'fp_rect'):
            start = find_first(rect, 'start')
            end = find_first(rect, 'end')
            if start and end:
                xs.extend([float(start[1]), float(end[1])])
                ys.extend([float(start[2]), float(end[2])])
        
        for line in find_all(fp_node, 'fp_line'):
            start = find_first(line, 'start')
            end = find_first(line, 'end')
            if start and end:
                xs.extend([float(start[1]), float(end[1])])
                ys.extend([float(start[2]), float(end[2])])
        
        if xs and ys:
            return (min(xs), min(ys), max(xs), max(ys))
    
    return None


# Known USB-C port offsets for common dev board modules.
# These are relative to the module's center (at position).
# Format: { 'keyword': (dx, dy, which_edge_of_module) }
# dy negative = toward min-Y edge of footprint (KiCad "top")
MCU_MODULE_USB_OFFSETS = {
    'rp2040-zero': {
        'dx': 0.0,        # USB centered on module X
        'dy': -12.0,      # USB at the top edge of the module (min Y)
        'module_edge': 'min_y',  # which edge of the module the USB is on
    },
    # Add more dev boards here as needed:
    # 'pro-micro': { 'dx': 0.0, 'dy': ..., 'module_edge': ... },
}


def compute_params(bbox, switches, usb_connector, mcu, tree=None, board_origin_mode='bbox'):
    """
    Compute all the parameters needed for OpenSCAD.
    
    KiCad uses absolute coordinates, but OpenSCAD case starts at (0,0).
    We translate everything relative to the board's bounding box origin.
    """
    if bbox is None:
        print("ERROR: No board outline found on Edge.Cuts layer!")
        sys.exit(1)

    origin_x = bbox['min_x']
    origin_y = bbox['min_y']

    params = {}

    # Board dimensions
    params['pcb_len'] = round(bbox['width'], 4)
    params['pcb_wid'] = round(bbox['height'], 4)
    params['pcb_thick'] = 1.6  # standard, could parse from stackup if present

    # Case parameters (these are design choices, not from PCB)
    params['wall_thick'] = 2.0
    params['bottom_thick'] = 2.0
    params['clear_above_pcb'] = 5.0
    params['plate_thick'] = 2.0
    params['gap_above_pcb'] = 0.5
    params['pcb_clearance'] = 1.0  # extra room around PCB in case
    params['pcb_floor_gap'] = 0.5

    # Ledge parameters
    params['ledge_height'] = 2.0
    params['ledge_thick'] = 2.5

    # Key switch positions (translated to board-relative coords)
    params['key_size'] = 13.970  # standard MX switch cutout
    switch_positions = []
    for sw in sorted(switches, key=lambda s: (s['y'], s['x'])):
        switch_positions.append([
            round(sw['x'] - origin_x, 4),
            round(sw['y'] - origin_y, 4)
        ])
    params['switch_positions'] = switch_positions

    # Detect grid layout
    if len(switches) > 0:
        unique_x = sorted(set(round(s['x'] - origin_x, 2) for s in switches))
        unique_y = sorted(set(round(s['y'] - origin_y, 2) for s in switches))
        params['grid_cols'] = len(unique_x)
        params['grid_rows'] = len(unique_y)
        if len(unique_x) > 1:
            params['key_pitch_x'] = round(unique_x[1] - unique_x[0], 4)
        if len(unique_y) > 1:
            params['key_pitch_y'] = round(unique_y[1] - unique_y[0], 4)

    # ----- USB connector position -----
    usb_resolved = False

    if usb_connector:
        # Standalone USB connector footprint
        usb_x_rel = round(usb_connector['x'] - origin_x, 4)
        usb_y_rel = round(usb_connector['y'] - origin_y, 4)
        params['usb_x'] = usb_x_rel
        params['usb_y'] = usb_y_rel
        params['usb_angle'] = usb_connector['angle']
        usb_resolved = True
    elif mcu:
        # USB is built into the MCU module — look up known offsets
        mcu_name_lower = mcu['footprint_name'].lower()
        matched_module = None
        for keyword, offset_data in MCU_MODULE_USB_OFFSETS.items():
            if keyword in mcu_name_lower or keyword in mcu['value'].lower():
                matched_module = offset_data
                break
        
        if matched_module:
            usb_abs_x = mcu['x'] + matched_module['dx']
            usb_abs_y = mcu['y'] + matched_module['dy']
            params['usb_x'] = round(usb_abs_x - origin_x, 4)
            params['usb_y'] = round(usb_abs_y - origin_y, 4)
            params['usb_angle'] = 0
            usb_resolved = True
            print(f"  USB derived from MCU module ({mcu['footprint_name']})")
            print(f"  USB position (board-relative): ({params['usb_x']}, {params['usb_y']})")
        else:
            print(f"WARNING: MCU module '{mcu['footprint_name']}' not in known USB offset table.")
            print(f"  Add it to MCU_MODULE_USB_OFFSETS in parse_kicad.py")
            print(f"  MCU center is at ({mcu['x']}, {mcu['y']})")

    if usb_resolved:
        # Determine which edge the USB is on
        ux = params['usb_x']
        uy = params['usb_y']
        dist_to_top    = uy                      # min Y edge
        dist_to_bottom = bbox['height'] - uy     # max Y edge
        dist_to_left   = ux                      # min X edge
        dist_to_right  = bbox['width'] - ux      # max X edge
        min_dist = min(dist_to_top, dist_to_bottom, dist_to_left, dist_to_right)
        if min_dist == dist_to_top:
            params['usb_edge'] = 'top'    # min Y in KiCad
        elif min_dist == dist_to_bottom:
            params['usb_edge'] = 'bottom'
        elif min_dist == dist_to_left:
            params['usb_edge'] = 'left'
        else:
            params['usb_edge'] = 'right'
    else:
        print("WARNING: No USB connector found. Using defaults.")
        params['usb_x'] = 0
        params['usb_y'] = 0
        params['usb_edge'] = 'top'

    # USB opening dimensions (standard USB-C)
    params['usb_width'] = 9.0
    params['usb_height'] = 4.0
    params['usb_bottom_clear'] = 0.8

    # MCU position and bounding box (for plate cutout if needed)
    if mcu:
        params['mcu_x'] = round(mcu['x'] - origin_x, 4)
        params['mcu_y'] = round(mcu['y'] - origin_y, 4)
        params['mcu_footprint'] = mcu['footprint_name']
        
        # Try to get bounding box from footprint geometry
        if tree:
            bounds = get_footprint_bounds(tree, [mcu], mcu['reference'])
            if bounds:
                # bounds are (local_min_x, local_min_y, local_max_x, local_max_y)
                params['mcu_local_bounds'] = list(bounds)
                params['mcu_width'] = round(bounds[2] - bounds[0], 4)
                params['mcu_height'] = round(bounds[3] - bounds[1], 4)
                # Absolute board-relative bounding box
                params['mcu_bbox'] = [
                    round(mcu['x'] + bounds[0] - origin_x, 4),
                    round(mcu['y'] + bounds[1] - origin_y, 4),
                    round(mcu['x'] + bounds[2] - origin_x, 4),
                    round(mcu['y'] + bounds[3] - origin_y, 4),
                ]
    else:
        print("WARNING: No MCU footprint found.")
        params['mcu_x'] = None
        params['mcu_y'] = None

    # Board origin (for reference)
    params['_board_origin_kicad'] = [origin_x, origin_y]
    params['_board_bbox_kicad'] = bbox

    return params


# ──────────────────────────────────────────────
# 7. Generate OpenSCAD files from params
# ──────────────────────────────────────────────

def generate_scad_params_file(params, output_path):
    """Write a params.scad file that OpenSCAD can `include`."""
    lines = []
    lines.append("// ============================================")
    lines.append("// AUTO-GENERATED — do not edit by hand")
    lines.append("// Generated by parse_kicad.py")
    lines.append("// ============================================")
    lines.append("")

    lines.append("// ----- Board dimensions -----")
    lines.append(f"pcb_len      = {params['pcb_len']};")
    lines.append(f"pcb_wid      = {params['pcb_wid']};")
    lines.append(f"pcb_thick    = {params['pcb_thick']};")
    lines.append("")

    lines.append("// ----- Case design parameters -----")
    lines.append(f"wall_thick   = {params['wall_thick']};")
    lines.append(f"bottom_thick = {params['bottom_thick']};")
    lines.append(f"clear_above_pcb = {params['clear_above_pcb']};")
    lines.append(f"pcb_clearance = {params['pcb_clearance']};")
    lines.append(f"pcb_floor_gap = {params['pcb_floor_gap']};")
    lines.append("")

    lines.append("// ----- Plate parameters -----")
    lines.append(f"plate_thick  = {params['plate_thick']};")
    lines.append(f"gap_above_pcb = {params['gap_above_pcb']};")
    lines.append("")

    lines.append("// ----- Ledge parameters -----")
    lines.append(f"ledge_height = {params['ledge_height']};")
    lines.append(f"ledge_thick  = {params['ledge_thick']};")
    lines.append("")

    lines.append("// ----- Key switch parameters -----")
    lines.append(f"key_size     = {params['key_size']};")
    if 'key_pitch_x' in params:
        lines.append(f"key_pitch_x  = {params['key_pitch_x']};")
    if 'key_pitch_y' in params:
        lines.append(f"key_pitch_y  = {params['key_pitch_y']};")
    if 'grid_cols' in params:
        lines.append(f"grid_cols    = {params['grid_cols']};")
        lines.append(f"grid_rows    = {params['grid_rows']};")
    lines.append("")

    # Switch positions as OpenSCAD array
    lines.append("// Switch center positions (board-relative, mm)")
    lines.append("switch_positions = [")
    for pos in params['switch_positions']:
        lines.append(f"    [{pos[0]}, {pos[1]}],")
    lines.append("];")
    lines.append("")

    lines.append("// ----- USB connector -----")
    lines.append(f"usb_x        = {params['usb_x']};")
    lines.append(f"usb_y        = {params['usb_y']};")
    lines.append(f'usb_edge     = "{params["usb_edge"]}";')
    lines.append(f"usb_width    = {params['usb_width']};")
    lines.append(f"usb_height   = {params['usb_height']};")
    lines.append(f"usb_bottom_clear = {params['usb_bottom_clear']};")
    lines.append("")

    if params.get('mcu_x') is not None:
        lines.append("// ----- MCU -----")
        lines.append(f"mcu_x        = {params['mcu_x']};")
        lines.append(f"mcu_y        = {params['mcu_y']};")
        if 'mcu_width' in params:
            lines.append(f"mcu_width    = {params['mcu_width']};")
            lines.append(f"mcu_height   = {params['mcu_height']};")
        if 'mcu_bbox' in params:
            bb = params['mcu_bbox']
            lines.append(f"// MCU bounding box (board-relative): [{bb[0]}, {bb[1]}] to [{bb[2]}, {bb[3]}]")
            lines.append(f"mcu_bbox_x0  = {bb[0]};")
            lines.append(f"mcu_bbox_y0  = {bb[1]};")
            lines.append(f"mcu_bbox_x1  = {bb[2]};")
            lines.append(f"mcu_bbox_y1  = {bb[3]};")
        lines.append("")

    with open(output_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"  Written: {output_path}")


def generate_case_scad(output_path):
    """Generate the bottom case OpenSCAD file."""
    content = '''\
// ============================================
// Bottom case — auto-generated
// include the params file first
// ============================================
include <params.scad>

// ----- Derived -----
pcb_len_clearance = pcb_len + pcb_clearance;
pcb_wid_clearance = pcb_wid + pcb_clearance;

outer_len    = pcb_len_clearance + 2*wall_thick;
outer_wid    = pcb_wid_clearance + 2*wall_thick;
case_inner_z = bottom_thick + pcb_thick + clear_above_pcb;
case_total_z = case_inner_z + wall_thick;

// PCB-support ledge Z
ledge_z0 = bottom_thick + pcb_floor_gap;

// USB position in case coordinates
// USB is specified relative to board origin; we add wall_thick offset
usb_case_x = wall_thick + usb_x;
usb_case_y = wall_thick + usb_y;

// USB cutout extents
usb_x0 = usb_case_x - usb_width/2;
usb_x1 = usb_case_x + usb_width/2;

// Z position for USB opening
usb_bottom_z = bottom_thick + usb_bottom_clear;

module bottom_case() {
    difference() {
        // outer shell
        cube([outer_len, outer_wid, case_total_z]);

        // inner cavity
        translate([wall_thick - 0.1, wall_thick - 0.1, bottom_thick - 0.1])
            cube([pcb_len_clearance + 0.2, pcb_wid_clearance + 0.2,
                  case_inner_z + wall_thick + 0.2]);

        // USB-C cutout — placed on the correct edge
        if (usb_edge == "top") {
            // top = min Y in KiCad = +Y wall in OpenSCAD (if not flipped)
            // Adjust based on your coordinate convention
            translate([usb_x0, outer_wid - wall_thick - 0.2, usb_bottom_z])
                cube([usb_width, wall_thick + 0.4, usb_height]);
        }
        if (usb_edge == "bottom") {
            translate([usb_x0, -0.2, usb_bottom_z])
                cube([usb_width, wall_thick + 0.4, usb_height]);
        }
        if (usb_edge == "left") {
            translate([-0.2, usb_case_y - usb_width/2, usb_bottom_z])
                cube([wall_thick + 0.4, usb_width, usb_height]);
        }
        if (usb_edge == "right") {
            translate([outer_len - wall_thick - 0.2, usb_case_y - usb_width/2, usb_bottom_z])
                cube([wall_thick + 0.4, usb_width, usb_height]);
        }
    }

    // ----- PCB support ledge -----
    // front ledge (-Y)
    translate([wall_thick, wall_thick, ledge_z0])
        cube([pcb_len_clearance, ledge_thick, ledge_height]);

    // back ledge (+Y) — split around USB if USB is on this edge
    if (usb_edge == "top") {
        // left segment
        translate([wall_thick, outer_wid - wall_thick - ledge_thick, ledge_z0])
            cube([usb_x0 - wall_thick, ledge_thick, ledge_height]);
        // right segment
        translate([usb_x1, outer_wid - wall_thick - ledge_thick, ledge_z0])
            cube([outer_len - wall_thick - usb_x1, ledge_thick, ledge_height]);
    } else {
        translate([wall_thick, outer_wid - wall_thick - ledge_thick, ledge_z0])
            cube([pcb_len_clearance, ledge_thick, ledge_height]);
    }

    // left side ledge
    translate([wall_thick, wall_thick, ledge_z0])
        cube([ledge_thick, pcb_wid_clearance, ledge_height]);

    // right side ledge
    translate([outer_len - wall_thick - ledge_thick, wall_thick, ledge_z0])
        cube([ledge_thick, pcb_wid_clearance, ledge_height]);
}

bottom_case();
'''
    with open(output_path, 'w') as f:
        f.write(content)
    print(f"  Written: {output_path}")


def generate_plate_scad(output_path):
    """Generate the positioning plate OpenSCAD file."""
    content = '''\
// ============================================
// Positioning plate — auto-generated
// ============================================
include <params.scad>

// ----- Derived -----
pcb_len_clearance = pcb_len + pcb_clearance;
pcb_wid_clearance = pcb_wid + pcb_clearance;

offset_x = wall_thick;
offset_y = wall_thick;

// PCB top Z in the case
pcb_top_z = bottom_thick + pcb_thick;

// Bottom of plate
plate_bottom_z = pcb_top_z + gap_above_pcb;

// ----- Model -----
difference() {
    // Solid plate matching PCB footprint
    translate([offset_x, offset_y, plate_bottom_z])
        cube([pcb_len_clearance, pcb_wid_clearance, plate_thick]);

    // Key switch cutouts
    for (p = switch_positions) {
        sx = p[0];
        sy = p[1];
        // center the cutout on the switch position
        cx = offset_x + sx - key_size/2;
        cy = offset_y + sy - key_size/2;
        translate([cx, cy, plate_bottom_z - 0.1])
            cube([key_size, key_size, plate_thick + 0.2]);
    }
}
'''
    with open(output_path, 'w') as f:
        f.write(content)
    print(f"  Written: {output_path}")


# ──────────────────────────────────────────────
# 8. Main
# ──────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 parse_kicad.py <path/to/file.kicad_pcb> [--dump]")
        print("")
        print("Options:")
        print("  --dump    Print all footprints (use this first to identify")
        print("            your switch/USB/MCU footprint names)")
        sys.exit(1)

    pcb_path = sys.argv[1]
    dump_mode = '--dump' in sys.argv

    if not os.path.exists(pcb_path):
        print(f"ERROR: File not found: {pcb_path}")
        sys.exit(1)

    print(f"Parsing: {pcb_path}")
    tree = parse_file(pcb_path)

    # Extract everything
    bbox, segments = extract_board_outline(tree)
    footprints = extract_footprints(tree)

    if dump_mode:
        print(f"\n{'='*60}")
        print(f"BOARD OUTLINE (Edge.Cuts)")
        print(f"{'='*60}")
        if bbox:
            print(f"  Bounding box: {bbox['width']:.2f} x {bbox['height']:.2f} mm")
            print(f"  Min corner:   ({bbox['min_x']:.2f}, {bbox['min_y']:.2f})")
            print(f"  Max corner:   ({bbox['max_x']:.2f}, {bbox['max_y']:.2f})")
            print(f"  Segments:     {len(segments)}")
        else:
            print("  No Edge.Cuts geometry found!")

        print(f"\n{'='*60}")
        print(f"ALL FOOTPRINTS ({len(footprints)} total)")
        print(f"{'='*60}")
        for fp in sorted(footprints, key=lambda f: f['reference']):
            print(f"  {fp['reference']:>8s}  |  {fp['footprint_name']:<45s}  |  "
                  f"({fp['x']:8.3f}, {fp['y']:8.3f})  angle={fp['angle']}  "
                  f"layer={fp['layer']}  value={fp['value']}")

        print(f"\n{'='*60}")
        print("CLASSIFICATION ATTEMPT")
        print(f"{'='*60}")
        switches, usb, mcu, other = classify_footprints(footprints)
        print(f"  Switches found: {len(switches)}")
        for s in switches:
            print(f"    {s['reference']}: {s['footprint_name']} at ({s['x']:.3f}, {s['y']:.3f})")
        print(f"  USB connector: {usb['reference'] if usb else 'NOT FOUND'}")
        print(f"  MCU:           {mcu['reference'] if mcu else 'NOT FOUND'}")
        print(f"  Other:         {len(other)} components")

        print("\n--> If classification is wrong, edit classify_footprints() patterns")
        return

    # Classify
    switches, usb_connector, mcu, other = classify_footprints(footprints)

    print(f"\nBoard:     {bbox['width']:.2f} x {bbox['height']:.2f} mm")
    print(f"Switches:  {len(switches)}")
    print(f"USB:       {'found' if usb_connector else 'NOT FOUND'}")
    print(f"MCU:       {'found' if mcu else 'NOT FOUND'}")

    # Compute parameters
    params = compute_params(bbox, switches, usb_connector, mcu, tree=tree)

    # Output directory
    out_dir = os.path.join(os.path.dirname(pcb_path), '..', 'output')
    os.makedirs(out_dir, exist_ok=True)

    # Write JSON (for reference / debugging)
    json_path = os.path.join(out_dir, 'params.json')
    with open(json_path, 'w') as f:
        json.dump(params, f, indent=2)
    print(f"  Written: {json_path}")

    # Write OpenSCAD files
    generate_scad_params_file(params, os.path.join(out_dir, 'params.scad'))
    generate_case_scad(os.path.join(out_dir, 'bottom_case.scad'))
    generate_plate_scad(os.path.join(out_dir, 'plate.scad'))

    print("\nDone! Open output/bottom_case.scad or output/plate.scad in OpenSCAD.")


if __name__ == '__main__':
    main()