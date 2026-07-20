import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Save, KeyRound, User, FileText, Upload, Download, Trash2, CheckCircle2 } from 'lucide-react';
import { useAuth, apiFetch } from '../auth.jsx';
import UserMenu from '../components/UserMenu.jsx';
import {
  ContactFields, LinksFields, AuthorizationFields, PreferencesFields,
  ExperienceFields, EeoFields, pickEditableFields,
} from '../components/profile/ProfileForms.jsx';

// Application-profile sections shown as tabs (same data as the onboarding wizard,
// same PUT /api/profile/full endpoint — everything stays editable after onboarding).
const APP_TABS = [
  { key: 'contact', label: 'Contact' },
  { key: 'links', label: 'Links' },
  { key: 'authorization', label: 'Work Auth' },
  { key: 'preferences', label: 'Preferences' },
  { key: 'experience', label: 'Experience' },
  { key: 'eeo', label: 'EEO' },
  { key: 'resume', label: 'Resume' },
];

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

  // Application profile (Task 9)
  const [appProfile, setAppProfile] = useState(null);
  const [applyReady, setApplyReady] = useState(false);
  const [missingFields, setMissingFields] = useState([]);
  const [appTab, setAppTab] = useState('contact');
  const [appMsg, setAppMsg] = useState(null);
  const [appBusy, setAppBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);

  const setP = (k) => (e) => setProfile((p) => ({ ...p, [k]: e.target.value }));
  const setPW = (k) => (e) => setPw((p) => ({ ...p, [k]: e.target.value }));

  const setAppField = (key, value) => setAppProfile((p) => ({ ...p, [key]: value }));
  const setApp = (key) => (e) => setAppField(key, e.target.value);

  useEffect(() => {
    (async () => {
      try {
        const resp = await apiFetch('/api/profile/full');
        if (resp.ok) {
          const data = await resp.json();
          setAppProfile(data.profile);
          setApplyReady(data.apply_ready);
          setMissingFields(data.missing_fields || []);
        }
      } catch (err) {
        console.error('Failed to load application profile:', err);
      }
    })();
  }, []);

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

  const saveAppProfile = async () => {
    setAppMsg(null);
    setAppBusy(true);
    try {
      const resp = await apiFetch('/api/profile/full', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pickEditableFields(appProfile)),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail?.message || data.detail || 'Save failed');
      setApplyReady(data.apply_ready);
      setMissingFields(data.missing_fields || []);
      setAppMsg({ ok: true, text: 'Application profile saved.' });
      // Keep the auth-context gate flags fresh for the dashboard's Auto-Apply button.
      const me = await apiFetch('/api/me');
      if (me.ok) updateUser((await me.json()).user);
    } catch (err) {
      setAppMsg({ ok: false, text: err.message });
    } finally {
      setAppBusy(false);
    }
  };

  const onResumeFile = async (file) => {
    if (!file) return;
    setAppMsg(null);
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('resume', file);
      const resp = await apiFetch('/api/profile/resume', { method: 'POST', body: fd });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Upload failed');
      setAppProfile((p) => ({ ...p, has_resume: true, resume_filename: data.profile.resume_filename }));
      setApplyReady(data.apply_ready);
      setMissingFields(data.missing_fields || []);
      setAppMsg({ ok: true, text: 'Resume uploaded.' });
      const me = await apiFetch('/api/me');
      if (me.ok) updateUser((await me.json()).user);
    } catch (err) {
      setAppMsg({ ok: false, text: err.message });
    } finally {
      setUploading(false);
    }
  };

  const downloadResume = async () => {
    try {
      const resp = await apiFetch('/api/profile/resume');
      if (!resp.ok) throw new Error('Download failed');
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = appProfile?.resume_filename || 'resume';
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setAppMsg({ ok: false, text: err.message });
    }
  };

  const deleteResume = async () => {
    const resp = await apiFetch('/api/profile/resume', { method: 'DELETE' });
    if (resp.ok) {
      const data = await resp.json();
      setAppProfile((p) => ({ ...p, has_resume: false, resume_filename: '' }));
      setApplyReady(data.apply_ready);
      setMissingFields(data.missing_fields || []);
      setAppMsg({ ok: true, text: 'Resume removed.' });
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

      {/* ── Application profile (Task 9) ─────────────────────────────────── */}
      <div className="sidebar-panel" id="application-profile-panel" style={{ marginTop: '1.5rem' }}>
        <div className="sidebar-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <FileText size={18} className="text-primary" /> Application Profile
          {applyReady ? (
            <span className="badge" style={{ backgroundColor: 'var(--success-glow)', color: 'var(--success)', display: 'flex', gap: '0.25rem', alignItems: 'center' }}>
              <CheckCircle2 size={11} /> Auto-Apply ready
            </span>
          ) : (
            <span className="badge badge-neutral" title={missingFields.join(', ')}>
              {missingFields.length} field{missingFields.length === 1 ? '' : 's'} missing
            </span>
          )}
        </div>
        <p className="auth-hint" style={{ marginTop: 0 }}>
          The apply agent fills employer application forms with these details.{' '}
          <Link to="/onboarding">Reopen the setup wizard</Link> to walk through them step by step.
        </p>

        {appMsg && (
          <div className={appMsg.ok ? 'auth-success' : 'auth-error'}>{appMsg.text}</div>
        )}

        {appProfile === null ? (
          <div className="auth-hint">Loading…</div>
        ) : (
          <>
            <div className="settings-tabs" style={{ marginBottom: '1rem' }}>
              {APP_TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className={`tab ${appTab === t.key ? 'active' : ''}`}
                  onClick={() => setAppTab(t.key)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {appTab === 'contact' && <ContactFields profile={appProfile} set={setApp} />}
            {appTab === 'links' && <LinksFields profile={appProfile} set={setApp} />}
            {appTab === 'authorization' && <AuthorizationFields profile={appProfile} set={setApp} setField={setAppField} />}
            {appTab === 'preferences' && <PreferencesFields profile={appProfile} set={setApp} setField={setAppField} />}
            {appTab === 'experience' && <ExperienceFields profile={appProfile} set={setApp} setField={setAppField} />}
            {appTab === 'eeo' && <EeoFields profile={appProfile} set={setApp} />}

            {appTab === 'resume' && (
              <div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".docx,.pdf"
                  hidden
                  onChange={(e) => onResumeFile(e.target.files?.[0])}
                />
                {appProfile.has_resume ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <FileText size={16} className="text-primary" />
                    <span>{appProfile.resume_filename || 'Resume on file'}</span>
                    <button type="button" className="btn btn-sm" onClick={downloadResume}><Download size={13} /> Download</button>
                    <button type="button" className="btn btn-sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                      <Upload size={13} /> {uploading ? 'Uploading…' : 'Replace'}
                    </button>
                    <button type="button" className="btn btn-sm" onClick={deleteResume}><Trash2 size={13} /> Remove</button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading}
                  >
                    <Upload size={16} /> {uploading ? 'Uploading…' : 'Upload resume (.docx or .pdf)'}
                  </button>
                )}
              </div>
            )}

            {appTab !== 'resume' && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={saveAppProfile}
                disabled={appBusy}
                style={{ marginTop: '1.25rem' }}
              >
                <Save size={16} /> {appBusy ? 'Saving…' : 'Save application profile'}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
