# UNDX Manual

Last updated: 2026-06-01

## 1. What UNDX Is

UNDX stands for **Unknown Destination X**.

Inside CoinPilotXAI, UNDX is the premium intelligence and engineering command layer. It is designed to help plan, analyze, organize, review, and eventually execute controlled software-building work around CoinPilotXAI.

UNDX is not the Pulse feed. It lives inside:

- `/pulse/premium`
- `/pulse/premium/undx`

The Pulse feed is for social activity. UNDX is for premium command work.

At a high level, UNDX is built to answer one question:

> What should CoinPilotXAI build next, why should it be built, what systems does it affect, what risks exist, and what approval gates are needed before anything changes?

UNDX began as a premium command center and has grown into a multi-module operating system for missions, projects, agents, repository intelligence, approval workflows, and controlled execution.

## 2. What UNDX Can Do

UNDX can currently help with these major categories:

- Create mission blueprints from user ideas.
- Store generated missions in Mission Memory.
- Turn missions into structured UNDX projects.
- Track active projects and switch between projects.
- Run an Agent Council over a mission or project.
- Use the UNDX Intelligence Router to choose AI providers when configured.
- Chat with UNDX through the server-side OpenAI intelligence bridge.
- Plan repository connections without file access.
- Simulate read-only access, preview manifests, approval requests, and safety gates.
- Build repository profiles, architecture reports, technology reports, task reports, and code proposals.
- Maintain project workspaces with tasks, milestones, memory notes, directives, linked repository plans, linked reports, and runtime sessions.
- Generate planning artifacts such as task packages, sandbox plans, code proposals, patch previews, approval requests, and rollback plans.
- Connect to an approved local desktop connector for controlled repository scanning and proposal generation.
- Generate exact diff proposals through the Execution Kernel.
- Apply approved changes only after the required approval phrase.
- Run selected safe validations through the desktop connector.
- Prepare Git actions behind explicit approval gates.

UNDX is built around a safety ladder. Most command views are planning-only. The Execution Kernel and Desktop Connector introduce controlled local repository capabilities, but still require explicit approval for writes and Git actions.

## 3. What UNDX Must Not Do Without Approval

UNDX is intentionally guarded.

Unless a phase and approval gate explicitly allow it, UNDX must not:

- Edit files.
- Create files.
- Delete files.
- Run terminal commands.
- Execute code.
- Perform Git operations.
- Push commits.
- Deploy software.
- Access secrets.
- Expose API keys.
- Modify repositories silently.

Planning modules can produce recommendations and proposals. Execution modules must show exact file changes and require approval before writing.

## 4. Where To Open UNDX

Open UNDX from the premium area:

```text
/pulse/premium/undx
```

Common direct sections:

```text
/pulse/premium/undx#undx-builder-console
/pulse/premium/undx#undx-chat-interface
/pulse/premium/undx#undx-project-creator
/pulse/premium/undx#undx-agent-council
/pulse/premium/undx#undx-execution-kernel
/pulse/premium/undx#undx-performance-diagnostics
```

UNDX uses a left command sidebar. Each module is a command view. Opening a command view changes the active workspace without placing UNDX inside the Pulse feed.

## 5. Core Ideas

### Mission

A mission is the raw objective.

Example:

```text
Add Pulse Labs page to Pulse nav.
```

Missions can come from:

- Builder Console input.
- UNDX Chat responses.
- Mission Memory.
- Project lifecycle steps.

### Mission Blueprint

A blueprint is UNDX’s structured interpretation of a mission.

It usually includes:

- Mission name.
- Objective.
- Suggested modules.
- Build steps.
- Security notes.
- Next action.

### Mission Memory

Mission Memory stores generated missions in browser localStorage. It lets UNDX remember previous objectives between refreshes.

Storage key:

```text
undxMissionMemory
```

### Project

A project is a persistent UNDX object created from a mission, chat response, or builder directive.

Storage key:

```text
undxProjectRegistry
```

Project IDs use this format:

```text
UNDX-0001
UNDX-0002
UNDX-0003
```

Projects can hold:

- Objective.
- Type.
- Priority.
- Status.
- Tasks.
- Milestones.
- Memory notes.
- Linked missions.
- Linked repository plans.
- Linked repository profiles.
- Agent outputs.
- Runtime sessions.
- Approval records.
- Sandbox plans.
- Code proposals.

### Active Project

The active project is the project that UNDX modules should use as context.

When switching from Wallet Guardian to Pulse Labs, the active project should change everywhere:

- Builder Console.
- Agent Council.
- Engineering OS.
- Project Lifecycle.
- Repository Intelligence.
- Task Intelligence.
- Code Generation.
- Patch Preview.
- Approval.
- Sandbox.

This prevents one project’s records from contaminating another project.

## 6. The Main UNDX Workflow

The intended high-level workflow is:

```text
Mission
→ Project
→ Agent Council
→ Repository Plan
→ Task Package
→ Builder Console
→ Sandbox Simulation
→ Code Generation
→ Approval Request
→ Execution Kernel
→ Approved Write
→ Safe Validation
→ Approved Git
```

Most of this workflow is planning and review. The final execution steps only happen through the Desktop Connector / Execution Kernel and require approval.

## 7. Builder Intelligence Console

The Builder Console is the simplest starting point.

Use it when you have an idea and want UNDX to turn it into a mission blueprint.

How to use it:

1. Open:

   ```text
   /pulse/premium/undx#undx-builder-console
   ```

2. Type a mission directive.

   Example:

   ```text
   Build a Pulse Labs navigation page that explains experimental CoinPilotXAI tools.
   ```

3. Click:

   ```text
   Generate Mission Blueprint
   ```

4. Review the generated blueprint.

5. Save or convert the mission into a project.

If the input is empty, UNDX should show:

```text
Enter a mission before initializing UNDX.
```

## 8. Mission Memory

Mission Memory is where UNDX stores generated missions.

Open:

```text
/pulse/premium/undx#undx-mission-memory
```

Mission cards can support actions such as:

- Replay Mission.
- View Evolution.
- Create Project From This Mission.
- Clear Mission Memory.

Use Mission Memory when:

- You created several missions and want to return to one.
- You want to create a new project from a saved mission.
- You want to prevent UNDX from using an old default mission.

Best practice:

Always create a new project from the correct mission before running project-specific modules.

## 9. Mission Evolution

Mission Evolution expands a saved mission into the next likely build phase.

It can produce:

- Suggested next phase.
- Recommended modules.
- Risk notes.
- Expansion path.
- UNDX Evolution Score.

Use it when you want to ask:

> If this mission works, what should it become next?

Example:

Mission:

```text
Pulse Labs Navigation Page
```

Likely next phase:

```text
Product Interface Expansion
```

## 10. UNDX Chat Interface

UNDX Chat is the conversational intelligence layer.

Open:

```text
/pulse/premium/undx#undx-chat-interface
```

It starts with:

```text
UNDX Core online. OpenAI intelligence bridge active. What mission should CoinPilotXAI evolve next?
```

The chat sends directives to the backend route:

```text
/api/undx/chat
```

The backend must keep API keys server-side. The frontend must never expose `OPENAI_API_KEY` or other provider keys.

Chat history is stored locally:

```text
undxChatMemory
```

Useful chat actions:

- Send Directive.
- Clear Chat History.
- Send To Builder Console.
- Save To Mission Memory.

Use chat when:

- You want freeform strategy.
- You want UNDX to classify a mission.
- You want a build plan.
- You want a response that can become a mission or project.

## 11. UNDX Intelligence Router

The Intelligence Router is the server-side provider selector.

Files:

```text
undx_router.py
undx_worker.py
```

Expected provider variables:

```text
OPENAI_API_KEY
CLAUDE_AI_API
Gemini_AI_API
DEEPSEEK_AI_API
GROQ_AI_API
```

Feature flags:

```text
UNDX_ROUTER_ENABLED
UNDX_MULTI_MODEL_MODE
UNDX_DEFAULT_AI_PROVIDER
```

Provider mapping for the Agent Council:

- Architect Agent → Claude.
- Research Agent → Gemini.
- Builder Agent → OpenAI.
- Optimization Agent → DeepSeek.
- Rapid Response Agent → Groq.
- Fallback → OpenAI.

The router should report:

- Provider selected.
- Provider status.
- Fallback status.

Provider statuses should mean:

- **Online**: provider key exists and routing is available.
- **Missing API Key**: provider is configured in UI but no server-side key exists.
- **Offline**: provider exists but is unavailable.
- **Fallback Active**: UNDX is using OpenAI instead.

## 12. Agent Council

Open:

```text
/pulse/premium/undx#undx-agent-council
```

The Agent Council reviews a mission or active project from multiple perspectives.

Core agents include:

- Architect Agent.
- Builder Agent.
- Security Agent.
- Research Agent.
- Product Agent.
- Deployment Agent.

Expanded runtime agents include:

- Testing Agent.
- Documentation Agent.
- Optimization Agent.
- Product Strategy Agent.
- Rapid Response Agent.

Click:

```text
Run Agent Council
```

The council should:

- Read the current Builder Console text, selected mission, or active project.
- Produce recommendations.
- Produce a council summary.
- Show priority and complexity.
- Save output to Mission Memory or project memory when requested.

Use the Agent Council before moving from idea to task generation.

## 13. Project Creator

Open:

```text
/pulse/premium/undx#undx-project-creator
```

The Project Creator turns a mission into a structured project.

Project fields:

- Project Name.
- Project Type.
- Project Objective.
- Priority.

Suggested project types:

- Crypto Intelligence.
- Security.
- AI Systems.
- Automation.
- Research.
- Product Experience.
- Infrastructure.
- Other.

After creating a project, UNDX should:

- Assign a project ID.
- Store it in the registry.
- Make it the active project.
- Update dashboard counters.
- Make other modules use the new active context.

Project card actions:

- Open Project.
- Send To Builder Console.
- Run Agent Council.

## 14. Project Registry

The Project Registry lists projects stored in localStorage.

Each project card should show:

- Project ID.
- Name.
- Type.
- Status.
- Created Date.

Use the registry to switch between projects and prevent accidental mixing of project data.

## 15. Project Workspace

The Project Workspace is where a selected project becomes operational.

It can show:

- Project ID.
- Project Name.
- Project Type.
- Status.
- Priority.
- Created Date.
- Objective.
- Linked Mission.

It can manage:

- Project Tasks.
- Project Milestones.
- Project Memory.
- Build Directive.
- Linked Repository Plans.
- Linked Preview Manifests.
- Runtime Sessions.
- Repository Intelligence.
- Approval Status.

Common actions:

- Add Task.
- Mark In Progress.
- Mark Done.
- Delete Task.
- Mark Milestone Complete.
- Add Memory Note.
- Delete Note.
- Generate Build Directive.
- Copy Build Directive.
- Send Directive To UNDX Chat.

## 16. Autonomous Project Lifecycle

Open:

```text
/pulse/premium/undx#undx-autonomous-project-lifecycle
```

The lifecycle connects UNDX modules into an end-to-end planning chain.

Lifecycle steps:

- Mission Created.
- Project Active.
- Agent Council Complete.
- Repository Plan Generated.
- Task Package Generated.
- Builder Directive Created.
- Sandbox Simulation Created.
- Code Proposal Created.
- Approval Request Created.

Controls:

- Run Next Step.
- Run Full Planning Chain.

Use this when you want UNDX to move a project from idea toward an approval-ready package.

## 17. Unified Agent Runtime

Open:

```text
/pulse/premium/undx#undx-unified-agent-runtime
```

The Unified Agent Runtime coordinates multiple agents around the active project.

It can generate:

- Architect Review.
- Builder Plan.
- Testing Plan.
- Security Review.
- Documentation Plan.
- Research Notes.
- Optimization Notes.
- Product Strategy Notes.
- Final Runtime Recommendation.

Runtime sessions are stored under:

```text
undxAgentRuntimeSessions
```

Runtime IDs use:

```text
RUNTIME-UNDX-0001
```

Use it when:

- A project has enough context to need multi-agent coordination.
- You want consensus and conflict detection.
- You want to generate task packages, sandbox plans, or code proposals from an agent review.

## 18. Repository Planning Modules

UNDX includes several repository planning views. These were designed before real local access.

They are useful for safely preparing repository understanding without touching files.

