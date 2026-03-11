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
