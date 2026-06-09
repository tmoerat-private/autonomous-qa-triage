import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  PieChart, Pie, Cell, Tooltip, Legend,
  LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer,
} from 'recharts'
import { getSummary, getTrends, getTopFailing, getRecentReleaseScores } from '../api/client.js'
import CategoryBadge from '../components/CategoryBadge.jsx'
import ReleaseRiskWidget from '../components/ReleaseRiskWidget.jsx'

const CATEGORY_COLORS = {
  product_bug: '#ef4444',
  flaky_test: '#eab308',
  env_issue: '#f97316',
  timeout: '#a855f7',
  infra_issue: '#3b82f6',
  config_error: '#6366f1',
  dependency_failure: '#6b7280',
}

function pct(value, total) {
  if (!total) return '0%'
  return `${Math.round((value / total) * 100)}%`
}

function relativeDate(isoString) {
  if (!isoString) return '—'
  const now = new Date()
  const then = new Date(isoString)
  const diffMs = now - then
  const diffMins = Math.floor(diffMs / 60000)
  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins} minute${diffMins === 1 ? '' : 's'} ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`
  const diffDays = Math.floor(diffHours / 24)
  return `${diffDays} day${diffDays === 1 ? '' : 's'} ago`
}

// Loading skeleton placeholder card
function SkeletonCard() {
  return (
    <div
      style={{
        background: 'var(--bg-elevated)',
        borderRadius: 8,
        padding: 24,
        height: 96,
        animation: 'sk-pulse 1.5s ease-in-out infinite',
      }}
    />
  )
}

// Loading skeleton placeholder for charts
function SkeletonBlock({ height = 300 }) {
  return (
    <div
      style={{
        background: 'var(--bg-elevated)',
        borderRadius: 8,
        height,
        animation: 'sk-pulse 1.5s ease-in-out infinite',
      }}
    />
  )
}

function ErrorCard({ message }) {
  return (
    <div
      style={{
        background: 'var(--danger-bg)',
        border: '1px solid var(--danger)',
        color: 'var(--danger-fg)',
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

const PERIODS = ['24h', '7d', '30d']

export default function Dashboard() {
  const [period, setPeriod] = useState('7d')

  const {
    data: summary,
    isLoading: summaryLoading,
    error: summaryError,
  } = useQuery({
    queryKey: ['summary', period],
    queryFn: () => getSummary(period),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })

  const {
    data: trends = [],
    isLoading: trendsLoading,
    error: trendsError,
  } = useQuery({
    queryKey: ['trends'],
    queryFn: () => getTrends(30),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })

  const {
    data: topFailing = [],
    isLoading: topFailingLoading,
  } = useQuery({
    queryKey: ['topFailing'],
    queryFn: () => getTopFailing(7),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })

  const { data: releaseScores = [] } = useQuery({
    queryKey: ['releaseScores'],
    queryFn: () => getRecentReleaseScores('org/api-service', 5),
    // Non-fatal — silently returns [] on error
    throwOnError: false,
    retry: false,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })

  const mainLoading = summaryLoading || trendsLoading || topFailingLoading
  const mainError =
    (summaryError?.response?.data?.detail || summaryError?.message) ||
    (trendsError?.response?.data?.detail || trendsError?.message)

  // Build pie chart data from summary.by_category
  const pieData = summary
    ? Object.entries(summary.by_category || {})
        .filter(([, v]) => v > 0)
        .map(([key, value]) => ({ name: key.replace(/_/g, ' '), value, key }))
    : []

  // Format trend dates as MM-DD
  const trendData = trends.map((row) => ({
    ...row,
    label: row.date ? row.date.slice(-5) : '',
  }))

  return (
    <div>
      {/* Keyframe for skeleton pulse */}
      <style>{`
        @keyframes sk-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>
          Dashboard
        </h1>
        <div style={{ display: 'flex', gap: 4 }}>
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              style={{
                padding: '5px 12px',
                borderRadius: 6,
                fontSize: 13,
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'background 150ms, color 150ms',
                border: period === p ? 'none' : '1px solid var(--border)',
                background: period === p ? 'var(--accent)' : 'var(--bg-surface)',
                color: period === p ? '#fff' : 'var(--text-muted)',
              }}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {mainError && <ErrorCard message={mainError} />}

      {mainLoading ? (
        <>
          {/* Summary card skeletons */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: 16,
              marginBottom: 24,
            }}
          >
            {[0, 1, 2, 3].map((i) => <SkeletonCard key={i} />)}
          </div>

          {/* Chart skeletons */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24, marginBottom: 24 }}>
            <SkeletonBlock height={336} />
            <SkeletonBlock height={336} />
          </div>

          {/* Table skeleton */}
          <SkeletonBlock height={200} />
        </>
      ) : (
        <>
          {/* Summary cards */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: 16,
              marginBottom: 24,
            }}
          >
            <SummaryCard
              title="Total Failures"
              value={summary?.total ?? 0}
            />
            <SummaryCard
              title="Product Bugs"
              value={summary?.by_category?.product_bug ?? 0}
              sub={`${pct(summary?.by_category?.product_bug ?? 0, summary?.total)} of total`}
              color="var(--danger)"
            />
            <SummaryCard
              title="Flaky Tests"
              value={summary?.flaky_count ?? 0}
              sub={`${pct(summary?.flaky_count ?? 0, summary?.total)} of total`}
              color="var(--warning)"
            />
            <SummaryCard
              title="Env Issues"
              value={summary?.by_category?.env_issue ?? 0}
              sub={`${pct(summary?.by_category?.env_issue ?? 0, summary?.total)} of total`}
              color="#f97316"
            />
          </div>

          {/* Charts */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
              gap: 24,
              marginBottom: 24,
            }}
          >
            {/* Pie chart */}
            <div
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 8,
                padding: 24,
              }}
            >
              <h2 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
                Failures by Category
              </h2>
              {pieData.length === 0 ? (
                <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>No data for this period.</p>
              ) : (
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={pieData}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      outerRadius={100}
                    >
                      {pieData.map((entry) => (
                        <Cell key={entry.key} fill={CATEGORY_COLORS[entry.key] || '#9ca3af'} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value) => [value, '']} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>

            {/* Line chart */}
            <div
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 8,
                padding: 24,
              }}
            >
              <h2 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
                Trends (Last 30 Days)
              </h2>
              {trendData.length === 0 ? (
                <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>No trend data available.</p>
              ) : (
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={trendData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} />
                    <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} allowDecimals={false} />
                    <Tooltip
                      contentStyle={{
                        background: 'var(--bg-elevated)',
                        border: '1px solid var(--border)',
                        borderRadius: 6,
                        color: 'var(--text-primary)',
                      }}
                      formatter={(v) => [v, 'Failures']}
                    />
                    <Line
                      type="monotone"
                      dataKey="count"
                      stroke="var(--accent)"
                      dot={false}
                      name="Total Failures"
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* Top failing tests */}
          <div
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              padding: 24,
              marginBottom: 24,
            }}
          >
            <h2 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
              Top Failing Tests
            </h2>
            {topFailing.length === 0 ? (
              <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: 0 }}>
                No failing tests in this period.
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr>
                      {['#', 'Test Name', 'Failures (30d)'].map((h) => (
                        <th
                          key={h}
                          style={{
                            textAlign: 'left',
                            padding: '8px 12px',
                            fontSize: 12,
                            fontWeight: 600,
                            color: 'var(--text-muted)',
                            textTransform: 'uppercase',
                            letterSpacing: '0.05em',
                            borderBottom: '1px solid var(--border)',
                          }}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {topFailing.slice(0, 10).map((row, i) => {
                      const name = row.test_name || ''
                      const truncated = name.length > 60 ? name.slice(0, 60) + '…' : name
                      return (
                        <tr
                          key={i}
                          style={{ borderBottom: '1px solid var(--border)' }}
                          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = '' }}
                        >
                          <td style={{ padding: '8px 12px', color: 'var(--text-muted)', width: 32 }}>{i + 1}</td>
                          <td
                            style={{
                              padding: '8px 12px',
                              color: 'var(--accent-light)',
                              fontFamily: 'var(--font-mono)',
                              fontSize: 12,
                            }}
                            title={name}
                          >
                            {truncated}
                          </td>
                          <td style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {row.count ?? '—'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Release Risk */}
          <ReleaseRiskWidget scores={releaseScores} />
        </>
      )}
    </div>
  )
}

function SummaryCard({ title, value, sub, color }) {
  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 24,
      }}
    >
      <p style={{ margin: '0 0 4px', fontSize: 12, fontWeight: 500, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {title}
      </p>
      <p style={{ margin: 0, fontSize: 30, fontWeight: 700, color: color || 'var(--text-primary)' }}>
        {value}
      </p>
      {sub && (
        <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-muted)' }}>{sub}</p>
      )}
    </div>
  )
}
