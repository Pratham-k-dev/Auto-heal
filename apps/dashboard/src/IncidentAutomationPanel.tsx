
import { useCallback, useEffect, useState } from "react";

const API =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8001";

type RCA = {
  id?: number;
  analysis_id?: number;
  root_cause: string;
  confidence: number;
  supporting_evidence: string[];
  evidence_gaps: string[];
  proposed_action: string;
  action_reason: string;
  requires_approval: boolean;
  created_at?: string;
};

type Remediation = {
  id: number;
  incident_id: number;
  rca_analysis_id: number;
  action: string;
  status: string;
  created_at: string;
  approved_at: string | null;
  completed_at: string | null;
  verification: {
    verified?: boolean;
    alert_cleared?: boolean;
    service_stable?: boolean;
    fault_mode_set?: string;
    recovery_checks?: Array<{
      passed: boolean;
      order_status_code: number;
    }>;
  };
  error: string | null;
};

type Props = {
  incidentId: number;
};

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

function prettyDate(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function formatConfidence(value: number) {
  return `${Math.round(value * 100)}%`;
}

export default function IncidentAutomationPanel({
  incidentId,
}: Props) {
  const [rca, setRca] = useState<RCA | null>(null);
  const [remediations, setRemediations] = useState<Remediation[]>([]);
  const [loading, setLoading] = useState(true);
  const [approvingId, setApprovingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [rcaResult, remediationResult] = await Promise.all([
        getJSON<{ analyses: RCA[] }>(
          `${API}/incidents/${incidentId}/rca`
        ),
        getJSON<{ remediations: Remediation[] }>(
          `${API}/incidents/${incidentId}/remediations`
        ),
      ]);

      setRca(rcaResult.analyses?.[0] ?? null);
      setRemediations(remediationResult.remediations ?? []);
      setError("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to load automation status"
      );
    } finally {
      setLoading(false);
    }
  }, [incidentId]);

  useEffect(() => {
    setLoading(true);
    void refresh();

    const timer = window.setInterval(() => {
      void refresh();
    }, 5000);

    return () => window.clearInterval(timer);
  }, [refresh]);

  async function approve(remediation: Remediation) {
    if (
      !window.confirm(
        `Approve and execute "${remediation.action}" for incident ${incidentId}?`
      )
    ) {
      return;
    }

    setApprovingId(remediation.id);
    setError("");
    setNotice("");

    try {
      const response = await fetch(
        `${API}/incidents/${incidentId}/remediations/${remediation.id}/approve`,
        { method: "POST" }
      );

      const result = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          typeof result.detail === "string"
            ? result.detail
            : `Approval failed (${response.status})`
        );
      }

      setNotice(
        result.status === "succeeded"
          ? "Remediation succeeded. Recovery verification completed."
          : "Approval processed. Refreshing the latest execution status."
      );

      await refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Approval failed"
      );
    } finally {
      setApprovingId(null);
    }
  }

  if (loading) {
    return <section className="automation-panel">Loading automation status…</section>;
  }

  const latestRemediation = remediations[0];

  return (
    <section className="automation-panel">
      <div className="automation-header">
        <div>
          <h2>Automated investigation</h2>
          <p>Incident #{incidentId} · refreshes every 5 seconds</p>
        </div>
        <button type="button" onClick={() => void refresh()}>
          Refresh
        </button>
      </div>

      {error && <p className="automation-error">{error}</p>}
      {notice && <p className="automation-notice">{notice}</p>}

      <div className="automation-section">
        <h3>Root cause analysis</h3>

        {!rca ? (
          <p>No RCA result is available yet. Waiting for the backend workflow.</p>
        ) : (
          <>
            <p className="automation-root-cause">{rca.root_cause}</p>
            <p>
              Confidence: <strong>{formatConfidence(rca.confidence)}</strong>
            </p>
            <p>
              Action proposed: <strong>{rca.proposed_action}</strong>
            </p>
            <p>{rca.action_reason}</p>
            <p className="automation-muted">
              Analysis #{rca.id ?? rca.analysis_id ?? "—"} ·{" "}
              {prettyDate(rca.created_at)}
            </p>

            <h4>Supporting evidence</h4>
            {rca.supporting_evidence?.length ? (
              <ul>
                {rca.supporting_evidence.map((item, index) => (
                  <li key={`${index}-${item}`}>{item}</li>
                ))}
              </ul>
            ) : (
              <p>No supporting evidence supplied.</p>
            )}

            {rca.evidence_gaps?.length > 0 && (
              <>
                <h4>Evidence gaps</h4>
                <ul>
                  {rca.evidence_gaps.map((item, index) => (
                    <li key={`${index}-${item}`}>{item}</li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>

      <div className="automation-section">
        <h3>Remediation approval</h3>

        {!latestRemediation ? (
          <p>No remediation request exists yet.</p>
        ) : (
          <>
            <p>
              Action: <strong>{latestRemediation.action}</strong>
            </p>
            <p>
              Status:{" "}
              <span className={`automation-status status-${latestRemediation.status}`}>
                {latestRemediation.status}
              </span>
            </p>
            <p className="automation-muted">
              Requested: {prettyDate(latestRemediation.created_at)}
            </p>

            {latestRemediation.status === "pending" && (
              <button
                type="button"
                className="automation-approve"
                disabled={approvingId !== null}
                onClick={() => void approve(latestRemediation)}
              >
                {approvingId === latestRemediation.id
                  ? "Executing and verifying…"
                  : "Approve & execute"}
              </button>
            )}

            {["executing"].includes(latestRemediation.status) && (
              <p>Remediation is executing. Waiting for verification…</p>
            )}

            {latestRemediation.status === "succeeded" && (
              <div>
                <h4>Recovery verification</h4>
                <p>
                  Verified:{" "}
                  {latestRemediation.verification?.verified ? "Yes" : "No"}
                </p>
                <p>
                  Service stable:{" "}
                  {latestRemediation.verification?.service_stable
                    ? "Yes"
                    : "No"}
                </p>
                <p>
                  Alert cleared:{" "}
                  {latestRemediation.verification?.alert_cleared
                    ? "Yes"
                    : "No"}
                </p>
                <p>
                  Fault mode:{" "}
                  {latestRemediation.verification?.fault_mode_set ?? "—"}
                </p>
              </div>
            )}

            {latestRemediation.status === "failed" && (
              <p className="automation-error">
                Execution failed: {latestRemediation.error ?? "Unknown error"}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
