import React, { createContext, useContext, useEffect, useState } from 'react'

const STORAGE_KEY = 'aqa-theme'
const ThemeContext = createContext({ theme: 'dark', toggleTheme: () => {} })

// Resolve the initial theme: stored preference wins, otherwise fall back to the
// OS-level color scheme, otherwise default to dark (the app's original look).
function getInitialTheme() {
  if (typeof window === 'undefined') return 'dark'
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  if (window.matchMedia?.('(prefers-color-scheme: light)').matches) return 'light'
  return 'dark'
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(getInitialTheme)

  // Apply the theme to <html> and persist it whenever it changes.
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  return useContext(ThemeContext)
}
