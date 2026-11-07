# BLACKBOX — Project Context & Technical Blueprint

> **Product tagline:** Your AI incident commander that doesn't just explain outages. It fixes them.
> 
> **Hackathon:** Flip the Switch Hackathon — WeMakeDevs × Switch  
> **Category:** DevOps / SRE / Autonomous Enterprise Operations  
> **Product type:** Multi-agent autonomous incident response and remediation platform  
> **Core philosophy:** Every production incident should make the engineering organization smarter.

---

# 1. Instructions for Claude

You are acting as the Principal Software Architect, Senior Backend Engineer, AI Agent Engineer, DevOps Specialist, and Product Designer responsible for building BLACKBOX.

You are not building a chatbot, a monitoring dashboard, or a simple collection of AI agents.

You are building a production-inspired, multi-agent incident response system that uses **Switch as its central human-agent collaboration and orchestration environment**.

Your primary objective is to deliver a polished, functional, end-to-end hackathon product that demonstrates how multiple specialized AI agents can collaboratively investigate, diagnose, remediate, and learn from production incidents while maintaining strict human authorization boundaries.

## Your engineering principles

1. Build working functionality rather than impressive-looking mockups.
    
2. Use Switch's native collaboration primitives wherever possible.
    
3. Make agent-to-agent communication visible in actual Switch rooms.
    
4. Maintain persistent shared incident context.
    
5. Separate agent responsibilities and permissions.
    
6. Never allow an agent to bypass the human approval mechanism.
    
7. Make all execution actions demonstrable and verifiable.
    
8. Prioritize a complete vertical slice over numerous incomplete integrations.
    
9. Keep the architecture modular enough to support real enterprise integrations later.
    
10. Optimize for a reproducible three-minute hackathon demonstration.
    

Before implementing anything, inspect the available project repository, Switch documentation, APIs, SDKs, protocol implementation and existing code.

**Do not invent Switch API endpoints, event schemas, tool names, or capabilities.** Use the actual implementation and documentation available in the repository.

---

# 2. Product Overview

## 2.1 What is BLACKBOX?

BLACKBOX is an autonomous incident commander designed for modern engineering organizations.

It coordinates multiple specialized AI agents inside enterprise communication platforms such as Slack, Microsoft Teams, Mattermost or Discord through Switch.

When a production incident occurs, BLACKBOX:

1. Detects the incident.
    
2. Collects relevant observability data.
    
3. Correlates recent deployments and infrastructure changes.
    
4. Identifies the likely root cause.
    
5. Assigns investigation to a coding specialist.
    
6. Generates a remediation patch.
    
7. Executes automated tests.
    
8. Prepares a recovery proposal.
    
9. Requests explicit human authorization.
    
10. Executes the approved action in a controlled environment.
    
11. Verifies recovery.
    
12. Generates an incident report.
    
13. Creates permanent preventive engineering tasks.
    

The product turns incident management from a fragmented, human-intensive process into a coordinated, AI-assisted operational workflow.

## 2.2 The problem

Production incidents are expensive.

Consider a large e-commerce organization experiencing a payment-service outage.

Its engineering team must coordinate:

- SRE engineers.
    
- Backend engineers.
    
- Database administrators.
    
- Infrastructure teams.
    
- Engineering managers.
    
- Incident commanders.
    

Information is fragmented across monitoring tools, logs, Git repositories, deployment systems and chat messages.

Traditional tools can detect incidents and notify engineers, but investigation, coordination, remediation and verification still require considerable manual effort.

BLACKBOX addresses the coordination gap.

## 2.3 Product thesis

**The next generation of incident management is not another monitoring dashboard. It is a collaborative AI engineering team operating alongside humans.**

Switch provides the shared organizational environment. Specialized agents provide the engineering capabilities. Humans retain authority over consequential operations.

---

# 3. The Hackathon Technology: Switch

Switch, developed by sandbox-quantum, is the central collaboration gateway for BLACKBOX.

It connects AI agents to enterprise communication environments and enables them to collaborate within stateful rooms.

BLACKBOX must demonstrate meaningful use of Switch rather than treating it as an ordinary messaging integration.

## 3.1 Switch capabilities to utilize

### Rooms

Each production incident should have its own dedicated room.

Example:

`incident-payment-api-1042`

The room contains:

- Human incident commander.
    
- Sentinel agent.
    
- RootCause agent.
    
- Recovery agent.
    
- Incident context.
    
- Investigation tasks.
    
- Proposed remediation.
    
- Approval decisions.
    
