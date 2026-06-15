import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Briefcase, UserPlus } from 'lucide-react';
import { useAuth } from '../auth.jsx';

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: '', password: '', confirm: '', full_name: '', phone: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (form.password.length < 8) return setError('Password must be at least 8 characters.');
    if (form.password !== form.confirm) return setError('Passwords do not match.');
    setBusy(true);
    try {
      await register({ email: form.email, password: form.password, full_name: form.full_name, phone: form.phone });
      navigate('/', { replace: true });
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
        <h2 className="auth-title">Create account</h2>
        <p className="auth-subtitle">Your email is your username.</p>

        {error && <div className="auth-error">{error}</div>}

        <label className="control-label">Full name</label>
        <input className="input-text" value={form.full_name} onChange={set('full_name')} placeholder="Jane Doe" />

        <label className="control-label" style={{ marginTop: '0.75rem' }}>Email</label>
        <input className="input-text" type="email" value={form.email} onChange={set('email')} placeholder="you@example.com" required />

        <label className="control-label" style={{ marginTop: '0.75rem' }}>Phone (optional)</label>
        <input className="input-text" value={form.phone} onChange={set('phone')} placeholder="+1 555 123 4567" />

        <label className="control-label" style={{ marginTop: '0.75rem' }}>Password (min 8 chars)</label>
        <input className="input-text" type="password" value={form.password} onChange={set('password')} placeholder="••••••••" required />

        <label className="control-label" style={{ marginTop: '0.75rem' }}>Confirm password</label>
        <input className="input-text" type="password" value={form.confirm} onChange={set('confirm')} placeholder="••••••••" required />

        <button className="btn btn-primary" type="submit" disabled={busy} style={{ marginTop: '1.25rem', width: '100%' }}>
          <UserPlus size={16} />
          {busy ? 'Creating…' : 'Create account'}
        </button>

        <p className="auth-footer">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </div>
  );
}
