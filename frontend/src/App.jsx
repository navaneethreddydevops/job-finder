import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth.jsx';
import { ToastProvider } from './components/Toast.jsx';
import CommandPalette from './components/CommandPalette.jsx';
import Dashboard from './Dashboard.jsx';
import Login from './pages/Login.jsx';
import Register from './pages/Register.jsx';
import Profile from './pages/Profile.jsx';
import { lazy, Suspense, useState, useEffect } from 'react';

// Lazy-loaded routes: these pages pull in heavy libraries (docx-preview for the
// resume optimizer, recharts for analytics) that the dashboard never uses, so
// they're split into their own chunks and only fetched/parsed on first visit.
const ResumeOptimizer = lazy(() => import('./pages/ResumeOptimizer.jsx'));
const Analytics = lazy(() => import('./pages/Analytics.jsx'));
const Settings = lazy(() => import('./pages/Settings.jsx'));
import { SpeedInsights } from '@vercel/speed-insights/react';

function RequireAuth({ children }) {
  const { token, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return <div className="auth-screen"><div className="auth-card">Loading…</div></div>;
  }
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return children;
}

function AppContent() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const handleKeyDown = (e) => {
      // Cmd+K or Ctrl+K to open command palette
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setPaletteOpen(true);
      }
      // Ctrl+N for new search
      if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
        e.preventDefault();
        navigate('/');
      }
      // Ctrl+B for bookmarks
      if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
        e.preventDefault();
        // Trigger bookmarks filter in dashboard
        window.dispatchEvent(new CustomEvent('filter-bookmarks'));
      }
      // Ctrl+L for applications
      if ((e.ctrlKey || e.metaKey) && e.key === 'l') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('filter-applications'));
      }
      // ? for help
      if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [navigate]);

  const handleCommandNavigate = (action) => {
    if (action.startsWith('/')) {
      navigate(action);
    } else if (action === 'new-search') {
      navigate('/');
    } else if (action === 'bookmarks') {
      window.dispatchEvent(new CustomEvent('filter-bookmarks'));
    } else if (action === 'applications') {
      window.dispatchEvent(new CustomEvent('filter-applications'));
    }
  };

  return (
    <>
      <CommandPalette
        isOpen={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onNavigate={handleCommandNavigate}
      />
      <Suspense fallback={<div className="auth-screen"><div className="auth-card">Loading…</div></div>}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/" element={<RequireAuth><Dashboard /></RequireAuth>} />
          <Route path="/profile" element={<RequireAuth><Profile /></RequireAuth>} />
          <Route path="/resume/optimizer" element={<RequireAuth><ResumeOptimizer /></RequireAuth>} />
          <Route path="/analytics" element={<RequireAuth><Analytics /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </>
  );
}

function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <BrowserRouter>
          <AppContent />
        </BrowserRouter>
        <SpeedInsights />
      </ToastProvider>
    </AuthProvider>
  );
}

export default App;