- Execution history.
    
- Final incident report.
    

### Roles

Agents must have distinct identities and responsibilities.

|Identity|Responsibility|Permission boundary|
|---|---|---|
|Sentinel|Monitor and investigate|Read operational evidence, create findings|
|RootCause|Diagnose and develop fixes|Read repository, run sandbox diagnostics, stage patches|
|Recovery|Validate and execute approved recovery|Prepare execution, verify authorization before acting|
|Human incident commander|Authorize consequential actions|Approve, reject or request changes|

Do not give every agent unrestricted access to every tool.

### Shared context and room memory

The room must preserve incident state across the entire lifecycle.

For example:

- Initial alert.
    
- Incident severity.
    
- Service affected.
    
- Deployment history.
    
- Root-cause hypotheses.
    
- Diagnostic results.
    
- Code changes.
    
- Test results.
    
- Approval status.
    
- Recovery verification.
    

An agent joining an existing incident must be able to understand the current state without reconstructing the entire investigation from scratch.

### Agent Protocol

Use Switch's actual Agent Protocol implementation.

Investigate the repository to establish the correct transport, event formats, authentication mechanism, subscription behavior and message-sending API.

The intended capabilities include:

- Observing relevant room events.
    
- Receiving direct mentions.
    
- Overhearing relevant conversations where permitted.
    
- Sending room messages.
    
- Performing agent-to-agent handoffs.
    
- Maintaining a stateful investigation.
    
- Updating room artifacts.
    

Use the documented implementation. If the protocol uses SSE for incoming events and HTTP for outbound communication, implement accordingly rather than assuming WebSockets.

### Tasks

Every meaningful unit of work should be represented as an explicit task.

Example:

- Investigate payment-service failure.
    
- Correlate deployment history.
    
- Identify root cause.
    
- Generate patch.
    
- Execute regression tests.
    
- Prepare recovery plan.
    
- Obtain approval.
    
- Verify recovery.
    
- Generate postmortem.
    

### Decisions

Switch decisions must govern consequential actions.

A message saying "I recommend approval" is not authorization.

The execution service must independently verify an actual approved decision using the documented Switch functionality.

---

# 4. Core Product Architecture

## 4.1 High-level architecture

```
                  ENTERPRISE SYSTEMS
                         |
       +-----------------+------------------+
       |                 |                  |
   Monitoring         GitHub             Deployment
   Prometheus         Repository          Events
   Grafana            Commit History      CI/CD
       |                 |                  |
       +-----------------+------------------+
                         |
                         v
               BLACKBOX EVENT INGESTION
                         |
                         v
                 SWITCH GATEWAY
                         |
              INCIDENT COLLABORATION ROOM
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
      SENTINEL       ROOTCAUSE       RECOVERY
       Agent           Agent          Agent
          |              |              |
          |              v              |
          |        Claude Code /         |
          |        OpenAI Codex          |
          |              |              |
          |              v              |
          |       Diagnostics + Patch   |
          |              |              |
          +--------------+--------------+
                         |
                         v
                REMEDIATION PROPOSAL
                         |
                         v
                  SWITCH DECISION
                         |
                         v
                 HUMAN AUTHORIZATION
                         |
                         v
                EXECUTION GATEWAY
                         |
                         v
                SANDBOX ENVIRONMENT
                         |
                         v
                RECOVERY VALIDATION
                         |
                         v
                INCIDENT POSTMORTEM
                         |
                         v
              ORGANIZATIONAL LEARNING
```

## 4.2 Recommended technology stack

Use these technologies unless the existing repository or hackathon environment suggests a better alternative.

|Component|Technology|
|---|---|
|Collaboration gateway|Switch|
|Backend|Python + FastAPI|
|Agent orchestration|Custom Python agent services|
|Agent communication|Switch Agent Protocol|
|LLM|Claude / OpenAI|
|Coding agent|Claude Code or OpenAI Codex|
|Tool integrations|MCP|
|Data storage|SQLite for MVP|
|Structured data|Pydantic|
|Observability simulation|Python event generators|
|Metrics|Prometheus-compatible mock metrics|
|Logs|Structured JSON logs|
|Repository operations|Git|
|Test execution|Docker sandbox or isolated local workspace|
|Frontend|React + TypeScript|
|Styling|Tailwind CSS|
|Charts|Recharts|
|Deployment|Docker Compose|

Do not introduce unnecessary infrastructure such as Kubernetes, Kafka, Redis or Elasticsearch unless there is a concrete implementation requirement.

