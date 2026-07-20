import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Briefcase, Upload, FileText, CheckCircle2, ArrowLeft, ArrowRight } from 'lucide-react';
import { useAuth, apiFetch } from '../auth.jsx';
import { useToast } from '../components/Toast.jsx';
import {
  ContactFields, LinksFields, AuthorizationFields, PreferencesFields,
  ExperienceFields, EeoFields, pickEditableFields,
} from '../components/profile/ProfileForms.jsx';

// Onboarding wizard (Task 9): collects everything a careers-page application form
// asks for, so the apply agent (Task 10) can apply autonomously. Fully skippable —
// the dashboard is never blocked; the Auto-Apply button is what requires a complete
// profile. Progress persists server-side on every step (onboarding_step), so a
// refresh resumes where the user left off.

const DRAFT_KEY = 'jf_onboarding_draft';

const STEPS = [
  { key: 'contact', title: 'Contact details', blurb: 'Used to fill the name/email/phone/address fields on application forms.' },
  { key: 'links', title: 'Profiles & links', blurb: 'LinkedIn, GitHub, and portfolio links most forms ask for.' },
  { key: 'authorization', title: 'Work authorization', blurb: 'Required on every US application. The agent answers these exactly as you set them — it never guesses.' },
  { key: 'preferences', title: 'Preferences', blurb: 'Salary expectations, availability, and locations for screening questions.' },
  { key: 'experience', title: 'Experience', blurb: 'Background details for experience and education questions.' },
  { key: 'eeo', title: 'Self-identification (optional)', blurb: 'Voluntary EEO questions. Skipping keeps everything at “Decline to self-identify”.' },
  { key: 'resume', title: 'Resume', blurb: 'The file the agent uploads with each application (.docx or .pdf, max 5 MB).' },
  { key: 'review', title: 'Review & finish', blurb: 'Check that everything is ready for autonomous applications.' },
];

