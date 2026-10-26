# AutoHeal — Complete Dashboard Integration

## Objective

Finish integrating the existing AutoHeal backend with the existing React dashboard. The goal is a working, demo-ready incident-management and automated root-cause-analysis application.

**Do not redesign the project, rebuild the backend from scratch, or add unnecessary features.** Inspect the existing code first, reuse what is already implemented, and make the smallest changes needed to complete frontend integration.

Work autonomously. Inspect files, trace API calls, implement fixes, run the application, and test the integration. Ask me a question only if an important requirement cannot be determined from the code or existing behavior.

## 1. Existing architecture

The project is located at:

`C:\AutoHeal`

Expected local services:

| Service | URL |
|---|---|
| Mock production service | `http://localhost:8000` |
| FastAPI backend | `http://localhost:8001` |
| RCA/agent service | `http://localhost:8002` |
| Prometheus | `http://localhost:9090` |
| Alertmanager | `http://localhost:9093` |
| React/Vite dashboard | Usually `http://localhost:5173` |

Inspect the Docker Compose configuration and actual frontend configuration to confirm these ports. Do not assume the dashboard directory or API configuration without inspecting the repository.

The backend uses FastAPI and PostgreSQL. The agent uses LangGraph/Groq. Prometheus collects metrics and Alertmanager sends alerts to the backend.

## 2. Backend endpoints already identified

The following routes have been found in the backend code. Verify their request schemas, response schemas, and exact behavior by inspecting the implementation. Do not invent endpoints or request fields.

### Health

`GET /health`

Checks backend health.

### Incident management

`GET /incidents`

Lists incidents. The dashboard should use this as its primary incident-list data source.

`GET /incidents/{incident_id}`

Returns incident details, including associated events and automation information where available.

`POST /incidents/webhook/alertmanager`

Receives alerts from Alertmanager. This is an infrastructure integration endpoint, not a dashboard action.

`POST /incidents/{incident_id}/acknowledge`

Acknowledges an incident. Inspect whether the endpoint accepts a request body before implementing its frontend call.

### Root-cause analysis

`POST /incidents/{incident_id}/analyze`

Triggers RCA for an incident.

`GET /incidents/{incident_id}/rca`

Retrieves RCA results for an incident.

The RCA UI should display the actual available fields, including the root-cause explanation, supporting evidence, relevant metrics/logs/recent changes, and proposed remediation when present. Inspect the response schema and adapt the UI to it rather than assuming field names.

### Remediation workflow

`POST /incidents/{incident_id}/remediations`

Creates a remediation request based on an eligible RCA. The backend enforces eligibility and approval requirements.

`POST /incidents/{incident_id}/remediations/{remediation_id}/approve`

Approves and executes the requested remediation, followed by verification. This is the existing approval/execution endpoint.

**Human approval must remain mandatory. Never trigger this endpoint automatically just because RCA finishes or a remediation is suggested.**

There is no confirmed separate remediation-execution endpoint. Do not create one or call a guessed route.

### Endpoint inventory requirement

Inspect all backend routers and report any additional existing endpoints that are relevant to the demo. Include their HTTP method, path, purpose, request format, response format, and whether the dashboard needs to call them.

Do not add duplicate endpoints simply because the frontend expects a different route.

## 3. Required end-to-end dashboard workflow

Implement and verify the following user journey.

### A. Incident overview

- Fetch incidents from `GET /incidents`.
- Display useful information supported by the actual API response: incident ID, alert/title, affected service, severity, status, and timestamps.
- Show meaningful loading, empty, and error states.
- Allow selecting an incident to open its details.
- Refresh the data after an action or state-changing operation.
- Do not rely solely on hardcoded or mock frontend data.

### B. Incident details

- Fetch the selected incident using `GET /incidents/{incident_id}`.
- Display its status, alert details, relevant metadata, event history, and automation information returned by the backend.
- Provide an acknowledge action using the existing endpoint.
- After acknowledging, refresh the incident details and list so the UI reflects the actual backend state.

### C. RCA

- Show whether RCA has been run and whether a result is available.
- Provide an explicit action to run RCA through `POST /incidents/{incident_id}/analyze`.
- Display the real RCA response, using `GET /incidents/{incident_id}/rca` where appropriate.
- Show the root cause, supporting evidence, relevant observations, and proposed remediation using fields actually returned by the backend.
- Handle failures and missing RCA results gracefully.
- Prevent duplicate accidental submissions while a request is in progress.
- Do not fabricate RCA explanations or evidence in the UI.

### D. Remediation request

