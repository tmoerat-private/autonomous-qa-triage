import React from 'react'

const STATUS_CLASSES = {
  new: 'bg-sky-100 text-sky-800',
  triaging: 'bg-amber-100 text-amber-800',
  triaged: 'bg-green-100 text-green-800',
  resolved: 'bg-emerald-100 text-emerald-800',
  ignored: 'bg-gray-100 text-gray-500',
}

function toTitleCase(str) {
  return str.charAt(0).toUpperCase() + str.slice(1)
}

export default function StatusBadge({ status }) {
  const classes = STATUS_CLASSES[status] || 'bg-gray-100 text-gray-500'
  const label = status ? toTitleCase(status) : 'Unknown'
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${classes}`}>
      {label}
    </span>
  )
}