The hackathon product should be straightforward to run locally.

---

# 5. Multi-Agent System

BLACKBOX consists of three primary agents.

Each agent must operate as a distinct participant in the Switch room.

The agents should communicate through actual Switch room events and structured handoffs, not merely through internal function calls.

## 5.1 Agent A: SENTINEL

**Identity:** BLACKBOX Sentinel  
**Role:** Incident detection and investigation agent  
**Primary responsibility:** Discover incidents and establish initial evidence.

### Responsibilities

1. Monitor incoming alerts.
    
2. Identify service degradation.
    
3. Detect abnormal error rates.
    
4. Correlate incidents with deployment events.
    
5. Inspect application logs.
    
6. Identify affected services.
    
7. Assign severity.
    
8. Create an incident investigation task.
    
9. Produce a structured evidence package.
    
10. Hand off findings to RootCause.
    

### Trigger conditions

Sentinel should respond to:

- New critical monitoring alerts.
    
- Abnormal HTTP 5xx rates.
    
- High API latency.
    
- Database connection saturation.
    
- Payment failure spikes.
    
- Deployment-related error spikes.
    
- Explicit human incident reports.
    

### Event subscription

Sentinel should primarily use the documented room-event subscription mechanism to observe relevant incident activity.

It should be able to overhear new alerts and relevant human messages without requiring a direct mention for every event.

However, it should not indiscriminately process every conversation in every room.

### Example message

```
[SENTINEL]

Incident detected: INC-1042

Service: payment-service
Severity: SEV-1
HTTP 5xx rate: 18%
Average latency: 4.2 seconds

Correlations:
- Deployment #842 occurred 4 minutes before the incident.
- Database connection utilization: 94%.
- Error rate increased immediately after deployment.

Initial hypothesis:
Potential connection pool regression.

Handing investigation to RootCause.
```

### Tools

Implement modular tools such as:

- `get_service_metrics`
    
- `get_recent_logs`
    
- `get_recent_deployments`
    
- `get_deployment_diff`
    
- `get_service_health`
    
- `create_incident`
    
- `create_investigation_task`
    

For the MVP, these can use deterministic mock data.

---

## 5.2 Agent B: ROOTCAUSE

**Identity:** BLACKBOX RootCause  
**Role:** Senior autonomous debugging engineer  
**Primary responsibility:** Find the root cause and develop a tested remediation.

This agent is the main technical differentiator.

It should use a coding agent such as Claude Code or Codex to inspect an actual demo repository, understand the code and generate a real patch.

### Responsibilities

1. Receive Sentinel's investigation handoff.
    
2. Read the shared incident context.
    
3. Inspect repository structure.
    
4. Examine relevant application code.
    
5. Review recent Git commits.
    
6. Inspect deployment differences.
    
7. Identify likely faulty code or configuration.
    
8. Run diagnostics.
    
9. Develop a patch.
    
10. Execute regression tests.
    
11. Produce a remediation proposal.
    
12. Hand off to Recovery.
    

### Coding-agent integration

Prefer a genuine Claude Code or Codex integration where feasible.

The coding agent must have access only to the designated demo repository.

For the MVP, it should be able to:

- Read source files.
    
- Inspect Git history.
    
- Modify code.
    
- Generate diffs.
    
- Execute permitted tests.
    
- Report test results.
    

If a direct integration proves too time-consuming, create a coding-agent adapter with a deterministic fallback.

Do not pretend that a mocked response is an actual coding-agent execution.

Clearly identify simulated components in the demo.

### Example output

```
[ROOTCAUSE]

Investigation completed: INC-1042

Root cause:
A database connection pool configuration regression.

Affected file:
config/database.yml

Finding:
The deployment reduced the maximum connection pool size
from 50 to 10 while request concurrency remained unchanged.

Proposed remediation:
Restore the connection pool configuration.

Changes:
- Modified database.yml
- Added connection pool regression test
- Updated configuration validation

Test results:
27/27 passed

Rollback strategy:
Revert remediation commit.

Status:
Awaiting Recovery validation.
```

### Tools

- `inspect_repository`
    
- `read_source_file`
    
- `get_git_history`
    
- `get_commit_diff`
    
- `run_diagnostics`
    
- `generate_patch`
    
- `run_regression_tests`
    
- `get_test_results`
    
- `create_pull_request`
    

---

## 5.3 Agent C: RECOVERY

**Identity:** BLACKBOX Recovery  
**Role:** Release engineer and controlled execution agent  
**Primary responsibility:** Validate proposed remediation and perform approved recovery.

