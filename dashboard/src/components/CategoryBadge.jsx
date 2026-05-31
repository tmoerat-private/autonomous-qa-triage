import React from 'react'

const CATEGORY_CLASSES = {
  product_bug: 'bg-red-100 text-red-800',
  flaky_test: 'bg-yellow-100 text-yellow-800',
  env_issue: 'bg-orange-100 text-orange-800',
  timeout: 'bg-purple-100 text-purple-800',
  infra_issue: 'bg-blue-100 text-blue-800',
  config_error: 'bg-indigo-100 text-indigo-800',
  dependency_failure: 'bg-gray-100 text-gray-800',
}

function toTitleCase(str) {
  return str
    .replace(/_/g, ' ')
    .replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
}

export default function CategoryBadge({ category }) {
  const classes = CATEGORY_CLASSES[category] || 'bg-gray-100 text-gray-500'
  const label = category ? toTitleCase(category) : 'Unknown'
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${classes}`}>
      {label}
    </span>
  )
}