### Repository Planner

Open:

```text
/pulse/premium/undx#undx-repository-planner
```

It stores planned repositories in:

```text
undxRepositoryPlans
```

It can record:

- Repository / Folder Name.
- Repository Type.
- Planned Path or URL.
- Access Level.
- Purpose / Notes.

Default access level:

```text
Planning Only
```

### Read-Only Access Blueprint

Open:

```text
/pulse/premium/undx#undx-read-only-access-blueprint
```

This explains how future read-only access should work:

```text
Repository registered
→ Read-only access requested
→ User approves request
→ UNDX previews files
→ UNDX generates analysis
→ User reviews results
→ Future edit permissions remain separate
```

### Preview Gateway

Open:

```text
/pulse/premium/undx#undx-read-only-preview-gateway
```

Stores preview manifests in:

```text
undxPreviewManifests
```

Manifest IDs use:

```text
MANIFEST-UNDX-0001
```

This is a simulation layer. It should not read real files.

## 19. Approval Protocol

Open:

```text
/pulse/premium/undx#undx-approval-protocol
```

The Approval Protocol defines safe gates for future high-impact actions.

Gate types:

- Repository Read Access.
- File Edit Access.
- Terminal Command Access.
- Git Commit Access.
- Deployment Access.
- Secret Management Access.

Default permission level:

```text
Always Require Approval
```

Storage keys:

```text
undxApprovalProtocol
undxApprovalHistory
```

Use this module to simulate approval decisions and keep an audit trail.

## 20. Repository Intelligence Foundation

Open:

```text
/pulse/premium/undx#undx-repository-intelligence-foundation
```

This module builds repository intelligence profiles.

Storage keys:

```text
undxRepositoryProfiles
undxEngineeringRecommendations
undxRepositoryMemory
```

Profile IDs:

```text
REPO-UNDX-0001
```

Profiles can include:

- Repository name.
- Project link.
- Technology stack.
- Languages.
- Frameworks.
- Services.
- APIs.
- Routes.
- Database layer.
- Frontend layer.
- Backend layer.
- Architecture notes.
- Risk notes.

This module is read-only intelligence. It does not edit repositories.

## 21. Repository Context Engine

Open:

```text
/pulse/premium/undx#undx-repository-context-engine
```

The Repository Context Engine can generate a repository profile containing:

- Application Name.
- Framework.
- Main Entry File.
- Database Type.
- Workers.
- Premium Modules.
- Known Routes.
- Known Services.

Storage key:

```text
undxRepositoryContext
```

Use it when a project needs architecture-aware directives instead of generic instructions.

## 22. Codebase Understanding Layer

Open:

```text
/pulse/premium/undx#undx-codebase-understanding-layer
```

This module maps CoinPilotXAI architecture concepts such as:

- Routes.
- Files.
- Modules.
- Services.
- Data flows.
- Risk areas.
- Recommended implementation locations.

It helps UNDX write project-aware instructions instead of generic build text.

## 23. Engineering Task Intelligence

Open:

```text
/pulse/premium/undx#undx-engineering-task-intelligence
```

This module turns project and repository intelligence into engineering task packages.

It can produce:

- Recommended next task.
- Task summary.
- Priority map.
- Dependency map.
- Risk ranking.
- Execution order.
- Validation strategy.
- Approval recommendations.

Use it before code generation or patch preview.

## 24. Repository-Aware Code Generation

Open:

```text
/pulse/premium/undx#undx-repository-aware-code-generation
```

This view is for project-aware code proposal planning.

It should use:

- Active project.
- Mission objective.
- Codebase understanding.
- Task intelligence.
- Repository profile.

Expected behavior:

- A Pulse Labs project should generate Pulse Labs routes, files, modules, tests, and docs.
- Wallet Guardian data should not appear in Pulse Labs unless intentionally linked.
- Inferred items should be labeled as planning-only when real repository intelligence is not available.

This module should not write files by itself.

## 25. Patch Preview

Open:

```text
/pulse/premium/undx#undx-human-approved-patch-preview
```

Patch Preview prepares approval-ready package information.

It should show:

- Proposed files.
- Proposed changes.
- Risks.
- Rollback plan.
- Validation plan.
- Approval readiness.

