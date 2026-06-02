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

const selectStyle = {
  border: '1px solid var(--border)',
  borderRadius: 6,
  padding: '6px 12px',
  fontSize: 13,
  color: 'var(--text-primary)',
  background: 'var(--bg-surface)',
  outline: 'none',
  cursor: 'pointer',
}

function Spinner() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '80px 0' }}>
      <style>{`@keyframes fail-spin { to { transform: rotate(360deg); } }`}</style>
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: '50%',
          border: '4px solid var(--bg-elevated)',
          borderTopColor: 'var(--accent)',
          animation: 'fail-spin 0.8s linear infinite',
        }}
      />
    </div>
  )
}

function ErrorBanner({ message }) {
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

  const thStyle = {
    textAlign: 'left',
    padding: '12px 16px',
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    borderBottom: '1px solid var(--border)',
  }

  const tdStyle = {
    padding: '12px 16px',
    color: 'var(--text-muted)',
  }

  const pageBtnStyle = (disabled) => ({
    padding: '6px 12px',
    fontSize: 13,
    fontWeight: 500,
    borderRadius: 6,
    border: '1px solid var(--border)',
    background: 'var(--bg-surface)',
    color: 'var(--text-primary)',
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.4 : 1,
  })

  return (
    <div>
      <h1 style={{ margin: '0 0 24px', fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>
        Test Failures
      </h1>

      {/* Filter bar */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={selectStyle}>
          <option value="">All Statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>

        <select value={category} onChange={(e) => setCategory(e.target.value)} style={selectStyle}>
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
          style={{ ...selectStyle, flex: 1, minWidth: 192, cursor: 'text' }}
        />
      </div>

      {error && <ErrorBanner message={error} />}

      {loading ? (
        <Spinner />
      ) : (
        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            overflow: 'hidden',
          }}
        >
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={thStyle}>Test Name</th>
                  <th style={thStyle}>Category</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Branch</th>
                  <th style={thStyle}>Created</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ ...tdStyle, textAlign: 'center', padding: '32px 16px' }}>
                      No failures found.
                    </td>
                  </tr>
                ) : (
                  filtered.map((f) => {
                    const name = f.test_name || ''
                    const truncated = name.length > 50 ? name.slice(0, 50) + '…' : name
                    return (
                      <tr
                        key={f.id}
                        style={{ borderTop: '1px solid var(--border)' }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = '' }}
                      >
                        <td style={tdStyle}>
                          <Link
                            to={`/failures/${f.id}`}
                            title={name}
                            style={{
                              color: 'var(--accent-light)',
                              fontFamily: 'var(--font-mono)',
                              fontSize: 12,
                              textDecoration: 'none',
                            }}
                          >
                            {truncated}
                          </Link>
                        </td>
                        <td style={tdStyle}>
                          <CategoryBadge category={f.category} />
                        </td>
                        <td style={tdStyle}>
                          <StatusBadge status={f.status} />
                        </td>
                        <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                          {f.branch || <span style={{ color: 'var(--bg-elevated)' }}>—</span>}
                        </td>
                        <td style={tdStyle}>{formatDate(f.created_at)}</td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '12px 16px',
              borderTop: '1px solid var(--border)',
            }}
          >
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              style={pageBtnStyle(page <= 1)}
            >
              Previous
            </button>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              style={pageBtnStyle(page >= totalPages)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
