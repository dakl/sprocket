"""Surface finishes a body or a face can have, as the viewer's PBR numbers.

A document names a preset, never raw numbers, so a later change to how a
finish looks changes every document that uses it. The mesh carries the
numbers (roughness, metalness), so the viewer needs no copy of this table.
"""

PRESETS = {
    "matte_plastic": (0.85, 0.0),
    "glossy": (0.15, 0.0),
    "anodised_aluminium": (0.45, 0.85),
    "bare_metal": (0.2, 1.0),
}