Recovery must be the most restricted agent.

It must never be able to bypass human authorization.

### Responsibilities

1. Receive remediation proposal.
    
2. Verify that a valid patch exists.
    
3. Review test results.
    
4. Validate operational risk.
    
5. Check service dependencies.
    
6. Generate a recovery plan.
    
7. Generate a rollback plan.
    
8. Create a Switch approval task or decision.
    
9. Wait for actual human authorization.
    
10. Verify authorization independently.
    
11. Execute the approved sandbox action.
    
12. Run post-execution health checks.
    
13. Record execution evidence.
    
14. Generate the final incident report.
    

### Mandatory security boundary

Recovery must NOT:

- Treat an ordinary chat message as approval.
    
- Execute privileged operations based on an LLM-generated claim.
    
- Approve its own tasks.
    
- Reuse approval from another incident.
    
- Execute a different patch from the one that was approved.
    
- Execute if approval has expired or been revoked.
    
- Claim recovery succeeded without verification.
    

The executor must bind the approval to:

- Incident ID.
    
- Proposed action.
    
- Patch or artifact hash.
    
- Target environment.
    
- Authorized approver.
    
- Decision status.
    

### Example approval request

```
[RECOVERY]

Recovery proposal: INC-1042

Action:
Apply database connection pool configuration fix.

Target:
payment-service / staging environment

Expected outcome:
Restore successful payment processing.

Validation:
27/27 regression tests passed.

Rollback:
Revert to previous configuration.

Risk:
Low — sandbox deployment.

Status:
AWAITING HUMAN APPROVAL

Required decision:
Approve or reject remediation.
```

### Tools

- `validate_remediation`
    
- `calculate_patch_hash`
    
- `generate_rollback_plan`
    
- `create_approval_request`
    
- `verify_switch_decision`
    
- `execute_sandbox_deployment`
    
- `run_health_checks`
    
- `generate_incident_report`
    

---

# 6. The Core End-to-End Workflow

This is the most important part of the entire product.

Implement this workflow completely before adding additional features.

## Scenario: Payment service production outage

A simulated e-commerce company experiences an outage after deploying a faulty application configuration.

### Step 1: Incident trigger

A monitoring webhook is sent to BLACKBOX.

Example payload:

```
{
  "alert_id": "ALT-1042",
  "service": "payment-service",
  "severity": "critical",
  "error_rate": 18.0,
  "latency_ms": 4200,
  "database_connection_usage": 94,
  "timestamp": "2026-11-06T10:30:00Z"
}
```

The event ingestion service processes it and creates a new incident room using the documented Switch interface.

### Step 2: Sentinel investigates

Sentinel receives the alert.

It retrieves:

- Recent application logs.
    
- Service metrics.
    
- Recent deployment history.
    
- Relevant Git commits.
    

It identifies a deployment correlation and posts its findings.

It creates an investigation task.

### Step 3: RootCause receives the handoff

RootCause receives a structured handoff containing:

- Incident ID.
    
- Affected service.
    
- Severity.
    
- Metrics.
    
- Relevant logs.
    
- Deployment ID.
    
- Investigation hypotheses.
    

RootCause reads the shared room context and starts examining the repository.

It identifies the faulty configuration.

### Step 4: RootCause creates remediation

The coding agent modifies the actual demo application.

It generates a Git diff and runs tests.

It reports:

- Root cause.
    
- Changed files.
    
- Patch hash.
    
- Test results.
    
- Potential impact.
    
- Rollback strategy.
    

### Step 5: Recovery validates the proposal

Recovery receives the patch information.

It independently validates:

- The target service.
    
- The patch artifact.
    
- Test results.
    
- Risk level.
    
- Recovery plan.
    

It creates an approval request in Switch.

### Step 6: Human approval

The incident commander reviews the proposal.

The human authorizes the actual Switch decision through the documented interface.

The approval must be associated with the specific incident and proposed action.

A chat command such as:

`APPROVE INC-1042`

can be used as the human-friendly interaction, but it must trigger or confirm a genuine Switch decision rather than directly authorize execution.

### Step 7: Controlled execution

Recovery verifies the decision.

If approved:

- Execute the patch in the isolated demo environment.
    
- Restart the simulated service if necessary.
    
- Run health checks.
    
- Verify the error rate has decreased.
    

If rejected:

- Stop execution.
    
- Record the rejection.
    
- Keep the incident open.
    
- Allow the human to request changes.
    

### Step 8: Final report

Generate an incident report and publish it in the Switch room.

