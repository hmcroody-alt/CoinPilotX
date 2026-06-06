"""Creator AI Copilot local fallback tools."""

from __future__ import annotations

from . import ai_social_engine


def generate_captions(text="", topic=""):
    base = (text or "").strip() or "New Pulse insight"
    return [
        base[:180],
        f"What this means: {base[:140]}",
        f"Quick lesson: {base[:140]}",
    ]


def improve_hook(text=""):
    text = (text or "").strip()
    return f"Here is the signal most people miss: {text}" if text else "Here is the signal most people miss:"


def create_hashtags(text="", topic=""):
    return ai_social_engine.suggest_hashtags(text, topic)


def predict_post_performance(text="", creator_score=0, safety_score=100):
    length_bonus = 15 if 80 <= len(text or "") <= 700 else 5
    score = min(100, int(length_bonus + int(creator_score or 0) * 0.35 + int(safety_score or 100) * 0.35))
    return {"score": score, "label": "strong" if score >= 70 else "promising" if score >= 45 else "needs a sharper hook"}


def rewrite_content(text="", mode="educational"):
    text = (text or "").strip()
    if mode == "shorten":
        return text[:220]
    if mode == "viral":
        return improve_hook(text)
    if mode == "expand":
        return f"{text}\n\nWhy it matters: add one practical lesson, one risk, and one next step."
    return f"{text}\n\nEducational note: this is community learning, not financial advice."
