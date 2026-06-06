from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = (ROOT / "services" / "pulse_ai" / "space_ai_engine.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
SCHEDULER = (ROOT / "services" / "pulse_ai" / "space_post_scheduler.py").read_text(encoding="utf-8")


def main():
    failures = []
    checks = [
        ("space strategies", "SPACE_CONTENT_STRATEGIES", ENGINE),
        ("diversity scoring", "def diversity_score", ENGINE),
        ("old body blocked", "the community that wins is not the loudest one", (ROOT / "services" / "pulse_ai" / "space_quality_guard.py").read_text(encoding="utf-8")),
        ("AI engine used by scheduler", "generate_space_post", SCHEDULER),
        ("AI metadata engine marker", "pulse_space_ai", ENGINE),
        ("AI generated status route", "Space AI generated", BOT),
    ]
    for label, needle, text in checks:
        if needle not in text:
            failures.append(f"{label} missing")
    if "return f\"\"\"Hot take for {space}" in ENGINE:
        failures.append("generic Hot take fallback still present")
    if failures:
        print("Pulse feed AI seed audit failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("Pulse feed AI seed audit passed.")


if __name__ == "__main__":
    main()