Patch Preview is not the same as writing files. It prepares the human review step.

## 26. Modification Sandbox

Open:

```text
/pulse/premium/undx#undx-controlled-repository-modification-sandbox
```

The sandbox simulates changes and expected outcomes.

Use it to ask:

- What could break?
- What should be tested?
- What rollback plan is needed?
- What approval gates apply?

It must remain simulation-only unless connected to a controlled execution phase.

## 27. Execution Kernel

Open:

```text
/pulse/premium/undx#undx-execution-kernel
```

The Execution Kernel is the bridge between UNDX planning and real controlled local repository work.

Files:

```text
undx_execution_kernel.py
undx_desktop_connector.py
```

Approval phrase for file writes:

```text
APPROVE UNDX WRITE
```

The Execution Kernel can:

- Connect to the local Desktop Connector.
- Scan approved repository paths.
- Build a repository map.
- Generate code proposals.
- Show target files.
- Show unified diffs.
- Store a generated proposal for approval.
- Apply approved changes only after the approval phrase.
- Run safe validation commands.
- Prepare Git status, commit, and push behind approval gates.

The Execution Kernel must not:

- Scan unapproved paths.
- Read protected files.
- Write without approval.
- Run arbitrary commands.
- Push without approval.

## 28. Desktop Connector

The Desktop Connector runs locally, usually at:

```text
http://127.0.0.1:8765
```

Health endpoint:

```text
http://127.0.0.1:8765/health
```

It should report:

- Connector name.
- Version.
- Machine name.
- Online status.
- Allowed workspace count.
- Proposal engine status.

The connector is what gives UNDX controlled local repository visibility.

Important:

The browser app may call the connector directly or through a proxy route, depending on CSP and local configuration.

If blocked by CSP, the UI should show a clear connector error.

## 29. Repository-Aware Proposal Generation

Through the Desktop Connector, UNDX can generate real code proposals.

Example workspace:

```text
/Users/hmcherie/Desktop/UNDX-Test
```

Example mission:

```text
Replace index.html with a dark futuristic SaaS landing page for UNDX Test Lab.
```

Expected output:

- Target file:

  ```text
  index.html
  ```

- Unified diff preview.
- Exact file changes.
- Summary.
- Requires approval.

Nothing should be written until the user enters:

```text
APPROVE UNDX WRITE
```

## 30. Safe Validation

The Desktop Connector supports selected safe validations.

Examples:

- Python compile.
- UNDX audit.
- Site functional audit.
- Performance audit.
- Pulse feed layout audit.
- File existence validation.
- JS syntax validation where available.

Validation must be bounded and explicit. UNDX should not run arbitrary terminal commands.

## 31. Git Gateway

The Execution Kernel includes Git-related actions, but these must remain gated.

Git approval phrases:

```text
APPROVE UNDX GIT
APPROVE UNDX PUSH
```

Git actions should:

- Show status first.
- Stage only approved files.
- Commit only approved changes.
- Push only after explicit approval.

UNDX should never silently run Git.

## 32. Performance Diagnostics

Open:

```text
/pulse/premium/undx#undx-performance-diagnostics
```

This module exists because UNDX became very large.

It should help inspect:

- Page load time.
- Render time.
- LocalStorage size.
- Largest storage keys.
- Active polling loops.
- Active listeners.
- Active repository scans.
- Lazy-loaded views.

Use it when UNDX freezes, feels slow, or shows stale state.

Useful action:

- Trim oversized UNDX storage keys when safe.

## 33. LocalStorage Map

UNDX uses localStorage heavily.

Common keys include:

```text
undxMissionMemory
undxChatMemory
undxProjectRegistry
undxRepositoryPlans
undxApprovalProtocol
undxApprovalHistory
undxPreviewManifests
undxRepositoryContext
undxRepositoryProfiles
undxEngineeringRecommendations
undxRepositoryMemory
undxAgentRuntimeSessions
```

If UNDX feels frozen, inspect Performance Diagnostics first. Large localStorage records can slow startup and rendering.

## 34. Safe Rendering Rule

User text should be rendered safely.

UNDX should:

