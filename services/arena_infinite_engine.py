"""Infinite Arena content orchestration."""

from __future__ import annotations

from . import arena_scenario_engine, arena_world_engine


def briefing(player_profile=None):
    world = arena_world_engine.current_world_state()
    scenario = arena_scenario_engine.generate_scenario("personalized", 2, player_profile, world)
    return {
        "ok": True,
        "world_headline": world["title"],
        "intelligence_briefing": world["narrative"],
        "recommended_scenario": scenario,
        "ethical_note": "Arena rewards mastery, learning, and healthy competition. No real-money trading occurs inside Arena matches.",
    }


def generate_quest(player_profile=None):
    scenario = arena_scenario_engine.generate_scenario("quest", 1, player_profile, arena_world_engine.current_world_state())
    return {
        "quest_key": f"dynamic_{scenario['scenario_key']}",
        "title": f"Train: {scenario['scenario_key'].replace('_', ' ').title()}",
        "description": scenario["prompt"],
        "reward_xp": 60,
        "scenario": scenario,
    }
