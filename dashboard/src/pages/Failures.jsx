import React, { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { getFailures } from '../api/client.js'
import CategoryBadge from '../components/CategoryBadge.jsx'
import StatusBadge from '../components/StatusBadge.jsx'

const STATUSES = ['new', 'triaging', 'triaged', 'resolved', 'ignored']
const CATEGORIES = [
  'product_bug',
  'flaky_test',
  'env_issue',
  'timeout',
  'infra_issue',
  'config_error',
  'dependency_failure',
]

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

function formatDate(isoString) {
  if (!isoString) return '—'
  return isoString.slice(0, 10)
}

export default function Failures() {
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    const params = { page, limit: 20 }
    if (status) params.status = status
    if (category) params.category = category
    try {
      const result = await getFailures(params)
      setData(result)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load failures')
    } finally {
      setLoading(false)
    }
  }, [page, status, category])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1)
  }, [status, category])

  const items = data?.items || []
  const totalPages = data?.pages || 1

  // Client-side search filter
  const filtered = search
    ? items.filter((f) =>
        (f.test_name || '').toLowerCase().includes(search.toLowerCase())
      )
    : items

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Test Failures</h1>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-3 mb-4">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="border border-gray-300 rounded-md px-3 py-1.5 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <option value="">All Statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>

        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="border border-gray-300 rounded-md px-3 py-1.5 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <option value="">All Categories</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c.replace(/_/g, ' ').replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1))}
            </option>
          ))}
        </select>

        <input
          type="text"
          placeholder="Search test name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border border-gray-300 rounded-md px-3 py-1.5 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 flex-1 min-w-48"
        />
      </div>

      {error && <ErrorBanner message={error} />}

      {loading ? (
        <Spinner />
      ) : (
        <div className="bg-white rounded-lg shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Test Name</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Category</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Status</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Branch</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-gray-400">
                      No failures found.
                    </td>
                  </tr>
                ) : (
                  filtered.map((f) => {
                    const name = f.test_name || ''
                    const truncated = name.length > 50 ? name.slice(0, 50) + '…' : name
                    return (
                      <tr key={f.id} className="hover:bg-gray-50">
                        <td className="px-4 py-3">
                          <Link
                            to={`/failures/${f.id}`}
                            className="text-indigo-600 hover:text-indigo-800 font-mono text-xs"
                            title={name}
                          >
                            {truncated}
                          </Link>
                        </td>
                        <td className="px-4 py-3">
                          <CategoryBadge category={f.category} />
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge status={f.status} />
                        </td>
                        <td className="px-4 py-3 text-gray-500 font-mono text-xs">
                          {f.branch || <span className="text-gray-300">—</span>}
                        </td>
                        <td className="px-4 py-3 text-gray-500">
                          {formatDate(f.created_at)}
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="px-3 py-1.5 text-sm font-medium rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <span className="text-sm text-gray-600">
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1.5 text-sm font-medium rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
