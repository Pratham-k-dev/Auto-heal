# AutoHeal — Complete Implementation Task: Payment Dependency Fallback Remediation

You are working on my existing AutoHeal project at:

`C:\AutoHeal`

You are responsible for making the code changes directly in the repository. I do not want instructions asking me to manually edit snippets.

The goal is to add one allowlisted remediation, `enable_payment_fallback`, to the existing incident-management and Auto-RCA workflow. The feature must work end-to-end across the mock service, LangGraph agent, backend approval workflow, verification, reconciliation, and audit history.

Read the entire task before editing. Inspect the actual repository and preserve all unrelated existing functionality.

---

# A. Existing project context

AutoHeal is an incident-management and automated root-cause-analysis platform.

Existing architecture:

- **Mock service:** `apps/mock-service/app.py`
- **Backend API:** `apps/backend/app/incidents.py`
- **Agent graph:** `apps/agent/app/graph.py`
- FastAPI backend and mock service.
- LangGraph-based RCA agent.
- PostgreSQL persistence.
- Prometheus metrics and alerts.
- Alertmanager webhook ingestion.
- React dashboard.
- Docker Compose orchestration.

Known local service endpoints:

| Service | Local port |
|---|---:|
| Mock service | 8000 |
| Backend API | 8001 |
| Agent API | 8002 |
| Prometheus | 9090 |
| Alertmanager | 9093 |

The backend's `incidents.py` already contains:

- Alertmanager webhook ingestion.
- Incident creation, deduplication, updates and resolution.
- Incident event auditing.
- Persistent automation-job tracking.
- Automatic RCA invocation.
- RCA persistence.
- Human approval requests.
- Remediation execution and verification.
- Stuck-remediation reconciliation.
- Automation retry and startup recovery.

Do not remove or replace these existing capabilities.

## Existing remediation actions

Two actions already exist:

1. `reset_injected_fault`
2. `rollback_release`

The new action is:

3. `enable_payment_fallback`

The first two actions must continue to work exactly as intended.

## Existing mock-service state and faults

The mock service has state resembling:

```python
fault_mode = "normal"
release = "release-stable"
```

The existing service supports fault injection, including:

- `normal`
- `high_latency`
- `high_errors`
- `dependency_down`
- `dependency_slow`
- `dependency_timeout`
- release-regression behavior

Inspect the actual implementation for exact names and semantics before modifying it.

The project already has health, orders, dependency-call, audit-event, and administrative remediation behavior. Extend those existing functions instead of introducing parallel endpoints unnecessarily.

## Existing alert names

The backend and agent use these alert names:

- `HighErrorRate`
- `DeploymentRegression`
- `PaymentDependencySlow`
- `PaymentDependencyTimeouts`

The actual incident alert name is stored in the `incidents.labels` JSONB field under the key `alertname`.

The existing backend queries Prometheus using an alert name and checks whether that alert is still firing.

---

# B. Exact intended behavior of the new remediation

The new action is called:

`enable_payment_fallback`

This is a **simulated local fallback** for the mock payment dependency. It is not a real payment gateway integration.

The purpose is to mitigate a slow or timing-out payment dependency while preserving the underlying fault for diagnosis.

When the current fault is either `dependency_slow` or `dependency_timeout`, the approved remediation must:

1. Enable the local payment fallback.
2. Keep the current fault mode unchanged.
3. Keep the current release unchanged.
4. Make `/api/orders` return HTTP 200.
5. Include `"payment_dependency": "fallback"` in the orders JSON response.
6. Skip the normal payment-dependency call while the fallback is active for those supported fault modes.
7. Record an audit event whenever the fallback is used.
8. Expose `payment_fallback_enabled` through `/health`.
9. Verify that the service is functioning through the fallback.
10. Verify that the corresponding incident alert has cleared before declaring success.

This action is a mitigation, not a repair of the underlying dependency. The fault mode should remain `dependency_slow` or `dependency_timeout`; the release should remain whatever it was before the remediation.

The action must not claim success if the fallback was enabled but orders still fail, the response does not identify the fallback, the release changed unexpectedly, or the relevant alert remains firing.

---

# C. Critical constraints

These are mandatory.

