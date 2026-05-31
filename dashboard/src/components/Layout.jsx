import React, { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { getHealth } from '../api/client.js'

export default function Layout() {
  const [healthy, setHealthy] = useState(null)

  useEffect(() => {
    function check() {
      getHealth()
        .then(() => setHealthy(true))
        .catch(() => setHealthy(false))
    }
    check()
    const id = setInterval(check, 30000)
    return () => clearInterval(id)
  }, [])

  const navLinkClass = ({ isActive }) =>
    `flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive
        ? 'bg-indigo-600 text-white'
        : 'text-gray-300 hover:bg-gray-700 hover:text-white'
    }`

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-64 bg-gray-900 flex flex-col flex-shrink-0">
        {/* Logo */}
        <div className="px-6 py-5 border-b border-gray-700">
          <span className="text-white font-bold text-lg tracking-tight">
            Autonomous QA
          </span>
          <p className="text-gray-400 text-xs mt-0.5">Failure Triage Platform</p>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-1">
          <NavLink to="/" end className={navLinkClass}>
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
            </svg>
            Dashboard
          </NavLink>
          <NavLink to="/failures" className={navLinkClass}>
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            Failures
          </NavLink>
        </nav>

        {/* Footer — connection status */}
        <div className="px-4 py-4 border-t border-gray-700">
          <div className="flex items-center gap-2">
            <span
              className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                healthy === null
                  ? 'bg-gray-500'
                  : healthy
                  ? 'bg-green-400'
                  : 'bg-red-500'
              }`}
            />
            <span className="text-gray-400 text-xs">
              {healthy === null ? 'Checking...' : healthy ? 'API connected' : 'API unreachable'}
            </span>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 bg-gray-50 overflow-y-auto p-6">
        <Outlet />
      </main>
    </div>
  )
}