export default function Onboarding() {
  const { user, updateUser } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();
  const fileInputRef = useRef(null);

  const [profile, setProfile] = useState(null); // null until loaded
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [applyReady, setApplyReady] = useState(false);
  const [missingFields, setMissingFields] = useState([]);
  const [resumePreview, setResumePreview] = useState('');
  const [uploading, setUploading] = useState(false);

  // Load stored profile + resume the saved step; overlay any unsaved local draft.
  useEffect(() => {
    (async () => {
      try {
        const resp = await apiFetch('/api/profile/full');
        if (!resp.ok) throw new Error('Failed to load profile');
        const data = await resp.json();
        let loaded = data.profile;
        try {
          const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
          if (draft) loaded = { ...loaded, ...draft };
        } catch { /* corrupt draft — ignore */ }
        // Prefill contact basics from the account when the profile is empty.
        loaded = {
          ...loaded,
          full_name: loaded.full_name || user?.full_name || '',
          email: loaded.email || user?.email || '',
          phone: loaded.phone || user?.phone || '',
        };
        setProfile(loaded);
        setApplyReady(data.apply_ready);
        setMissingFields(data.missing_fields || []);
        setStep(Math.min(loaded.onboarding_step || 0, STEPS.length - 1));
      } catch (err) {
        setError(err.message);
        setProfile({});
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setField = (key, value) => {
    setProfile((p) => {
      const next = { ...p, [key]: value };
      try { localStorage.setItem(DRAFT_KEY, JSON.stringify(pickEditableFields(next))); } catch { /* quota */ }
      return next;
    });
  };
  const set = (key) => (e) => setField(key, e.target.value);

  const save = async (extra = {}) => {
    const resp = await apiFetch('/api/profile/full', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...pickEditableFields(profile), ...extra }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail?.message || data.detail || 'Save failed');
    setApplyReady(data.apply_ready);
    setMissingFields(data.missing_fields || []);
    localStorage.removeItem(DRAFT_KEY);
    return data;
  };

  const goTo = async (nextStep, { finish = false, skip = false } = {}) => {
    setError('');
    setBusy(true);
    try {
      await save({
        onboarding_step: finish ? STEPS.length - 1 : nextStep,
        ...(finish ? { onboarding_completed: true } : {}),
      });
      if (finish || skip) {
        // Refresh the gate flags on the user object so the dashboard sees them.
        const me = await apiFetch('/api/me');
        if (me.ok) updateUser((await me.json()).user);
        addToast(finish ? 'Profile complete — Auto-Apply is ready!' : 'Progress saved — finish anytime from Profile.', 'success');
        navigate('/', { replace: true });
      } else {
        setStep(nextStep);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const onResumeFile = async (file) => {
    if (!file) return;
    const lower = file.name.toLowerCase();
    if (!lower.endsWith('.docx') && !lower.endsWith('.pdf')) {
      setError('Please upload a .docx or .pdf file.');
      return;
    }
    setError('');
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('resume', file);
      const resp = await apiFetch('/api/profile/resume', { method: 'POST', body: fd });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Upload failed');
      setProfile((p) => ({ ...p, has_resume: true, resume_filename: data.profile.resume_filename }));
      setApplyReady(data.apply_ready);
      setMissingFields(data.missing_fields || []);
      setResumePreview(data.resume_text_preview || '');
      addToast(data.resume_text_empty
        ? 'Resume stored (no text could be extracted — the file itself will still be uploaded).'
        : 'Resume uploaded.', 'success');
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  };

  if (profile === null) {
    return <div className="auth-screen"><div className="auth-card">Loading…</div></div>;
  }

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  return (
    <div className="auth-screen onboarding-screen">
      <div className="auth-card onboarding-card" id="onboarding-wizard">
        <div className="auth-logo">
          <Briefcase size={28} className="logo-icon" />
          <span className="logo-text">Job Finder</span>
        </div>
        <h2 className="auth-title">Set up your application profile</h2>
        <p className="auth-subtitle">
          Answer once — the apply agent uses these details to fill employer
          application forms for you.
        </p>

        {/* Step chips */}
        <div className="wizard-steps" role="tablist" aria-label="Onboarding steps">
          {STEPS.map((s, i) => (
            <button
              key={s.key}
              type="button"
              role="tab"
              aria-selected={i === step}
              className={`wizard-step-chip ${i === step ? 'active' : ''} ${i < step ? 'done' : ''}`}
              onClick={() => setStep(i)}
              title={s.title}
            >
              <span className="resume-step-num">{i < step ? '✓' : i + 1}</span>
            </button>
          ))}
        </div>

        <h3 className="wizard-step-title">{current.title}</h3>
        <p className="wizard-step-blurb">{current.blurb}</p>

        {error && <div className="auth-error">{error}</div>}

        <div className="wizard-step-body">
          {current.key === 'contact' && <ContactFields profile={profile} set={set} />}
          {current.key === 'links' && <LinksFields profile={profile} set={set} />}
          {current.key === 'authorization' && <AuthorizationFields profile={profile} set={set} setField={setField} />}
          {current.key === 'preferences' && <PreferencesFields profile={profile} set={set} setField={setField} />}
          {current.key === 'experience' && <ExperienceFields profile={profile} set={set} setField={setField} />}
          {current.key === 'eeo' && <EeoFields profile={profile} set={set} />}

          {current.key === 'resume' && (
            <div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".docx,.pdf"
                hidden
                onChange={(e) => onResumeFile(e.target.files?.[0])}
              />
              {profile.has_resume ? (
                <div className="auth-success" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <FileText size={16} />
                  <span>{profile.resume_filename || 'Resume on file'}</span>
                  <button type="button" className="btn btn-sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                    Replace
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  id="ob-resume-upload"
                  className="btn btn-primary"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  style={{ width: '100%', justifyContent: 'center' }}
                >
                  <Upload size={16} />
                  {uploading ? 'Uploading…' : 'Upload resume (.docx or .pdf)'}
                </button>
              )}
              {resumePreview && (
                <div className="resume-text-preview">
                  <span className="control-label">Extracted text preview</span>
                  <pre>{resumePreview}</pre>
                </div>
              )}
            </div>
          )}

          {current.key === 'review' && (
            <div>
              {applyReady ? (
                <div className="auth-success" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <CheckCircle2 size={16} />
                  Everything the apply agent needs is in place.
                </div>
              ) : (
                <div className="auth-error">
                  <strong>Still missing for Auto-Apply:</strong>
                  <ul style={{ margin: '0.5rem 0 0 1.1rem' }}>
                    {missingFields.map((f) => <li key={f}>{f}</li>)}
                  </ul>
                </div>
              )}
              <p className="auth-hint" style={{ marginTop: '0.75rem' }}>
                You can edit any of this later under <strong>Profile → Application Profile</strong>.
              </p>
            </div>
          )}
        </div>

        <div className="wizard-actions">
          <button
            type="button"
            className="btn"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0 || busy}
          >
            <ArrowLeft size={14} /> Back
          </button>
          <button
            type="button"
            id="ob-skip-btn"
            className="clear-filters-link wizard-skip"
            onClick={() => goTo(step, { skip: true })}
            disabled={busy}
          >
            Skip for now
          </button>
          {isLast ? (
            <button
              type="button"
              id="ob-finish-btn"
              className="btn btn-primary"
              onClick={() => goTo(step, { finish: true })}
              disabled={busy}
            >
              <CheckCircle2 size={14} /> {busy ? 'Saving…' : 'Finish'}
            </button>
          ) : (
            <button
              type="button"
              id="ob-next-btn"
              className="btn btn-primary"
              onClick={() => goTo(step + 1)}
              disabled={busy}
            >
              {busy ? 'Saving…' : 'Next'} <ArrowRight size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
