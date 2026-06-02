import React from 'react'

// Map each status to a theme-aware tone token (defined in index.css for both
// light and dark themes), so badges re-theme on toggle.
const STATUS_TONE = {
  new:      'sky',
  triaging: 'amber',
  triaged:  'green',
  resolved: 'emerald',
  ignored:  'gray',
}

function toTitleCase(str) {
  return str.charAt(0).toUpperCase() + str.slice(1)
}

export default function StatusBadge({ status }) {
  const tone = STATUS_TONE[status] || 'gray'
  const label = status ? toTitleCase(status) : 'Unknown'
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '2px 10px',
        borderRadius: 9999,
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: 'nowrap',
        backgroundColor: `var(--badge-${tone}-bg)`,
        color: `var(--badge-${tone}-fg)`,
      }}
    >
      {label}
    </span>
  )
}
