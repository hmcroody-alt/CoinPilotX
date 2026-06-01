# UNDX Active Proposal Route Audit

## Active Route Map

1. Browser button: `Generate Code Proposal`
2. Frontend handler: `undxKernelGenerateProposal()` in `bot.py`
3. Connector API caller: `undxDesktopConnectorApi('/proposal/generate', payload)`
4. Preferred backend route: `/api/undx/desktop-connector/proposal/generate`
5. Proxy target: `${UNDX_DESKTOP_CONNECTOR_URL}/proposal/generate`
6. Desktop Connector route: `undx_desktop_connector.proposal_generate()`
7. Active proposal generator: `undx_desktop_connector.generate_proposal()`
8. Brain entry point: `undx_brain_layer.analyze_mission()`
9. Brain classifier: `undx_brain_layer.parse_mission()`
10. Brain file selector: `undx_brain_layer.select_repository_files()`
11. Brain safety gate: `undx_brain_layer.enforce_safety()`

Secondary backend route:

1. API endpoint: `/api/undx/kernel/propose`
2. Backend handler: `api_undx_kernel_propose()` in `bot.py`
3. Active proposal generator: `undx_execution_kernel.generate_proposal()`
4. Kernel generator: `undx_execution_kernel.propose_generic_repository_change()`
5. Brain entry point: `undx_brain_layer.analyze_mission()`
6. Brain classifier: `undx_brain_layer.parse_mission()`
7. Brain file selector: `undx_brain_layer.select_repository_files()`
8. Brain safety gate: `undx_brain_layer.enforce_safety()`

## Active Engine Map

- `ACTIVE_PROPOSAL_ENGINE=UNDX_BRAIN_LAYER`
- `ACTIVE_MISSION_CLASSIFIER=undx_brain_layer.parse_mission`
- `ACTIVE_FILE_SELECTOR=undx_brain_layer.select_repository_files`

The Desktop Connector health response and proposal response now expose:

- `engineSource`
- `brainLayerActive`
- `activeProposalEngine`
- `activeMissionClassifier`
- `activeFileSelector`

The `/api/undx/desktop-connector/proposal/generate` proxy and `/api/undx/kernel/propose` route log the active engine fields for every proposal response.

## Planning-Only Safety

When the Brain Layer classifies a mission as `planning-only`:

- `proposalType` is `planning-report`
- `diffAllowed` is `false`
- `requiresApproval` is `false`
- `diff` must be empty
- `changes` must be empty
- legacy landing-page markers are blocked

Blocked legacy markers:

- `Built by UNDX Execution Kernel`
- `Launch UNDX`
- `Command Visibility`
- `Repository Intelligence`
- `Approval Gates`

## Proof

The focused audits verify that the exact Pulse Communications planning mission returns:

- Mission Type: `planning-only`
- Target System: `communications`
- Proposal Type: `planning-report`
- Diff Allowed: `false`
- Diff: empty
- Brain Layer Active: `true`
- `static/offline.html`: not selected
- raw mission text: not injected into generated HTML/code

Audits:

- `scripts/undx_brain_layer_audit.py`
- `scripts/undx_desktop_connector_audit.py`
