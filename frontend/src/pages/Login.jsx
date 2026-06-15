import { useState } from 'react';
import { useNavigate, Link, useLocation } from 'react-router-dom';
import { Briefcase, LogIn } from 'lucide-react';
import { useAuth } from '../auth.jsx';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('test@test.com');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const from = location.state?.from?.pathname || '/';

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-logo">
          <Briefcase size={28} className="logo-icon" />
          <span className="logo-text">Job Finder</span>
        </div>
        <h2 className="auth-title">Sign in</h2>
        <p className="auth-subtitle">Use your email and password to continue.</p>

        {error && <div className="auth-error">{error}</div>}

        <label className="control-label">Email</label>
        <input className="input-text" type="email" value={email}
          onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required />

        <label className="control-label" style={{ marginTop: '0.75rem' }}>Password</label>
        <input className="input-text" type="password" value={password}
          onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" required />

        <button className="btn btn-primary" type="submit" disabled={busy} style={{ marginTop: '1.25rem', width: '100%' }}>
          <LogIn size={16} />
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="auth-footer">
          No account? <Link to="/register">Create one</Link>
        </p>
        <p className="auth-hint">Test login: test@test.com / testtest</p>
      </form>
    </div>
  );
}
