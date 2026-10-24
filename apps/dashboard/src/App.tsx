
import { useCallback, useEffect, useState } from 'react'
import './App.css'
import IncidentWorkflow from './IncidentWorkflow'
const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

type Incident = {
  id: number
  fingerprint: string
  title: string
  severity: string
  status: string
  source: string
  labels: Record<string, string>
  annotations: Record<string, string>
  starts_at?: string | null
  last_seen_at?: string | null
  created_at?: string | null
  updated_at?: string | null
}

type IncidentEvent = {
  id: number
  event_type: string
  details: Record<string, unknown>
  created_at: string
}

type IncidentDetail = {
  incident: Incident
  events: IncidentEvent[]
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`)
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

function formatDate(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function readable(value: string) {
  return value.replaceAll('_', ' ')
}

function App() {
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<IncidentDetail | null>(null)
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  const loadIncidents = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true)
    else setLoadingList(true)

    try {
      const data = await apiGet<Incident[]>('/incidents')
      setIncidents(data)

      setSelectedId((current) => {
        if (current !== null && data.some((item) => item.id === current)) {
          return current
        }
        return data.length ? data[0].id : null
      })

      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load incidents')
    } finally {
      setLoadingList(false)
      setRefreshing(false)
    }
  }, [])

  const loadDetail = useCallback(async (id: number) => {
    setLoadingDetail(true)
    setDetail(null)

    try {
      const data = await apiGet<IncidentDetail>(`/incidents/${id}`)
      setDetail(data)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load incident')
    } finally {
      setLoadingDetail(false)
    }
  }, [])

  useEffect(() => {
    void loadIncidents()
  }, [loadIncidents])

  useEffect(() => {
    if (selectedId !== null) void loadDetail(selectedId)
    else setDetail(null)
  }, [selectedId, loadDetail])

  const openCount = incidents.filter((i) => i.status === 'open').length
  const acknowledgedCount = incidents.filter(
    (i) => i.status === 'acknowledged',
  ).length
  const resolvedCount = incidents.filter((i) => i.status === 'resolved').length

  const selected = detail?.incident
  const labels = selected?.labels ?? {}
  const annotations = selected?.annotations ?? {}

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <div className="brand-name">AutoHeal</div>
            <div className="brand-caption">Incident operations</div>
          </div>
        </div>

        <div className="nav-label">WORKSPACE</div>
        <div className="nav-item active">
          <span className="nav-icon">◈</span>
          Incidents
          <span className="nav-count">{incidents.length}</span>
        </div>
        <div className="nav-item muted">
          <span className="nav-icon">⌁</span>
          RCA & remediation
          <span className="soon">IN INCIDENT</span>
        </div>

        <div className="sidebar-bottom">
          <div className="connection-dot" />
          <div>
            <div className="connection-title">API connection</div>
            <div className="connection-subtitle">Via local Vite proxy</div>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div>
            <div className="eyebrow">OPERATIONS / OVERVIEW</div>
            <h1>Incident center</h1>
            <p className="page-description">
              Monitor incidents and inspect the evidence behind each event.
            </p>
          </div>
          <button
            className="button secondary"
            onClick={() => void loadIncidents(true)}
            disabled={refreshing}
          >
            <span className={refreshing ? 'spin' : ''}>↻</span>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <span>!</span>
            <div>
              <strong>Unable to complete request</strong>
              <p>{error}. Check that the backend is running on port 8001.</p>
            </div>
            <button className="dismiss" onClick={() => setError('')}>×</button>
          </div>
        )}

        <section className="stats-grid">
          <StatCard label="Total incidents" value={incidents.length} icon="◈" />
          <StatCard label="Open" value={openCount} icon="!" accent="red" />
          <StatCard
            label="Acknowledged"
            value={acknowledgedCount}
            icon="◷"
            accent="amber"
          />
          <StatCard
            label="Resolved"
            value={resolvedCount}
            icon="✓"
            accent="green"
          />
        </section>

        <section className="workspace-grid">
          <div className="panel incident-panel">
            <div className="panel-heading">
              <div>
                <h2>Incident queue</h2>
                <p>Latest incidents received by AutoHeal</p>
              </div>
              <span className="subtle-count">{incidents.length} records</span>
            </div>

            {loadingList ? (
              <div className="empty-state">Loading incidents…</div>
            ) : incidents.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">◈</div>
                <strong>No incidents yet</strong>
                <p>When Alertmanager sends an alert, it will appear here.</p>
              </div>
            ) : (
              <div className="incident-list">
                {incidents.map((incident) => (
                  <button
                    key={incident.id}
                    className={`incident-row ${
                      selectedId === incident.id ? 'selected' : ''
                    }`}
                    onClick={() => setSelectedId(incident.id)}
                  >
                    <div className={`severity-marker ${incident.severity.toLowerCase()}`} />
                    <div className="incident-row-main">
                      <div className="incident-row-title">
                        {incident.title || 'Untitled incident'}
                      </div>
                      <div className="incident-meta">
                        <span>#{incident.id}</span>
                        <span>·</span>
                        <span>{formatDate(incident.updated_at)}</span>
                      </div>
                      <div className="incident-row-bottom">
                        <StatusPill status={incident.status} />
                        <span className="severity-text">
                          {incident.severity}
                        </span>
                      </div>
                    </div>
                    <span className="row-chevron">›</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="panel detail-panel">
            {!selectedId ? (
              <div className="empty-state detail-empty">
                <div className="empty-icon">↖</div>
                <strong>Select an incident</strong>
                <p>Choose an incident from the queue to inspect its details.</p>
              </div>
            ) : loadingDetail ? (
              <div className="empty-state">Loading incident details…</div>
            ) : selected ? (
              <>
                <div className="panel-heading detail-heading">
                  <div>
                    <div className="eyebrow">INCIDENT #{selected.id}</div>
                    <h2>{selected.title}</h2>
                  </div>
                  <StatusPill status={selected.status} />
                </div>

                <div className="detail-summary">
                  <DetailField label="Severity" value={selected.severity} />
                  <DetailField label="Source" value={selected.source} />
                  <DetailField
                    label="First seen"
                    value={formatDate(selected.starts_at || selected.created_at)}
                  />
                  <DetailField
                    label="Last updated"
                    value={formatDate(selected.updated_at)}
                  />
                </div>

                <Section title="Alert labels">
                  {Object.keys(labels).length ? (
                    <div className="tag-list">
                      {Object.entries(labels).map(([key, value]) => (
                        <span className="data-tag" key={key}>
                          <b>{key}</b> {String(value)}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="muted-text">No labels recorded.</p>
                  )}
                </Section>
                <IncidentWorkflow
  incidentId={selected.id}
  onUpdated={() => {
    void loadDetail(selected.id)
    void loadIncidents(true)
  }}
/>

                <Section title="Annotations">
                  {Object.keys(annotations).length ? (
                    <div className="annotation-list">
                      {Object.entries(annotations).map(([key, value]) => (
                        <div className="annotation-item" key={key}>
                          <span>{key}</span>
                          <p>{String(value)}</p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="muted-text">No annotations recorded.</p>
                  )}
                </Section>

                <Section title="Event timeline">
                  {detail?.events.length ? (
                    <div className="timeline">
                      {detail.events.map((event) => (
                        <div className="timeline-item" key={event.id}>
                          <div className="timeline-rail">
                            <span />
                          </div>
                          <div className="timeline-content">
                            <div className="timeline-event-title">
                              {readable(event.event_type)}
                            </div>
                            <div className="timeline-date">
                              {formatDate(event.created_at)}
                            </div>
                            {Object.keys(event.details ?? {}).length > 0 && (
                              <pre className="event-details">
                                {JSON.stringify(event.details, null, 2)}
                              </pre>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="muted-text">No events recorded.</p>
                  )}
                </Section>

                <div className="detail-footer">
                  <span>Fingerprint</span>
                  <code>{selected.fingerprint}</code>
                </div>
              </>
            ) : (
              <div className="empty-state">
                Could not load this incident. Select it again or refresh.
              </div>
            )}
          </div>
        </section>

        <footer className="page-footer">
          <span>AutoHeal · Incident operations</span>
          <span>Data served by FastAPI</span>
        </footer>
      </main>
    </div>
  )
}

function StatCard({
  label,
  value,
  icon,
  accent = '',
}: {
  label: string
  value: number
  icon: string
  accent?: string
}) {
  return (
    <div className="stat-card">
      <div className={`stat-icon ${accent}`}>{icon}</div>
      <div>
        <div className="stat-label">{label}</div>
        <div className="stat-value">{value}</div>
      </div>
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  return (
    <span className={`status-pill ${status.toLowerCase()}`}>
      <span className="status-dot" />
      {readable(status)}
    </span>
  )
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-field">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  )
}

export default App
