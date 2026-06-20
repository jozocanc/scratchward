"""Built-in drill and mobility library for the trainer.

Each entry is tagged with the strokes-gained category and/or swing-fault
it addresses, so the trainer can select drills that target a golfer's
actual leaks. The trainer phase consumes ``DRILLS``; this is the data it
selects from.

Fault tags are stable strings the swing analyzer will also emit, e.g.
"fast-tempo", "low-x-factor", "head-sway", "spine-loss".
"""

from __future__ import annotations

# Each drill: id, name, sg_categories, fault_tags, minutes, type, why.
DRILLS = [
    {
        "id": "tee-gate",
        "name": "Tee gate alignment drill",
        "sg_categories": ["off-the-tee"],
        "fault_tags": ["push", "pull"],
        "minutes": 15,
        "type": "range",
        "why": "Tightens start line off the tee; fewer big misses.",
    },
    {
        "id": "9-shot",
        "name": "Trackman/eye 9-window approach control",
        "sg_categories": ["approach"],
        "fault_tags": [],
        "minutes": 25,
        "type": "range",
        "why": "Builds distance + flight control for scoring irons.",
    },
    {
        "id": "ladder-wedges",
        "name": "Wedge ladder (30/50/70/90 yds)",
        "sg_categories": ["short-game", "approach"],
        "fault_tags": [],
        "minutes": 20,
        "type": "short-game",
        "why": "Dials in partial-wedge distances inside 100 — the biggest leak for most amateurs.",
    },
    {
        "id": "up-down-circle",
        "name": "Up-and-down circle (8 balls around green)",
        "sg_categories": ["short-game"],
        "fault_tags": [],
        "minutes": 20,
        "type": "short-game",
        "why": "Scrambling reps under a pass/fail target.",
    },
    {
        "id": "gate-putting",
        "name": "Gate putting (start-line) + 3/6/9 ft circle",
        "sg_categories": ["putting"],
        "fault_tags": [],
        "minutes": 20,
        "type": "putting",
        "why": "Start line + makes inside 10 ft, where strokes are won.",
    },
    {
        "id": "lag-ladder",
        "name": "Lag putting ladder (20/30/40 ft)",
        "sg_categories": ["putting"],
        "fault_tags": [],
        "minutes": 15,
        "type": "putting",
        "why": "Speed control to cut 3-putts.",
    },
    {
        "id": "metronome-tempo",
        "name": "Metronome tempo drill (~3:1)",
        "sg_categories": ["off-the-tee", "approach"],
        "fault_tags": ["fast-tempo", "slow-tempo"],
        "minutes": 10,
        "type": "range",
        "why": "Restores a repeatable backswing:downswing ratio near the 3:1 benchmark.",
    },
    {
        "id": "x-factor-turn",
        "name": "Shoulder-turn-over-stable-hips drill",
        "sg_categories": ["off-the-tee"],
        "fault_tags": ["low-x-factor"],
        "minutes": 10,
        "type": "mobility",
        "why": "Increases shoulder-hip separation at the top for more speed.",
    },
    {
        "id": "head-still-wall",
        "name": "Head-against-wall / no-sway drill",
        "sg_categories": [],
        "fault_tags": ["head-sway"],
        "minutes": 10,
        "type": "swing",
        "why": "Reduces lateral head movement address-to-impact for center contact.",
    },
    {
        "id": "spine-angle-chair",
        "name": "Spine-angle / posture retention drill",
        "sg_categories": [],
        "fault_tags": ["spine-loss", "early-extension"],
        "minutes": 10,
        "type": "swing",
        "why": "Holds spine angle through impact; fixes early extension.",
    },
]

# Mobility / warm-up exercises, selected when faults point at physical limits.
MOBILITY = [
    {
        "id": "thoracic-rotation",
        "name": "Thoracic-spine rotations",
        "fault_tags": ["low-x-factor", "spine-loss"],
        "minutes": 5,
        "why": "Unlocks upper-back rotation for a fuller, safer turn.",
    },
    {
        "id": "hip-90-90",
        "name": "90/90 hip mobility",
        "fault_tags": ["early-extension", "head-sway"],
        "minutes": 5,
        "why": "Improves hip internal rotation to stop standing up at impact.",
    },
    {
        "id": "wrist-flexbar",
        "name": "Wrist flexbar / forearm prep",
        "fault_tags": [],
        "minutes": 5,
        "why": "Warms up the wrists for club control and injury prevention.",
    },
]