- Display the remediation proposed by the RCA, if eligible.
- Allow the user to request that remediation through `POST /incidents/{incident_id}/remediations`.
- Display the returned remediation ID, action, status, and approval requirement using the actual response.
- Handle pending requests, ineligible actions, duplicate requests, and API errors clearly.
- Do not assume every incident has an eligible remediation.

### E. Explicit human approval

- Present a clear approval action for a pending remediation.
- Show the exact action that will execute before the user approves it.
- Call `POST /incidents/{incident_id}/remediations/{remediation_id}/approve` only following an explicit user action.
- Disable repeated clicks while the request is running.
- Display the actual execution result, final status, verification result, and error details if returned.
- Refresh incident details, RCA/remediation information, and event history after the action.
- If execution fails, show that it failed. Never display success simply because the HTTP request completed or the mock service appears healthy.

### F. Audit/history

Use the incident details and available event/history data to show the lifecycle, including applicable events such as:

- Incident creation and alert receipt
- Acknowledgement
- RCA execution and result
- Remediation request
- Human approval
- Execution result
- Post-action verification

Use existing backend event data. Do not fabricate audit entries or add a new audit system unless the existing implementation genuinely lacks something essential.

## 4. Existing remediation behavior and known issue

The mock service supports fault modes:

- `normal`
- `high_errors`
- `high_latency`
- `dependency_slow`
- `dependency_timeout`
- `deployment_regression`

Its remediation endpoint supports:

- `reset_injected_fault`
- `rollback_release`

The rollback action is applicable when the release is in the regression state.

A previous remediation approval attempt for incident `4`, remediation `20`, action `rollback_release`, returned:

- Status: `failed`
- Verification: `verified: false`
- Error: `Remediation execution or verification failed: HTTPStatusError`

A subsequent mock-service health check returned `fault_mode: normal` and `release: release-stable`.

Treat this as an unresolved discrepancy. Do not assume the remediation succeeded just because the service was healthy afterward. Do not blindly rerun the action, delete records, reset PostgreSQL, or erase incident history. The dashboard must display the backend's recorded result faithfully.

First complete frontend integration. Investigate the discrepancy only if it blocks testing or the demo.

## 5. Frontend implementation requirements

Inspect the current React/Vite application before editing it.

- Reuse the existing component structure, styling, and libraries where practical.
- Centralize API calls in the existing API client, or create a small one if none exists.
- Confirm the backend base URL and CORS behavior.
- Match all calls to the actual backend HTTP methods, paths, request bodies, and response schemas.
- Handle non-2xx responses, loading states, empty data, and stale data correctly.
- Prevent stale selections or old API responses from corrupting the currently displayed incident.
- Refresh relevant data after mutations.
- Use the actual incident IDs returned by the backend.
- Do not silently fall back to fabricated incident data when an API call fails.
- Do not expose API keys, Groq credentials, or other secrets in frontend code.
- Avoid adding dependencies unless necessary.

Keep the UI straightforward and suitable for a live demonstration. Prioritize correctness and a complete incident-to-remediation workflow over visual polish.

## 6. Constraints

1. **Do not delete or reset the PostgreSQL database or Docker volumes.**
2. Do not delete the project directory, perform broad cleanup, or overwrite unrelated files.
3. Do not remove working backend functionality.
4. Do not change the mandatory human-approval gate.
5. Do not invent backend routes, schema fields, or successful execution results.
6. Do not add new product features unless required to make the existing workflow work.
7. Do not spend time debugging unrelated backend internals if the frontend can be integrated against the existing API.
8. If a backend defect genuinely blocks integration, identify the exact route, actual request, actual response/error, and minimal required fix before changing it.

## 7. Execution plan

Follow these steps in order:

1. Inspect the repository, Docker Compose configuration, backend routers, frontend components, and API client.
2. Produce a concise map of actual backend routes and response shapes relevant to the dashboard.
3. Compare existing frontend calls with those routes and identify integration gaps.
4. Implement the dashboard integration using the existing API.
5. Start or reuse the existing services without resetting persistent data.
6. Test the complete workflow: list incidents → open details → acknowledge → run RCA → request remediation → explicitly approve → display execution and verification → inspect history.
7. Test error cases, especially failed RCA, rejected/ineligible remediation, and failed execution.
8. Fix frontend defects found during testing.
9. Report what changed, which files were edited, which workflow steps were actually tested, which tests passed or failed, and any remaining blockers.

Use existing incidents where practical. If new test incidents are necessary, create them carefully and leave existing records untouched.

## Definition of done

The dashboard is demo-ready when it uses the real backend, displays actual incident and RCA data, supports the intended incident lifecycle, requires explicit approval for remediation, accurately reports execution and verification outcomes, and handles errors without pretending the operation succeeded.

Start by inspecting the repository and proceed through implementation and testing. Do not stop after producing a plan or merely listing potential issues.