1. **Human approval must remain mandatory.** RCA may propose a remediation and create a pending approval request, but it must never execute a remediation without an explicit approval request from a human.
2. Do not reset, recreate, delete, or migrate the existing database merely to implement this feature.
3. Do not delete Docker volumes or existing incident/remediation history.
4. Never run `docker compose down -v`.
5. Do not change unrelated routes, response formats, database tables, or workflow behavior unless required for correctness.
6. Do not invent new database columns when existing fields can support the feature.
7. Do not replace real existing behavior with mock success responses that bypass actual verification.
8. Preserve the existing audit trail.
9. Do not weaken existing validation or allowlist checks.
10. Do not make unrelated changes to the RCA graph.
11. Preserve the `DeploymentRegression` eligibility condition in `apps/agent/app/graph.py`. It intentionally omits `alert_is_firing`. Do not add that condition as part of this task.
12. Inspect the actual source files and Git diff before editing. Previous manual edits may have introduced malformed syntax and indentation.
13. Make the changes yourself. Do not return a plan and ask me to apply it manually.

---

# D. Changes required in `apps/mock-service/app.py`

Inspect the current implementation first.

## D1. Add fallback state

Ensure the module has:

```python
payment_fallback_enabled = False
```

Keep this alongside the existing fault and release state. Follow the existing locking convention whenever reading or updating shared state.

## D2. Extend `/health`

The health response must include:

```json
{
  "payment_fallback_enabled": false
}
```

The value must reflect the actual shared state, not a hardcoded constant.

Preserve all existing health-response fields.

## D3. Extend `/api/orders`

Inspect how orders currently call the payment dependency.

When both conditions hold:

- `payment_fallback_enabled is True`
- `fault_mode` is either `dependency_slow` or `dependency_timeout`

the orders handler must:

- Skip the ordinary payment dependency call.
- Use the existing response structure as much as possible.
- Return HTTP 200.
- Set `"payment_dependency": "fallback"`.
- Record an audit event identifying the fallback usage.
- Preserve the rest of the orders behavior.

When fallback is disabled, or the fault mode is not one of the two supported dependency faults, preserve the existing behavior.

Do not globally bypass dependency calls for every fault mode.

Do not accidentally use a stale snapshot of `fault_mode` or fallback state if the existing code uses a lock for consistent reads.

## D4. Extend fault injection

When a new fault is injected, disable the previous fallback state:

```python
payment_fallback_enabled = False
```

Follow the existing lock and global-state conventions.

Preserve existing fault-injection behavior, especially release-regression behavior.

Do not change the current release merely because fallback is reset.

## D5. Extend `/admin/remediation`

Add `enable_payment_fallback` to the existing allowlist.

For this action:

- Accept only when the current fault mode is `dependency_slow` or `dependency_timeout`.
- Otherwise return HTTP 409.
- Set `payment_fallback_enabled = True`.
- Do not change `fault_mode`.
- Do not change `release`.
- Return the resulting state, including at least `fault_mode`, `release`, and `payment_fallback_enabled`.

Preserve the existing semantics of the two original actions.

### `reset_injected_fault`

- Set `fault_mode = "normal"`.
- Disable payment fallback.
- Do not change the release.

### `rollback_release`

- Preserve the existing rollback semantics.
- For the known release-regression scenario, return the service to `release-stable` and normal fault state as the existing implementation intends.

Do not combine these two actions into a single implementation that accidentally resets the release for fault reset or changes the release during fallback.

---

# E. Changes required in `apps/agent/app/graph.py`

Inspect the actual LangGraph implementation and make the action consistent across evidence collection and analysis.

## E1. PaymentDependencySlow

The `PaymentDependencySlow` evidence branch must select:

```python
eligible_action = "enable_payment_fallback"
```

only when its required evidence is valid, the alert is firing, latency exceeds the existing threshold, and service health is valid.

The existing threshold in the implementation is expected to be greater than `1.0` for latency. Preserve the existing evidence semantics rather than inventing a new threshold.

The eligible reason should explain that dependency latency exceeds the threshold and fallback is eligible for human approval.

If the evidence is invalid, the alert is not firing, the threshold is not exceeded, or service health is invalid, mark the action ineligible with a meaningful reason.

## E2. PaymentDependencyTimeouts

The `PaymentDependencyTimeouts` evidence branch must select:

```python
eligible_action = "enable_payment_fallback"
```

only when required evidence is valid, the alert is firing, timeout ratio exceeds the existing threshold, and service health is valid.

