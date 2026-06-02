import React from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Failures from './pages/Failures.jsx'
import FailureDetail from './pages/FailureDetail.jsx'
import Agents from './pages/Agents.jsx'
import Releases from './pages/Releases.jsx'
import Settings from './pages/Settings.jsx'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="failures" element={<Failures />} />
        <Route path="failures/:id" element={<FailureDetail />} />
        <Route path="agents" element={<Agents />} />
        <Route path="releases" element={<Releases />} />
        <Route path="settings" element={<Settings />} />
      </Route>
    </Routes>
  )
}
