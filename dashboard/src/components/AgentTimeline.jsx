import React, { useEffect, useState } from 'react'
import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
})

// Human-readable agent name map
const AGENT_LABELS = {
  failure_classifier:   'Failure Classifier',
  log_analyzer:         'Log Analyzer',
  root_cause_analyzer:  'Root Cause Analyzer',
  ticket_creator:       'Ticket Creator',
  notification_sender:  'Notification Sender',
  heal_suggester:       'Heal Suggester',
  release_risk_scorer:  'Release Risk Scorer',
  screenshot_analyzer:  'Screenshot Analyzer',
  orchestrator:         'Orchestrator',
}

function humanizeName(raw) {
  if (!raw) return 'Unknown Agent'
  return AGENT_LABELS[raw] || raw
    .split(/[_\s]+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

function formatDuration(ms) {
  if (ms == null) return null
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatTimestamp(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toISOString().slice(11, 19)
  } catch {
    return iso
  }
}

// Status icon as a small inline element
function StatusIcon({ status }) {
  if (status === 'completed') {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 22,
          height: 22,
          borderRadius: '50%',
          background: 'rgba(13, 148, 136, 0.15)',
          color: '#0D9488',
          fontSize: 13,
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        ✓
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 22,
          height: 22,
          borderRadius: '50%',
          background: 'rgba(239, 68, 68, 0.15)',
          color: '#EF4444',
          fontSize: 13,
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        ✗
      </span>
    )
  }
  if (status === 'running') {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 22,
          height: 22,
          borderRadius: '50%',
          background: 'rgba(59, 130, 246, 0.15)',
          color: '#3B82F6',
          fontSize: 14,
          flexShrink: 0,
          animation: 'tl-spin 1s linear infinite',
        }}
      >
        ⟳
      </span>
    )
  }
  // skipped or unknown
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 22,
        height: 22,
        borderRadius: '50%',
        background: 'rgba(148, 163, 184, 0.12)',
        color: '#94A3B8',
        fontSize: 14,
        flexShrink: 0,
      }}
    >
      ○
    </span>
  )
}

function DurationBadge({ ms }) {
  const label = formatDuration(ms)
  if (!label) return null
  return (
    <span
      style={{
        fontSize: 11,
        fontVariantNumeric: 'tabular-nums',
        background: 'var(--bg-elevated)',
        color: 'var(--text-muted)',
        borderRadius: 4,
        padding: '1px 6px',
        marginLeft: 6,
        flexShrink: 0,
      }}
    >
      {label}
    </span>
  )
}

function TimelineItem({ run, isLast }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div style={{ display: 'flex', gap: 0, position: 'relative' }}>
      {/* Left: connector line + circle node */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          marginRight: 14,
          flexShrink: 0,
          width: 22,
        }}
      >
        <StatusIcon status={run.status} />
        {/* Vertical connector line — hidden on the last item */}
        {!isLast && (
          <div
            style={{
              flex: 1,
              width: 2,
              background: 'var(--border)',
              minHeight: 16,
              marginTop: 4,
            }}
          />
        )}
      </div>

      {/* Right: card */}
      <div style={{ flex: 1, paddingBottom: 12 }}>
        <div
          role="button"
          tabIndex={0}
          onClick={() => setExpanded((v) => !v)}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpanded((v) => !v) }}
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: '8px 12px',
            cursor: 'pointer',
            userSelect: 'none',
            transition: 'border-color 150ms',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {humanizeName(run.agent_name)}
              </span>
              <DurationBadge ms={run.duration_ms} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
              <span style={{ fontSize: 11, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
                {formatTimestamp(run.started_at)}
              </span>
              <span
                style={{
                  fontSize: 11,
                  color: 'var(--text-muted)',
                  transition: 'transform 150ms',
                  transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
                  display: 'inline-block',
                }}
              >
                ›
              </span>
            </div>
          </div>
        </div>

        {/* Collapsible output panel */}
        {expanded && (
          <div
            style={{
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderTop: 'none',
              borderRadius: '0 0 6px 6px',
              padding: '10px 12px',
            }}
          >
            <p
              style={{
                margin: 0,
                fontSize: 12,
                color: run.output_summary ? 'var(--text-primary)' : 'var(--text-muted)',
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {run.output_summary || 'No output summary available for this step.'}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function TotalElapsed({ runs }) {
  const starts = runs
    .map((r) => r.started_at ? new Date(r.started_at).getTime() : null)
    .filter(Boolean)
  const ends = runs
    .map((r) => r.completed_at ? new Date(r.completed_at).getTime() : null)
    .filter(Boolean)

  if (starts.length === 0 || ends.length === 0) return null

  const totalMs = Math.max(...ends) - Math.min(...starts)
  const label = formatDuration(totalMs)
  if (!label) return null

  return (
    <div
      style={{
        marginTop: 4,
        paddingTop: 12,
        borderTop: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      }}
    >
      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Total elapsed:</span>
      <span
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: 'var(--accent-light)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {label}
      </span>
    </div>
  )
}

/**
 * AgentTimeline
 *
 * Props:
 *   failureId  — string | undefined
 *   runs       — array (optional) — if provided, skips internal fetch
 *                (used when parent already has the data via useQuery)
 */
export default function AgentTimeline({ failureId, runs: runsProp }) {
  const [runs, setRuns] = useState(runsProp || [])
  const [loading, setLoading] = useState(!runsProp && Boolean(failureId))
  const [error, setError] = useState(null)

  useEffect(() => {
    // If parent passed runs directly, use those (no internal fetch needed)
    if (runsProp !== undefined) {
      setRuns(runsProp)
      setLoading(false)
      return
    }
    if (!failureId) return

    setLoading(true)
    setError(null)
    api
      .get('/api/v1/agents/runs', { params: { failure_id: failureId } })
      .then((res) => setRuns(res.data))
      .catch((err) =>
        setError(err?.response?.data?.detail || err.message || 'Failed to load agent runs')
      )
      .finally(() => setLoading(false))
  }, [failureId, runsProp])

  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: '16px 20px',
      }}
    >
      <style>{`@keyframes tl-spin { to { transform: rotate(360deg); } }`}</style>

      <h2
        style={{
          margin: '0 0 16px',
          fontSize: 14,
          fontWeight: 600,
          color: 'var(--text-primary)',
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
        }}
      >
        Agent Pipeline
      </h2>

      {loading && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '24px 0' }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              border: '3px solid var(--bg-elevated)',
              borderTopColor: 'var(--accent)',
              animation: 'tl-spin 0.8s linear infinite',
            }}
          />
        </div>
      )}

      {error && (
        <div
          style={{
            background: '#3f1515',
            border: '1px solid var(--danger)',
            color: '#fca5a5',
            padding: '10px 14px',
            borderRadius: 6,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {!loading && !error && runs.length === 0 && (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
          No agent runs recorded for this failure.
        </p>
      )}

      {!loading && !error && runs.length > 0 && (
        <>
          <div style={{ position: 'relative' }}>
            {runs.map((run, idx) => (
              <TimelineItem key={run.id || idx} run={run} isLast={idx === runs.length - 1} />
            ))}
          </div>
          <TotalElapsed runs={runs} />
        </>
      )}
    </div>
  )
}
