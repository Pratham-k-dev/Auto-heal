
import { useCallback, useEffect, useState } from 'react'
import './IncidentWorkflow.css'

const API =
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

type RCA = {
  id?: number
  analysis_id?: number
  incident_id: number
  root_cause: string
  confidence: number
  supporting_evidence: unknown[]
  evidence_gaps: unknown[]
  proposed_action: string
  action_reason: string
  requires_approval: boolean
  created_at?: string
}

type Remediation = {
  id: number
  action: string
  status: string
  approved_at?: string | null
  completed_at?: string | null
  verification?: Record<string, unknown> | null
  error?: string | null
}

async function request<T>(path: string, method = 'GET'): Promise<T> {
  const response = await fetch(`${API}${path}`, { method })
  const text = await response.text()

  let data: any
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    throw new Error(
      `API returned non-JSON content (${response.status}). Check the backend URL.`,
    )
  }

  if (!response.ok) {
    const detail = data?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : detail?.message || JSON.stringify(detail) ||
          `Request failed (${response.status})`
    throw new Error(message)
  }

  return data as T
}

function showValue(value: unknown) {
  return typeof value === 'string'
    ? value
    : JSON.stringify(value, null, 2)
}

function WorkflowSection({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="workflow-section">
      <h3>{title}</h3>
      {children}
    </section>
  )
}

