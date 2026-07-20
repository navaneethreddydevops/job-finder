// Shared profile form sections (Task 9) — used by the /onboarding wizard and the
// Profile page tabs so the same data stays editable everywhere. Each section takes
// `{ profile, set }` where `set(key)` returns an onChange handler and `setField(key,
// value)` sets non-event values (booleans, arrays).

export const VISA_OPTIONS = [
  { value: '', label: 'Select…' },
  { value: 'us_citizen', label: 'US Citizen' },
  { value: 'permanent_resident', label: 'Permanent Resident (Green Card)' },
  { value: 'h1b', label: 'H-1B' },
  { value: 'opt_ead', label: 'F-1 OPT / EAD' },
  { value: 'tn', label: 'TN' },
  { value: 'other', label: 'Other' },
];

export const EEO_DECLINE = 'Decline to self-identify';
export const EEO_GENDER_OPTIONS = [EEO_DECLINE, 'Male', 'Female', 'Non-binary'];
export const EEO_RACE_OPTIONS = [
  EEO_DECLINE,
  'American Indian or Alaska Native',
  'Asian',
  'Black or African American',
  'Hispanic or Latino',
  'Native Hawaiian or Other Pacific Islander',
  'White',
  'Two or More Races',
];
export const EEO_VETERAN_OPTIONS = [
  EEO_DECLINE,
  'I am not a protected veteran',
  'I identify as one or more of the classifications of a protected veteran',
];
export const EEO_DISABILITY_OPTIONS = [
  EEO_DECLINE,
  'No, I do not have a disability',
  'Yes, I have a disability (or previously had one)',
];

const US_STATES = 'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split(' ');

function Field({ label, children, required }) {
  return (
    <div className="profile-field">
      <label className="control-label">
        {label}
        {required && <span className="profile-required" title="Required to auto-apply"> *</span>}
      </label>
      {children}
    </div>
  );
}