The report should contain:

- Incident timeline.
    
- Initial alert.
    
- Root-cause analysis.
    
- Evidence.
    
- Git changes.
    
- Test results.
    
- Approval information.
    
- Execution result.
    
- Recovery verification.
    
- Preventive recommendations.
    

### Step 9: Organizational learning

This is an important product differentiator.

After resolution, create preventive engineering tasks.

For example:

- Add connection-pool regression tests.
    
- Introduce configuration validation.
    
- Update the deployment checklist.
    
- Improve monitoring thresholds.
    
- Update the service runbook.
    

The resolved incident should become a reusable organizational knowledge artifact.

---

# 7. Shared Incident Data Model

Define a common structured data model so agents can collaborate without losing information.

Use Pydantic models.

## Incident

```
class Incident:
    id: str
    title: str
    service: str
    severity: str
    status: str
    created_at: datetime
    room_id: str
    description: str
    metrics: dict
    affected_components: list[str]
```

## Investigation

```
class Investigation:
    id: str
    incident_id: str
    agent_id: str
    hypothesis: str
    evidence: list[dict]
    confidence: float
    status: str
    created_at: datetime
```

## Remediation

```
class Remediation:
    id: str
    incident_id: str
    root_cause: str
    description: str
    changed_files: list[str]
    patch_hash: str
    test_results: dict
    risk_level: str
    rollback_plan: str
    status: str
```

## Approval

```
class Approval:
    id: str
    incident_id: str
    remediation_id: str
    switch_decision_id: str
    requested_action: str
    target_environment: str
    approver_id: str | None
    status: str
    created_at: datetime
    decided_at: datetime | None
```

## Execution

```
class Execution:
    id: str
    incident_id: str
    approval_id: str
    patch_hash: str
    environment: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    verification_results: dict
```

Use appropriate Pydantic validation and persistence rather than relying on untyped dictionaries throughout the system.

---

# 8. Suggested Agent Handoff Schema

Agent handoffs are fundamental to the product.

Use a standardized handoff object.

```
{
  "handoff_id": "HND-1042-001",
  "incident_id": "INC-1042",
  "from_agent": "sentinel",
  "to_agent": "rootcause",
  "handoff_type": "investigation",
  "summary": "Payment service degradation correlated with deployment #842.",
  "evidence": [
    {
      "type": "metric",
      "name": "http_5xx_rate",
      "value": 18
    },
    {
      "type": "deployment",
      "id": "842"
    }
  ],
  "hypotheses": [
    "Database connection pool regression"
  ],
  "required_actions": [
    "Inspect recent deployment",
    "Review database configuration",
    "Reproduce failure"
  ],
  "room_id": "incident-payment-api-1042"
}
```

The receiving agent should acknowledge the handoff and update the room with its progress.

Do not make handoffs dependent on fragile natural-language parsing alone.

---

# 9. Incident Lifecycle State Machine

Implement explicit lifecycle transitions.

```
DETECTED
   |
   v
INVESTIGATING
   |
   v
ROOT_CAUSE_IDENTIFIED
   |
   v
REMEDIATION_STAGED
   |
   v
VALIDATING
   |
   v
AWAITING_APPROVAL
   |
   +----------------------+
   |                      |
   v                      v
APPROVED               REJECTED
   |                      |
   v                      v
EXECUTING              NEEDS_REVISION
   |                      |
   v                      |
VERIFYING <---------------+
   |
   +------------------+
   |                  |
   v                  v
RESOLVED            FAILED
   |
   v
POSTMORTEM_GENERATED
   |
   v
PREVENTIVE_TASKS_CREATED
```

Important:

- A rejected action must never proceed to execution.
    
- A failed verification must never be marked resolved.
    
- Reopening an incident must preserve the previous history.
    
- Each transition must be validated.
    
- Each consequential transition must have an auditable record.
    

---

# 10. Frontend Requirements

The frontend should feel like a professional enterprise SRE product.

Avoid a generic chatbot interface.

The product should have three primary views.

## 10.1 Incident Command Dashboard

Route: `/dashboard`

Display:

- Total active incidents.
    
- Critical incidents.
    
- Mean time to resolution.
    
- Agent activity.
    
- Pending human approvals.
    
- Resolved incidents.
    
- Recent incident timeline.
    

Use realistic, seeded demo data.

The dashboard should visually communicate that BLACKBOX is actively managing operational incidents.

## 10.2 Live Incident War Room

Route: `/incidents/:id`

This is the most important screen.

Recommended layout:

### Left panel: Incident overview

