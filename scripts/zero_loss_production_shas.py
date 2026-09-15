#!/usr/bin/env python3
"""Report the commit SHA each Railway service instance is actually running.

`origin/main` is not production. Services deploy independently and one that
never restarted keeps serving old code no matter what the ref says, so the
running SHA has to be read off the active deployment rather than inferred.
"""
import json
import subprocess
import sys

PROJECT_ID = "111b3838-09d4-4f13-8b8b-6ed332bad06f"

QUERY = """
query {
  project(id: "%s") {
    name
    environments { edges { node { name serviceInstances { edges { node {
      serviceName
      activeDeployments {
        createdAt
        status
        meta
      }
    } } } } } }
  }
}
""" % PROJECT_ID


def fetch():
    out = subprocess.run(
        ["railway", "api", QUERY],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(f"railway api failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def main():
    data = fetch()
    project = data.get("data", {}).get("project") or {}
    rows = []
    for env in project.get("environments", {}).get("edges", []):
        env_name = env["node"]["name"]
        for si in env["node"].get("serviceInstances", {}).get("edges", []):
            node = si["node"]
            name = node.get("serviceName")
            deps = node.get("activeDeployments") or []
            if not deps:
                rows.append((env_name, name, "(none)", "NO_ACTIVE", None))
                continue
            for dep in deps:
                meta = dep.get("meta") or {}
                rows.append(
                    (
                        env_name,
                        name,
                        (meta.get("commitHash") or "(none)")[:12],
                        dep.get("status") or "?",
                        meta.get("branch"),
                    )
                )
    rows.sort(key=lambda r: (r[0], r[1] or ""))
    for env_name, name, sha, status, branch in rows:
        print(f"{env_name:12} {name or '?':42} SHA={sha:14} status={status:10} branch={branch}")
    json.dump(rows, open("/tmp/production_shas.json", "w"), indent=1)


if __name__ == "__main__":
    main()