The existing timeout-ratio threshold is expected to be greater than `0.2`. Preserve the actual code's evidence validation and threshold semantics.

## E3. Allowlist and approval requirements

Update all relevant action allowlists in the graph:

- The model prompt's list of allowed actions.
- The server-side `allowed_actions` set.
- Any additional validation that rejects unknown actions.

Keep `requires_approval = True` for executable remediation proposals.

Ensure the final evidence contains the appropriate remediation eligibility and action fields.

**Do not change the existing `DeploymentRegression` eligibility condition to add `alert_is_firing`.** This is an explicit scope constraint.

Do not remove evidence-backed RCA or replace the graph with hardcoded action selection.

---

# F. Required repairs in `apps/backend/app/incidents.py`

I previously pasted the complete contents of this file. The latest manually edited version contains errors, so inspect the actual current file rather than trusting the pasted version as perfectly valid.

The latest pasted version includes all existing incident and automation endpoints, and has attempted to add the new action. The following problems were visible and must be checked and corrected.

## F1. Imports and module structure

The pasted version has duplicate imports, including duplicate imports from `fastapi`.

Clean these up safely. Preserve all required imports, including the existing database connection, JSONB wrapper, HTTP client, asyncio, logging, environment-variable access, and FastAPI background tasks.

Ensure `logger` is defined before it is used at runtime. Preserve the startup-recovery task tracking set.

Do not remove existing routes or functions.

## F2. `run_automatic_workflow()`

Preserve the existing automation flow:

1. Atomically claim a pending automation job.
2. Mark it running and increment attempts.
3. Invoke the existing RCA endpoint logic.
4. If the RCA proposes an allowlisted remediation and requires approval, create a pending remediation request.
5. Verify that the remediation refers to the latest analysis and that its action matches the RCA.
6. Mark the investigation job completed when the investigation and decision finish.
7. Record success or failure in the existing audit trail.

Add `enable_payment_fallback` to the executable-action set.

Do not automatically approve or execute the remediation.

## F3. `analyze_incident()`

The backend's RCA validation must accept:

- `reset_injected_fault`
- `rollback_release`
- `enable_payment_fallback`
- `no_action`

Preserve validation of root cause, confidence, supporting evidence, evidence gaps, action reason, and approval flag.

Preserve existing database persistence in `rca_analyses` and the `rca_completed` audit event.

## F4. `request_remediation()`

The action allowlist must include all three executable actions:

```python
{
    "reset_injected_fault",
    "rollback_release",
    "enable_payment_fallback",
}
```

Preserve the existing behavior:

- Use the latest RCA analysis.
- Reject when no RCA exists.
- Reject when the proposed action is not executable.
- Reject when approval is not required by the RCA.
- Reuse the same pending request when it matches the latest analysis.
- Prevent conflicting concurrent executing requests.
- Reject stale pending requests superseded by newer RCA results.
- Persist the remediation and its audit event.

## F5. `approve_remediation()` SQL bug

The latest pasted version contains a malformed SQL allowlist resembling:

```sql
AND action IN (
    'reset_injected_fault',
    'rollback_release'
    'enable_payment_fallback'
)
```

There is a missing comma between the final two action strings. Correct this SQL.

The action filter must include all three actions with proper commas.

Keep the pending-to-executing transition, approval timestamp, and audit event.

## F6. Correctly select the incident's alert name

The actual alert name must come from the incident record, not from an assumed action-to-single-alert mapping.

The incident schema already stores alert labels in:

`incidents.labels`

The alert name is:

`incidents.labels["alertname"]`

Use the incident ID passed to the approval or reconciliation endpoint to retrieve the corresponding labels.

Create a small shared helper if appropriate, for example:

```python
_expected_alert_for_incident(action, labels)
```

The helper must validate the actual alert name against this allowlist:

```python
{
    "reset_injected_fault": {"HighErrorRate"},
    "rollback_release": {"DeploymentRegression"},
    "enable_payment_fallback": {
        "PaymentDependencySlow",
        "PaymentDependencyTimeouts",
    },
}
```

Return the actual incident alert name as a single string.

Reject missing or unexpected alert names safely.

Do not set the fallback alert to `None`.

Do not arbitrarily choose `PaymentDependencySlow` for a timeout incident, or vice versa.

Handle the `incidents.labels` JSONB value according to the actual PostgreSQL driver behavior. If the driver already returns a dictionary, use it directly; if it returns a JSON string, decode it safely.

