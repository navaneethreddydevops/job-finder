import { useEffect, useRef, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { renderAsync } from 'docx-preview';
import {
  ArrowLeft, Upload, Sparkles, Download, FileText, Plus, Trash2, Save, Eye, Pencil,
} from 'lucide-react';
import { apiFetch } from '../auth.jsx';
import UserMenu from '../components/UserMenu.jsx';

const STATE_KEY = 'jf_resume_state';

function dataUrlToArrayBuffer(dataUrl) {
  const base64 = dataUrl.split(',')[1];
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

// docx-preview options — ignoreWidth/Height reflow the page to the container so it fits.
const DOCX_OPTS = { className: 'docx', inWrapper: true, ignoreWidth: true, ignoreHeight: true };

export default function ResumeOptimizer() {
  const [jd, setJd] = useState('');
  const [fileName, setFileName] = useState('');
  const [fileDataUrl, setFileDataUrl] = useState('');
  const [status, setStatus] = useState({ status: 'idle', stage: '', progress: 0, error: '', has_result: false });
  const [error, setError] = useState('');

  // Editable content
  const [content, setContent] = useState(null);      // optimized structured resume
  const [originalText, setOriginalText] = useState(''); // editable extracted original
  const [origMode, setOrigMode] = useState('preview'); // 'preview' | 'edit'
  const [optMode, setOptMode] = useState('edit');      // 'edit' | 'preview'
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(0);

  const leftRef = useRef(null);
  const rightRef = useRef(null);
  const pollRef = useRef(null);
  const fileInputRef = useRef(null);

  // ---- persistence: restore JD + uploaded resume + edits across refresh ----
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STATE_KEY) || '{}');
      if (saved.jd) setJd(saved.jd);
      if (saved.fileName) setFileName(saved.fileName);
      if (saved.fileDataUrl) setFileDataUrl(saved.fileDataUrl);
      if (saved.content) setContent(saved.content);
      if (saved.originalText) setOriginalText(saved.originalText);
    } catch { /* ignore */ }
    refreshStatus();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ jd, fileName, fileDataUrl, content, originalText }));
  }, [jd, fileName, fileDataUrl, content, originalText]);

  // Render uploaded resume into the left pane (preview mode).
  useEffect(() => {
    if (origMode === 'preview' && fileDataUrl && leftRef.current) {
      leftRef.current.innerHTML = '';
      try { renderAsync(dataUrlToArrayBuffer(fileDataUrl), leftRef.current, undefined, DOCX_OPTS); } catch { /* ignore */ }
    }
  }, [fileDataUrl, origMode]);

  const renderResultPreview = useCallback(async () => {
    try {
      const resp = await apiFetch('/api/resume/download');
      if (!resp.ok) return;
      const buf = await resp.arrayBuffer();
      if (rightRef.current) {
        rightRef.current.innerHTML = '';
        await renderAsync(buf, rightRef.current, undefined, DOCX_OPTS);
      }
    } catch { /* ignore */ }
  }, []);

  // Render the optimized docx whenever we switch into preview mode.
  useEffect(() => {
    if (optMode === 'preview' && status.has_result) renderResultPreview();
  }, [optMode, status.has_result, savedAt, renderResultPreview]);

  const loadResult = useCallback(async () => {
    try {
      const resp = await apiFetch('/api/resume/result');
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.content) setContent(data.content);
      if (data.original_text) setOriginalText((cur) => cur || data.original_text);
    } catch { /* ignore */ }
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const resp = await apiFetch('/api/resume/status');
      if (!resp.ok) return;
      const data = await resp.json();
      setStatus(data);
      if (data.job_description) setJd((cur) => cur || data.job_description);
      if (data.status === 'running') startPolling();
      if (data.status === 'done' && data.has_result) loadResult();
    } catch { /* ignore */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadResult]);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const resp = await apiFetch('/api/resume/status');
        if (!resp.ok) return;
        const data = await resp.json();
        setStatus(data);
        if (data.status !== 'running') {
          clearInterval(pollRef.current);
          pollRef.current = null;
          if (data.status === 'done') loadResult();
          if (data.status === 'error') setError(data.error || 'Generation failed.');
        }
      } catch { /* ignore */ }
    }, 1500);
  }, [loadResult]);

  const onFile = (file) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.docx')) { setError('Please upload a .docx Word file.'); return; }
    setError('');
    const reader = new FileReader();
    reader.onload = () => { setFileName(file.name); setFileDataUrl(reader.result); setOrigMode('preview'); };
    reader.readAsDataURL(file);
  };

  const optimize = async () => {
    setError('');
    if (!jd.trim()) { setError('Enter a job description / requirement first.'); return; }
    const fd = new FormData();
    fd.append('job_description', jd);
    // Prefer edited original text; otherwise send the uploaded file.
    if (originalText.trim()) {
      fd.append('original_text', originalText);
    } else if (fileDataUrl) {
      fd.append('resume', new Blob([dataUrlToArrayBuffer(fileDataUrl)], {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      }), fileName || 'resume.docx');
    }
    setContent(null);
    setStatus({ status: 'running', stage: 'Queued', progress: 5, error: '', has_result: false });
    try {
      const resp = await apiFetch('/api/resume/optimize', { method: 'POST', body: fd });
      if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(d.detail || 'Failed to start.'); }
      startPolling();
    } catch (err) { setError(err.message); setStatus((s) => ({ ...s, status: 'error' })); }
  };

  // ---- structured-content editing helpers ----
  const updateContent = (mutator) => {
    setContent((prev) => {
      const next = structuredClone(prev || { summary: '', sections: [] });
      mutator(next);
      return next;
    });
  };
  const setSummary = (v) => updateContent((c) => { c.summary = v; });
  const setSectionTitle = (si, v) => updateContent((c) => { c.sections[si].title = v; });
  const setItemText = (si, ii, v) => updateContent((c) => { c.sections[si].items[ii].text = v; });
  const addItem = (si) => updateContent((c) => { c.sections[si].items.push({ text: '', is_new: true }); });
  const removeItem = (si, ii) => updateContent((c) => { c.sections[si].items.splice(ii, 1); });
  const addSection = () => updateContent((c) => { c.sections.push({ title: 'New Section', items: [] }); });
  const removeSection = (si) => updateContent((c) => { c.sections.splice(si, 1); });

  const saveContent = async () => {
    if (!content) return;
    setSaving(true);
    setError('');
    try {
      const resp = await apiFetch('/api/resume/content', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });
      if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(d.detail || 'Save failed.'); }
      setStatus((s) => ({ ...s, has_result: true }));
      setSavedAt(Date.now());
    } catch (err) { setError(err.message); }
    finally { setSaving(false); }
  };

  const downloadDocx = async () => {
    if (content) await saveContent(); // ensure the file reflects current edits
    const resp = await apiFetch('/api/resume/download');
    if (!resp.ok) return;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'optimized_resume.docx';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  const running = status.status === 'running';
  const newCount = content
    ? content.sections.reduce((n, s) => n + s.items.filter((i) => i.is_new).length, 0)
    : 0;

  return (
    <div className="app-container">
      <header className="app-header">
        <Link to="/" className="btn"><ArrowLeft size={16} /> Back</Link>
        <div className="logo-section">
          <FileText className="logo-icon" size={24} />
          <span className="logo-text">Resume Optimizer</span>
        </div>
        <div className="header-actions" style={{ marginLeft: 'auto' }}>
          <UserMenu />
        </div>
      </header>

      {/* Top chat window: job description + optimize */}
      <section className="resume-chat">
        <label className="control-label">Job Description / Requirement</label>
        <textarea className="input-text resume-jd" value={jd} onChange={(e) => setJd(e.target.value)}
          placeholder="Paste the job description or list the requirements you want your resume tailored to…" />
        <div className="resume-chat-actions">
          <button className="btn btn-primary" onClick={optimize} disabled={running}>
            <Sparkles size={16} /> {running ? 'Optimizing…' : 'Optimize'}
          </button>
          {content && (
            <button className="btn" onClick={saveContent} disabled={saving}>
              <Save size={16} /> {saving ? 'Saving…' : 'Save edits'}
            </button>
          )}
        </div>
        {error && <div className="auth-error" style={{ marginTop: '0.75rem' }}>{error}</div>}
        {(running || status.progress > 0) && (
          <div className="resume-progress-wrap">
            <div className="resume-progress-bar"><div className="resume-progress-fill" style={{ width: `${status.progress}%` }} /></div>
            <span className="resume-progress-label">{status.stage || 'Working'} — {status.progress}%</span>
          </div>
        )}
      </section>

      {/* Split pane: existing resume (left) | optimized result (right) */}
      <div className="resume-split">
        {/* LEFT — existing resume */}
        <div className="resume-pane">
          <div className="resume-pane-header">
            <span>Existing Resume {fileName ? `· ${fileName}` : ''}</span>
            <div className="resume-pane-tools">
              <button className={`btn btn-sm ${origMode === 'preview' ? 'active' : ''}`} onClick={() => setOrigMode('preview')} disabled={!fileDataUrl}><Eye size={13} /> Preview</button>
              <button className={`btn btn-sm ${origMode === 'edit' ? 'active' : ''}`} onClick={() => { if (!originalText && fileDataUrl) loadResult(); setOrigMode('edit'); }}><Pencil size={13} /> Edit</button>
              <button className="btn btn-sm" onClick={() => fileInputRef.current?.click()}><Upload size={13} /> {fileName ? 'Replace' : 'Upload'}</button>
              <input ref={fileInputRef} type="file" accept=".docx" hidden onChange={(e) => onFile(e.target.files?.[0])} />
            </div>
          </div>
          {origMode === 'edit' ? (
            <div className="resume-edit-host">
              <textarea className="input-text resume-original-edit" value={originalText}
                onChange={(e) => setOriginalText(e.target.value)}
                placeholder="Your existing resume text — edit freely, then click Optimize to re-tailor it." />
            </div>
          ) : (
            <div className="resume-drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); onFile(e.dataTransfer.files?.[0]); }}>
              {!fileDataUrl && (
                <div className="resume-drop-hint"><Upload size={28} /><p>Drag & drop your Word resume here, or click Upload.</p></div>
              )}
              <div ref={leftRef} className="docx-host" />
            </div>
          )}
        </div>

        {/* RIGHT — optimized result */}
        <div className="resume-pane">
          <div className="resume-pane-header">
            <span>Optimized Result {newCount > 0 && <span className="diff-count">+{newCount} new</span>}</span>
            <div className="resume-pane-tools">
              <button className={`btn btn-sm ${optMode === 'edit' ? 'active' : ''}`} onClick={() => setOptMode('edit')} disabled={!content}><Pencil size={13} /> Edit</button>
              <button className={`btn btn-sm ${optMode === 'preview' ? 'active' : ''}`} onClick={() => setOptMode('preview')} disabled={!status.has_result}><Eye size={13} /> Preview</button>
              <button className="btn btn-sm btn-primary" onClick={downloadDocx} disabled={!status.has_result}><Download size={13} /> Download</button>
            </div>
          </div>

          {optMode === 'preview' ? (
            <div className="resume-drop result"><div ref={rightRef} className="docx-host" /></div>
          ) : (
            <div className="resume-edit-host">
              {!content && !running && (
                <div className="resume-drop-hint static"><Sparkles size={28} /><p>Your optimized resume will appear here. Green = newly added to match the role.</p></div>
              )}
              {running && (
                <div className="resume-drop-hint static"><Sparkles size={28} className="spin" /><p>{status.stage || 'Generating…'}</p></div>
              )}
              {content && (
                <div className="resume-editor">
                  <label className="control-label">Professional Summary</label>
                  <textarea className="input-text" rows={4} value={content.summary} onChange={(e) => setSummary(e.target.value)} />
                  {content.sections.map((sec, si) => (
                    <div key={si} className="resume-section-block">
                      <div className="resume-section-head">
                        <input className="input-text resume-section-title" value={sec.title} onChange={(e) => setSectionTitle(si, e.target.value)} />
                        <button className="btn btn-sm icon-danger" title="Remove section" onClick={() => removeSection(si)}><Trash2 size={13} /></button>
                      </div>
                      {sec.items.map((it, ii) => (
                        <div key={ii} className={`resume-item ${it.is_new ? 'is-new' : ''}`}>
                          {it.is_new && <span className="new-badge">NEW</span>}
                          <textarea className="input-text resume-item-text" rows={2} value={it.text} onChange={(e) => setItemText(si, ii, e.target.value)} />
                          <button className="btn btn-sm icon-danger" title="Remove point" onClick={() => removeItem(si, ii)}><Trash2 size={13} /></button>
                        </div>
                      ))}
                      <button className="btn btn-sm" onClick={() => addItem(si)}><Plus size={13} /> Add point</button>
                    </div>
                  ))}
                  <button className="btn btn-sm" style={{ marginTop: '0.75rem' }} onClick={addSection}><Plus size={13} /> Add section</button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