// Yes/No/unanswered tri-state as two pills. null = not answered (blocks auto-apply).
function YesNo({ value, onChange, idPrefix }) {
  return (
    <div className="filter-pills">
      {[
        { v: true, label: 'Yes' },
        { v: false, label: 'No' },
      ].map(({ v, label }) => (
        <button
          key={label}
          type="button"
          id={`${idPrefix}-${label.toLowerCase()}`}
          className={`filter-pill ${value === v ? 'active' : ''}`}
          onClick={() => onChange(v)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

// Comma/Enter-separated tag list backed by a plain array of strings.
export function TagInput({ value = [], onChange, placeholder, id }) {
  const commit = (e) => {
    const raw = e.target.value.trim().replace(/,$/, '');
    if (!raw) return;
    if (!value.includes(raw)) onChange([...value, raw]);
    e.target.value = '';
  };
  return (
    <div className="tag-input-wrap">
      {value.map((tag) => (
        <span key={tag} className="requirement-tag tag-removable">
          {tag}
          <button type="button" aria-label={`Remove ${tag}`} onClick={() => onChange(value.filter((t) => t !== tag))}>×</button>
        </span>
      ))}
      <input
        id={id}
        className="input-text tag-input-field"
        placeholder={placeholder}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commit(e); }
        }}
        onBlur={commit}
      />
    </div>
  );
}

export function ContactFields({ profile, set }) {
  return (
    <div className="profile-fields-grid">
      <Field label="Full name" required>
        <input className="input-text" id="ob-full-name" value={profile.full_name || ''} onChange={set('full_name')} placeholder="Jane Doe" />
      </Field>
      <Field label="Email" required>
        <input className="input-text" id="ob-email" type="email" value={profile.email || ''} onChange={set('email')} placeholder="you@example.com" />
      </Field>
      <Field label="Phone" required>
        <input className="input-text" id="ob-phone" value={profile.phone || ''} onChange={set('phone')} placeholder="+1 555 123 4567" />
      </Field>
      <Field label="Street address">
        <input className="input-text" id="ob-street" value={profile.address_street || ''} onChange={set('address_street')} placeholder="123 Main St" />
      </Field>
      <Field label="City" required>
        <input className="input-text" id="ob-city" value={profile.address_city || ''} onChange={set('address_city')} placeholder="Austin" />
      </Field>
      <Field label="State" required>
        <select className="input-text" id="ob-state" value={profile.address_state || ''} onChange={set('address_state')}>
          <option value="">Select…</option>
          {US_STATES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </Field>
      <Field label="ZIP code">
        <input className="input-text" id="ob-zip" value={profile.address_zip || ''} onChange={set('address_zip')} placeholder="78701" />
      </Field>
    </div>
  );
}

export function LinksFields({ profile, set }) {
  return (
    <div className="profile-fields-grid">
      <Field label="LinkedIn URL">
        <input className="input-text" id="ob-linkedin" value={profile.linkedin_url || ''} onChange={set('linkedin_url')} placeholder="https://linkedin.com/in/you" />
      </Field>
      <Field label="GitHub URL">
        <input className="input-text" id="ob-github" value={profile.github_url || ''} onChange={set('github_url')} placeholder="https://github.com/you" />
      </Field>
      <Field label="Portfolio / website">
        <input className="input-text" id="ob-portfolio" value={profile.portfolio_url || ''} onChange={set('portfolio_url')} placeholder="https://you.dev" />
      </Field>
    </div>
  );
}

export function AuthorizationFields({ profile, setField, set }) {
  return (
    <div className="profile-fields-grid">
      <Field label="Are you authorized to work in the United States?" required>
        <YesNo idPrefix="ob-authorized" value={profile.authorized_us} onChange={(v) => setField('authorized_us', v)} />
      </Field>
      <Field label="Will you now or in the future require visa sponsorship?" required>
        <YesNo idPrefix="ob-sponsorship" value={profile.requires_sponsorship} onChange={(v) => setField('requires_sponsorship', v)} />
      </Field>
      <Field label="Citizenship / visa status">
        <select className="input-text" id="ob-visa" value={profile.visa_status || ''} onChange={set('visa_status')}>
          {VISA_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </Field>
    </div>
  );
}

export function PreferencesFields({ profile, set, setField }) {
  return (
    <div className="profile-fields-grid">
      <Field label="Desired roles / titles">
        <TagInput id="ob-roles" value={profile.desired_roles || []} onChange={(v) => setField('desired_roles', v)} placeholder="e.g. Principal DevOps Engineer — Enter to add" />
      </Field>
      <Field label="Minimum salary expectation (USD)">
        <input className="input-text" id="ob-salary" type="number" min="0" step="1000" value={profile.salary_min ?? ''} onChange={(e) => setField('salary_min', e.target.value === '' ? null : parseInt(e.target.value, 10))} placeholder="180000" />
      </Field>
      <Field label="Notice period">
        <input className="input-text" id="ob-notice" value={profile.notice_period || ''} onChange={set('notice_period')} placeholder="e.g. 2 weeks" />
      </Field>
      <Field label="Available to start">
        <input className="input-text" id="ob-availability" type="date" value={profile.availability_date || ''} onChange={set('availability_date')} />
      </Field>
      <Field label="Willing to relocate?">
        <YesNo idPrefix="ob-relocate" value={profile.willing_to_relocate} onChange={(v) => setField('willing_to_relocate', v)} />
      </Field>
      <Field label="Preferred locations">
        <TagInput id="ob-locations" value={profile.preferred_locations || []} onChange={(v) => setField('preferred_locations', v)} placeholder="e.g. Remote, Austin TX — Enter to add" />
      </Field>
    </div>
  );
}

export function ExperienceFields({ profile, set, setField }) {
  const education = profile.education || [];
  const setEdu = (idx, key, value) => {
    const next = education.map((e, i) => (i === idx ? { ...e, [key]: value } : e));
    setField('education', next);
  };
  return (
    <div className="profile-fields-grid">
      <Field label="Years of professional experience" required>
        <input className="input-text" id="ob-years" type="number" min="0" max="60" value={profile.years_experience ?? ''} onChange={(e) => setField('years_experience', e.target.value === '' ? null : parseInt(e.target.value, 10))} placeholder="10" />
      </Field>
      <Field label="Current / most recent title">
        <input className="input-text" id="ob-title" value={profile.current_title || ''} onChange={set('current_title')} placeholder="Principal DevOps Engineer" />
      </Field>
      <Field label="Current / most recent company">
        <input className="input-text" id="ob-company" value={profile.current_company || ''} onChange={set('current_company')} placeholder="Acme Corp" />
      </Field>
      <Field label="Key skills">
        <TagInput id="ob-skills" value={profile.skills || []} onChange={(v) => setField('skills', v)} placeholder="e.g. Kubernetes — Enter to add" />
      </Field>
      <Field label="Education">
        <div className="education-list">
          {education.map((edu, idx) => (
            <div key={idx} className="education-row">
              <input className="input-text" placeholder="Degree (e.g. BS)" value={edu.degree || ''} onChange={(e) => setEdu(idx, 'degree', e.target.value)} />
              <input className="input-text" placeholder="Field of study" value={edu.field || ''} onChange={(e) => setEdu(idx, 'field', e.target.value)} />
              <input className="input-text" placeholder="School" value={edu.school || ''} onChange={(e) => setEdu(idx, 'school', e.target.value)} />
              <input className="input-text education-year" placeholder="Year" value={edu.year || ''} onChange={(e) => setEdu(idx, 'year', e.target.value)} />
              <button type="button" className="btn btn-sm" aria-label="Remove education entry" onClick={() => setField('education', education.filter((_, i) => i !== idx))}>×</button>
            </div>
          ))}
          <button type="button" id="ob-add-education" className="btn btn-sm" onClick={() => setField('education', [...education, { degree: '', field: '', school: '', year: '' }])}>
            + Add education
          </button>
        </div>
      </Field>
    </div>
  );
}

export function EeoFields({ profile, set }) {
  const selects = [
    { key: 'eeo_gender', label: 'Gender', options: EEO_GENDER_OPTIONS },
    { key: 'eeo_race', label: 'Race / ethnicity', options: EEO_RACE_OPTIONS },
    { key: 'eeo_veteran', label: 'Veteran status', options: EEO_VETERAN_OPTIONS },
    { key: 'eeo_disability', label: 'Disability status', options: EEO_DISABILITY_OPTIONS },
  ];
  return (
    <>
      <div className="auth-hint" style={{ marginBottom: '0.75rem' }}>
        These voluntary self-identification questions appear on most US application
        forms. Answering is entirely optional — everything defaults to
        “{EEO_DECLINE}”, and the apply agent will use exactly what you choose here.
      </div>
      <div className="profile-fields-grid">
        {selects.map(({ key, label, options }) => (
          <Field key={key} label={label}>
            <select className="input-text" id={`ob-${key}`} value={profile[key] || EEO_DECLINE} onChange={set(key)}>
              {options.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </Field>
        ))}
      </div>
    </>
  );
}

// The editable field keys sent to PUT /api/profile/full (mirrors backend PROFILE_FIELDS
// minus onboarding meta, which callers manage explicitly).
export const PROFILE_EDIT_KEYS = [
  'full_name', 'email', 'phone',
  'address_street', 'address_city', 'address_state', 'address_zip', 'address_country',
  'linkedin_url', 'github_url', 'portfolio_url',
  'authorized_us', 'requires_sponsorship', 'visa_status',
  'desired_roles', 'salary_min', 'salary_currency', 'notice_period',
  'availability_date', 'willing_to_relocate', 'preferred_locations',
  'years_experience', 'current_title', 'current_company', 'education', 'skills',
  'eeo_gender', 'eeo_race', 'eeo_veteran', 'eeo_disability',
];

export function pickEditableFields(profile) {
  const out = {};
  for (const k of PROFILE_EDIT_KEYS) {
    if (profile[k] !== undefined) out[k] = profile[k];
  }
  return out;
}