The existing Prometheus query should continue to use a single string variable:

```python
expected_alert
```

and query the corresponding firing alert:

```python
alert_response = await client.get(
    f"{prometheus_url}/api/v1/query",
    params={
        "query": (
            f'ALERTS{{alertname="{expected_alert}",'
            'alertstate="firing"}'
        )
    },
)
```

Validate the Prometheus response and treat any non-success status as a verification failure.

## F7. Capture the pre-action health snapshot

In `approve_remediation()`, fetch `/health` before executing the remediation.

Store the returned state in:

```python
pre_action_health
```

Persist this snapshot in the existing `remediation_actions.verification` JSONB field while the remediation is executing. The final verification result can later replace or extend this JSON.

This snapshot is essential because:

- `reset_injected_fault` must preserve the release.
- `enable_payment_fallback` must preserve both the fault mode and release.
- Reconciliation may need to resume after an interrupted execution.

Do not attempt to infer the previous state from the current state after remediation.

If the mock service or health endpoint fails before the action, fail safely and persist the remediation's failure status and audit event where possible.

## F8. Action-specific action-response validation

After calling:

`POST {service_url}/admin/remediation`

parse the returned JSON and validate it by action.

### `reset_injected_fault`

Require:

- `fault_mode == "normal"`
- `release` equals the release from `pre_action_health`

Fallback should be disabled as a result of resetting the fault if that is the existing intended mock-service behavior.

### `rollback_release`

Require:

- `fault_mode == "normal"`
- `release == "release-stable"`

### `enable_payment_fallback`

Require:

- `payment_fallback_enabled is True`
- `fault_mode` is `dependency_slow` or `dependency_timeout`
- `fault_mode` equals the pre-action fault mode
- `release` equals the pre-action release

Do not use a blanket condition that requires every action to leave the service in normal fault mode and stable release.

## F9. Fix the approval verification loop

The previous manual edits accidentally placed `pre_action_response` and the pre-action health snapshot inside the verification-loop area with broken indentation. Fix the entire function structure.

The intended order is:

1. Claim the remediation for execution.
2. Retrieve the incident alert name.
3. Capture pre-action health.
4. Execute the approved remediation.
5. Validate the immediate action response.
6. Persist the pre-action snapshot and execution state.
7. Run ten recovery-verification samples.
8. Record final verification and audit results.
9. Mark the remediation succeeded or failed.

Preserve the existing verification interval: ten samples with five seconds between samples, meaning nine sleeps between the ten samples.

For each sample:

- Fetch health.
- Fetch `/api/orders`.
- Parse the orders response JSON safely.
- Query Prometheus for the actual incident alert.
- Record service and alert results.

If an HTTP request fails, Prometheus returns a non-success response, or response parsing fails in a way that prevents verification, treat the verification as failed and preserve the error information.

### Action-specific service verification

For `reset_injected_fault`, require:

- Health status is `ok`.
- Fault mode is `normal`.
- Release equals `pre_action_health["release"]`.
- Orders return HTTP 200.

For `rollback_release`, require:

- Health status is `ok`.
- Fault mode is `normal`.
- Release is `release-stable`.
- Orders return HTTP 200.

For `enable_payment_fallback`, require:

- Health status is `ok`.
- `payment_fallback_enabled is True`.
- Fault mode remains either `dependency_slow` or `dependency_timeout`.
- Fault mode equals the pre-action fault mode.
- Release equals the pre-action release.
- Orders return HTTP 200.
- The orders JSON contains `"payment_dependency": "fallback"`.

Use a shared helper for these checks if it keeps the implementation clear and consistent between approval and reconciliation.

### Alert verification

For every sample, query Prometheus for the actual incident alert name.

The remediation is verified only when:

- All ten service checks pass.
- All ten Prometheus queries succeed.
- The relevant alert is not firing in any of the ten samples.

A fallback remediation must not be marked successful just because the orders endpoint returns HTTP 200. The fallback state, unchanged fault/release state, and corresponding alert-clear condition must also pass.

Persist the final status, verification JSON, completion timestamp, error field, and existing `remediation_succeeded` or `remediation_failed` audit event.

## F10. Correct `reconcile_remediation()`

The existing reconciliation function handles interrupted executions, but the pasted version has an incorrect blanket service-state check.

