import { createContext, useContext, useEffect, useState, useCallback } from 'react';

const AuthContext = createContext(null);
const STORAGE_KEY = 'jf_auth';

let currentToken = null;

/**
 * Base URL of the backend API. Empty in local dev (Vite proxies /api to :8000);
 * set to the FastAPI Cloud backend origin (e.g. https://job-finder.fastapicloud.dev) at
 * build time via VITE_API_BASE_URL when the frontend is deployed to Vercel.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

/** Resolve a relative API path against API_BASE (leaves absolute URLs untouched). */
export function apiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path}`;
}

/**
 * Fetch wrapper that attaches the bearer token. On a 401 it clears auth and
 * reloads so the user is bounced to the login screen.
 */
export async function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (currentToken) headers['Authorization'] = `Bearer ${currentToken}`;
  const resp = await fetch(apiUrl(url), { ...options, headers });
  if (resp.status === 401) {
    localStorage.removeItem(STORAGE_KEY);
    currentToken = null;
    if (!url.includes('/login') && !url.includes('/register')) {
      window.location.assign('/login');
    }
  }
  return resp;
}

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });
  const [loading, setLoading] = useState(true);

  // Keep the module-level token in sync for apiFetch.
  // eslint-disable-next-line react-hooks/globals
  currentToken = auth?.token || null;

  const persist = useCallback((next) => {
    if (next) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
    currentToken = next?.token || null;
    setAuth(next);
  }, []);

  // Validate the stored token on first load.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (auth?.token) {
        try {
          const resp = await apiFetch('/api/me');
          if (!cancelled && resp.ok) {
            const data = await resp.json();
            persist({ token: auth.token, user: data.user });
          } else if (!cancelled) {
            persist(null);
          }
        } catch {
          /* offline — keep cached auth */
        }
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(async (email, password) => {
    const resp = await fetch(apiUrl('/api/login'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Login failed');
    persist({ token: data.token, user: data.user });
    return data.user;
  }, [persist]);

  const register = useCallback(async (payload) => {
    const resp = await fetch(apiUrl('/api/register'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Registration failed');
    persist({ token: data.token, user: data.user });
    return data.user;
  }, [persist]);

  const logout = useCallback(async () => {
    try { await apiFetch('/api/logout', { method: 'POST' }); } catch { /* ignore */ }
    persist(null);
    window.location.assign('/login');
  }, [persist]);

  const updateUser = useCallback((user) => {
    setAuth((prev) => {
      const next = prev ? { ...prev, user } : prev;
      if (next) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  return (
    <AuthContext.Provider
      value={{ token: auth?.token || null, user: auth?.user || null, loading, login, register, logout, updateUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