- Use textContent or safe escaping.
- Avoid injecting raw user HTML.
- Avoid unsafe innerHTML unless the content is controlled.
- Keep secrets out of frontend storage.
- Keep API keys server-side.

## 35. How To Make UNDX Do Things

### Create a mission

1. Open Builder Console.
2. Type the mission.
3. Click Generate Mission Blueprint.
4. Save it to Mission Memory.

Example:

```text
Create a Pulse Labs navigation page for experimental CoinPilotXAI tools.
```

### Create a project from a mission

1. Open Mission Memory.
2. Find the mission.
3. Click Create Project From This Mission.
4. Confirm the new project appears in Project Registry.
5. Confirm it becomes the active project.

### Switch active project

1. Open Project Creator or Project Registry.
2. Use the Active Project switcher.
3. Select the project.
4. Open the workspace.
5. Confirm project-specific counters and records update.

### Run Agent Council

1. Make sure a project is active or a mission is in Builder Console.
2. Open Agent Council.
3. Click Run Agent Council.
4. Review all agent cards.
5. Save output to Mission Memory or project memory.

### Generate a task package

1. Open the active project.
2. Make sure mission and agent output exist.
3. Open Engineering Task Intelligence.
4. Generate a task package.
5. Attach the report to the project.

### Generate a code proposal

Planning-only route:

1. Open Repository-Aware Code Generation.
2. Confirm the active project is correct.
3. Generate code proposal artifacts.
4. Review proposed files, validation plan, and risks.

Execution route:

1. Start the Desktop Connector.
2. Open Execution Kernel.
3. Confirm connector status is Online.
4. Register or select an approved workspace.
5. Scan the repository.
6. Enter a mission directive.
7. Generate Code Proposal.
8. Review exact diff.
9. Type `APPROVE UNDX WRITE`.
10. Apply approved changes.
11. Run safe validation.

### Run the full planning chain

1. Create or select a project.
2. Open Autonomous Project Lifecycle.
3. Click Run Full Planning Chain.
4. Watch each step create the next record.
5. Refresh and confirm persistence.

## 36. Example Workflow: Pulse Labs Page

Goal:

```text
Add Pulse Labs page to Pulse nav.
```

Recommended UNDX flow:

1. Open Builder Console.
2. Enter:

   ```text
   Add a Pulse Labs navigation page to Pulse for experimental CoinPilotXAI tools.
   ```

3. Generate Mission Blueprint.
4. Save to Mission Memory.
5. Click Create Project From This Mission.
6. Confirm project:

   ```text
   UNDX-0002 Pulse Labs Navigation Page
   ```

7. Open Project Workspace.
8. Run Agent Council.
9. Generate Repository Plan.
10. Generate Task Package.
11. Send directive to Builder Console.
12. Generate Sandbox Simulation.
13. Generate Code Proposal.
14. Create Approval Request.
15. If real code changes are needed, move to Execution Kernel.
16. Scan the approved repository.
17. Generate exact diff.
18. Approve write only after reviewing file changes.
19. Run validation.
20. Commit only after explicit approval.

Expected project-aware outputs:

- Proposed page: Pulse Labs.
- Proposed nav update: Pulse navigation.
- Proposed route: `/pulse/labs`.
- Proposed template/component: Pulse Labs page.
- Proposed QA: desktop/mobile Pulse navigation check.
- No Wallet Guardian references unless intentionally linked.

## 37. Example Workflow: UNDX-Test Landing Page

Workspace:

```text
/Users/hmcherie/Desktop/UNDX-Test
```

Mission:

```text
Replace index.html with a dark futuristic SaaS landing page for UNDX Test Lab.
Include top navigation, hero section, Launch UNDX button, feature cards, pricing section, footer text: Generated by UNDX, and responsive CSS inside the same file.
```

Steps:

1. Start the Desktop Connector.
2. Open Execution Kernel.
3. Confirm connector Online.
4. Scan `/Users/hmcherie/Desktop/UNDX-Test`.
5. Confirm `index.html` is detected.
6. Generate Code Proposal.
7. Confirm Diff Preview is populated.
8. Confirm Exact File Changes shows `index.html`.
9. Confirm no files were written yet.
10. Type:

    ```text
    APPROVE UNDX WRITE
    ```

