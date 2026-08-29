#!/usr/bin/env bash
#
# Commit + deploy the UNDX shared mutation service APIs (mission 06).
#
# This supersedes scripts/commit_action_surface_expansion.sh. DO NOT run that one.
# It stages mission 05's file list, which no longer describes the working tree:
# it commits services/undx_capability_registry.py and services/undx_agent_tools.py
# but NOT services/pulse_social_graph_service.py, pulse_profile_service.py or
# pulse_mutation_audit.py, which those files now import at module scope. The result
# builds locally (the files are on disk, just untracked) and ImportErrors on Railway,
# where the checkout only has what was pushed.
#
# Mission 05 was never committed either, and both missions edit the same files, so
# their changes cannot be separated by path. This is one commit carrying both.
#
# Railway deploys from the pushed branch, so the push is the deploy.

set -euo pipefail

cd "$(dirname "$0")/.."

# The agent sandbox can create .git/index.lock but not remove it, so it leaves stale
# locks behind. Clear them or git refuses before it starts.
find .git -maxdepth 1 -name 'index.lock' -delete 2>/dev/null || true
find .git -maxdepth 1 -name 'HEAD.lock' -delete 2>/dev/null || true

echo "==> Verifying before committing"
python3 -m pytest tests/undx_agent/test_service_api_completion.py \
                  tests/undx_agent/test_action_surface_expansion.py \
                  tests/undx_agent/test_knowledge_map.py \
                  tests/undx_agent/test_question_framed_writes.py -q

echo "==> Registry totals and five-file wiring"
python3 -c "
from services.undx_capability_registry import REGISTRY, unregistered_tool_names
writes = [s for s in REGISTRY.values() if s.is_write]
assert unregistered_tool_names() == [], unregistered_tool_names()
print(f'  {len(REGISTRY)} capabilities, {len(writes)} writes, 0 unregistered')
"

echo "==> Import check (catches exactly the failure the 05 script would have shipped)"
python3 -c "
import services.pulse_social_graph_service, services.pulse_profile_service
import services.pulse_mutation_audit, services.undx_agent_tools
print('  all new service modules import')
"

echo "==> Real-time audio change gate"
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD || {
    echo "audio gate reported findings — read them before pushing" >&2
    exit 1
}

echo "==> Staging"
git add bot.py \
        services/messenger_intelligence_service.py \
        services/pulse_feed_engine.py \
        services/pulse_settings_routes.py \
        services/pulse_mutation_audit.py \
        services/pulse_profile_service.py \
        services/pulse_social_graph_service.py \
        services/undx_agent_runtime.py \
        services/undx_agent_tools.py \
        services/undx_capability_registry.py \
        services/undx_knowledge_map.py \
        services/undx_policy.py \
        services/undx_verification.py \
        tests/undx_agent/test_action_surface_expansion.py \
        tests/undx_agent/test_service_api_completion.py \
        tests/undx_agent/test_knowledge_map.py \
        scripts/commit_service_api_completion.sh \
        UNDX_AGENTIC_RUNTIME/

echo "==> Nothing left behind?"
git status --porcelain | grep -v '^A \|^M \|^??.*\.fuse_hidden' || true

git commit -m "feat(pulsesoc): complete shared mutation service APIs for UNDX

Also carries the mission 05 action-surface expansion, which was never
committed and edits the same files, so the two cannot be split by path."

echo
echo "Committed. Review the diff, then push."
echo
echo "  NOTE: this branch is $(git rev-parse --abbrev-ref HEAD) and it tracks origin/main."
echo "  To put it on main:      git push origin HEAD:main"
echo "  To push as its own branch and open a PR instead:"
echo "                          git push -u origin HEAD"
echo
echo "Once Railway's deploy is live, confirm the SHA it is serving:"
echo "    curl -s https://pulsesoc.com/api/pulse/undx/agent/availability | head -40"
