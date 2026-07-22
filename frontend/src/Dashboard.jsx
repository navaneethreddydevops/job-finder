import { useState, useEffect, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
  Briefcase,
  Search,
  RefreshCw,
  Terminal,
  MapPin,
  Calendar,
  Link as LinkIcon,
  Mail,
  Phone,
  X,
  Sparkles,
  Layers,
  CheckCircle2,
  Cpu,
  FileText,
  ChevronDown,
  ChevronUp,
  Copy,
  Clock,
  BarChart3,
  Check,
  LayoutGrid,
  List,
  Globe,
  MousePointerClick,
  Type,
  Upload,
  Send,
  ChevronsRight,
  Loader2,
  ListChecks,
  MessageSquare,
  Keyboard,
} from 'lucide-react';
import { Bot } from 'lucide-react';
import { Link as RouterLink } from 'react-router-dom';
import UserMenu from './components/UserMenu.jsx';
import { useToast } from './components/Toast.jsx';
import ApplicationStatus from './components/ApplicationStatus.jsx';
import ExportButton from './components/ExportButton.jsx';
import { apiFetch, apiUrl } from './auth';
import { useDarkMode } from './hooks/useDarkMode';
import { Heart } from 'lucide-react';

// ── helpers ──────────────────────────────────────────────────────────────────

// Max agent-console lines kept in state. Mirrors the backend's LOG_HISTORY_MAX
// (backend/main.py) so the live view and a post-refresh replay show the same tail.
const LOG_LINES_MAX = 1500;

// Claude models the user can pick for the job-finder ORCHESTRATOR run. Mirrors
// ALLOWED_MODELS in backend/agent.py — keep the two lists in sync. The job_scout
// subagent and the resume optimizer are unaffected by this selection.
const CLAUDE_MODELS = [
  { id: 'claude-fable-5', label: 'Fable 5', hint: 'Most capable' },
  { id: 'claude-opus-4-8', label: 'Opus 4.8', hint: 'Powerful' },
  { id: 'claude-sonnet-5', label: 'Sonnet 5', hint: 'Balanced' },
  { id: 'claude-haiku-4-5', label: 'Haiku 4.5', hint: 'Fastest' },
];
const DEFAULT_MODEL = 'claude-sonnet-5';

function timeAgo(isoStr) {
  if (!isoStr) return null;
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
  if (diff < 10) return 'just now';
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return null;
}

function useCountUp(target, duration = 500) {
  const [val, setVal] = useState(0);
  const prevRef = useRef(0);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setVal(target); prevRef.current = target; return;
    }
    const from = prevRef.current;
    const diff = target - from;
    if (diff === 0) return;
    const start = performance.now();
    const raf = (now) => {
      const p = Math.min((now - start) / duration, 1);
      setVal(Math.round(from + diff * p));
      if (p < 1) requestAnimationFrame(raf);
      else prevRef.current = target;
    };
    requestAnimationFrame(raf);
  }, [target, duration]);
  return val;
}

function AnimatedNumber({ value }) {
  const display = useCountUp(value);
  return <>{display}</>;
}

// ── component ─────────────────────────────────────────────────────────────────

