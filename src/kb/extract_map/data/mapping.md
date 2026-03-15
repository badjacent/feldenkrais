# Stage 2: MAPPING

Map the extracted lesson instructions to canonical ontology terms. You are given the output of Stage 1 (extraction) below.

## Rules

- Only map to canonical terms listed in the ontology section below.
- If no canonical term fits, output to ontology_gaps — do NOT invent canonical terms.
- confidence "low" means the mapping is plausible but uncertain — do NOT omit low-confidence mappings, flag them.
- A single teacher term may map to multiple canonical terms — this is expected and correct. Create one vocabulary_map entry per mapping.
- A single canonical term may be the target of multiple teacher terms from the same lesson — record all of them. This is vocabulary redundancy data, not an error.
- For effort_qualities: only tag a quality if the teacher explicitly cues it (e.g. "lightly", "slowly", "without force"). Do not infer effort from the movement itself.

## Canonical Ontology

### Positions
lying_supine, lying_prone, lying_side_left, lying_side_right, sitting, sitting_cross_legged, standing, kneeling, all_fours, half_kneeling, walking

### Body Segments
head, neck, cervical_spine, thoracic_spine, lumbar_spine, pelvis, sacrum, hip_joint_left, hip_joint_right, femur_left, femur_right, knee_left, knee_right, tibia_left, tibia_right, ankle_left, ankle_right, foot_left, foot_right, shoulder_girdle_left, shoulder_girdle_right, upper_arm_left, upper_arm_right, elbow_left, elbow_right, forearm_left, forearm_right, wrist_left, wrist_right, hand_left, hand_right, ribs, sternum, clavicle_left, clavicle_right, scapula_left, scapula_right, jaw, eyes, tongue, diaphragm, pelvic_floor, abdomen, chest, nose, fingers_left, fingers_right, forehead

### Connectivity Patterns (from Laban/Bartenieff)
breath — expansion/condensation through the whole body
core_distal — movement radiating from core to extremities or vice versa
head_tail — connection/opposition between head and tail (coccyx)
upper_lower — relationship between upper body and lower body
body_half — right side vs left side as units
cross_lateral — diagonal connections (right arm/left leg, etc.)

### Effort Qualities (from Laban Movement Analysis)
light, strong — Weight factor (amount of force)
direct, indirect — Space factor (spatial focus)
sustained, sudden — Time factor (urgency/pacing)
free, bound — Flow factor (continuity/control)

### Constraint Types
reduced_range, fixed_point, added_weight, asymmetric, tempo_change, eyes_closed, breath_linked, reversed_direction, passive_segment

### Attention Targets
weight_distribution, contact_surface, breath_pattern, effort_level, movement_quality, spatial_path, timing, bilateral_comparison, rest_position_change

### Exit States
increased_contact, reduced_effort, expanded_range, improved_symmetry, changed_self_image, integrated_pattern, grounded, lengthened

## Extraction output

