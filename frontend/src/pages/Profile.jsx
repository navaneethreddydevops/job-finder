import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Save, KeyRound, User } from 'lucide-react';
import { useAuth, apiFetch } from '../auth.jsx';
import UserMenu from '../components/UserMenu.jsx';

export default function Profile() {
  const { user, updateUser } = useAuth();
  const [profile, setProfile] = useState({
    full_name: user?.full_name || '',
    phone: user?.phone || '',
    email: user?.email || '',
  });
  const [profileMsg, setProfileMsg] = useState(null);
  const [pw, setPw] = useState({ current_password: '', new_password: '', confirm: '' });
  const [pwMsg, setPwMsg] = useState(null);

  const setP = (k) => (e) => setProfile((p) => ({ ...p, [k]: e.target.value }));
  const setPW = (k) => (e) => setPw((p) => ({ ...p, [k]: e.target.value }));

  const saveProfile = async (e) => {
    e.preventDefault();
    setProfileMsg(null);
    const resp = await apiFetch('/api/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(profile),
    });
    const data = await resp.json();
    if (resp.ok) {
      updateUser(data.user);
      setProfileMsg({ ok: true, text: 'Profile updated.' });
    } else {
      setProfileMsg({ ok: false, text: data.detail || 'Update failed.' });
    }
  };

  const changePassword = async (e) => {
    e.preventDefault();
    setPwMsg(null);
    if (pw.new_password.length < 8) return setPwMsg({ ok: false, text: 'New password must be at least 8 characters.' });
    if (pw.new_password !== pw.confirm) return setPwMsg({ ok: false, text: 'New passwords do not match.' });
    const resp = await apiFetch('/api/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: pw.current_password, new_password: pw.new_password }),
    });
    const data = await resp.json();
    if (resp.ok) {
      setPw({ current_password: '', new_password: '', confirm: '' });
      setPwMsg({ ok: true, text: 'Password changed.' });
    } else {
      setPwMsg({ ok: false, text: data.detail || 'Change failed.' });
    }
  };

  return (
    <div className="app-container">
      <header className="app-header">
        <Link to="/" className="btn"><ArrowLeft size={16} /> Back to Dashboard</Link>
        <span className="logo-text" style={{ fontSize: '1.1rem' }}>Account Settings</span>
        <div className="header-actions" style={{ marginLeft: 'auto' }}>
          <UserMenu />
        </div>
      </header>

      <div className="profile-grid">
        <form className="sidebar-panel" onSubmit={saveProfile}>
          <div className="sidebar-title"><User size={18} className="text-primary" /> Profile</div>
          {profileMsg && (
            <div className={profileMsg.ok ? 'auth-success' : 'auth-error'}>{profileMsg.text}</div>
          )}
          <label className="control-label">Full name</label>
          <input className="input-text" value={profile.full_name} onChange={setP('full_name')} />
          <label className="control-label" style={{ marginTop: '0.75rem' }}>Email</label>
          <input className="input-text" type="email" value={profile.email} onChange={setP('email')} />
          <label className="control-label" style={{ marginTop: '0.75rem' }}>Phone</label>
          <input className="input-text" value={profile.phone} onChange={setP('phone')} />
          <button className="btn btn-primary" type="submit" style={{ marginTop: '1.25rem' }}>
            <Save size={16} /> Save profile
          </button>
        </form>

        <form className="sidebar-panel" onSubmit={changePassword}>
          <div className="sidebar-title"><KeyRound size={18} className="text-primary" /> Change password</div>
          {pwMsg && (
            <div className={pwMsg.ok ? 'auth-success' : 'auth-error'}>{pwMsg.text}</div>
          )}
          <label className="control-label">Current password</label>
          <input className="input-text" type="password" value={pw.current_password} onChange={setPW('current_password')} required />
          <label className="control-label" style={{ marginTop: '0.75rem' }}>New password (min 8 chars)</label>
          <input className="input-text" type="password" value={pw.new_password} onChange={setPW('new_password')} required />
          <label className="control-label" style={{ marginTop: '0.75rem' }}>Confirm new password</label>
          <input className="input-text" type="password" value={pw.confirm} onChange={setPW('confirm')} required />
          <button className="btn btn-primary" type="submit" style={{ marginTop: '1.25rem' }}>
            <KeyRound size={16} /> Update password
          </button>
        </form>
      </div>
    </div>
  );
}
