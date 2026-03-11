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