- Incident ID.
    
- Severity.
    
- Service affected.
    
- Current status.
    
- Incident timeline.
    
- Metrics.
    

### Center panel: Switch collaboration

This should display the actual messages and activities received from Switch.

Show:

- Sentinel messages.
    
- RootCause messages.
    
- Recovery messages.
    
- Human messages.
    
- Agent handoffs.
    
- Tasks.
    
- Approval decisions.
    

Make agents visually distinguishable.

### Right panel: Investigation and remediation

Display:

- Root-cause hypothesis.
    
- Evidence.
    
- Repository changes.
    
- Code diff.
    
- Test results.
    
- Recovery plan.
    
- Approval state.
    

### Bottom panel: Execution status

Show:

- Current execution stage.
    
- Approval status.
    
- Deployment state.
    
- Health checks.
    
- Recovery verification.
    

## 10.3 Incident Postmortem

Route: `/incidents/:id/report`

Display:

- Executive summary.
    
- Incident impact.
    
- Root cause.
    
- Timeline.
    
- Agent contributions.
    
- Remediation changes.
    
- Approval record.
    
- Verification metrics.
    
- Lessons learned.
    
- Preventive tasks.
    

The report should be generated from actual incident state and event history, not hardcoded UI text.

---

# 11. Visual Design Direction

BLACKBOX should look like a serious enterprise reliability engineering platform.

## Design philosophy

- Dark-first interface.
    
- High information density without clutter.
    
- Strong typography.
    
- Professional data visualization.
    
- Subtle borders and restrained colors.
    
- Live event indicators.
    
- Clear severity indicators.
    
- Excellent responsive layout.
    

## Suggested visual language

|Element|Direction|
|---|---|
|Main background|Near-black / charcoal|
|Panels|Dark slate|
|Primary accent|Electric blue|
|Critical alerts|Red|
|Successful execution|Green|
|Pending approvals|Amber|
|Agent identity|Distinct icon and accent|
|Typography|Inter or equivalent|
|Code diff|Monospace|

Avoid excessive gradients, unnecessary animations and decorative AI imagery.

The interface should look credible to an engineering director evaluating an enterprise DevOps product.

---

# 12. Demo Environment

A complete, reproducible demo environment is essential.

Do not depend on real production services.

Build a small simulated e-commerce application with:

- Payment API.
    
- Database configuration.
    
- Health endpoint.
    
- Metrics endpoint.
    
- Deployment metadata.
    
- Synthetic logs.
    

The demo application should initially work correctly.

Then introduce a controlled faulty deployment.

## Suggested simulation

Normal state:

```
{
  "service": "payment-service",
  "status": "healthy",
  "error_rate": 0.2,
  "latency_ms": 100,
  "database_pool_size": 50
}
```

Faulty deployment:

```
{
  "service": "payment-service",
  "status": "degraded",
  "error_rate": 18.0,
  "latency_ms": 4200,
  "database_pool_size": 10
}
```

The faulty configuration should be associated with a real change in the demo repository.

RootCause must discover the regression and create an actual fix.

The successful execution should restore the original behavior.

## Demo event injection

Provide a dashboard action:

`TRIGGER PAYMENT INCIDENT`

When clicked, it should:

1. Inject the synthetic monitoring event.
    
2. Trigger Sentinel.
    
3. Create the Switch incident room.
    
4. Start the agent workflow.
    
5. Display live progress.
    

The demonstration must not require manually typing each agent message.

---

# 13. Security and Governance Requirements

Security is a first-class product requirement.

Implement the following.

## Agent permissions

- Sentinel: Read operational data and create investigation artifacts.
    
- RootCause: Read repository and modify an isolated workspace.
    
- Recovery: Prepare and execute only authorized sandbox actions.
    
- Human: Approve or reject consequential actions.
    

## Execution security

1. Run code in an isolated workspace.
    
2. Restrict filesystem access.
    
3. Do not expose unrestricted shell execution.
    
4. Do not expose production credentials.
    
5. Validate every proposed operation.
    
6. Verify Switch approval independently.
    
7. Bind approval to the specific remediation hash.
    
8. Record execution results.
    
9. Provide rollback capability.
    
10. Fail closed if authorization cannot be verified.
    

## Auditability

Store:

- Original alert.
    
- Agent findings.
    
- Agent handoffs.
    
- Human messages relevant to the incident.
    
- Task creation and completion.
    
- Proposed remediation.
    
- Approval decision.
    
- Execution result.
    
- Verification result.
    

