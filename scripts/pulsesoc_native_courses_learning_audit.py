#!/usr/bin/env python3
"""Audit the PulseSoc Native Courses + Learning gateway foundation."""

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
        "mobile-native/src/api/learning.ts",
        "mobile-native/src/screens/CoursesLearningScreen.tsx",
        "mobile-native/src/screens/CreatorStudioScreen.tsx",
        "mobile-native/src/screens/SearchScreen.tsx",
        "mobile-native/src/screens/SettingsScreen.tsx",
        "mobile-native/src/navigation/AppNavigator.tsx",
        "mobile-native/src/navigation/linking.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "mobile-native/src/navigation/types.ts",
        "reports/pulsesoc_native_courses_learning_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    learning_api = read("mobile-native/src/api/learning.ts")
    screen = read("mobile-native/src/screens/CoursesLearningScreen.tsx")
    creator = read("mobile-native/src/screens/CreatorStudioScreen.tsx")
    search = read("mobile-native/src/screens/SearchScreen.tsx")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    types = read("mobile-native/src/navigation/types.ts")
    report = read("reports/pulsesoc_native_courses_learning_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "/api/education/categories",
        "/api/education/lessons",
        "/api/education/lesson/",
        "/api/education/quiz/submit",
        "/api/education/tutor",
        "openLearningWebFallback",
        "learningWebRoute",
    ]:
        require(token in learning_api, f"learning API missing {token}", failures)

    for token in [
        "Learning Gateway",
        "Course gateway",
        "Teacher Dashboard",
        "Mark Complete",
        "Ask Tutor",
        "Course Catalog Web",
        "paid enrollment",
    ]:
        require(token in screen, f"Courses screen missing {token}", failures)

    for token in ["Courses", "CourseDetail", "LearningLessonDetail", "TeacherProfileGateway", "TeacherDashboardGateway"]:
        require(token in app_nav, f"navigator missing {token}", failures)
        require(token in types, f"route types missing {token}", failures)

    for token in [
        'path: "pulse/courses"',
        'path: "pulse/courses/:courseId"',
        'path: "education/lesson/:lessonSlug"',
        'path: "pulse/teachers/:teacherId?"',
        'TeacherDashboardGateway: "pulse/teacher-dashboard"',
    ]:
        require(token in linking, f"linking missing {token}", failures)

    require("learningRouteTarget" in routing, "notification routing missing learning target helper", failures)
    require('navigation.navigate("Courses"' in settings, "Settings missing Courses entry", failures)
    require('navigation.navigate("Courses"' in creator, "Creator Studio missing Courses entry", failures)
    require('key: "learning"' in search and "LearningGatewayShortcut" in search, "Search missing learning gateway", failures)
    require("PulseSoc Native Courses + Learning Gateway Foundation" in report, "feature report missing title", failures)
    require("Native Courses + Learning Practical QA Hardening" in report, "feature report missing QA recommendation", failures)
    require("Native Courses + Learning Gateway Foundation" in progress, "master progress missing Courses section", failures)
    require("LogiNexus" not in learning_api + "\n" + screen + "\n" + creator + "\n" + search + "\n" + settings, "internal LogiNexus name leaked into native source", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native courses learning audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
