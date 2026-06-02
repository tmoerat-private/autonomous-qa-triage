import React from 'react'

// Map each category to a theme-aware tone token (defined in index.css for
// both light and dark themes), so badges re-theme on toggle.
const CATEGORY_TONE = {
  product_bug:        'red',
  flaky_test:         'yellow',
  env_issue:          'orange',
  timeout:            'purple',
  infra_issue:        'blue',
  config_error:       'indigo',
  dependency_failure: 'gray',
}

function toTitleCase(str) {
  return str
    .replace(/_/g, ' ')
    .replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
}

export default function CategoryBadge({ category }) {
  const tone = CATEGORY_TONE[category] || 'gray'
  const label = category ? toTitleCase(category) : 'Unknown'
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