The audit trail should be append-oriented. Do not silently overwrite historical decisions.

For the hackathon, SQLite with appropriate append-only event handling is sufficient.

Do not claim cryptographic immutability unless it is actually implemented.

---

# 14. The Three-Minute Hackathon Demo

The demonstration should tell one compelling story.

Do not spend the video explaining every architecture component.

Show the product working.

## 0:00–0:25 — The incident

Open the BLACKBOX dashboard.

Show a simulated payment-service outage.

```
CRITICAL INCIDENT

Payment Service
HTTP 5xx: 18%
Latency: 4.2 seconds
Database Connections: 94%

Status: INVESTIGATING
```

Sentinel appears in the Switch room.

It correlates the alert with deployment #842.

## 0:25–0:55 — Multi-agent investigation

Sentinel posts its evidence.

RootCause receives the handoff.

RootCause begins inspecting the repository.

Show the live agent activity.

## 0:55–1:30 — The coding agent fixes the problem

RootCause identifies the faulty database connection pool configuration.

Show:

- Actual code file.
    
- Actual Git diff.
    
- Actual test execution.
    
- Test results.
    

Example:

`27/27 regression tests passed.`

## 1:30–2:00 — Human approval

Recovery posts the remediation proposal.

Show the approval interface.

The engineer reviews the action and types:

`APPROVE INC-1042`

The interface should show that a genuine Switch decision has been recorded.

## 2:00–2:35 — Autonomous recovery

Recovery validates authorization.

It executes the sandbox deployment.

Show the metrics changing:

```
BEFORE

HTTP 5xx: 18%
Latency: 4200ms

AFTER

HTTP 5xx: 0.4%
Latency: 120ms

Service: HEALTHY
```

These should be actual results from the simulated service, not manually updated dashboard values.

## 2:35–3:00 — Organizational learning

The incident report appears.

Show:

- Root cause.
    
- Patch.
    
- Approval.
    
- Execution evidence.
    
- Recovery verification.
    
- Preventive engineering tasks.
    

Final message:

**"BLACKBOX doesn't just resolve incidents. It makes every incident an opportunity to build a more resilient engineering organization."**

---

# 15. Development Roadmap

Prioritize a working vertical slice.

## Phase 1: Foundation

- Inspect Switch repository and documentation.
    
- Configure local development environment.
    
- Establish Switch connectivity.
    
- Create agent identities.
    
- Create incident rooms.
    
- Verify incoming and outgoing event communication.
    

## Phase 2: Core agents

- Implement Sentinel.
    
- Implement RootCause.
    
- Implement Recovery.
    
- Create structured handoff schemas.
    
- Implement shared incident state.
    
- Add event persistence.
    

## Phase 3: End-to-end workflow

- Implement synthetic monitoring alerts.
    
- Connect Sentinel to alert ingestion.
    
- Integrate RootCause with the demo repository.
    
- Generate real code patches.
    
- Execute regression tests.
    
- Implement approval workflow.
    
- Implement gated sandbox execution.
    
- Implement health verification.
    

## Phase 4: Frontend

- Incident command dashboard.
    
- Live incident room.
    
- Agent activity visualization.
    
- Code diff display.
    
- Approval interface.
    
- Postmortem view.
    

## Phase 5: Polish

- Error handling.
    
- Agent recovery from interruptions.
    
- Duplicate event handling.
    
- Audit trail.
    
- Demo reset functionality.
    
- Documentation.
    
- Reproducible setup.
    
- Three-minute recording.
    

---

# 16. Features: MVP vs Future Roadmap

Do not attempt to implement every feature during the hackathon.

## Must-have MVP

- Switch integration.
    
- Three distinct agent identities.
    
- Real agent-to-agent handoffs.
    
- Event monitoring.
    
- Incident room creation.
    
- Shared incident context.
    
- Synthetic monitoring data.
    
- Coding-agent repository inspection.
    
- Actual patch generation.
    
- Actual regression testing.
    
- Switch approval workflow.
    
- Independently verified authorization.
    
- Sandbox execution.
    
- Recovery verification.
    
- Incident report.
    
- Live dashboard.
    

## Nice-to-have

- Multiple simultaneous incidents.
    
- Multiple service dependencies.
    
- Agent confidence scoring.
    
- Automated runbook generation.
    
- Slack and Teams integrations.
    
- Real Prometheus integration.
    
- GitHub pull-request integration.
    
- Grafana integration.
    
- Advanced incident analytics.
    
- Organizational knowledge graph.
    

## Future enterprise vision

