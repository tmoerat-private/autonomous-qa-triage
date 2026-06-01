import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getFailure, getScreenshots } from '../api/client.js'
import CategoryBadge from '../components/CategoryBadge.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import VisualRegressionPanel from '../components/VisualRegressionPanel.jsx'

function Spinner() {
  return (
    <div className="flex justify-center items-center py-20">
      <div className="w-10 h-10 rounded-full border-4 border-gray-200 border-t-indigo-600 animate-spin" />
    </div>
  )
}

function ErrorBanner({ message }) {
  return (
    <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded mb-4">
      <strong>Error:</strong> {message}
    </div>
  )
}

function formatTime(isoString) {
  if (!isoString) return '—'
  try {
    return new Date(isoString).toISOString().slice(11, 19)
  } catch {
    return isoString
  }
}

function AgentRunIcon({ status }) {
  if (status === 'completed') {
    return <span className="text-green-500 font-bold">✓</span>
  }
  if (status === 'failed') {
    return <span className="text-red-500 font-bold">✗</span>
  }
  if (status === 'running') {
    return <span className="text-amber-500 font-bold">⟳</span>
  }
  return <span className="text-gray-400">—</span>
}

export default function FailureDetail() {
  const { id } = useParams()
  const [failure, setFailure] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [screenshots, setScreenshots] = useState([])

  useEffect(() => {
    setLoading(true)
    setError(null)
    Promise.all([
      getFailure(id),
      getScreenshots(id).catch(() => []),  // non-fatal if no screenshots
    ])
      .then(([data, shots]) => {
        setFailure(data)
        setScreenshots(shots)
      })
      .catch((err) => {
        setError(err?.response?.data?.detail || err.message || 'Failed to load failure details')
      })
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} />
  if (!failure) return null

  const confidence = failure.confidence != null ? Math.round(failure.confidence * 100) : null
  const agentRuns = failure.agent_runs || []

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/failures"
          className="text-sm text-indigo-600 hover:text-indigo-800 flex items-center gap-1 mb-3"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Back to Failures
        </Link>
        <div className="flex flex-wrap items-start gap-3">
          <h1 className="text-xl font-bold text-gray-900 font-mono break-all flex-1">
            {failure.test_name || `Failure #${id}`}
          </h1>
          <div className="flex items-center gap-2 flex-shrink-0">
            <StatusBadge status={failure.status} />
            <CategoryBadge category={failure.category} />
          </div>
        </div>
      </div>

      {/* Two-column grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        {/* Left — Error details */}
        <div className="space-y-4">
          <div className="bg-white rounded-lg shadow-sm p-6">
            <h2 className="text-sm font-semibold text-gray-700 mb-3">Error Message</h2>
            <pre className="bg-gray-900 text-green-400 font-mono text-xs p-4 rounded overflow-x-auto max-h-48 whitespace-pre-wrap">
              {failure.error_message || <span className="text-gray-500">No error message</span>}
            </pre>
          </div>

          <div className="bg-white rounded-lg shadow-sm p-6">
            <h2 className="text-sm font-semibold text-gray-700 mb-3">Stack Trace</h2>
            <pre className="bg-gray-900 text-green-400 font-mono text-xs p-4 rounded overflow-x-auto max-h-64 whitespace-pre-wrap">
              {failure.stack_trace || <span className="text-gray-500">No stack trace available</span>}
            </pre>
          </div>
        </div>

        {/* Right — Triage info */}
        <div className="space-y-4">
          <div className="bg-white rounded-lg shadow-sm p-6">
            <h2 className="text-sm font-semibold text-gray-700 mb-3">Classification</h2>
            <div className="space-y-3">
              <div>
                <CategoryBadge category={failure.category} />
              </div>
              {confidence !== null && (
                <div>
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>Confidence</span>
                    <span>{confidence}%</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-indigo-500 h-2 rounded-full transition-all"
                      style={{ width: `${confidence}%` }}
                    />
                  </div>
                </div>
              )}
              {failure.reasoning && (
                <p className="text-sm text-gray-500 italic">{failure.reasoning}</p>
              )}
            </div>
          </div>

          <div className="bg-white rounded-lg shadow-sm p-6 space-y-4">
            <h2 className="text-sm font-semibold text-gray-700 mb-1">Metadata</h2>

            <div className="flex items-start gap-2 text-sm">
              <span className="text-gray-500 w-24 flex-shrink-0">Ticket</span>
              {failure.ticket_url ? (
                <a
                  href={failure.ticket_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-indigo-600 hover:underline break-all"
                >
                  {failure.ticket_url}
                </a>
              ) : (
                <span className="text-gray-400">No ticket created</span>
              )}
            </div>

            <div className="flex items-center gap-2 text-sm">
              <span className="text-gray-500 w-24 flex-shrink-0">Notification</span>
              {failure.notification_sent ? (
                <span className="text-green-600 font-medium">Sent ✓</span>
              ) : (
                <span className="text-gray-400">Not sent</span>
              )}
            </div>

            {failure.branch && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-gray-500 w-24 flex-shrink-0">Branch</span>
                <span className="font-mono text-xs text-gray-700">{failure.branch}</span>
              </div>
            )}

            {failure.repository && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-gray-500 w-24 flex-shrink-0">Repository</span>
                <span className="font-mono text-xs text-gray-700">{failure.repository}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Visual Analysis */}
      <VisualRegressionPanel
        screenshots={screenshots}
        visualAnalysis={failure.visual_analysis || null}
      />

      {/* Agent Run Timeline */}
      <div className="bg-white rounded-lg shadow-sm p-6">
        <h2 className="text-base font-semibold text-gray-700 mb-4">Agent Runs</h2>
        {agentRuns.length === 0 ? (
          <p className="text-sm text-gray-400">No agent runs recorded.</p>
        ) : (
          <ol className="space-y-3">
            {agentRuns.map((run, i) => (
              <li key={run.id || i} className="flex items-start gap-3 text-sm">
                <span className="mt-0.5 text-base w-5 flex-shrink-0 text-center">
                  <AgentRunIcon status={run.status} />
                </span>
                <div className="flex-1 min-w-0">
                  <span className="font-semibold text-gray-800">{run.agent_name || `Agent ${i + 1}`}</span>
                  {run.duration_ms != null && (
                    <span className="text-gray-400 ml-2">{run.duration_ms}ms</span>
                  )}
                </div>
                <span className="text-gray-400 flex-shrink-0 font-mono">
                  {formatTime(run.started_at)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  )
}