11. Apply approved changes.
12. Open the file locally and verify the page renders.

## 38. Safety Checklist Before Real Changes

Before using UNDX to write files, confirm:

- The active project is correct.
- The workspace path is approved.
- The repository scan is current.
- Protected files are blocked.
- Target files are shown.
- Unified diff is visible.
- Approval phrase is required.
- Backup will be created.
- Validation plan is clear.
- Git actions are separate from file writes.

## 39. Troubleshooting

### UNDX is slow or frozen

Open:

```text
/pulse/premium/undx#undx-performance-diagnostics
```

Check:

- LocalStorage size.
- Largest UNDX keys.
- Active repository scans.
- Active polling loops.
- Render time.

Then trim oversized keys if the diagnostics panel says it is safe.

### Desktop Connector says blocked by CSP

Check:

- Connector health endpoint works directly.
- App CSP allows local connector URLs in dev mode.
- Frontend is using the proxy if direct fetch is blocked.
- Static JS cache is not stale.

Health URL:

```text
http://127.0.0.1:8765/health
```

### Agent Council says provider unavailable

Check:

- `UNDX_ROUTER_ENABLED`.
- `UNDX_MULTI_MODEL_MODE`.
- Provider API key variables.
- OpenAI fallback key.
- `/api/undx/agent-council/providers` or equivalent provider-health endpoint.

### Project output uses the wrong project

Check:

- Active Project switcher.
- Project Workspace title.
- Project ID in the generated report.
- Mission Memory source.
- Linked records.

Do not continue if Wallet Guardian appears in a Pulse Labs project unless it was intentionally attached.

### Proposal generation shows no diff

Check:

- Generate Code Proposal button sends a request.
- Endpoint is `/proposal/generate` on the Desktop Connector or the app proxy.
- Payload includes workspace path and mission directive.
- Backend reports `Proposal Engine: Repository-Aware`.
- Response includes `diff`, `changes`, and `targetFiles`.

### File write did not happen

Check:

- Approval phrase is exactly:

  ```text
  APPROVE UNDX WRITE
  ```

- Proposal exists.
- Target files are inside approved workspace.
- Protected files are not targeted.
- Backup creation succeeded.

## 40. Developer Notes

## 40. Intelligence Evolution Core

Open:

```text
/pulse/premium/undx#undx-intelligence-evolution-core
```

The Intelligence Evolution Core turns the broader UNDX intelligence doctrine into a concrete command view.

It is built around the three-layer intelligence architecture:

- **Advanced Narrow Intelligence:** operational UNDX modules for repository understanding, project planning, agent reviews, task intelligence, code proposals, performance diagnostics, and safe execution workflows.
- **Artificial General Intelligence:** governed research mode for cross-domain reasoning, multimodal understanding concepts, and human-centered collaboration. UNDX does not claim achieved AGI.
- **Artificial Superintelligence:** theoretical locked layer. Recursive self-improvement, autonomous deployment, and uncontrolled optimization remain disabled.

The view also makes the ethical framework operational:

- Transparency.
- Accountability.
- Human oversight.
- Explainable decision-making.
- Security-first design.
- Continuous risk assessment.

It includes a defensive cyber intelligence center. This is for security planning, not offensive activity.

Defensive capabilities represented in the view:

- Threat detection planning.
- Vulnerability review.
- Incident coordination.
- Infrastructure hardening recommendations.
- Behavioral analytics.
- Predictive defense scoring.

The Active Defense Operations section, also labeled the **Angry Section**, is explicitly defensive-only. It can prepare containment plans, forensic checklists, administrator notifications, and incident response coordination. It cannot attack, retaliate, scan without authorization, access credentials, generate malware, or run unapproved commands.

Storage key:

```text
undxIntelligenceEvolutionReports
```

Actions:

- Generate Intelligence State Report.
- Run Defensive Readiness Review.
- Generate Real Intelligence State.
- Generate Cyber Defense Readiness.
- Generate Cross-Domain Reasoning Map.
- Generate Multimodal Blueprint.
- Generate Adaptive Learning Memory.
- Generate Incident Command Package.
- Generate Governance Snapshot.
- Generate Controlled Action Readiness.
- Generate Production Intelligence Loop.
- Send To UNDX Chat.
- Save To Mission Memory.
- Delete Report.

