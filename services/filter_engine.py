"""Creator filter catalog and lightweight filter plans."""

from __future__ import annotations


FILTERS = {
    "Founder Gold": ["warm_glow", "gold_frame", "soft_contrast"],
    "PulseSoc Neon": ["cyan_glow", "contrast", "vibrance"],
    "Cyber Glow": ["edge_light", "cool_tint", "clarity"],
    "AI Vision": ["sharpness", "clean_highlights", "blue_shift"],
    "Prestige Glass": ["soft_blur_bg", "glass_frame", "skin_balance"],
    "Market Heat": ["warm_gradient", "dynamic_contrast"],
    "Midnight Elite": ["low_light", "noise_reduction", "deep_shadow"],
    "Creator Studio": ["portrait_balance", "clarity", "studio_frame"],
    "Arena Fire": ["red_orange_glow", "motion_energy"],
}


def filter_plan(name="PulseSoc Neon"):
    name = name if name in FILTERS else "PulseSoc Neon"
    return {"name": name, "steps": FILTERS[name], "preview_required": True, "preserve_original": True}