function Dashboard() {
  const { addToast } = useToast();
  // Initialize dark mode (just calling to set up the theme)
  useDarkMode();

  const [jobs, setJobs] = useState([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [status, setStatus] = useState({ status: 'idle', query: null });
  const [query, setQuery] = useState('Senior Data Engineer');
  const [logs, setLogs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [activeAgentTool, setActiveAgentTool] = useState(null);
  const [healthStatus, setHealthStatus] = useState('unknown');
  const [appStats, setAppStats] = useState({
    total_applications: 0,
    applied_count: 0,
    interviewing_count: 0,
    offer_count: 0,
    rejected_count: 0,
  });

  // Search customization state
  const [jobTypes, setJobTypes] = useState(new Set(['fulltime', 'remote']));
  const [timePeriodDays, setTimePeriodDays] = useState(7);
  const [model, setModel] = useState(() => {
    const stored = localStorage.getItem('jf_model');
    return CLAUDE_MODELS.some((m) => m.id === stored) ? stored : DEFAULT_MODEL;
  });

  // filters
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedSource, setSelectedSource] = useState('All');
  const [selectedLocation, setSelectedLocation] = useState('All');
  const [selectedApplied, setSelectedApplied] = useState('All');

  // UI state
  const [viewMode, setViewMode] = useState(
    () => localStorage.getItem('jf_view_mode') || 'grid'
  );
  const [currentPage, setCurrentPage] = useState(1);
  const [filterOpen, setFilterOpen] = useState(false);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [agentStartTime, setAgentStartTime] = useState(null);
  const [elapsed, setElapsed] = useState('');
  const [onboardingDismissed, setOnboardingDismissed] = useState(
    () => localStorage.getItem('jf_onboarded') === '1'
  );
  const [showBookmarksOnly, setShowBookmarksOnly] = useState(false);
  const [bookmarkCount, setBookmarkCount] = useState(0);

  // ── Autonomous apply agent (Task 10) ────────────────────────────────────────
  // Per-job apply state: { status, error, applicationId, lines }. Hydrated from
  // /api/applications on mount, then driven by 1.5 s polling while a run is live.
  const [applyStates, setApplyStates] = useState({});
  // Missing-profile-fields list when the backend rejects an auto-apply (gating modal).
  const [gatingFields, setGatingFields] = useState(null);
  // Object URL of the needs_review/confirmation screenshot for the open details dialog.
  const [applyShotUrl, setApplyShotUrl] = useState(null);
  // Live milestone screenshot of an in-flight run (refreshed off the status poll).
  const [liveShotUrl, setLiveShotUrl] = useState(null);
  // True while the CDP MJPEG stream is actively painting frames (remote sidecar only).
  const [liveStreamOn, setLiveStreamOn] = useState(false);
  const liveCanvasRef = useRef(null);
  // Controlled value for the human-in-the-loop input box (verification codes etc).
  const [applyInputValue, setApplyInputValue] = useState('');
  const applyPollersRef = useRef({});
  const applyPrevStatusRef = useRef({});
  // Keeps the live step timeline pinned to the newest step.
  const stepListRef = useRef(null);

  const JOBS_PER_PAGE = 12;

  const consoleEndRef = useRef(null);
  const eventSourceRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  // Last SSE event id we received, so a reconnect resumes from there (the backend
  // replays only newer lines — no duplicated console output, no reset flash).
  const lastEventIdRef = useRef('');
  const dialogRef = useRef(null);
  const lastFocusRef = useRef(null);
  const stateRef = useRef({});

  stateRef.current = {
    jobs, filteredJobs: [],
    query, status, logs, searchTerm,
    selectedLocation, selectedSource, selectedApplied, selectedJob, model,
  };

  // ── freshness filter ────────────────────────────────────────────────────────
  // The scouts already constrain every search to the selected window at the source
  // (LinkedIn f_TPR, Workday "last week", etc.), so a job the agent returned is
  // in-window by construction. This filter is only a safety net: KEEP by default and
  // drop a job ONLY when its free-text date_posted positively proves it's older than
  // the selected window. Do NOT drop jobs whose date is missing/unparseable — that
  // silently hid every job when the backend flag was absent (posted_within_7d=0,
  // date_posted=null). Window respects the user's timePeriodDays, not a hardcoded 7.
  const isWithinWindow = (job) => {
    if (job.posted_within_7d) return true;
    const d = (job.date_posted || '').toLowerCase();
    if (!d) return true; // no date info — trust the agent's server-side window filter
    if (/(just|now|moment|today|yesterday|hour|minute|second)/.test(d)) return true;
    const dayMatch = d.match(/(\d+)\s*day/);
    if (dayMatch) return parseInt(dayMatch[1], 10) <= timePeriodDays;
    const weekMatch = d.match(/(\d+)\s*week/);
    if (weekMatch) return parseInt(weekMatch[1], 10) * 7 <= timePeriodDays;
    const monthMatch = d.match(/(\d+)\s*month/);
    if (monthMatch) return parseInt(monthMatch[1], 10) * 30 <= timePeriodDays;
    return true; // unrecognized format — keep rather than silently drop
  };

  // ── data fetching ───────────────────────────────────────────────────────────
  const fetchJobs = async (showToast = false) => {
    setJobsLoading(true);
    try {
      const resp = await apiFetch('/api/jobs');
      if (resp.ok) {
        const data = await resp.json();
        const fresh = (data.jobs || []).filter(isWithinWindow);
        setJobs(fresh);
        if (fresh.length > 0) setFilterOpen(true);
        if (showToast) addToast('Database synced', 'success');
      }
    } catch (err) {
      console.error('Failed to fetch jobs:', err);
    } finally {
      setJobsLoading(false);
    }
  };

  const fetchAppStats = async () => {
    try {
      const resp = await apiFetch('/api/applications/stats');
      if (resp.ok) {
        const stats = await resp.json();
        setAppStats(stats);
      }
    } catch (err) {
      console.error('Failed to fetch application stats:', err);
    }
  };

  const fetchBookmarkCount = async () => {
    try {
      const resp = await apiFetch('/api/bookmarks/count');
      if (resp.ok) {
        const data = await resp.json();
        setBookmarkCount(data.count);
      }
    } catch (err) {
      console.error('Failed to fetch bookmark count:', err);
    }
  };

  const handleToggleBookmark = async (jobId, isCurrentlyBookmarked) => {
    try {
      const resp = await apiFetch(`/api/jobs/${jobId}/bookmark`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (resp.ok) {
        const data = await resp.json();
        setJobs(prev =>
          prev.map(j =>
            j.id === jobId ? { ...j, is_bookmarked: data.is_bookmarked } : j
          )
        );
        if (selectedJob?.id === jobId) {
          setSelectedJob(prev => ({ ...prev, is_bookmarked: data.is_bookmarked }));
        }
        fetchBookmarkCount();
        addToast(data.message, 'success');
      }
    } catch (err) {
      addToast('Failed to toggle bookmark', 'error');
      console.error('Error toggling bookmark:', err);
    }
  };

  const handleToggleApplied = async (jobId, currentApplied) => {
    try {
      const newApplied = !currentApplied;
      const resp = await apiFetch(`/api/jobs/${jobId}/apply`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ applied: newApplied }),
      });
      if (resp.ok) {
        setJobs(prev => prev.map(j => j.id === jobId ? { ...j, applied: newApplied } : j));
        if (stateRef.current.selectedJob?.id === jobId) {
          setSelectedJob(prev => ({ ...prev, applied: newApplied }));
        }
        addToast(newApplied ? 'Marked as applied' : 'Removed applied status', 'success');
      } else {
        addToast('Failed to update status', 'error');
      }
    } catch (err) {
      addToast('Failed to update status', 'error');
      console.error('Error toggling applied status:', err);
    }
  };

  // ── apply agent helpers ─────────────────────────────────────────────────────
  const APPLY_ACTIVE = new Set(['queued', 'running', 'awaiting_input']);

  const setApplyState = (jobId, patch) => {
    setApplyStates(prev => ({ ...prev, [jobId]: { ...prev[jobId], ...patch } }));
  };

  const stopApplyPolling = (jobId) => {
    if (applyPollersRef.current[jobId]) {
      clearInterval(applyPollersRef.current[jobId]);
      delete applyPollersRef.current[jobId];
    }
  };

  const startApplyPolling = (jobId) => {
    stopApplyPolling(jobId);
    applyPollersRef.current[jobId] = setInterval(async () => {
      try {
        const resp = await apiFetch(`/api/jobs/${jobId}/apply-agent/status`);
        if (!resp.ok) return;
        const data = await resp.json();
        setApplyState(jobId, {
          status: data.apply_status,
          error: data.apply_error,
          inputPrompt: data.apply_input_prompt || '',
          applicationId: data.application_id,
          lines: data.progress_lines || [],
          steps: data.steps || [],
          milestone: data.milestone || 0,
          hasLiveShot: !!data.has_live_screenshot,
        });
        if (data.apply_status === 'awaiting_input' && applyPrevStatusRef.current[jobId] !== 'awaiting_input') {
          addToast('The apply agent needs an answer to keep filling the form — open the job to respond', 'error');
        }
        applyPrevStatusRef.current[jobId] = data.apply_status;
        if (data.apply_status && !APPLY_ACTIVE.has(data.apply_status)) {
          stopApplyPolling(jobId);
          fetchJobs();
          fetchAppStats();
          if (data.apply_status === 'submitted') {
            addToast('Application submitted by the agent 🎉', 'success');
          } else if (data.apply_status === 'needs_review') {
            addToast(`Apply agent needs your review: ${data.apply_error}`, 'error');
          } else {
            addToast(`Apply agent failed: ${data.apply_error}`, 'error');
          }
        }
      } catch (err) { console.error('Apply status poll failed:', err); }
    }, 1500);
  };

  const handleAutoApply = async (jobId) => {
    try {
      const resp = await apiFetch(`/api/jobs/${jobId}/apply-agent`, { method: 'POST' });
      const data = await resp.json();
      if (resp.ok) {
        setApplyState(jobId, { status: 'queued', error: '', applicationId: data.application_id, lines: [], steps: [], milestone: 0 });
        addToast('Apply agent started — filling out the application…', 'success');
        startApplyPolling(jobId);
      } else if (resp.status === 409 && data.detail?.missing_fields) {
        setGatingFields(data.detail.missing_fields);
      } else {
        const msg = data.detail?.message || data.detail || 'Failed to start the apply agent.';
        addToast(typeof msg === 'string' ? msg : 'Failed to start the apply agent.', 'error');
      }
    } catch (err) {
      addToast('Network error starting the apply agent', 'error');
      console.error('Auto-apply failed:', err);
    }
  };

  // Hydrate per-job apply chips from stored application rows, and resume polling
  // for any run that was live before a refresh.
  const hydrateApplyStates = async () => {
    try {
      const resp = await apiFetch('/api/applications');
      if (!resp.ok) return;
      const apps = await resp.json();
      const next = {};
      for (const app of apps) {
        if (app.apply_status) {
          next[app.job_id] = {
            status: app.apply_status,
            error: app.apply_error || '',
            applicationId: app.id,
            lines: [],
            steps: [],
            milestone: 0,
          };
          if (APPLY_ACTIVE.has(app.apply_status)) startApplyPolling(app.job_id);
        }
      }
      setApplyStates(next);
    } catch (err) { console.error('Failed to hydrate apply states:', err); }
  };

  const APPLY_CHIP = {
    queued: { label: 'Agent queued…', cls: 'apply-chip-active' },
    running: { label: 'Agent applying…', cls: 'apply-chip-active' },
    awaiting_input: { label: 'Needs your input', cls: 'apply-chip-warn' },
    submitted: { label: 'Auto-applied', cls: 'apply-chip-success' },
    needs_review: { label: 'Needs review', cls: 'apply-chip-warn' },
    failed: { label: 'Apply failed', cls: 'apply-chip-error' },
  };

  // The 5-node milestone stepper mirrors the backend progress-NN milestones.
  const APPLY_MILESTONES = ['Open form', 'Fill details', 'Upload resume', 'Ready to submit', 'Confirmation'];

  // Icon per structured-step kind (from the backend `steps[]`).
  const stepIcon = (kind) => {
    const p = { size: 13 };
    switch (kind) {
      case 'navigate': return <Globe {...p} />;
      case 'click': return <MousePointerClick {...p} />;
      case 'fill': return <Type {...p} />;
      case 'select': return <ListChecks {...p} />;
      case 'upload': return <Upload {...p} />;
      case 'select_key':
      case 'key': return <Keyboard {...p} />;
      case 'wait': return <Loader2 {...p} />;
      case 'thought': return <MessageSquare {...p} />;
      case 'input': return <Keyboard {...p} />;
      case 'milestone': return <Check {...p} />;
      case 'start': return <ChevronsRight {...p} />;
      case 'done': return <Send {...p} />;
      case 'stopped': return <X {...p} />;
      default: return <Sparkles {...p} />;
    }
  };

  // Most-recent page URL the agent navigated to (for the browser-frame URL bar).
  const applyCurrentUrl = (st, fallback) => {
    const steps = st?.steps || [];
    for (let i = steps.length - 1; i >= 0; i--) if (steps[i].url) return steps[i].url;
    return fallback || '';
  };
  const hostOf = (url) => { try { return new URL(url).hostname; } catch { return url || ''; } };

  const submitApplyInput = async (jobId) => {
    const value = applyInputValue.trim();
    if (!value) return;
    try {
      const resp = await apiFetch(`/api/jobs/${jobId}/apply-agent/input`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value }),
      });
      if (resp.ok) {
        setApplyInputValue('');
        addToast('Sent — the agent is resuming', 'success');
      } else {
        const data = await resp.json();
        addToast(data.detail || 'The agent is not waiting for input right now', 'error');
      }
    } catch {
      addToast('Network error sending input', 'error');
    }
  };

  const fetchStatus = async () => {
    try {
      const resp = await fetch(apiUrl('/api/status'));
      if (resp.ok) setStatus(await resp.json());
    } catch (err) {
      console.error('Failed to fetch status:', err);
    }
  };

  // If the SSE connection drops mid-run (proxy timeout, network blip), retry after a
  // short delay instead of leaving the console dead. The backend replays the whole
  // run's log buffer on reconnect, so the console is reset first to avoid duplicates.
  const scheduleReconnect = () => {
    if (reconnectTimerRef.current) return; // a retry is already pending
    reconnectTimerRef.current = setTimeout(async () => {
      reconnectTimerRef.current = null;
      try {
        const resp = await fetch(apiUrl('/api/status'));
        if (resp.ok) {
          const data = await resp.json();
          setStatus(data);
          if (data.status === 'running') {
            // Resume from the last id we saw (startStreaming reads lastEventIdRef),
            // so the backend replays only new lines — the console is NOT cleared.
            startStreaming();
          }
          // idle: the run finished while we were disconnected — the status-polling
          // effect notices and does the final fetchJobs(); nothing to reconnect to.
          return;
        }
      } catch { /* backend unreachable — fall through and retry */ }
      scheduleReconnect();
    }, 2000);
  };

  const startStreaming = () => {
    if (eventSourceRef.current) eventSourceRef.current.close();
    // Resume from the last line we saw so a reconnect (platform request-duration cap,
    // proxy blip, refresh) replays only newer lines instead of re-dumping the buffer.
    // Native EventSource reconnects send this via the Last-Event-ID header; for our
    // first/manual open we pass it as a query param (EventSource can't set headers).
    const resume = lastEventIdRef.current;
    const url = apiUrl('/api/stream') + (resume ? `?last_event_id=${encodeURIComponent(resume)}` : '');
    const es = new EventSource(url);
    eventSourceRef.current = es;
    es.onmessage = (event) => {
      if (event.lastEventId) lastEventIdRef.current = event.lastEventId;
      try {
        const data = JSON.parse(event.data);
        // Cap the console scrollback to match the backend's LOG_HISTORY_MAX replay
        // buffer, so a long agent run can't grow browser memory/DOM without bound.
        setLogs(prev => (prev.length >= LOG_LINES_MAX
          ? [...prev.slice(prev.length - LOG_LINES_MAX + 1), data.message]
          : [...prev, data.message]));
        if (typeof data.message === 'string' && data.message.includes('Database now holds')) {
          fetchJobs();
        }
      } catch (err) { console.error('Failed to parse log message:', err); }
    };
    es.onerror = () => {
      // A transient drop leaves readyState CONNECTING — let the browser's native
      // reconnect resume it (it re-sends Last-Event-ID, so no flash). Only when the
      // browser gives up (CLOSED — e.g. a fatal/CORS error) do we retry manually.
      if (es.readyState === EventSource.CLOSED) {
        console.log('SSE permanently closed — scheduling manual reconnect');
        scheduleReconnect();
      }
    };
  };

  const handlePullJobs = async (e) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget || e.target);
    const queryValue = formData.get('query')?.toString() || query;
    if (!queryValue.trim()) return;
    setQuery(queryValue);

    const runPromise = (async () => {
      try {
        setLogs([]);
        const resp = await apiFetch('/api/pull', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query: queryValue,
            job_types: Array.from(jobTypes),
            time_period_days: timePeriodDays,
            model,
          }),
        });
        if (resp.ok) {
          // Merge (don't replace): keep server-provided keys like
          // apply_agent_available so the Auto-Apply buttons don't vanish mid-run.
          setStatus(prev => ({ ...prev, status: 'running', query: queryValue }));
          setAgentStartTime(performance.now());
          setConsoleOpen(true);
          startStreaming();
          return `Successfully started backend job pull for query: "${queryValue}"`;
        } else {
          const errorData = await resp.json();
          const errText = errorData.detail || 'Failed to trigger job pull.';
          if (!e.agentInvoked) addToast(errText, 'error');
          return `Error triggering agent: ${errText}`;
        }
      } catch (err) {
        addToast('Network error', 'error');
        return `Network error: ${err.message}`;
      }
    })();

    if (e.agentInvoked && typeof e.respondWith === 'function') e.respondWith(runPromise);
  };

  const handleFilterFormSubmit = (e) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget || e.target);
    const term = formData.get('searchTerm')?.toString() || '';
    setSearchTerm(term);
    if (e.agentInvoked && typeof e.respondWith === 'function') {
      e.respondWith(Promise.resolve(`Applied search query: "${term}"`));
    }
  };

  // ── effects ─────────────────────────────────────────────────────────────────

  useEffect(() => {
    fetchJobs();
    fetchAppStats();
    fetchBookmarkCount();
    hydrateApplyStates();

    // Check if an agent is already running (e.g. from a page refresh)
    const checkAgentStatus = async () => {
      try {
        const resp = await fetch(apiUrl('/api/status'));
        if (resp.ok) {
          const data = await resp.json();
          setStatus(data);
          if (data.status === 'running' && data.query) {
            // Agent is still running (e.g. the user refreshed mid-run). Reconnect to the
            // stream; the backend replays this run's buffered log lines, so the console
            // repopulates via the normal onmessage handler. `logs` stays at its []
            // default and fills with strings — do NOT seed it with a placeholder object,
            // which would break formatLog()/copyLog() (they expect strings).
            setQuery(data.query);
            setConsoleOpen(true);
            setAgentStartTime(performance.now());
            startStreaming();
          }
        }
      } catch (err) {
        console.error('Failed to check agent status on mount:', err);
        fetchStatus();
      }
    };
    checkAgentStatus();

    return () => {
      if (eventSourceRef.current) eventSourceRef.current.close();
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      Object.values(applyPollersRef.current).forEach(clearInterval);
      applyPollersRef.current = {};
    };
  }, []);

  // Load the apply screenshot for the open details dialog (auth-protected, so it's
  // fetched via apiFetch into an object URL rather than a plain <img src>).
  useEffect(() => {
    let revoked = false;
    let url = null;
    setApplyShotUrl(null);
    const state = selectedJob ? applyStates[selectedJob.id] : null;
    if (state?.applicationId && ['needs_review', 'submitted', 'failed'].includes(state.status)) {
      (async () => {
        try {
          const resp = await apiFetch(`/api/applications/${state.applicationId}/screenshot`);
          if (!resp.ok) return;
          const blob = await resp.blob();
          if (revoked) return;
          url = URL.createObjectURL(blob);
          setApplyShotUrl(url);
        } catch { /* no screenshot — panel simply omits it */ }
      })();
    }
    return () => { revoked = true; if (url) URL.revokeObjectURL(url); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedJob, applyStates[selectedJob?.id]?.status]);

  // Live milestone screenshot while the open job's run is in flight: refresh every
  // 3 s (auth-protected, so fetched via apiFetch into an object URL).
  useEffect(() => {
    const jobId = selectedJob?.id;
    const state = jobId ? applyStates[jobId] : null;
    const live = state && APPLY_ACTIVE.has(state.status);
    if (!live) { setLiveShotUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return null; }); return; }
    let stopped = false;
    const tick = async () => {
      try {
        const resp = await apiFetch(`/api/jobs/${jobId}/apply-agent/live-screenshot`);
        if (!resp.ok || stopped) return;
        const blob = await resp.blob();
        if (stopped) return;
        const url = URL.createObjectURL(blob);
        setLiveShotUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return url; });
      } catch { /* no screenshot yet */ }
    };
    tick();
    const interval = setInterval(tick, 3000);
    return () => { stopped = true; clearInterval(interval); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedJob?.id, applyStates[selectedJob?.id]?.status]);

  // True live browser stream (remote sidecar CDP screencast) → MJPEG frames drawn
  // to a canvas. Kept auth in the header via apiFetch (no token in URL). 404 (local
  // mode / no run) leaves liveStreamOn=false so the screenshot view takes over.
  // Reconnects on stream end while the run is active — this absorbs the managed
  // host's request-duration cap (the stream is cut periodically), like the SSE log.
  useEffect(() => {
    const jobId = selectedJob?.id;
    const state = jobId ? applyStates[jobId] : null;
    const live = state && APPLY_ACTIVE.has(state.status);
    if (!live) { setLiveStreamOn(false); return; }

    let stopped = false;
    let controller = null;

    const indexOfCRLFCRLF = (buf, from) => {
      for (let i = from; i + 3 < buf.length; i++) {
        if (buf[i] === 13 && buf[i + 1] === 10 && buf[i + 2] === 13 && buf[i + 3] === 10) return i;
      }
      return -1;
    };
    const draw = async (jpeg) => {
      const canvas = liveCanvasRef.current;
      if (!canvas) return;
      try {
        const bitmap = await createImageBitmap(new Blob([jpeg], { type: 'image/jpeg' }));
        if (stopped) { bitmap.close(); return; }
        if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
          canvas.width = bitmap.width; canvas.height = bitmap.height;
        }
        canvas.getContext('2d').drawImage(bitmap, 0, 0);
        bitmap.close();
        setLiveStreamOn(true);
      } catch { /* skip a bad frame */ }
    };

    const runOnce = async () => {
      controller = new AbortController();
      const resp = await apiFetch(`/api/jobs/${jobId}/apply-agent/live-stream`, { signal: controller.signal });
      if (!resp.ok || !resp.body) { const e = new Error('no-stream'); e.noStream = resp.status === 404; throw e; }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('latin1');
      let buf = new Uint8Array(0);
      while (!stopped) {
        const { done, value } = await reader.read();
        if (done) break;
        const merged = new Uint8Array(buf.length + value.length);
        merged.set(buf); merged.set(value, buf.length); buf = merged;
        // Extract as many complete frames as the buffer holds.
        while (true) {
          const headerEnd = indexOfCRLFCRLF(buf, 0);
          if (headerEnd === -1) break;
          const header = decoder.decode(buf.slice(0, headerEnd));
          const m = header.match(/Content-Length:\s*(\d+)/i);
          if (!m) { buf = buf.slice(headerEnd + 4); continue; }
          const len = parseInt(m[1], 10);
          const start = headerEnd + 4;
          if (buf.length < start + len) break; // wait for the rest of this frame
          await draw(buf.slice(start, start + len));
          buf = buf.slice(start + len);
        }
      }
    };

    (async () => {
      while (!stopped) {
        try {
          await runOnce();
        } catch (err) {
          if (stopped || err?.name === 'AbortError') return;
          if (err?.noStream) { setLiveStreamOn(false); return; } // local mode / no stream — use screenshots
        }
        if (stopped) return;
        setLiveStreamOn(false);
        await new Promise((r) => setTimeout(r, 1500)); // brief backoff, then reconnect
      }
    })();

    return () => { stopped = true; setLiveStreamOn(false); try { controller?.abort(); } catch {} };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedJob?.id, applyStates[selectedJob?.id]?.status]);

  // Auto-scroll the step timeline to the newest step as it grows.
  useEffect(() => {
    const el = stepListRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedJob?.id, applyStates[selectedJob?.id]?.steps?.length]);

  // health check
  useEffect(() => {
    const check = async () => {
      try {
        const resp = await fetch(apiUrl('/api/health'));
        const data = await resp.json().catch(() => null);
        setHealthStatus(resp.ok && data?.status === 'operational' ? 'ok' : 'error');
      } catch { setHealthStatus('error'); }
    };
    check();
    const interval = setInterval(check, 5000);
    return () => clearInterval(interval);
  }, []);

  // persist the jobs view mode (grid / list)
  useEffect(() => {
    localStorage.setItem('jf_view_mode', viewMode);
  }, [viewMode]);

  // persist the selected orchestrator model
  useEffect(() => {
    localStorage.setItem('jf_model', model);
  }, [model]);

  // status polling while running
  useEffect(() => {
    let interval;
    if (status.status === 'running') {
      if (!eventSourceRef.current || eventSourceRef.current.readyState === EventSource.CLOSED) {
        startStreaming();
      }
      interval = setInterval(async () => {
        try {
          const resp = await fetch(apiUrl('/api/status'));
          if (resp.ok) {
            const data = await resp.json();
            setStatus(data);
            if (data.status === 'idle') {
              clearInterval(interval);
              fetchJobs();
              if (eventSourceRef.current) eventSourceRef.current.close();
            } else if (
              !eventSourceRef.current ||
              eventSourceRef.current.readyState === EventSource.CLOSED
            ) {
              // Backstop: still running but the stream is down — reconnect (de-duped
              // against onerror's own retry by the shared timer ref).
              scheduleReconnect();
            }
          }
        } catch (err) { console.error('Failed polling status:', err); }
      }, 3000);
    }
    return () => clearInterval(interval);
  }, [status.status]);

  // auto-scroll console
  useEffect(() => {
    if (consoleOpen && consoleEndRef.current) {
      consoleEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, consoleOpen]);

  // elapsed timer
  useEffect(() => {
    if (status.status !== 'running') { setElapsed(''); return; }
    const interval = setInterval(() => {
      if (!agentStartTime) return;
      const secs = Math.floor((performance.now() - agentStartTime) / 1000);
      const mm = String(Math.floor(secs / 60)).padStart(2, '0');
      const ss = String(secs % 60).padStart(2, '0');
      setElapsed(`${mm}:${ss}`);
    }, 1000);
    return () => clearInterval(interval);
  }, [status.status, agentStartTime]);

  // '/' keyboard shortcut focuses search
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(e.target.tagName)) {
        e.preventDefault();
        document.getElementById('local-search-input')?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // dialog open/close + focus return
  useEffect(() => {
    if (selectedJob) {
      dialogRef.current?.showModal();
    } else {
      dialogRef.current?.close();
      setTimeout(() => lastFocusRef.current?.focus(), 50);
    }
  }, [selectedJob]);

  // ── derived values ──────────────────────────────────────────────────────────

  const sources = useMemo(() => {
    const list = new Set();
    jobs.forEach(j => { if (j.source) list.add(j.source); });
    return ['All', ...Array.from(list)];
  }, [jobs]);

  const filteredJobs = useMemo(() => {
    return jobs.filter(job => {
      if (showBookmarksOnly && !job.is_bookmarked) return false;
      const sl = searchTerm.toLowerCase();
      const matchesSearch = !sl ||
        job.title.toLowerCase().includes(sl) ||
        job.company.toLowerCase().includes(sl) ||
        job.description.toLowerCase().includes(sl) ||
        job.key_requirements.some(r => r.toLowerCase().includes(sl));
      const matchesSource = selectedSource === 'All' || job.source === selectedSource;
      const isRemote = job.location.toLowerCase().includes('remote');
      const matchesLocation = selectedLocation === 'All' ||
        (selectedLocation === 'Remote' ? isRemote : !isRemote);
      const matchesApplied = selectedApplied === 'All' ||
        (selectedApplied === 'Applied' ? job.applied : !job.applied);
      return matchesSearch && matchesSource && matchesLocation && matchesApplied;
    });
  }, [jobs, searchTerm, selectedSource, selectedLocation, selectedApplied, showBookmarksOnly]);

  useEffect(() => { setCurrentPage(1); },
    [searchTerm, selectedSource, selectedLocation, selectedApplied]);

  stateRef.current.filteredJobs = filteredJobs;

  const totalPages = Math.ceil(filteredJobs.length / JOBS_PER_PAGE);
  const paginatedJobs = filteredJobs.slice((currentPage - 1) * JOBS_PER_PAGE, currentPage * JOBS_PER_PAGE);

  const stats = useMemo(() => ({
    total: jobs.length,
    remote: jobs.filter(j => j.location.toLowerCase().includes('remote')).length,
    applied: jobs.filter(j => j.applied).length,
    applications: appStats.total_applications,
    applied_apps: appStats.applied_count,
    interviewing_apps: appStats.interviewing_count,
  }), [jobs, appStats]);

  const lastUpdated = useMemo(() => {
    const dates = jobs.map(j => j.created_at).filter(Boolean).sort();
    return dates.length ? timeAgo(dates[dates.length - 1]) : null;
  }, [jobs]);

  const activeFilterCount = [selectedLocation, selectedSource, selectedApplied]
    .filter(v => v !== 'All').length;

  const clearAllFilters = () => {
    setSelectedLocation('All');
    setSelectedSource('All'); setSelectedApplied('All');
    setSearchTerm('');
  };

  // ── WebMCP tool registration ────────────────────────────────────────────────
  useEffect(() => {
    const modelContext = document.modelContext || navigator.modelContext;
    if (modelContext && typeof modelContext.registerTool === 'function') {
      const controller = new AbortController();
      const signal = controller.signal;
      try {
        modelContext.registerTool({
          name: 'get_jobs_list',
          description: 'Retrieve all jobs matching the current search parameters and filters in the dashboard.',
          inputSchema: { type: 'object', properties: {} },
          execute() {
            const cur = stateRef.current;
            return {
              total_database_count: cur.jobs.length,
              filtered_display_count: cur.filteredJobs.length,
              active_filters: { searchTerm: cur.searchTerm, location: cur.selectedLocation, source: cur.selectedSource, applied: cur.selectedApplied },
              jobs: cur.filteredJobs.map((j, idx) => ({ index: idx, id: j.id, title: j.title, company: j.company, location: j.location, source: j.source, applied: j.applied, key_requirements: j.key_requirements })),
            };
          },
          annotations: { readOnlyHint: true },
        }, { signal });

        modelContext.registerTool({
          name: 'filter_jobs',
          description: 'Apply text search and filter selections in the dashboard viewport.',
          inputSchema: { type: 'object', properties: { searchTerm: { type: 'string' }, location: { type: 'string', enum: ['All', 'Remote', 'Onsite/Hybrid'] }, source: { type: 'string' }, applied: { type: 'string', enum: ['All', 'Applied', 'Not Applied'] } } },
          execute(input) {
            if (input.searchTerm !== undefined) setSearchTerm(input.searchTerm);
            if (input.location !== undefined) setSelectedLocation(input.location);
            if (input.source !== undefined) setSelectedSource(input.source);
            if (input.applied !== undefined) setSelectedApplied(input.applied);
            return { success: true, message: 'Dashboard viewport filters applied successfully' };
          },
        }, { signal });

        modelContext.registerTool({
          name: 'view_job_details',
          description: 'Open the details drawer modal for a job using its list index.',
          inputSchema: { type: 'object', properties: { index: { type: 'integer' } }, required: ['index'] },
          execute(input) {
            const cur = stateRef.current;
            if (input.index >= 0 && input.index < cur.filteredJobs.length) {
              const job = cur.filteredJobs[input.index];
              setSelectedJob(job);
              return { success: true, message: `Opened job details for "${job.title}" at "${job.company}"`, job };
            }
            return { success: false, error: `Invalid index: ${input.index}. Bounds 0–${cur.filteredJobs.length - 1}` };
          },
        }, { signal });

        modelContext.registerTool({
          name: 'trigger_agent_run',
          description: 'Trigger the backend scraping agent to run a live job crawl with the specified query.',
          inputSchema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
          async execute(input) {
            const q = input.query.trim();
            if (!q) return { success: false, error: 'Search query required' };
            setQuery(q); setLogs([]);
            try {
              const resp = await apiFetch('/api/pull', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: q, model: stateRef.current.model }) });
              if (resp.ok) { setStatus(prev => ({ ...prev, status: 'running', query: q })); setAgentStartTime(performance.now()); setConsoleOpen(true); startStreaming(); return { success: true, message: `Scraper initiated for '${q}'` }; }
              const e = await resp.json();
              return { success: false, error: e.detail || 'Scraper call failed' };
            } catch (err) { return { success: false, error: err.message }; }
          },
        }, { signal });

        modelContext.registerTool({
          name: 'toggle_job_applied',
          description: 'Toggle the applied status of a job posting.',
          inputSchema: { type: 'object', properties: { jobId: { type: 'integer' }, applied: { type: 'boolean' } }, required: ['jobId', 'applied'] },
          async execute(input) {
            try {
              const resp = await apiFetch(`/api/jobs/${input.jobId}/apply`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ applied: input.applied }) });
              if (resp.ok) {
                setJobs(prev => prev.map(j => j.id === input.jobId ? { ...j, applied: input.applied } : j));
                if (stateRef.current.selectedJob?.id === input.jobId) setSelectedJob(prev => ({ ...prev, applied: input.applied }));
                return { success: true, message: `Set applied=${input.applied} for job ${input.jobId}` };
              }
              return { success: false, error: 'Failed to update on backend' };
            } catch (err) { return { success: false, error: err.message }; }
          },
        }, { signal });

        modelContext.registerTool({
          name: 'apply_to_job',
          description: 'Start the autonomous apply agent for a job: it opens the posting in a headless browser, fills the application form from the stored user profile, uploads the resume, and submits. Requires a complete profile.',
          inputSchema: { type: 'object', properties: { jobId: { type: 'integer' } }, required: ['jobId'] },
          async execute(input) {
            try {
              const resp = await apiFetch(`/api/jobs/${input.jobId}/apply-agent`, { method: 'POST' });
              const data = await resp.json();
              if (resp.ok) {
                setApplyState(input.jobId, { status: 'queued', error: '', applicationId: data.application_id, lines: [] });
                startApplyPolling(input.jobId);
                return { success: true, message: `Apply agent started for job ${input.jobId} (application ${data.application_id}). Poll the job's chip for progress.` };
              }
              if (resp.status === 409 && data.detail?.missing_fields) {
                return { success: false, error: `Profile incomplete — missing: ${data.detail.missing_fields.join(', ')}` };
              }
              return { success: false, error: data.detail?.message || data.detail || 'Failed to start the apply agent' };
            } catch (err) { return { success: false, error: err.message }; }
          },
        }, { signal });

        modelContext.registerTool({
          name: 'clear_database',
          description: 'Reset and clear the current jobs database list in the UI.',
          inputSchema: { type: 'object', properties: {} },
          async execute() {
            try {
              const resp = await apiFetch('/api/jobs/clear', { method: 'POST' });
              if (resp.ok) { setJobs([]); setSelectedJob(null); return { success: true, message: 'Local jobs database cleared.' }; }
              return { success: false, error: 'Failed to clear on backend' };
            } catch (err) { return { success: false, error: err.message }; }
          },
        }, { signal });
      } catch (err) { console.warn('Failed to register WebMCP tool:', err); }
      return () => controller.abort();
    }
  }, [sources]);

  useEffect(() => {
    const handleActivated = (e) => setActiveAgentTool(e.toolName || e.detail?.toolName || 'WebMCP Tool');
    const handleCancel = () => setActiveAgentTool(null);
    window.addEventListener('toolactivated', handleActivated);
    window.addEventListener('toolcancel', handleCancel);
    return () => { window.removeEventListener('toolactivated', handleActivated); window.removeEventListener('toolcancel', handleCancel); };
  }, []);

  // ── log formatter ───────────────────────────────────────────────────────────
  const formatLog = (logText) => {
    if (logText.startsWith('[Tool Call]')) return <span className="log-tool">{logText}</span>;
    if (logText.startsWith('[Tool Complete]') || logText.startsWith('[Backend]')) return <span className="log-system">{logText}</span>;
    if (logText.startsWith('[Backend Error]') || logText.startsWith('Error:')) return <span className="log-error">{logText}</span>;
    return <span className="log-thought">{logText}</span>;
  };

  // ── copy log ────────────────────────────────────────────────────────────────
  const copyLog = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(logs.join('\n')).then(() => addToast('Log copied to clipboard', 'success'));
  };

  // ── render ──────────────────────────────────────────────────────────────────
  return (
    <div className="app-container" id="app-root-container">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="app-header" id="dashboard-header">
        <div className="logo-section">
          <Briefcase className="logo-icon" size={28} />
          <span className="logo-text">Job Finder</span>
        </div>

        {activeAgentTool && (
          <div className="logo-badge agent-active-badge">
            <Cpu size={12} />
            <span>Agent: {activeAgentTool}</span>
          </div>
        )}

        <div className="header-actions">
          {/* Primary navigation */}
          <Link to="/resume/optimizer" className="btn nav-link" title="Resume Optimizer">
            <FileText size={16} />
            <span className="header-btn-label">Resume Optimizer</span>
          </Link>

          <Link to="/analytics" className="btn nav-link" title="Analytics">
            <BarChart3 size={16} />
            <span className="header-btn-label">Analytics</span>
          </Link>

          {/* Account menu (holds Settings + theme toggle + logout) */}
          <UserMenu />
        </div>
      </header>

      {/* ── Stats Cards ────────────────────────────────────────────────────── */}
      <section className="stats-grid" id="stats-summary-panel">
        <div className="stat-card" id="stat-card-total" title="Total unique remote full-time postings from the last 7 days">
          <div className="stat-icon-wrapper primary"><Layers size={22} /></div>
          <div className="stat-info">
            <span className="stat-label">Total Jobs Found</span>
            <span className="stat-value"><AnimatedNumber value={stats.total} /></span>
          </div>
        </div>
        <div className="stat-card" id="stat-card-fulltime" title="Remote US full-time roles from 12 sources including LinkedIn, Indeed, Glassdoor, and company career pages">
          <div className="stat-icon-wrapper success"><CheckCircle2 size={22} /></div>
          <div className="stat-info">
            <span className="stat-label">Full-Time Roles</span>
            <span className="stat-value"><AnimatedNumber value={stats.total} /></span>
          </div>
        </div>
        <div className="stat-card" id="stat-card-remote" title="Jobs listed as fully remote">
          <div className="stat-icon-wrapper warning"><Sparkles size={22} /></div>
          <div className="stat-info">
            <span className="stat-label">Remote Roles</span>
            <span className="stat-value"><AnimatedNumber value={stats.remote} /></span>
          </div>
        </div>
        <div className="stat-card" id="stat-card-applied" title="Jobs you've marked as applied">
          <div className={`stat-icon-wrapper ${stats.applied > 0 ? 'success' : ''}`}>
            <CheckCircle2 size={22} />
          </div>
          <div className="stat-info">
            <span className="stat-label">Applied Jobs</span>
            <span className={`stat-value ${stats.applied > 0 ? 'stat-value-success' : ''}`}>
              <AnimatedNumber value={stats.applied} />
            </span>
          </div>
        </div>

        <div className="stat-card" id="stat-card-applications" title="Total application records created">
          <div className={`stat-icon-wrapper ${stats.applications > 0 ? 'primary' : ''}`}>
            <FileText size={22} />
          </div>
          <div className="stat-info">
            <span className="stat-label">Applications</span>
            <span className={`stat-value ${stats.applications > 0 ? 'stat-value-primary' : ''}`}>
              <AnimatedNumber value={stats.applications} />
            </span>
          </div>
        </div>

        <div className="stat-card" id="stat-card-interviewing" title="Applications in interviewing stage">
          <div className={`stat-icon-wrapper ${stats.interviewing_apps > 0 ? 'primary' : ''}`}>
            <Cpu size={22} />
          </div>
          <div className="stat-info">
            <span className="stat-label">Interviewing</span>
            <span className={`stat-value ${stats.interviewing_apps > 0 ? 'stat-value-primary' : ''}`}>
              <AnimatedNumber value={stats.interviewing_apps} />
            </span>
          </div>
        </div>
      </section>

      {/* last-updated line */}
      {lastUpdated && (
        <div className="stat-updated">
          <Clock size={12} />
          Updated {lastUpdated}
        </div>
      )}

      {/* ── Main Grid ──────────────────────────────────────────────────────── */}
      <div className="dashboard-grid">

        {/* ── Sidebar ──────────────────────────────────────────────────────── */}
        <aside className="sidebar-panel" id="agent-controls-panel">
          <div className="sidebar-title">
            <Sparkles size={18} className="text-primary" />
            Agent Controls
          </div>

          {/* — Section 1: Run Agent — */}
          <span className="sidebar-section-label">Run Agent</span>
          <form
            id="agent-run-form"
            onSubmit={handlePullJobs}
            className="control-group"
            toolname="trigger_agent_run_form"
            tooldescription="Trigger a backend web scraper agent run to search for full-time job postings matching a specified search query"
            toolautosubmit="true"
          >
            <label htmlFor="agent-query-input" className="control-label">Search Target</label>
            <input
              id="agent-query-input"
              name="query"
              type="text"
              className="input-text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. Data Engineer"
              disabled={status.status === 'running'}
              toolparamdescription="The search query for full-time jobs, for example 'Data Engineer' or 'Senior Python Developer'"
              required
            />

            {/* Job Type Filters */}
            <div className="search-option-group">
              <span className="control-label">Job Type</span>
              <div className="jobtype-toggles">
                {[
                  { key: 'fulltime', label: 'Full-Time' },
                  { key: 'remote', label: 'Remote' },
                  { key: 'contract', label: 'Contract' },
                ].map(({ key, label }) => {
                  const active = jobTypes.has(key);
                  return (
                    <label
                      key={key}
                      className={`jobtype-toggle ${active ? 'active' : ''}`}
                    >
                      <input
                        id={`filter-jobtype-${key}`}
                        type="checkbox"
                        checked={active}
                        onChange={(e) => {
                          const newTypes = new Set(jobTypes);
                          if (e.target.checked) newTypes.add(key);
                          else newTypes.delete(key);
                          setJobTypes(newTypes);
                        }}
                        disabled={status.status === 'running'}
                      />
                      <span className="jobtype-check">
                        {active && <Check size={11} strokeWidth={3} />}
                      </span>
                      {label}
                    </label>
                  );
                })}
              </div>
            </div>

            {/* Time Period Slider */}
            <div className="search-option-group">
              <div className="time-period-header">
                <label htmlFor="time-period-slider" className="control-label">Posted Within</label>
                <span className="time-period-value">
                  {timePeriodDays === 1 ? '24 hrs' : `${timePeriodDays} days`}
                </span>
              </div>
              <input
                id="time-period-slider"
                className="range-slider"
                type="range"
                min="1"
                max="90"
                value={timePeriodDays}
                onChange={(e) => setTimePeriodDays(parseInt(e.target.value, 10))}
                disabled={status.status === 'running'}
              />
              <div className="time-presets">
                {[
                  { days: 1, label: '24 Hours' },
                  { days: 7, label: '1 Week' },
                  { days: 14, label: '2 Weeks' },
                  { days: 30, label: '1 Month' },
                  { days: 90, label: '3 Months' },
                ].map(({ days, label }) => (
                  <button
                    key={days}
                    type="button"
                    className={`time-preset ${timePeriodDays === days ? 'active' : ''}`}
                    onClick={() => setTimePeriodDays(days)}
                    disabled={status.status === 'running'}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Model Selection */}
            <div className="search-option-group" id="model-select-group">
              <span className="control-label">Model</span>
              <div className="time-presets model-presets">
                {CLAUDE_MODELS.map(({ id, label, hint }) => (
                  <button
                    key={id}
                    type="button"
                    className={`time-preset model-preset ${model === id ? 'active' : ''}`}
                    onClick={() => setModel(id)}
                    disabled={status.status === 'running'}
                    title={`${id} — ${hint}`}
                  >
                    <span className="model-preset-label">{label}</span>
                    <span className="model-preset-hint">{hint}</span>
                  </button>
                ))}
              </div>
            </div>

            <button
              id="trigger-agent-btn"
              type="submit"
              className="btn btn-primary"
              disabled={status.status === 'running' || !query.trim()}
              style={{ marginTop: '1rem' }}
            >
              <RefreshCw size={16} className={status.status === 'running' ? 'spin' : ''} />
              {status.status === 'running' ? 'Agent Running…' : 'Trigger Agent Run'}
            </button>
          </form>

          {/* — Sync & Reset Actions — */}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
            <button
              id="sync-db-btn"
              className={`btn ${status.status === 'running' ? 'disabled' : ''}`}
              onClick={() => fetchJobs(true)}
              disabled={status.status === 'running'}
              title="Refresh and reload jobs from database"
              style={{ flex: 1 }}
            >
              <RefreshCw size={16} className={status.status === 'running' ? 'spin' : ''} />
              <span>Sync</span>
            </button>
            <button
              id="reset-db-btn"
              className="btn"
              onClick={async () => {
                try {
                  const resp = await apiFetch('/api/jobs/clear', { method: 'POST' });
                  if (resp.ok) {
                    setJobs([]);
                    setSelectedJob(null);
                    addToast('Database reset', 'success');
                  } else {
                    addToast('Failed to reset database', 'error');
                  }
                } catch (err) {
                  addToast('Error resetting database', 'error');
                  console.error(err);
                }
              }}
              disabled={status.status === 'running' || jobs.length === 0}
              title="Clear all jobs from database for a fresh run"
              style={{ flex: 1 }}
            >
              <X size={16} />
              <span>Reset</span>
            </button>
          </div>

          {/* — Collapsible Agent Console — */}
          {(status.status === 'running' || logs.length > 0) && (
            <div className="agent-console-panel" id="active-logs-console">
              <div className="console-header" onClick={() => setConsoleOpen(v => !v)} style={{ cursor: 'pointer' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <Terminal size={14} />
                  Agent Log
                  {status.status === 'running' && elapsed && (
                    <span className="console-elapsed">— {elapsed}</span>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                  {status.status === 'running' && (
                    <span className="logo-badge" style={{ animation: 'blink-badge 1.5s ease-in-out infinite' }}>live</span>
                  )}
                  {logs.length > 0 && (
                    <button
                      className="btn btn-sm console-copy-btn"
                      onClick={copyLog}
                      title="Copy log to clipboard"
                    >
                      <Copy size={12} />
                    </button>
                  )}
                  {consoleOpen ? <ChevronUp size={14} className="text-muted" /> : <ChevronDown size={14} className="text-muted" />}
                </div>
              </div>
              {consoleOpen && (
                <div className="console-body">
                  {logs.map((log, i) => (
                    <span key={i} className="console-log-text">{formatLog(log)}</span>
                  ))}
                  {status.status === 'running' && (
                    <span className="console-log-text log-system" style={{ borderLeft: '2px solid var(--accent)', paddingLeft: '4px', animation: 'pulse-slow 1s infinite' }}>
                      Agent is processing… ▋
                    </span>
                  )}
                  <div ref={consoleEndRef} />
                </div>
              )}
            </div>
          )}

          {/* — Section 2: Filter Results (collapsible) — */}
          <div
            className={`filter-section-header ${filterOpen ? 'open' : ''}`}
            onClick={() => setFilterOpen(v => !v)}
            style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem' }}
          >
            <span>
              Filter Results
              {activeFilterCount > 0 && (
                <span className="filter-count-badge">{activeFilterCount}</span>
              )}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              {activeFilterCount > 0 && (
                <button
                  className="clear-filters-link"
                  onClick={(e) => { e.stopPropagation(); clearAllFilters(); }}
                >
                  Clear
                </button>
              )}
              {filterOpen ? <ChevronUp size={14} className="text-muted" /> : <ChevronDown size={14} className="text-muted" />}
            </div>
          </div>

          {filterOpen && (
            <div id="filters-panel" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div className="control-group">
                <span className="control-label">Application Status</span>
                <div className="filter-pills">
                  {['All', 'Applied', 'Not Applied'].map((opt) => (
                    <button
                      key={opt}
                      id={`filter-applied-${opt.replace(/\s+/g, '-').toLowerCase()}`}
                      className={`filter-pill ${selectedApplied === opt ? 'active' : ''}`}
                      onClick={() => setSelectedApplied(opt)}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </div>

              <div className="control-group">
                <span className="control-label">Location Type</span>
                <div className="filter-pills">
                  {['All', 'Remote', 'Onsite/Hybrid'].map((opt) => (
                    <button
                      key={opt}
                      id={`filter-location-${opt.replace(/\//g, '-').toLowerCase()}`}
                      className={`filter-pill ${selectedLocation === opt ? 'active' : ''}`}
                      onClick={() => setSelectedLocation(opt)}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </div>

              {sources.length > 1 && (
                <div className="control-group">
                  <span className="control-label">Job Source</span>
                  <div className="filter-pills">
                    {sources.map((opt) => {
                      const count = opt === 'All' ? jobs.length : jobs.filter(j => j.source === opt).length;
                      return (
                        <button
                          key={opt}
                          id={`filter-source-${opt.replace(/\s+/g, '-').toLowerCase()}`}
                          className={`filter-pill ${selectedSource === opt ? 'active' : ''}`}
                          onClick={() => setSelectedSource(opt)}
                        >
                          {opt}{opt !== 'All' && <span className="source-count"> ({count})</span>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Bookmarks toggle */}
              <div className="control-group">
                <label className="control-label">Bookmarks</label>
                <div className="control-buttons">
                  <button
                    id="filter-bookmarks-all"
                    className={`filter-pill ${!showBookmarksOnly ? 'active' : ''}`}
                    onClick={() => setShowBookmarksOnly(false)}
                  >
                    All
                  </button>
                  <button
                    id="filter-bookmarks-saved"
                    className={`filter-pill ${showBookmarksOnly ? 'active' : ''}`}
                    onClick={() => setShowBookmarksOnly(true)}
                    title={`View ${bookmarkCount} bookmarked jobs`}
                  >
                    Saved {bookmarkCount > 0 && <span style={{ marginLeft: '0.25rem' }}>({bookmarkCount})</span>}
                  </button>
                </div>
              </div>
            </div>
          )}
        </aside>

        {/* ── Main Content ────────────────────────────────────────────────── */}
        <main className="main-content-panel" id="jobs-database-panel">

          {/* Onboarding banner — only shown when DB is empty and not dismissed */}
          {jobs.length === 0 && !jobsLoading && !onboardingDismissed && (
            <div className="onboarding-banner">
              <div className="onboarding-steps">
                <div className="onboarding-step">
                  <span className="onboarding-num">①</span>
                  <div>
                    <strong>Enter a search target</strong>
                    <p>Type your desired role in the sidebar</p>
                  </div>
                </div>
                <span className="onboarding-arrow">→</span>
                <div className="onboarding-step">
                  <span className="onboarding-num">②</span>
                  <div>
                    <strong>Trigger Agent Run</strong>
                    <p>The AI agent scours job boards</p>
                  </div>
                </div>
                <span className="onboarding-arrow">→</span>
                <div className="onboarding-step">
                  <span className="onboarding-num">③</span>
                  <div>
                    <strong>View live results</strong>
                    <p>Jobs stream in as they're found</p>
                  </div>
                </div>
              </div>
              <button
                className="onboarding-dismiss"
                onClick={() => { localStorage.setItem('jf_onboarded', '1'); setOnboardingDismissed(true); }}
                aria-label="Dismiss onboarding"
              >
                <X size={14} />
              </button>
            </div>
          )}

          {/* Panel header with search and active filter tags */}
          <div className="panel-header">
            <h2 className="panel-title">
              Jobs Database
              <span className="panel-title-count"> ({filteredJobs.length} visible)</span>
              {(() => {
                const liveApplies = Object.values(applyStates).filter(s => APPLY_ACTIVE.has(s?.status)).length;
                return liveApplies > 0 ? (
                  <span className="badge apply-chip apply-chip-active" id="apply-agents-running" style={{ marginLeft: '0.6rem', verticalAlign: 'middle' }}>
                    <Bot size={11} /> {liveApplies} agent{liveApplies > 1 ? 's' : ''} applying
                  </span>
                ) : null;
              })()}
            </h2>

            <div className="panel-toolbar">
              <form
                id="filter-form"
                onSubmit={handleFilterFormSubmit}
                style={{ position: 'relative', flex: 1, minWidth: '200px' }}
                toolname="search_jobs_form"
                tooldescription="Search and filter the currently loaded jobs in the local dashboard UI"
                toolautosubmit="true"
              >
                <input
                  id="local-search-input"
                  name="searchTerm"
                  type="text"
                  className="input-text"
                  style={{ paddingLeft: '2.25rem', paddingRight: '2.5rem' }}
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Search jobs… (/)"
                  toolparamdescription="Text query to search within titles, companies, or requirements"
                />
                <Search size={16} className="text-muted" style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
              </form>
              <div className="view-toggle" role="group" aria-label="Job view mode" id="view-mode-toggle">
                <button
                  type="button"
                  id="view-mode-grid"
                  className={`view-toggle-btn ${viewMode === 'grid' ? 'active' : ''}`}
                  onClick={() => setViewMode('grid')}
                  aria-pressed={viewMode === 'grid'}
                  title="Tile view"
                >
                  <LayoutGrid size={16} />
                </button>
                <button
                  type="button"
                  id="view-mode-list"
                  className={`view-toggle-btn ${viewMode === 'list' ? 'active' : ''}`}
                  onClick={() => setViewMode('list')}
                  aria-pressed={viewMode === 'list'}
                  title="List view"
                >
                  <List size={16} />
                </button>
              </div>
              <ExportButton format="csv" />
            </div>
          </div>

          {/* Active filter tags */}
          {activeFilterCount > 0 && (
            <div className="active-filter-tags">
              {selectedApplied !== 'All' && (
                <span className="active-filter-tag">
                  {selectedApplied}
                  <button onClick={() => setSelectedApplied('All')} aria-label="Remove applied filter"><X size={10} /></button>
                </span>
              )}
              {selectedLocation !== 'All' && (
                <span className="active-filter-tag">
                  {selectedLocation}
                  <button onClick={() => setSelectedLocation('All')} aria-label="Remove location filter"><X size={10} /></button>
                </span>
              )}
              {selectedSource !== 'All' && (
                <span className="active-filter-tag">
                  {selectedSource}
                  <button onClick={() => setSelectedSource('All')} aria-label="Remove source filter"><X size={10} /></button>
                </span>
              )}
              <button className="clear-filters-link" onClick={clearAllFilters}>Clear all</button>
            </div>
          )}

          {/* Job list — skeleton / results / empty state */}
          {jobsLoading ? (
            <div className={`jobs-grid ${viewMode === 'list' ? 'list-view' : ''}`} id="jobs-grid">
              {[1, 2, 3].map(i => (
                <div key={i} className="job-card skeleton-card">
                  <div className="skeleton skeleton-title" />
                  <div className="skeleton skeleton-line" />
                  <div className="skeleton skeleton-line short" />
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <div className="skeleton skeleton-badge" />
                    <div className="skeleton skeleton-badge" />
                  </div>
                  <div className="skeleton skeleton-text" />
                </div>
              ))}
            </div>
          ) : filteredJobs.length > 0 ? (
            <>
              <div className={`jobs-grid ${viewMode === 'list' ? 'list-view' : ''}`} id="jobs-grid">
                {paginatedJobs.map((job, localIdx) => {
                  const absoluteIdx = (currentPage - 1) * JOBS_PER_PAGE + localIdx;
                  return (
                    <div
                      key={job.id || absoluteIdx}
                      id={`job-card-${absoluteIdx}`}
                      className={`job-card ${job.applied ? 'applied' : ''} ${
                        ['queued', 'running'].includes(applyStates[job.id]?.status)
                          ? 'agent-live'
                          : applyStates[job.id]?.status === 'awaiting_input'
                            ? 'agent-waiting'
                            : ''
                      }`}
                      onClick={(e) => { lastFocusRef.current = e.currentTarget; setSelectedJob(job); }}
                      tabIndex={0}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); lastFocusRef.current = e.currentTarget; setSelectedJob(job); } }}
                    >
                      <div className="job-card-header">
                        <div style={{ flex: 1 }}>
                          <h3 className="job-title">{job.title}</h3>
                          <span className="job-company">{job.company}</span>
                        </div>
                        <ApplicationStatus
                          jobId={job.id}
                          applicationId={job.application_id}
                          currentStatus={job.application_status || 'draft'}
                          onStatusChange={(newStatus, appId) => {
                            setJobs(prev =>
                              prev.map(j =>
                                j.id === job.id
                                  ? { ...j, application_status: newStatus, application_id: appId }
                                  : j
                              )
                            );
                            fetchAppStats();
                            addToast(`Status updated to ${newStatus}`, 'success');
                          }}
                          onError={(err) => addToast(err, 'error')}
                        />
                        <button
                          className={`btn-bookmark ${job.is_bookmarked ? 'bookmarked' : ''}`}
                          onClick={(e) => { e.stopPropagation(); handleToggleBookmark(job.id, job.is_bookmarked); }}
                          title={job.is_bookmarked ? 'Remove bookmark' : 'Bookmark job'}
                          style={{
                            background: 'transparent',
                            border: 'none',
                            color: job.is_bookmarked ? '#ea4335' : 'var(--text-muted)',
                            cursor: 'pointer',
                            padding: '0.25rem',
                            display: 'flex',
                            alignItems: 'center',
                            marginTop: '-0.25rem',
                            marginRight: '-0.25rem',
                            transition: 'color 0.2s',
                          }}
                        >
                          <Heart size={18} fill={job.is_bookmarked ? '#ea4335' : 'transparent'} />
                        </button>
                      </div>

                      <div className="job-meta-row">
                        <span className="job-meta-item"><MapPin size={12} />{job.location}</span>
                        <span className="job-meta-item"><Calendar size={12} />{job.date_posted || 'Recently posted'}</span>
                      </div>

                      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
                        <span className="badge badge-source">{job.source}</span>
                        {job.applied && (
                          <span className="badge" style={{ backgroundColor: 'var(--success-glow)', color: 'var(--success)', border: '1px solid rgba(42,126,79,0.2)', display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                            <CheckCircle2 size={10} /> Applied
                          </span>
                        )}
                        {applyStates[job.id]?.status && APPLY_CHIP[applyStates[job.id].status] && (
                          <span className={`badge apply-chip ${APPLY_CHIP[applyStates[job.id].status].cls}`} title={applyStates[job.id].error || undefined}>
                            <Bot size={10} /> {APPLY_CHIP[applyStates[job.id].status].label}
                          </span>
                        )}
                        {status.apply_agent_available !== undefined && !job.applied && !APPLY_ACTIVE.has(applyStates[job.id]?.status) && applyStates[job.id]?.status !== 'submitted' && (
                          <button
                            className="btn btn-sm apply-agent-btn"
                            disabled={!status.apply_agent_available}
                            onClick={(e) => { e.stopPropagation(); handleAutoApply(job.id); }}
                            title={status.apply_agent_available
                              ? 'Have the agent fill out and submit this application using your profile'
                              : 'Auto-Apply is unavailable — the browser-agent service is not configured on this deployment'}
                          >
                            <Bot size={12} /> Auto-Apply
                          </button>
                        )}
                      </div>

                      {job.description ? (
                        <p className="job-desc-preview">{job.description}</p>
                      ) : (
                        <p className="job-desc-preview job-desc-empty">
                          No description captured — open the original posting for details.
                        </p>
                      )}

                      {job.key_requirements.length > 0 && (
                        <div className="job-requirements">
                          {job.key_requirements.slice(0, 4).map((req, rIdx) => (
                            <span key={rIdx} className="requirement-tag">{req}</span>
                          ))}
                          {job.key_requirements.length > 4 && (
                            <span className="requirement-tag">+{job.key_requirements.length - 4} more</span>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {totalPages > 1 && (
                <div className="pagination-container">
                  <button className="btn btn-pagination" disabled={currentPage === 1} onClick={() => setCurrentPage(p => Math.max(1, p - 1))}>
                    ← Prev
                  </button>
                  <span className="pagination-info">Page {currentPage} of {totalPages} · {filteredJobs.length} jobs</span>
                  <button className="btn btn-pagination" disabled={currentPage === totalPages} onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}>
                    Next →
                  </button>
                </div>
              )}
            </>
          ) : (
            <div className="empty-state" id="empty-state-view">
              <Briefcase size={40} className="empty-state-icon" />
              <h3 className="empty-state-title">
                {jobs.length === 0 ? 'No Jobs Found' : 'No Matching Jobs'}
              </h3>
              <p className="empty-state-desc">
                {jobs.length === 0
                  ? 'Enter a search target and trigger the agent to find remote US full-time roles across LinkedIn, Indeed, Glassdoor, ZipRecruiter, Dice, Wellfound, Built In, the ATS career portals, and company career pages.'
                  : 'No jobs match your active filters.'}
              </p>
              {jobs.length === 0 ? (
                <button
                  id="empty-state-default-btn"
                  className="btn btn-primary"
                  onClick={() => { setQuery('Senior Data Engineer'); document.getElementById('agent-query-input')?.focus(); }}
                  disabled={status.status === 'running'}
                  style={{ marginTop: '0.5rem' }}
                >
                  Start your first search →
                </button>
              ) : (
                <button className="btn" onClick={clearAllFilters} style={{ marginTop: '0.5rem' }}>
                  Clear filters
                </button>
              )}
            </div>
          )}
        </main>
      </div>

      {/* ── Job Details Dialog ─────────────────────────────────────────────── */}
      <dialog
        ref={dialogRef}
        className="job-details-dialog"
        onClose={() => setSelectedJob(null)}
        id="job-details-dialog"
        onKeyDown={(e) => { if (e.key === 'Escape') setSelectedJob(null); }}
      >
        {selectedJob && (
          <>
            <button
              id="close-modal-btn"
              className="modal-close-btn"
              onClick={() => setSelectedJob(null)}
              aria-label="Close details dialog"
            >
              <X size={20} />
            </button>

            <div className="modal-header">
              <h2 className="modal-job-title">{selectedJob.title}</h2>
              <div className="modal-company-section">
                <span className="job-company" style={{ fontSize: '1.1rem' }}>{selectedJob.company}</span>
                <span style={{ color: 'var(--text-muted)' }}>•</span>
                <span className="job-meta-item" style={{ fontSize: '0.95rem' }}>
                  <MapPin size={14} style={{ marginRight: '0.15rem' }} />
                  {selectedJob.location}
                </span>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                <span className="badge badge-source">{selectedJob.source}</span>
                <span className="badge badge-neutral" style={{ textTransform: 'none', display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                  <Calendar size={11} />Found: {selectedJob.date_posted || 'Recently'}
                </span>
                {selectedJob.applied && (
                  <span className="badge" style={{ backgroundColor: 'var(--success-glow)', color: 'var(--success)', border: '1px solid rgba(42,126,79,0.2)', display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                    <CheckCircle2 size={11} />Applied
                  </span>
                )}
              </div>
            </div>

            <div className="modal-body">
              {(selectedJob.contact_email || selectedJob.contact_phone || selectedJob.url) && (
                <div className="modal-section" id="modal-section-contact">
                  <span className="modal-section-title">Application & Contact</span>
                  <div className="contact-row">
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem' }}>
                      {selectedJob.contact_email && (
                        <div className="contact-item"><Mail size={14} />Email: <a href={`mailto:${selectedJob.contact_email}`}>{selectedJob.contact_email}</a></div>
                      )}
                      {selectedJob.contact_phone && (
                        <div className="contact-item"><Phone size={14} />Phone: {selectedJob.contact_phone}</div>
                      )}
                      {selectedJob.url?.startsWith('http') && (
                        <div className="contact-item"><LinkIcon size={14} /><a href={selectedJob.url} target="_blank" rel="noopener noreferrer">View Original Posting</a></div>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                      {status.apply_agent_available !== undefined && !selectedJob.applied && !APPLY_ACTIVE.has(applyStates[selectedJob.id]?.status) && applyStates[selectedJob.id]?.status !== 'submitted' && (
                        <button
                          id="auto-apply-btn"
                          className="btn btn-primary"
                          disabled={!status.apply_agent_available}
                          onClick={() => handleAutoApply(selectedJob.id)}
                          title={status.apply_agent_available
                            ? 'The agent opens this posting in a headless browser, fills the form from your profile, uploads your resume, and submits'
                            : 'Auto-Apply is unavailable — the browser-agent service is not configured on this deployment'}
                        >
                          <Bot size={16} />Auto-Apply with Agent
                        </button>
                      )}
                      {selectedJob.url?.startsWith('http') && (
                        <a
                          href={selectedJob.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="btn"
                          onClick={() => { if (!selectedJob.applied) handleToggleApplied(selectedJob.id, selectedJob.applied); }}
                          style={{ textDecoration: 'none' }}
                        >
                          <LinkIcon size={16} />Apply Now & Mark Applied
                        </a>
                      )}
                      <button
                        className={`btn ${selectedJob.applied ? 'btn-success-active' : ''}`}
                        onClick={() => handleToggleApplied(selectedJob.id, selectedJob.applied)}
                      >
                        <CheckCircle2 size={16} fill={selectedJob.applied ? 'var(--success-glow)' : 'transparent'} />
                        {selectedJob.applied ? 'Applied (Click to Undo)' : 'Mark as Applied'}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* ── Apply-agent run panel ─────────────────────────────────── */}
              {applyStates[selectedJob.id]?.status && (
                <div className="modal-section" id="modal-section-apply-agent">
                  <span className="modal-section-title">Apply Agent</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                    {APPLY_CHIP[applyStates[selectedJob.id].status] && (
                      <span className={`badge apply-chip ${APPLY_CHIP[applyStates[selectedJob.id].status].cls}`}>
                        <Bot size={11} /> {APPLY_CHIP[applyStates[selectedJob.id].status].label}
                      </span>
                    )}
                    {applyStates[selectedJob.id].error && (
                      <span style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                        {applyStates[selectedJob.id].error}
                      </span>
                    )}
                  </div>
                  {/* Human-in-the-loop: the paused agent needs a value to fill a field */}
                  {applyStates[selectedJob.id].status === 'awaiting_input' && (
                    <div className="apply-input-panel" id="apply-input-panel">
                      <span className="apply-input-label"><Keyboard size={13} /> The agent needs your answer to keep going</span>
                      <span className="apply-input-prompt">
                        {applyStates[selectedJob.id].inputPrompt || 'The agent needs your input to continue.'}
                      </span>
                      <div className="apply-input-row">
                        <input
                          id="apply-input-field"
                          className="input-text"
                          value={applyInputValue}
                          onChange={(e) => setApplyInputValue(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Enter') submitApplyInput(selectedJob.id); }}
                          placeholder="Type your answer here…"
                          autoFocus
                        />
                        <button className="btn btn-primary" onClick={() => submitApplyInput(selectedJob.id)}>
                          Send to agent
                        </button>
                      </div>
                      <span className="apply-input-hint">The agent may ask a few of these while it fills the form. It will never ask for passwords or logins.</span>
                    </div>
                  )}
                  {/* Milestone stepper + live browser view + step timeline */}
                  {(() => {
                    const st = applyStates[selectedJob.id];
                    const steps = st.steps || [];
                    const ms = st.milestone || 0;
                    const isActive = APPLY_ACTIVE.has(st.status);
                    const url = applyCurrentUrl(st, selectedJob.url);
                    const host = hostOf(url);
                    return (
                      <>
                        {/* 5-node milestone stepper */}
                        <div className="apply-stepper" id="apply-stepper" role="list">
                          {APPLY_MILESTONES.map((label, i) => {
                            const n = i + 1;
                            const complete = n < ms || (n === ms && !isActive && st.status !== 'awaiting_input');
                            const active = n === ms && !complete;
                            return (
                              <div key={n} className={`apply-step-node ${complete ? 'done' : active ? 'active' : 'todo'}`} role="listitem">
                                <span className="apply-step-num">{complete ? <Check size={13} /> : n}</span>
                                <span className="apply-step-label">{label}</span>
                              </div>
                            );
                          })}
                        </div>

                        <div className="apply-live-grid" id="apply-live-view">
                          {/* Live browser window: the screenshot framed as a browser */}
                          <div className="apply-browser-frame">
                            <div className="apply-browser-urlbar">
                              <span className="apply-browser-dots"><i /><i /><i /></span>
                              <span className="apply-browser-url" title={url}>{host || 'about:blank'}</span>
                              {isActive && <span className="apply-browser-live">● live</span>}
                            </div>
                            <div className="apply-browser-viewport">
                              {/* True live stream (canvas) stays mounted while active so the
                                  MJPEG effect can draw into it; shown once frames flow. */}
                              {isActive && (
                                <canvas
                                  ref={liveCanvasRef}
                                  id="apply-live-canvas"
                                  className="apply-browser-shot apply-browser-canvas"
                                  style={{ display: liveStreamOn ? 'block' : 'none' }}
                                />
                              )}
                              {!liveStreamOn && (
                                (liveShotUrl && isActive) ? (
                                  <img src={liveShotUrl} alt="Live view of the page the agent is on" className="apply-browser-shot" />
                                ) : applyShotUrl ? (
                                  <img src={applyShotUrl} alt="Final page the agent reached" className="apply-browser-shot" />
                                ) : (
                                  <div className="apply-browser-placeholder">
                                    <Bot size={22} />
                                    <span>{isActive ? 'Connecting to the live browser…' : 'No screenshot captured'}</span>
                                  </div>
                                )
                              )}
                            </div>
                          </div>

                          {/* Live step timeline — what the agent is doing in the browser */}
                          <div className="apply-steps" id="apply-step-timeline">
                            <span className="control-label">What the agent is doing</span>
                            <ol className="apply-step-list" ref={stepListRef}>
                              {steps.length === 0 && (
                                <li className="apply-step apply-step-muted">
                                  <span className="apply-step-icon"><Loader2 size={13} /></span>
                                  <span className="apply-step-body"><span className="apply-step-text">Getting started…</span></span>
                                </li>
                              )}
                              {steps.map((s, i) => {
                                const live = i === steps.length - 1 && isActive;
                                return (
                                  <li key={i} className={`apply-step apply-step-${s.kind || 'other'}${live ? ' apply-step-active' : ''}`}>
                                    <span className="apply-step-icon">{stepIcon(s.kind)}</span>
                                    <span className="apply-step-body">
                                      <span className="apply-step-text">{s.title}</span>
                                      {s.detail && <span className="apply-step-detail">{s.detail}</span>}
                                    </span>
                                  </li>
                                );
                              })}
                            </ol>
                          </div>
                        </div>

                        {(st.lines || []).length > 0 && (
                          <details className="apply-log-details">
                            <summary>Raw activity log</summary>
                            <div className="apply-progress-lines">
                              {st.lines.map((line, i) => (
                                <span key={i} className="console-log-text log-system">{line}</span>
                              ))}
                            </div>
                          </details>
                        )}
                      </>
                    );
                  })()}
                  {applyStates[selectedJob.id].status === 'needs_review' && selectedJob.url?.startsWith('http') && (
                    <a href={selectedJob.url} target="_blank" rel="noopener noreferrer" className="btn btn-sm" style={{ marginTop: '0.6rem', textDecoration: 'none' }}>
                      <LinkIcon size={13} /> Finish applying manually
                    </a>
                  )}
                </div>
              )}

              <div className="modal-section" id="modal-section-description">
                <span className="modal-section-title">Job Description</span>
                <p className={`modal-desc-text ${selectedJob.description ? '' : 'job-desc-empty'}`}>
                  {selectedJob.description
                    || 'No description was captured for this posting. Use “View Original Posting” above to read the full details on the source site.'}
                </p>
              </div>

              {selectedJob.key_requirements.length > 0 && (
                <div className="modal-section" id="modal-section-requirements">
                  <span className="modal-section-title">Required Technical Stack</span>
                  <div className="job-requirements" style={{ gap: '0.5rem', marginTop: '0.25rem' }}>
                    {selectedJob.key_requirements.map((req, idx) => (
                      <span key={idx} className="requirement-tag" style={{ padding: '0.3rem 0.65rem', fontSize: '0.8rem' }}>{req}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </dialog>

      {/* ── Profile-incomplete gating modal (Auto-Apply requires a full profile) ── */}
      {gatingFields && (
        <div className="gating-overlay" onClick={() => setGatingFields(null)}>
          <div className="auth-card gating-modal" id="apply-gating-modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="auth-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Bot size={20} className="text-primary" /> Complete your profile to auto-apply
            </h3>
            <p className="auth-subtitle">
              The apply agent fills employer forms with your saved details. It still needs:
            </p>
            <ul className="gating-missing-list">
              {gatingFields.map((f) => <li key={f}>{f}</li>)}
            </ul>
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem' }}>
              <RouterLink to="/onboarding" className="btn btn-primary" style={{ textDecoration: 'none', flex: 1, justifyContent: 'center' }}>
                Complete profile
              </RouterLink>
              <button className="btn" onClick={() => setGatingFields(null)}>Not now</button>
            </div>
          </div>
        </div>
      )}

      <footer className="app-footer" id="app-footer">
        <span className={`status-indicator ${healthStatus}`} id="footer-status-indicator">
          <span className="status-dot-wrap" aria-hidden="true">
            <span className="status-dot-ping" />
            <span className="status-dot" />
          </span>
          <span className="status-label">
            {healthStatus === 'ok'
              ? 'All systems operational'
              : healthStatus === 'error'
                ? 'Systems experiencing issues'
                : 'Checking systems…'}
          </span>
        </span>
      </footer>
    </div>
  );
}

export default Dashboard;