The reconciliation query must continue selecting the requested remediation by incident ID and remediation ID, require `status = 'executing'`, and allow all three actions.

It must load the incident labels from the associated incident record and use the same alert-name helper as `approve_remediation()`.

Read the previously persisted pre-action health snapshot from the existing `verification` JSONB field.

If the snapshot is missing for an action that requires verifying an unchanged prior state, fail reconciliation safely rather than guessing.

### Reconciliation service checks

Use the same action-specific checks as approval:

- **Reset:** fault mode normal; release equals pre-action release; orders return HTTP 200; health is OK.
- **Rollback:** fault mode normal; release is stable; orders return HTTP 200; health is OK.
- **Fallback:** fallback is enabled; the original dependency fault remains active and unchanged; release is unchanged; orders return HTTP 200 with `"payment_dependency": "fallback"`; health is OK.

Query Prometheus for the actual incident alert and verify that it is clear.

If service state is correct but the alert is still firing, reconciliation must fail.

If the alert is clear but the service state is wrong, reconciliation must fail.

Only mark reconciliation successful when both conditions pass.

Persist the updated status, verification JSON, completion timestamp, error information, and audit event using the existing database structure.

Do not add a database table or schema migration just for this feature.

## F11. Preserve the remaining backend routes

Do not remove or break:

- Alertmanager webhook ingestion.
- Incident listing and details.
- Acknowledgement.
- RCA execution and history.
- Remediation request creation.
- Remediation listing.
- Human approval.
- Automation startup recovery.
- Automation retry.
- Stuck-remediation reconciliation.

Preserve their existing response shapes as much as possible.

---

# G. Known bugs in the last manually edited version

The last `incidents.py` text I shared with ChatGPT contained the following visible problems. Check the actual repository for each of them:

1. Duplicate FastAPI imports.
2. A malformed SQL allowlist in `approve_remediation()` due to a missing comma.
3. Misplaced pre-action health retrieval and invalid indentation around the verification loop.
4. Verification sampling, alert querying, and sleep calls incorrectly indented beneath an `else` branch.
5. `expected_alert` set to `None` for `enable_payment_fallback`.
6. A declared `expected_alerts` set mapping that was not used to select the actual incident alert.
7. `reconcile_remediation()` still required `fault_mode == "normal"` and `release == "release-stable"` for all actions.
8. Reconciliation did not correctly validate the fallback response.
9. Reconciliation did not reliably retrieve the incident's actual alert name.
10. Reconciliation needed access to the pre-action health snapshot to safely validate unchanged state.
11. The action response and final verification data must not overwrite the pre-action snapshot before reconciliation can recover it.
12. Error paths must not leave remediation rows indefinitely executing when failure can safely be persisted.

Fix the actual code rather than blindly applying these descriptions if the current repository has already changed.

---

# H. Testing and validation requirements

After editing, run the following checks from the project root, using the appropriate Python interpreter/environment:

```powershell
python -m py_compile .\apps\mock-service\app.py
python -m py_compile .\apps\backend\app\incidents.py
python -m py_compile .\apps\agent\app\graph.py
git diff --check
```

If the repository uses a virtual environment or `uv`, use the existing project environment rather than installing dependencies unnecessarily.

Inspect the diff carefully.

Confirm:

- `DeploymentRegression` eligibility was not changed to add `alert_is_firing`.
- The three remediation actions remain allowlisted.
- Human approval is still required.
- The fallback action does not change the fault mode or release.
- The backend uses the incident's actual alert name.
- The approval verification and reconciliation checks are consistent.
- No existing database records or Docker volumes were deleted.
- No unrelated routes or functionality were removed.

Run relevant existing tests if available. If a test needs running Docker services, check their state first and avoid destructive commands.

If safe, perform a smoke test of the new fallback behavior, but do not reset the existing database or erase project state.

If any test fails, diagnose and fix the issue, then rerun the relevant checks.

---

# I. Final deliverable

Make the actual repository changes.

At the end, report:

1. Every file changed.
2. What changed in each file.
3. Which syntax checks and tests were actually run.
4. The actual pass/fail result of each check.
5. Any remaining limitations or manual smoke tests.
6. Confirmation that explicit human approval remains mandatory.
7. Confirmation that the existing database, incident history, remediation history, and Docker volumes were preserved.

Do not merely produce another plan or tell me to edit snippets. Implement and validate the changes directly.