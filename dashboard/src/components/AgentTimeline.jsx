import React, { useState } from 'react'

// Human-readable agent name map — keys match the LangGraph node names in
// src/agents/orchestrator.py.
const AGENT_LABELS = {
  pipeline_monitor:    'Pipeline Monitor',
  failure_classifier:  'Failure Classifier',
  log_analyzer:        'Log Analyzer',
  visual_analyzer:     'Visual Analyzer',
  root_cause:          'Root Cause Analyzer',
  heal_suggester:      'Heal Suggester',
  environment_health:  'Environment Health Check',
  duplicate_detector:  'Duplicate Detector',
  flaky_detector:      'Flaky Test Detector',
  rerun_trigger:       'Rerun Trigger',
  ticket_creator:      'Ticket Creator',
  notifier:            'Notifier',
  learner:             'Learner',
  release_scorer:      'Release Risk Scorer',
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
 *   runs — array of AgentRunItem (defaults to []) — the parent fetches this
 *          via useQuery (e.g. getAgentRunsForFailure) and passes it down.
 */
export default function AgentTimeline({ runs = [] }) {
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

      {runs.length === 0 && (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
          No agent runs recorded for this failure.
        </p>
      )}

      {runs.length > 0 && (
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
