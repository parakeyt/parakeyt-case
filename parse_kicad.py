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
    
    Strategy: classify primarily by FOOTPRINT NAME rather than reference
    designator — references can be anything (H1, SW1, S1, R1, etc.) but
    footprint names are stable across designs.
    """
    switches = []
    usb_connector = None
    mcu = None
    other = []

    # Footprint name keywords that indicate a key switch
    SWITCH_KEYWORDS = [
        'keyswitch', 'switch_mx', 'switch_choc', 'choc_v1', 'choc_v2',
        'kailh', 'cherry_mx', 'gateron', 'mx_only', 'hall', 'ah49',
        'sc59_dio',  # Project-specific: hall sensor footprint
    ]
    # Reference patterns that often indicate switches (used as a fallback)
    SWITCH_REF_PATTERNS = [r'^SW\d', r'^H\d', r'^K\d']

    # Footprint name keywords for MCU / dev boards.
    # ORDER MATTERS: more specific keywords first. Generic package names
    # like 'qfp'/'qfn' are checked LAST so they don't accidentally match
    # auxiliary chips (analog muxes, port expanders, etc.).
    MCU_KEYWORDS_PRIORITY = [
        # Specific dev boards (highest priority)
        ['rp2040', 'pro_micro', 'pro-micro', 'elite-c', 'elite_c',
         'teensy', 'arduino', 'pi-pico', 'pi_pico'],
        # MCU chip families (medium priority)
        ['stm32', 'atmega', 'esp32', 'nrf52', 'nrf51'],
        # Generic package names (lowest priority — only used if nothing else matches)
        ['qfp', 'qfn', 'tqfp', 'lqfp'],
    ]

    # First pass: collect all potential MCU candidates with their priority tier
    mcu_candidates = []  # list of (priority, fp)

    for fp in footprints:
        ref = fp['reference'].upper()
        name = fp['footprint_name'].lower()
        val = fp['value'].lower()

        # ----- Switches: name-based first, then reference fallback -----
        is_switch = any(kw in name for kw in SWITCH_KEYWORDS)
        if not is_switch:
            for pat in SWITCH_REF_PATTERNS:
                if re.match(pat, ref):
                    if 'switch' in name or 'key' in name:
                        is_switch = True
                    break

        if is_switch:
            switches.append(fp)
            continue

        # ----- USB connector (standalone) -----
        if 'usb' in name or 'usb' in val:
            usb_connector = fp
            continue

        # ----- MCU / dev board: check priority tiers -----
        matched_tier = None
        for tier_idx, kw_list in enumerate(MCU_KEYWORDS_PRIORITY):
            if any(kw in name for kw in kw_list) or any(kw in val for kw in kw_list):
                matched_tier = tier_idx
                break

        if matched_tier is not None:
            mcu_candidates.append((matched_tier, fp))
            continue

        other.append(fp)

    # Pick the MCU candidate with the highest priority (lowest tier index)
    if mcu_candidates:
        mcu_candidates.sort(key=lambda x: x[0])
        mcu = mcu_candidates[0][1]
        # Anything else that matched (lower-priority MCU keywords)
        # belongs in 'other' since we can only have one MCU
        for tier, fp in mcu_candidates[1:]:
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


def normalize_switch_angle(raw_angle):
    """
    KiCad footprint angles for switches placed on the bottom-flipped layout
    are typically 180 +/- tilt. Normalize to a tilt-from-vertical value.
    
    Examples:
        180  →   0  (straight)
        170  →  10  (tilted +10°)
        175  →   5  (tilted +5°)
       -170  → -10  (tilted -10°)
       -175  →  -5
        190  →  10  (or -10, same thing for a square cutout)
    
    Returns angle in degrees, in range [-90, 90).
    """
    a = float(raw_angle) % 360
    if a > 180:
        a -= 360
    # Now a is in [-180, 180]
    # For switches placed at "180" baseline, subtract 180 to get tilt
    if a >= 90:
        return a - 180
    elif a <= -90:
        return a + 180
    else:
        return a


def _spatial_match(config_keys, switches, origin_x, origin_y, key_unit_mm):
    """
    Fallback matcher: greedy nearest-neighbor matching by spatial position.
    Used when row-by-row matching is not applicable (e.g. curved layouts
    like Alice where keys within a "row" have different Y values).
    """
    if not config_keys:
        return [(sw, None) for sw in switches]

    config_mm = [(k['x'] * key_unit_mm, k['y'] * key_unit_mm) for k in config_keys]
    sw_rel = [(sw['x'] - origin_x, sw['y'] - origin_y) for sw in switches]

    cfg_cx = sum(p[0] for p in config_mm) / len(config_mm)
    cfg_cy = sum(p[1] for p in config_mm) / len(config_mm)
    sw_cx = sum(p[0] for p in sw_rel) / len(sw_rel)
    sw_cy = sum(p[1] for p in sw_rel) / len(sw_rel)
    dx = sw_cx - cfg_cx
    dy = sw_cy - cfg_cy
    config_aligned = [(c[0] + dx, c[1] + dy) for c in config_mm]

    matched = []
    used = set()
    for sw, swp in zip(switches, sw_rel):
        best_idx = -1
        best_d2 = float('inf')
        for i, cp in enumerate(config_aligned):
            if i in used:
                continue
            d2 = (cp[0] - swp[0]) ** 2 + (cp[1] - swp[1]) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_idx = i
        if best_idx >= 0:
            used.add(best_idx)
            matched.append((sw, config_keys[best_idx]))
        else:
            matched.append((sw, None))
    return matched


def match_config_to_switches(config_keys, switches, origin_x, origin_y, key_unit_mm=19.05):
    """
    Match config keys to PCB switches.
    
    Two strategies, picked automatically:
    
    A) **Row-by-row left-to-right** (used when both layouts have a clean
       grid with the same number of rows): handles wide multi-sensor keys
       like a 3-sensor space bar by letting wide config keys consume
       multiple consecutive PCB switches in their row.
    
    B) **Spatial nearest-neighbor** (fallback for curved/staggered layouts
       like Alice where keys in a "row" have different Y values): each
       config key claims its nearest PCB switch.
    
    Returns: list of (switch_dict, config_key_dict_or_None) tuples in the
             same order as the input switches list.
    """
    if not config_keys:
        return [(sw, None) for sw in switches]

    KEY_CUTOUT_1U = 13.97

    # ----- Group config keys into rows (by exact Y) -----
    config_rows = {}
    for k in config_keys:
        y = round(k['y'], 3)
        config_rows.setdefault(y, []).append(k)
    for y in config_rows:
        config_rows[y].sort(key=lambda k: k['x'])
    config_row_ys = sorted(config_rows.keys())

    # ----- Group PCB switches into rows (by Y proximity) -----
    sw_rel = [(i, sw, sw['x'] - origin_x, sw['y'] - origin_y)
              for i, sw in enumerate(switches)]
    pcb_rows = []
    used_indices = set()
    sw_sorted_by_y = sorted(sw_rel, key=lambda t: (t[3], t[2]))
    row_tol = key_unit_mm * 0.5
    for entry in sw_sorted_by_y:
        i, sw, x, y = entry
        if i in used_indices:
            continue
        row = [entry]
        used_indices.add(i)
        for entry2 in sw_sorted_by_y:
            i2, sw2, x2, y2 = entry2
            if i2 in used_indices:
                continue
            if abs(y2 - y) <= row_tol:
                row.append(entry2)
                used_indices.add(i2)
        row.sort(key=lambda t: t[2])
        pcb_rows.append(row)
    pcb_rows.sort(key=lambda row: row[0][3])

    # ----- Pick strategy based on row count alignment -----
    # Row-by-row matching only works if config row count matches PCB row count.
    # Otherwise (e.g. Alice with many fine-grained Y positions), fall back to
    # spatial matching.
    if len(config_row_ys) != len(pcb_rows):
        return _spatial_match(config_keys, switches, origin_x, origin_y, key_unit_mm)

    # ----- Row-by-row left-to-right matching -----
    sw_to_cfg = [None] * len(switches)
    for ri in range(len(config_row_ys)):
        cfg_row = config_rows[config_row_ys[ri]]
        pcb_row = pcb_rows[ri]

        cfg_idx = 0
        pcb_idx = 0
        while cfg_idx < len(cfg_row) and pcb_idx < len(pcb_row):
            cfg_key = cfg_row[cfg_idx]
            size_u = float(cfg_key.get('size', 1.0))
            cw = KEY_CUTOUT_1U + (size_u - 1.0) * key_unit_mm

            primary_entry = pcb_row[pcb_idx]
            primary_si = primary_entry[0]
            primary_x = primary_entry[2]
            sw_to_cfg[primary_si] = id(cfg_key)
            pcb_idx += 1

            # Wide keys consume additional consecutive PCB switches
            # whose X falls within the keycap's reach.
            if size_u > 1.0:
                while pcb_idx < len(pcb_row):
                    candidate_entry = pcb_row[pcb_idx]
                    candidate_x = candidate_entry[2]
                    if candidate_x - primary_x > cw - 0.5 * key_unit_mm:
                        break
                    sw_to_cfg[candidate_entry[0]] = id(cfg_key)
                    pcb_idx += 1

            cfg_idx += 1

    cfg_id_to_key = {id(k): k for k in config_keys}
    matched = []
    for si, sw in enumerate(switches):
        cfg_id = sw_to_cfg[si]
        if cfg_id is None:
            matched.append((sw, None))
        else:
            matched.append((sw, cfg_id_to_key[cfg_id]))
    return matched


def compute_params(bbox, switches, usb_connector, mcu, tree=None,
                   config=None, board_origin_mode='bbox'):
    """
    Compute all the parameters needed for case/plate generation.
    
    KiCad uses absolute coordinates, but the case starts at (0,0).
    We translate everything relative to the board's bounding box origin.
    
    If a config dict is provided (parsed config.json), per-key sizes and
    rotations are matched to PCB switches by spatial proximity.
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

    # Case parameters (design choices, not from PCB)
    params['wall_thick'] = 2.0
    params['bottom_thick'] = 2.0
    params['clear_above_pcb'] = 5.0
    params['plate_thick'] = 2.0
    params['gap_above_pcb'] = 0.5
    params['pcb_clearance'] = 1.0
    params['pcb_floor_gap'] = 0.5

    # Ledge parameters
    params['ledge_height'] = 2.0
    params['ledge_thick'] = 2.5

    # Tilt (from config) — degrees of upward tilt at the back of the case
    params['tilt_deg'] = float(config.get('tilt', 0)) if config else 0.0

    # Key unit and standard cutout size
    KEY_UNIT_MM = 19.05  # standard 1u spacing
    KEY_CUTOUT_1U = 13.97  # standard MX switch hole
    params['key_unit_mm'] = KEY_UNIT_MM
    params['key_size'] = KEY_CUTOUT_1U

    # ----- Per-switch data: position, rotation, size -----
    sorted_switches = sorted(switches, key=lambda s: (s['y'], s['x']))

    # Match config keys (if available) to PCB switches.
    # The matcher allows wide config keys (>1u) to claim multiple PCB
    # switches that fall within their keycap area (e.g. 3-sensor space bar).
    config_keys = config.get('keys', []) if config else []
    matches = match_config_to_switches(config_keys, sorted_switches,
                                       origin_x, origin_y, KEY_UNIT_MM)

    # Group matches by config key identity. Multiple PCB switches can map
    # to the same config key (wide keys with multiple sensors); we emit
    # ONE cutout per config key, centered on the centroid of its sensors.
    # PCB switches with no config match get their own 1u cutout.
    seen_cfg_ids = {}  # id(cfg_key) -> index in switch_data
    switch_data = []

    for sw, cfg_key in matches:
        sx_abs = sw['x']
        sy_abs = sw['y']
        rot_pcb = normalize_switch_angle(sw.get('angle', 0))

        if cfg_key is not None:
            cfg_id = id(cfg_key)
            if cfg_id in seen_cfg_ids:
                # This is a secondary sensor for an already-emitted cutout.
                # Pull the existing entry's center toward this sensor's
                # position so the cutout ends up at the centroid.
                idx = seen_cfg_ids[cfg_id]
                entry = switch_data[idx]
                entry['_sensor_xs'].append(sx_abs)
                entry['_sensor_ys'].append(sy_abs)
                entry['x'] = round(sum(entry['_sensor_xs']) / len(entry['_sensor_xs']) - origin_x, 4)
                entry['y'] = round(sum(entry['_sensor_ys']) / len(entry['_sensor_ys']) - origin_y, 4)
                continue

            size_u = float(cfg_key.get('size', 1.0))
            label = cfg_key.get('label', '')
            cutout_w = KEY_CUTOUT_1U + (size_u - 1.0) * KEY_UNIT_MM
            cutout_h = KEY_CUTOUT_1U
            cfg_rot = float(cfg_key.get('rotation', 0))
            rot = rot_pcb if abs(rot_pcb) > 0.01 else cfg_rot
        else:
            size_u = 1.0
            label = ''
            cutout_w = KEY_CUTOUT_1U
            cutout_h = KEY_CUTOUT_1U
            rot = rot_pcb

        entry = {
            'x': round(sx_abs - origin_x, 4),
            'y': round(sy_abs - origin_y, 4),
            'rotation': round(rot, 4),
            'size_u': round(size_u, 4),
            'cutout_w': round(cutout_w, 4),
            'cutout_h': round(cutout_h, 4),
            'label': label,
            '_sensor_xs': [sx_abs],
            '_sensor_ys': [sy_abs],
        }
        if cfg_key is not None:
            seen_cfg_ids[id(cfg_key)] = len(switch_data)
        switch_data.append(entry)

    # Strip internal tracking fields before returning
    for entry in switch_data:
        entry.pop('_sensor_xs', None)
        entry.pop('_sensor_ys', None)

    params['switches'] = switch_data
    # Legacy field for backward compatibility (just positions)
    params['switch_positions'] = [[s['x'], s['y']] for s in switch_data]

    # Detect grid layout (only meaningful for uniform grids)
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
        print("Usage: python3 parse_kicad.py <path/to/file.kicad_pcb> [options]")
        print("")
        print("Options:")
        print("  --dump            Print all footprints (use this first to identify")
        print("                    your switch/USB/MCU footprint names)")
        print("  --config <path>   Path to a config.json file with per-key sizes,")
        print("                    rotations, and case options like tilt")
        sys.exit(1)

    pcb_path = sys.argv[1]
    dump_mode = '--dump' in sys.argv

    # Parse --config flag
    config = None
    config_path = None
    if '--config' in sys.argv:
        idx = sys.argv.index('--config')
        if idx + 1 < len(sys.argv):
            config_path = sys.argv[idx + 1]
            if not os.path.exists(config_path):
                print(f"ERROR: Config file not found: {config_path}")
                sys.exit(1)
            with open(config_path, 'r') as f:
                config = json.load(f)
            print(f"Using config: {config_path}")

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
        for s in switches[:10]:
            print(f"    {s['reference']}: {s['footprint_name']} at "
                  f"({s['x']:.3f}, {s['y']:.3f}) angle={s['angle']}")
        if len(switches) > 10:
            print(f"    ... and {len(switches) - 10} more")
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
    if config:
        print(f"Config:    {len(config.get('keys', []))} keys, "
              f"tilt={config.get('tilt', 0)}°")

    # Compute parameters
    params = compute_params(bbox, switches, usb_connector, mcu,
                            tree=tree, config=config)

    # Output directory
    out_dir = os.path.join(os.path.dirname(pcb_path), '..', 'output')
    os.makedirs(out_dir, exist_ok=True)

    # Write JSON
    json_path = os.path.join(out_dir, 'params.json')
    with open(json_path, 'w') as f:
        json.dump(params, f, indent=2)
    print(f"  Written: {json_path}")

    print("\nDone! Run generate_case.py to build the 3D models.")


if __name__ == '__main__':
    main()