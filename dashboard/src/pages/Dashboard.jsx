import React, { useEffect, useState, useCallback } from 'react'
import {
  PieChart, Pie, Cell, Tooltip, Legend,
  LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer,
} from 'recharts'
import { getSummary, getTrends, getTopFailing } from '../api/client.js'
import CategoryBadge from '../components/CategoryBadge.jsx'

const CATEGORY_COLORS = {
  product_bug: '#ef4444',
  flaky_test: '#eab308',
  env_issue: '#f97316',
  timeout: '#a855f7',
  infra_issue: '#3b82f6',
  config_error: '#6366f1',
  dependency_failure: '#6b7280',
}

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

export default function Dashboard() {
  const [period, setPeriod] = useState('7d')
  const [summary, setSummary] = useState(null)
  const [trends, setTrends] = useState([])
  const [topFailing, setTopFailing] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [s, tr, tf] = await Promise.all([
        getSummary(period),
        getTrends(30),
        getTopFailing(7),
      ])
      setSummary(s)
      setTrends(tr)
      setTopFailing(tf)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load dashboard data')
    } finally {
      setLoading(false)
    }
  }, [period])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // Build pie chart data from summary.by_category
  const pieData = summary
    ? Object.entries(summary.by_category || {})
        .filter(([, v]) => v > 0)
        .map(([key, value]) => ({ name: key.replace(/_/g, ' '), value, key }))
    : []

  // Format trend dates as MM-DD
  // API returns {date, count} — a single total per day
  const trendData = trends.map((row) => ({
    ...row,
    label: row.date ? row.date.slice(-5) : '',
  }))

  const PERIODS = ['24h', '7d', '30d']

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                period === p
                  ? 'bg-indigo-600 text-white'
                  : 'bg-white text-gray-600 border border-gray-300 hover:bg-gray-50'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {error && <ErrorBanner message={error} />}
      {loading ? (
        <Spinner />
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <SummaryCard
              title="Total Failures"
              value={summary?.total ?? 0}
            />
            <SummaryCard
              title="Product Bugs"
              value={summary?.by_category?.product_bug ?? 0}
              sub={`${pct(summary?.by_category?.product_bug ?? 0, summary?.total)} of total`}
              color="text-red-600"
            />
            <SummaryCard
              title="Flaky Tests"
              value={summary?.flaky_count ?? 0}
              sub={`${pct(summary?.flaky_count ?? 0, summary?.total)} of total`}
              color="text-yellow-600"
            />
            <SummaryCard
              title="Env Issues"
              value={summary?.by_category?.env_issue ?? 0}
              sub={`${pct(summary?.by_category?.env_issue ?? 0, summary?.total)} of total`}
              color="text-orange-600"
            />
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            {/* Pie chart */}
            <div className="bg-white rounded-lg shadow-sm p-6">
              <h2 className="text-base font-semibold text-gray-700 mb-4">Failures by Category</h2>
              {pieData.length === 0 ? (
                <p className="text-gray-400 text-sm">No data for this period.</p>
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
            <div className="bg-white rounded-lg shadow-sm p-6">
              <h2 className="text-base font-semibold text-gray-700 mb-4">Trends (Last 30 Days)</h2>
              {trendData.length === 0 ? (
                <p className="text-gray-400 text-sm">No trend data available.</p>
              ) : (
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={trendData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip formatter={(v) => [v, 'Failures']} />
                    <Line
                      type="monotone"
                      dataKey="count"
                      stroke="#6366f1"
                      dot={false}
                      name="Total Failures"
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* Top failing tests */}
          <div className="bg-white rounded-lg shadow-sm p-6">
            <h2 className="text-base font-semibold text-gray-700 mb-4">Top Failing Tests</h2>
            {topFailing.length === 0 ? (
              <p className="text-gray-400 text-sm">No failing tests in this period.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className="text-left py-2 pr-4 font-medium text-gray-500 w-8">#</th>
                      <th className="text-left py-2 pr-4 font-medium text-gray-500">Test Name</th>
                      <th className="text-left py-2 font-medium text-gray-500">Failures (30d)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topFailing.slice(0, 10).map((row, i) => {
                      const name = row.test_name || ''
                      const truncated = name.length > 60 ? name.slice(0, 60) + '…' : name
                      return (
                        <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                          <td className="py-2 pr-4 text-gray-400">{i + 1}</td>
                          <td
                            className="py-2 pr-4 text-gray-800 font-mono text-xs"
                            title={name}
                          >
                            {truncated}
                          </td>
                          <td className="py-2 font-semibold text-gray-900">{row.count ?? '—'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function SummaryCard({ title, value, sub, color }) {
  return (
    <div className="bg-white rounded-lg shadow-sm p-6">
      <p className="text-sm font-medium text-gray-500">{title}</p>
      <p className={`text-3xl font-bold mt-1 ${color || 'text-gray-900'}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  )
}
