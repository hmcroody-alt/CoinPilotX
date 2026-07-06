#!/usr/bin/env python3
"""Audit the PulseSoc Native Courses + Learning QA hardening pass."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    required_files = [
        "mobile-native/src/screens/CoursesLearningScreen.tsx",
        "reports/pulsesoc_native_courses_learning_qa.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    screen = read("mobile-native/src/screens/CoursesLearningScreen.tsx")
    qa_report = read("reports/pulsesoc_native_courses_learning_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "progressMessage",
        "Saving progress...",
        "setProgressMessage(message)",
        "Quiz progress saved",
    ]:
        require(token in screen or token in qa_report, f"missing progress feedback token {token}", failures)

    for token in [
        "/pulse/courses",
        "/pulse/courses?category=scam-defense",
        "/pulse/courses/1",
        "/education/lesson/crypto-basics-101",
        "/pulse/teachers",
        "/pulse/teacher-dashboard",
        "Tutor interaction returned",
        "Recent learning cache",
        "Offline/Cache State",
        "Native Seller/Store Management Foundation",
    ]:
        require(token in qa_report, f"QA report missing {token}", failures)

    require("Native Courses + Learning Practical QA Hardening" in progress, "master progress missing QA hardening section", failures)
    require("Native Seller/Store Management Foundation" in progress, "master progress missing next recommendation", failures)
    require("LogiNexus" not in screen, "internal design-system label leaked into native Courses source", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native courses learning QA audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