BLACKBOX can eventually evolve into a self-healing engineering organization.

Potential capabilities:

- Automatic incident classification.
    
- Predictive incident detection.
    
- Cross-service root-cause analysis.
    
- Autonomous regression-test generation.
    
- Deployment risk assessment.
    
- Intelligent rollback.
    
- Runbook maintenance.
    
- Recurring incident prevention.
    
- Organizational engineering memory.
    

---

# 17. What Makes BLACKBOX Different?

BLACKBOX must communicate a clear differentiation from conventional AI-powered DevOps products.

|Existing approach|BLACKBOX|
|---|---|
|Monitoring alerts|Monitoring plus collaborative investigation|
|AI troubleshooting assistant|Multiple specialized engineering agents|
|ChatOps automation|Stateful, role-based agent collaboration|
|Automated code suggestions|Actual patches and test execution|
|Static approval workflows|Context-aware human authorization|
|Incident ticket closure|Verified recovery and organizational learning|

The most important differentiator is the combination of:

1. Specialized autonomous agents.
    
2. Persistent shared incident context.
    
3. Visible agent-to-agent choreography.
    
4. Coding-agent integration.
    
5. Human-controlled execution.
    
6. Evidence-backed recovery.
    
7. Organizational learning.
    

Switch should be central to all seven.

---

# 18. Engineering Execution Instructions for Claude

When beginning development, follow this exact sequence.

### First: Repository discovery

Inspect the existing repository.

Identify:

- Current application structure.
    
- Switch integration examples.
    
- Agent Protocol implementation.
    
- Supported room operations.
    
- Authentication mechanisms.
    
- Event subscription mechanism.
    
- Task and decision APIs.
    
- Existing UI components.
    
- Existing setup and deployment instructions.
    

Produce a short architecture assessment before implementing the system.

### Second: Create the implementation plan

Propose:

- Repository structure.
    
- Backend modules.
    
- Frontend modules.
    
- Agent architecture.
    
- Event schemas.
    
- Persistence strategy.
    
- Switch integration strategy.
    
- Coding-agent integration.
    
- Execution security model.
    

Keep the implementation within the 72-hour scope.

### Third: Implement incrementally

Implement the product in working stages.

After each stage:

- Run the application.
    
- Test the relevant integration.
    
- Verify actual behavior.
    
- Fix errors before continuing.
    

Do not create placeholder implementations for critical features and claim they are complete.

### Fourth: Build the demo

Prioritize the payment-service incident scenario.

The final product must support a complete, repeatable demonstration from incident injection to verified recovery.

### Fifth: Documentation

Create:

- `README.md`
    
- `ARCHITECTURE.md`
    
- `AGENTS.md`
    
- `DEMO.md`
    
- `.env.example`
    

Document how to configure Switch, start the services, run the simulation and reset the demo.

---

# 19. Final Product Acceptance Criteria

BLACKBOX is considered ready for the hackathon only when the following conditions are met.

|Requirement|Acceptance condition|
|---|---|
|Switch integration|Agents communicate through actual Switch interfaces|
|Agent identities|Three distinct agents appear in the room|
|Multi-agent collaboration|At least two agents perform genuine handoffs|
|Shared memory|Agents consume updated incident context|
|Monitoring|An injected alert triggers the workflow|
|Root-cause analysis|Coding agent identifies the seeded defect|
|Remediation|A real patch is generated|
|Testing|Tests execute against the actual demo repository|
|Human approval|A genuine Switch decision is required|
|Authorization|Execution independently verifies the decision|
|Execution|Approved patch is applied in the sandbox|
|Verification|Health checks confirm recovery|
|Audit|An incident report is generated from actual events|
|Learning|Preventive engineering tasks are created|
|Reproducibility|The demo can be reset and executed again|

---

# 20. The North Star

BLACKBOX is not another AI chatbot inside Slack.

It is an autonomous engineering response team that operates through Switch.

The human engineer should feel like an incident commander managing a team of highly specialized AI engineers.

The agents should feel like real teammates:

- Sentinel sees trouble before anyone else.
    
- RootCause investigates like an experienced software engineer.
    
- Recovery behaves like a cautious release engineer.
    
- The human remains the ultimate authority.
    

The product should demonstrate that enterprise AI is most powerful when agents have specialized responsibilities, share persistent organizational context, collaborate across tasks, and operate within enforceable human governance.

**The ultimate promise:**

> From production incident to verified recovery, with every decision accounted for and every incident making the organization more resilient.

**Build that experience. Nothing less.**