export default function IncidentWorkflow({
  incidentId,
  onUpdated,
}: {
  incidentId: number
  onUpdated: () => void
}) {
  const [analyses, setAnalyses] = useState<RCA[]>([])
  const [remediations, setRemediations] = useState<Remediation[]>([])
  const [loading, setLoading] = useState(true)
  const [analyzing, setAnalyzing] = useState(false)
  const [requesting, setRequesting] = useState(false)
  const [approvingId, setApprovingId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const loadWorkflow = useCallback(async () => {
    setLoading(true)
    setError('')

    try {
      const [rcaData, remediationData] = await Promise.all([
        request<{ analyses: RCA[] }>(`/incidents/${incidentId}/rca`),
        request<{ remediations: Remediation[] }>(
          `/incidents/${incidentId}/remediations`,
        ),
      ])

      setAnalyses(rcaData.analyses)
      setRemediations(remediationData.remediations)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not load workflow',
      )
    } finally {
      setLoading(false)
    }
  }, [incidentId])

  useEffect(() => {
    void loadWorkflow()
  }, [loadWorkflow])

  const latest = analyses[0]
  const actionInProgress = remediations.some(
    (item) => item.status === 'pending' || item.status === 'executing',
  )
  const canRequestRemediation = Boolean(
    latest &&
      [
        'reset_injected_fault',
        'rollback_release',
        'enable_payment_fallback',
      ].includes(
        latest.proposed_action,
      ) &&
      latest.requires_approval,
  )

  async function analyze() {
    setAnalyzing(true)
    setError('')
    setMessage('')

    try {
      const result = await request<RCA>(
        `/incidents/${incidentId}/analyze`,
        'POST',
      )
      setMessage('RCA analysis completed.')
      await loadWorkflow()
      onUpdated()
      // Keep the returned result useful even if history refresh fails.
      if (!result.root_cause) {
        setError('The agent returned no root cause.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'RCA failed')
    } finally {
      setAnalyzing(false)
    }
  }

  async function requestAction() {
    setRequesting(true)
    setError('')
    setMessage('')

    try {
      await request<Remediation>(
        `/incidents/${incidentId}/remediations`,
        'POST',
      )
      setMessage('Remediation request created. Approval is still required.')
      await loadWorkflow()
      onUpdated()
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not request remediation',
      )
      await loadWorkflow()
    } finally {
      setRequesting(false)
    }
  }

  async function approve(remediationId: number) {
    const confirmed = window.confirm(
      'Approve and execute this remediation? The mock service will be changed to normal mode and recovery checks will run.',
    )
    if (!confirmed) return

    setApprovingId(remediationId)
    setError('')
    setMessage('Executing remediation and verifying recovery. This may take about 45 seconds.')

    try {
      const result = await request<{
        status: string
        verification: Record<string, unknown>
        error?: string | null
      }>(
        `/incidents/${incidentId}/remediations/${remediationId}/approve`,
        'POST',
      )

      setMessage(
        result.status === 'succeeded'
          ? 'Remediation succeeded. Review the verification results below.'
          : `Remediation ${result.status}: ${result.error || 'Recovery was not confirmed.'}`,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Approval failed')
      setMessage('')
    } finally {
      setApprovingId(null)
      await loadWorkflow()
      onUpdated()
    }
  }

  return (
    <div className="incident-workflow">
      <div className="workflow-heading">
        <div>
          <div className="eyebrow">AUTOMATED OPERATIONS</div>
          <h2>RCA &amp; remediation</h2>
        </div>
        <button
          className="button secondary"
          onClick={() => void loadWorkflow()}
          disabled={loading || analyzing || requesting || approvingId !== null}
        >
          Refresh workflow
        </button>
      </div>

      {error && <div className="workflow-error">{error}</div>}
      {message && <div className="workflow-message">{message}</div>}

      <WorkflowSection title="1. Root cause analysis">
        <button
          className="button primary"
          onClick={() => void analyze()}
          disabled={loading || analyzing || requesting || approvingId !== null}
        >
          {analyzing ? 'Analyzing…' : 'Analyze RCA'}
        </button>

        {loading ? (
          <p className="workflow-muted">Loading analysis history…</p>
        ) : !latest ? (
          <p className="workflow-muted">
            No RCA yet. Run an analysis to inspect the evidence and proposed action.
          </p>
        ) : (
          <div className="workflow-result">
            <div className="workflow-result-top">
              <span className="workflow-label">Latest analysis</span>
              <span className="workflow-badge">
                {(latest.confidence * 100).toFixed(0)}% confidence
              </span>
            </div>

            <h4>Root cause</h4>
            <p>{latest.root_cause}</p>

            <h4>Supporting evidence</h4>
            {latest.supporting_evidence?.length ? (
              <ul>
                {latest.supporting_evidence.map((item, index) => (
                  <li key={index}>{showValue(item)}</li>
                ))}
              </ul>
            ) : (
              <p className="workflow-muted">No supporting evidence returned.</p>
            )}

            <h4>Evidence gaps</h4>
            {latest.evidence_gaps?.length ? (
              <ul>
                {latest.evidence_gaps.map((item, index) => (
                  <li key={index}>{showValue(item)}</li>
                ))}
              </ul>
            ) : (
              <p className="workflow-muted">No evidence gaps reported.</p>
            )}

            <div className="workflow-action">
              <span className="workflow-label">Proposed action</span>
              <strong>{latest.proposed_action.replaceAll('_', ' ')}</strong>
              <p>{latest.action_reason}</p>
              <p>
                Approval required:{' '}
                <strong>{latest.requires_approval ? 'Yes' : 'No'}</strong>
              </p>
            </div>

            {canRequestRemediation && (
              <button
                className="button primary"
                onClick={() => void requestAction()}
                disabled={
                  requesting || actionInProgress ||
                  analyzing || approvingId !== null
                }
              >
                {requesting ? 'Requesting…' : actionInProgress
                  ? 'Remediation already in progress'
                  : 'Request remediation'}
              </button>
            )}

            {!canRequestRemediation && latest.requires_approval && (
              <p className="workflow-muted">
                The latest RCA does not propose an eligible remediation action for this incident.
              </p>
            )}
          </div>
        )}
      </WorkflowSection>

      <WorkflowSection title="2. Approval & execution">
        {loading ? (
          <p className="workflow-muted">Loading remediation history…</p>
        ) : remediations.length === 0 ? (
          <p className="workflow-muted">
            No remediation requests yet. Run RCA and request an eligible action first.
          </p>
        ) : (
          <div className="workflow-history">
            {remediations.map((item) => (
              <div className="workflow-remediation" key={item.id}>
                <div className="workflow-result-top">
                  <strong>Remediation #{item.id}</strong>
                  <span className={`workflow-status ${item.status}`}>
                    {item.status}
                  </span>
                </div>
                <p>Action: {item.action.replaceAll('_', ' ')}</p>
                {item.error && (
                  <p className="workflow-error">{item.error}</p>
                )}

                {item.status === 'pending' && (
                  <button
                    className="button primary"
                    onClick={() => void approve(item.id)}
                    disabled={approvingId !== null || analyzing || requesting}
                  >
                    {approvingId === item.id
                      ? 'Executing and verifying…'
                      : 'Approve & execute'}
                  </button>
                )}

                {item.status === 'executing' && (
                  <p className="workflow-muted">
                    Execution is in progress. Refresh the workflow to check status.
                  </p>
                )}

                {item.verification && (
                  <div className="verification-block">
                    <h4>Verification results</h4>
                    <div className="verification-summary">
                      {(['service_stable', 'alert_cleared', 'verified'] as const).map(
                        (key) => (
                          <div className="verification-item" key={key}>
                            <span>{key.replaceAll('_', ' ')}</span>
                            <strong>
                              {String(item.verification?.[key] ?? 'unknown')}
                            </strong>
                          </div>
                        ),
                      )}
                    </div>
                    <details>
                      <summary>View detailed recovery checks</summary>
                      <pre>{JSON.stringify(item.verification, null, 2)}</pre>
                    </details>
                  </div>
                )}

                {item.completed_at && (
                  <p className="workflow-muted">
                    Completed: {new Date(item.completed_at).toLocaleString()}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </WorkflowSection>
    </div>
  )
}
