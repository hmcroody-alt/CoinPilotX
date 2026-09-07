#!/usr/bin/env python3
"""Extract the native product surface: screens, deep-link paths, master nav.

Three sources, because no single one is complete:

* ``AppNavigator.tsx`` / ``AuthNavigator.tsx`` register every screen, but a
  screen with no deep link has no URL to compare against the web.
* ``linking.ts`` maps screens to ``pulsesoc.com`` paths. Its ``prefixes``
  include the production website, so every path here is simultaneously a
  native deep link and a URL the web server must answer.
* ``masterNavigation.ts`` is the canonical destination registry the product
  itself ships — the closest thing to a declared list of "places in PulseSoc".

Emits JSON on stdout.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
NATIVE = REPO_ROOT / "mobile-native" / "src"

SCREEN_RE = re.compile(
    r'<(?:Stack|Tab|Drawer|RootStack)\.Screen\s+name="([^"]+)"(?:[^>]*?component=\{(\w+)\})?',
    re.S,
)
# A linking entry is  ScreenName: { path: "pulse/thing/:id", ... }  or  ScreenName: "pulse/thing"
LINK_BLOCK_RE = re.compile(r'(\w+)\s*:\s*\{([^{}]*?)\}', re.S)
PATH_RE = re.compile(r'path:\s*"([^"]+)"')
MASTER_ACTION_RE = re.compile(
    r'\{\s*label:\s*"([^"]+)",\s*route:\s*"([^"]+)",\s*status:\s*"([^"]+)",\s*description:\s*"([^"]*)"'
)
MASTER_SECTION_RE = re.compile(r'title:\s*"([^"]+)",\s*description:\s*"([^"]*)",\s*actions:', re.S)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def extract_screens() -> list[dict]:
    out: list[dict] = []
    for name in ("AppNavigator.tsx", "AuthNavigator.tsx"):
        source = read(NATIVE / "navigation" / name)
        for match in SCREEN_RE.finditer(source):
            out.append(
                {
                    "screen": match.group(1),
                    "component": match.group(2) or "",
                    "navigator": name.replace(".tsx", ""),
                }
            )
    return out


def extract_links() -> dict[str, str]:
    """Screen name -> deep-link path (without leading slash)."""
    source = read(NATIVE / "navigation" / "linking.ts")
    links: dict[str, str] = {}
    for match in LINK_BLOCK_RE.finditer(source):
        screen, body = match.group(1), match.group(2)
        path = PATH_RE.search(body)
        if path:
            links[screen] = path.group(1)
    # Also the shorthand form: ScreenName: "some/path"
    for match in re.finditer(r'(\w+)\s*:\s*"([\w:/\-?.]+)"\s*[,\n}]', source):
        screen, value = match.group(1), match.group(2)
        if screen not in links and screen not in {"path", "prefixes"} and "/" in value:
            links.setdefault(screen, value)
    return links


def extract_master_navigation() -> list[dict]:
    source = read(NATIVE / "navigation" / "masterNavigation.ts")
    sections = [(m.start(), m.group(1)) for m in MASTER_SECTION_RE.finditer(source)]

    def section_for(pos: int) -> str:
        current = ""
        for start, title in sections:
            if start <= pos:
                current = title
            else:
                break
        return current

    out: list[dict] = []
    for match in MASTER_ACTION_RE.finditer(source):
        out.append(
            {
                "label": match.group(1),
                "route": match.group(2),
                "status": match.group(3),
                "description": match.group(4),
                "section": section_for(match.start()),
            }
        )
    return out


def extract_screen_files() -> list[str]:
    return sorted(
        str(p.relative_to(REPO_ROOT))
        for p in NATIVE.rglob("*Screen.tsx")
        if "__tests__" not in p.parts
    )


def main() -> int:
    payload = {
        "screens": extract_screens(),
        "links": extract_links(),
        "master_navigation": extract_master_navigation(),
        "screen_files": extract_screen_files(),
    }
    json.dump(payload, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