Use this view when you want UNDX to reason about its own intelligence architecture, safety posture, cyber-defense readiness, and long-range evolution without pretending that research concepts are already unrestricted real-world capabilities.

The UNDX Evolution track is split into ten phases:

- **E1: Intelligence Evolution Core.** Establishes ANI / AGI / ASI boundaries, ethical controls, cyber-defense framing, the Angry Section, and the evolution report registry.
- **E2: Real Intelligence State Engine.** Reads local UNDX records and calculates a practical intelligence maturity score from missions, projects, repository profiles, task packages, code artifacts, approvals, simulations, and reports.
- **E3: Cyber Defense Readiness Engine.** Generates defensive readiness checklists for triage, protected resources, containment, forensics, administrator notification, recovery, and post-incident memory.
- **E4: Cross-Domain Reasoning Layer.** Connects software engineering, cybersecurity, product, crypto intelligence, media reliability, infrastructure, production operations, and human governance.
- **E5: Multimodal Intelligence Blueprint.** Defines how UNDX should handle text, image, audio, video, live stream, replay, log, database, repository, and browser-QA evidence safely.
- **E6: Adaptive Learning Memory.** Compares past missions, projects, reports, production failures, and approvals so future recommendations become sharper without uncontrolled self-modification.
- **E7: Defensive Incident Command.** Builds a defensive incident command package with scope, affected systems, evidence, containment, recovery, validation, and post-incident learning.
- **E8: Intelligence Governance Dashboard.** Shows what UNDX is allowed to do, what is blocked, and which approval gates are required before high-impact actions.
- **E9: Controlled Action Readiness.** Connects intelligence reports to task packages, sandbox plans, code proposals, diff preview, approval, Execution Kernel, backup, validation, and Git gates.
- **E10: Production Intelligence Loop.** Turns Railway logs, production truth dashboards, media proof, database root causes, worker health, browser QA, and deployment outcomes into release-readiness intelligence.

All ten phases store records through the same evolution registry so UNDX can compare doctrine, project state, defensive readiness, governance, and production truth in one place.

## 41. Developer Notes

Important source files:

```text
bot.py
undx_router.py
undx_worker.py
undx_execution_kernel.py
undx_desktop_connector.py
scripts/undx_homepage_audit.py
scripts/undx_desktop_connector_audit.py
```

Important audits:

```text
venv/bin/python scripts/undx_homepage_audit.py
venv/bin/python scripts/undx_desktop_connector_audit.py
venv/bin/python scripts/site_functional_audit.py
venv/bin/python scripts/performance_audit.py
```

Common validation:

```text
venv/bin/python -m py_compile bot.py undx_router.py undx_worker.py undx_execution_kernel.py undx_desktop_connector.py
venv/bin/python scripts/undx_homepage_audit.py
venv/bin/python scripts/undx_desktop_connector_audit.py
venv/bin/python scripts/site_functional_audit.py
venv/bin/python scripts/performance_audit.py
git diff --check
```

## 42. Current Product Status

UNDX is best understood as a premium engineering operating system in active construction.

Current strengths:

- Deep command-center UI.
- Mission and project memory.
- Router-backed chat and provider routing architecture.
- Project lifecycle wiring.
- Agent Council and Unified Runtime concepts.
- Repository intelligence planning layers.
- Controlled Desktop Connector and Execution Kernel.
- Approval-first file writing model.

Current risks:

- The UNDX page is very large.
- Many modules are localStorage-heavy.
- Some repository intelligence views are inferred/planning-only.
- Real execution depends on the local Desktop Connector.
- AI provider availability depends on server-side environment variables.
- Real writes must remain tightly gated.

## 43. The UNDX Principle

UNDX should become powerful only by becoming trustworthy first.

That means:

- Plan before changing.
- Review before writing.
- Show diffs before edits.
- Require approval before commands.
- Keep secrets hidden.
- Preserve backups.
- Keep project context clean.
- Never confuse planning success with production success.

UNDX is not just a dashboard. It is the system that helps CoinPilotXAI think before it builds.
