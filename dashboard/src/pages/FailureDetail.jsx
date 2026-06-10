import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  getFailure,
  getHealSuggestion,
  getReleaseScore,
  getRootCause,
  getScreenshots,
  getAgentRunsForFailure,
} from '../api/client.js'
import CategoryBadge from '../components/CategoryBadge.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import VisualRegressionPanel from '../components/VisualRegressionPanel.jsx'
import AgentTimeline from '../components/AgentTimeline.jsx'

// ─── Shared primitives ───────────────────────────────────────────────────────

function Spinner() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: '50%',
          border: '4px solid var(--bg-elevated)',
          borderTopColor: 'var(--accent)',
          animation: 'fd-spin 0.8s linear infinite',
        }}
      />
      <style>{`@keyframes fd-spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

function InlineSpinner() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '16px 0' }}>
      <div
        style={{
          width: 24,
          height: 24,
          borderRadius: '50%',
          border: '3px solid var(--bg-elevated)',
          borderTopColor: 'var(--accent)',
          animation: 'fd-spin 0.8s linear infinite',
        }}
      />
    </div>
  )
}

function ErrorBanner({ message }) {
  return (
    <div
      style={{
        background: '#3f1515',
        border: '1px solid var(--danger)',
        color: '#fca5a5',
        padding: '12px 16px',
        borderRadius: 8,
        marginBottom: 16,
        fontSize: 14,
      }}
    >
      <strong>Error:</strong> {message}
    </div>
  )
}

function SkeletonBlock({ height = 120 }) {
  return (
    <div
      style={{
        background: 'var(--bg-elevated)',
        borderRadius: 8,
        height,
        animation: 'fd-pulse 1.5s ease-in-out infinite',
      }}
    />
  )
}

// ─── Section card ─────────────────────────────────────────────────────────────

function Card({ title, children, style }) {
  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 24,
        ...style,
      }}
    >
      {title && (
        <h2
          style={{
            margin: '0 0 14px',
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-primary)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          {title}
        </h2>
      )}
      {children}
    </div>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const RISK_LEVEL_COLORS = {
  low:      { bg: '#0d3530', color: '#0D9488' },
  medium:   { bg: '#3d2e00', color: '#F59E0B' },
  high:     { bg: '#3d1f00', color: '#f97316' },
  critical: { bg: '#3f1515', color: '#EF4444' },
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function FailureDetail() {
  const { id } = useParams()

  // Primary failure data
  const {
    data: failure,
    isLoading: failureLoading,
    error: failureError,
  } = useQuery({
    queryKey: ['failure', id],
    queryFn: () => getFailure(id),
    enabled: Boolean(id),
  })

  // Screenshots — non-fatal
  const { data: screenshots = [] } = useQuery({
    queryKey: ['screenshots', id],
    queryFn: () => getScreenshots(id),
    enabled: Boolean(id),
    throwOnError: false,
    retry: false,
  })

  // Agent runs for AgentTimeline
  const { data: agentRuns = [], isLoading: agentRunsLoading } = useQuery({
    queryKey: ['agentRuns', id],
    queryFn: () => getAgentRunsForFailure(id),
    enabled: Boolean(id),
    throwOnError: false,
    retry: false,
  })

  // Heal suggestion — only after failure loaded
  const { data: suggestion, isLoading: suggestionLoading } = useQuery({
    queryKey: ['suggestion', id],
    queryFn: () => getHealSuggestion(id),
    enabled: Boolean(failure),
    throwOnError: false,
    retry: false,
  })

  // Root cause — only after failure loaded
  const { data: rootCause, isLoading: rootCauseLoading } = useQuery({
    queryKey: ['rootCause', id],
    queryFn: () => getRootCause(id),
    enabled: Boolean(failure),
    throwOnError: false,
    retry: false,
  })

  // Release score — only when commit_sha is present
  const { data: releaseScore, isLoading: releaseScoreLoading } = useQuery({
    queryKey: ['releaseScore', failure?.commit_sha, failure?.repository],
    queryFn: () => getReleaseScore(failure.commit_sha, failure.repository),
    enabled: Boolean(failure?.commit_sha),
    throwOnError: false,
    retry: false,
  })

  // ── Render states ────────────────────────────────────────────────────────

  if (failureLoading) return <Spinner />

  if (failureError) {
    return (
      <ErrorBanner
        message={failureError?.response?.data?.detail || failureError?.message || 'Failed to load failure details'}
      />
    )
  }

  if (!failure) return null

  const confidence = failure.classification?.confidence != null
    ? Math.round(failure.classification.confidence * 100)
    : null

  // ── Layout ───────────────────────────────────────────────────────────────

  return (
    <div>
      <style>{`
        @keyframes fd-spin  { to { transform: rotate(360deg); } }
        @keyframes fd-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      `}</style>

      {/* Back link + title */}
      <div style={{ marginBottom: 24 }}>
        <Link
          to="/failures"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            fontSize: 13,
            color: 'var(--accent-light)',
            textDecoration: 'none',
            marginBottom: 12,
          }}
        >
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Back to Failures
        </Link>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', gap: 12 }}>
          <h1
            style={{
              margin: 0,
              flex: 1,
              fontSize: 18,
              fontWeight: 700,
              color: 'var(--text-primary)',
              fontFamily: 'monospace',
              wordBreak: 'break-all',
            }}
          >
            {failure.test_name || `Failure #${id}`}
          </h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <StatusBadge status={failure.status} />
            <CategoryBadge category={failure.classification?.category} />
          </div>
        </div>
      </div>

      {/* Two-column: error details + triage info */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
          gap: 24,
          marginBottom: 24,
        }}
      >
        {/* Left — Error details */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card title="Error Message">
            <pre
              style={{
                background: '#0a0f1e',
                color: '#4ade80',
                fontFamily: 'monospace',
                fontSize: 12,
                padding: 16,
                borderRadius: 6,
                overflowX: 'auto',
                maxHeight: 192,
                margin: 0,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {failure.error_message || (
                <span style={{ color: 'var(--text-muted)' }}>No error message</span>
              )}
            </pre>
          </Card>

          <Card title="Stack Trace">
            <pre
              style={{
                background: '#0a0f1e',
                color: '#4ade80',
                fontFamily: 'monospace',
                fontSize: 12,
                padding: 16,
                borderRadius: 6,
                overflowX: 'auto',
                maxHeight: 256,
                margin: 0,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {failure.stack_trace || (
                <span style={{ color: 'var(--text-muted)' }}>No stack trace available</span>
              )}
            </pre>
          </Card>
        </div>

        {/* Right — Triage info */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card title="Classification">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <CategoryBadge category={failure.classification?.category} />
              </div>
              {confidence !== null && (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                    <span>Confidence</span>
                    <span>{confidence}%</span>
                  </div>
                  <div style={{ background: 'var(--bg-elevated)', borderRadius: 4, height: 6, overflow: 'hidden' }}>
                    <div style={{ background: 'var(--accent)', height: '100%', width: `${confidence}%`, borderRadius: 4, transition: 'width 300ms' }} />
                  </div>
                </div>
              )}
              {failure.classification?.reasoning && (
                <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)', fontStyle: 'italic' }}>
                  {failure.classification.reasoning}
                </p>
              )}
            </div>
          </Card>

          <Card title="Metadata">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <MetaRow label="Ticket">
                {failure.ticket_url ? (
                  <a
                    href={failure.ticket_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: 'var(--accent-light)', wordBreak: 'break-all', fontSize: 13 }}
                  >
                    {failure.ticket_url}
                  </a>
                ) : (
                  <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>No ticket created</span>
                )}
              </MetaRow>
              <MetaRow label="Notification">
                {failure.notification_sent ? (
                  <span style={{ color: 'var(--success)', fontSize: 13 }}>Sent ✓</span>
                ) : (
                  <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Not sent</span>
                )}
              </MetaRow>
              {failure.branch && (
                <MetaRow label="Branch">
                  <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-primary)' }}>
                    {failure.branch}
                  </span>
                </MetaRow>
              )}
              {failure.repository && (
                <MetaRow label="Repository">
                  <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-primary)' }}>
                    {failure.repository}
                  </span>
                </MetaRow>
              )}
            </div>
          </Card>
        </div>
      </div>

      {/* Root Cause Analysis */}
      <Card title="Root Cause Analysis" style={{ marginBottom: 24 }}>
        {rootCauseLoading ? (
          <InlineSpinner />
        ) : rootCause ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <span
                style={{
                  display: 'inline-block',
                  background: 'rgba(168, 85, 247, 0.15)',
                  color: '#c084fc',
                  borderRadius: 12,
                  padding: '2px 10px',
                  fontSize: 11,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.06em',
                }}
              >
                {rootCause.root_cause_category?.replace(/_/g, ' ')}
              </span>
            </div>
            <p style={{ margin: 0, fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.6 }}>
              {rootCause.root_cause_summary}
            </p>
            {rootCause.likely_cause_files?.length > 0 && (
              <div>
                <p style={{ margin: '0 0 6px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Likely cause files
                </p>
                <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {rootCause.likely_cause_files.map((f, i) => (
                    <li
                      key={i}
                      style={{
                        fontFamily: 'monospace',
                        fontSize: 12,
                        background: 'rgba(13, 148, 136, 0.1)',
                        color: 'var(--accent-light)',
                        padding: '3px 8px',
                        borderRadius: 4,
                      }}
                    >
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {rootCause.investigation_steps?.length > 0 && (
              <div>
                <p style={{ margin: '0 0 6px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Investigation steps
                </p>
                <ol style={{ margin: 0, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {rootCause.investigation_steps.map((step, i) => (
                    <li key={i} style={{ fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.5 }}>{step}</li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        ) : (
          <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
            No root cause analysis available yet.
          </p>
        )}
      </Card>

      {/* Heal Suggestion */}
      <Card title="Heal Suggestion" style={{ marginBottom: 24 }}>
        {suggestionLoading ? (
          <InlineSpinner />
        ) : suggestion ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <pre
              style={{
                margin: 0,
                fontSize: 13,
                color: 'var(--text-primary)',
                whiteSpace: 'pre-wrap',
                lineHeight: 1.6,
              }}
            >
              {suggestion.suggestion}
            </pre>
            {suggestion.fix_snippet && (
              <pre
                style={{
                  background: '#0a0f1e',
                  color: '#4ade80',
                  fontFamily: 'monospace',
                  fontSize: 12,
                  padding: 16,
                  borderRadius: 6,
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {suggestion.fix_snippet}
              </pre>
            )}
            {suggestion.affected_file && (
              <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)' }}>
                Affected file: {suggestion.affected_file}
              </p>
            )}
            {suggestion.confidence != null && (() => {
              const pct = Math.round(suggestion.confidence * 100)
              return (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                    <span>Confidence</span>
                    <span>{pct}%</span>
                  </div>
                  <div style={{ background: 'var(--bg-elevated)', borderRadius: 4, height: 6, overflow: 'hidden' }}>
                    <div style={{ background: 'var(--accent)', height: '100%', width: `${pct}%`, borderRadius: 4, transition: 'width 300ms' }} />
                  </div>
                </div>
              )
            })()}
            {suggestion.accepted === true && (
              <span
                style={{
                  display: 'inline-block',
                  background: 'rgba(34, 197, 94, 0.15)',
                  color: 'var(--success)',
                  borderRadius: 12,
                  padding: '2px 10px',
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                Accepted
              </span>
            )}
            {suggestion.accepted === false && (
              <span
                style={{
                  display: 'inline-block',
                  background: 'rgba(239, 68, 68, 0.15)',
                  color: 'var(--danger)',
                  borderRadius: 12,
                  padding: '2px 10px',
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                Rejected
              </span>
            )}
          </div>
        ) : (
          <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
            No heal suggestion generated yet.
          </p>
        )}
      </Card>

      {/* Release Risk */}
      {failure.commit_sha && (
        <Card title="Release Risk" style={{ marginBottom: 24 }}>
          {releaseScoreLoading ? (
            <InlineSpinner />
          ) : releaseScore ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {releaseScore.risk_level && (() => {
                  const level = releaseScore.risk_level.toLowerCase()
                  const c = RISK_LEVEL_COLORS[level] || { bg: 'var(--bg-elevated)', color: 'var(--text-muted)' }
                  return (
                    <span
                      style={{
                        background: c.bg,
                        color: c.color,
                        borderRadius: 12,
                        padding: '2px 10px',
                        fontSize: 11,
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        letterSpacing: '0.06em',
                      }}
                    >
                      {level}
                    </span>
                  )
                })()}
                <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-muted)' }}>
                  {failure.commit_sha.slice(0, 7)}
                </span>
              </div>
              {releaseScore.score != null && (() => {
                const scoreVal = Math.min(Math.round(releaseScore.score), 100)
                return (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                      <span>Score</span>
                      <span>{scoreVal}/100</span>
                    </div>
                    <div style={{ background: 'var(--bg-elevated)', borderRadius: 4, height: 6, overflow: 'hidden' }}>
                      <div style={{ background: 'var(--accent)', height: '100%', width: `${scoreVal}%`, borderRadius: 4, transition: 'width 300ms' }} />
                    </div>
                  </div>
                )
              })()}
              {releaseScore.risk_summary && (
                <p style={{ margin: 0, fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.6 }}>
                  {releaseScore.risk_summary}
                </p>
              )}
            </div>
          ) : (
            <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
              No release score available for this commit.
            </p>
          )}
        </Card>
      )}

      {/* Visual Analysis */}
      <div style={{ marginBottom: 24 }}>
        <VisualRegressionPanel
          screenshots={screenshots}
          visualAnalysis={failure.visual_analysis || null}
        />
      </div>

      {/* Agent Pipeline Timeline */}
      {agentRunsLoading ? (
        <SkeletonBlock height={200} />
      ) : (
        <AgentTimeline runs={agentRuns} />
      )}
    </div>
  )
}

// ─── Small helpers ────────────────────────────────────────────────────────────

function MetaRow({ label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
      <span
        style={{
          width: 100,
          flexShrink: 0,
          fontSize: 13,
          color: 'var(--text-muted)',
          paddingTop: 1,
        }}
      >
        {label}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
    </div>
  )
}
