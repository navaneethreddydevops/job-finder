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
  Settings as SettingsIcon,
} from 'lucide-react';
import UserMenu from './components/UserMenu.jsx';
import { useToast } from './components/Toast.jsx';
import ApplicationStatus from './components/ApplicationStatus.jsx';
import ThemeToggle from './components/ThemeToggle.jsx';
import ExportButton from './components/ExportButton.jsx';
import { apiFetch, apiUrl } from './auth';
import { useDarkMode } from './hooks/useDarkMode';
import { Heart } from 'lucide-react';

// ── helpers ──────────────────────────────────────────────────────────────────

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

  // filters
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedSource, setSelectedSource] = useState('All');
  const [selectedLocation, setSelectedLocation] = useState('All');
  const [selectedApplied, setSelectedApplied] = useState('All');

  // UI state
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

  const JOBS_PER_PAGE = 12;

  const consoleEndRef = useRef(null);
  const eventSourceRef = useRef(null);
  const dialogRef = useRef(null);
  const lastFocusRef = useRef(null);
  const stateRef = useRef({});

  stateRef.current = {
    jobs, filteredJobs: [],
    query, status, logs, searchTerm,
    selectedLocation, selectedSource, selectedApplied, selectedJob,
  };

  // ── 7-day freshness filter ──────────────────────────────────────────────────
  const isWithin7d = (job) => {
    if (job.posted_within_7d) return true;
    const d = (job.date_posted || '').toLowerCase();
    if (!d) return false;
    if (/(just|now|moment|today|yesterday|hour|minute|second)/.test(d)) return true;
    const dayMatch = d.match(/(\d+)\s*day/);
    if (dayMatch && parseInt(dayMatch[1], 10) <= 7) return true;
    const weekMatch = d.match(/(\d+)\s*week/);
    if (weekMatch && parseInt(weekMatch[1], 10) <= 1) return true;
    return false;
  };

  // ── data fetching ───────────────────────────────────────────────────────────
  const fetchJobs = async (showToast = false) => {
    setJobsLoading(true);
    try {
      const resp = await apiFetch('/api/jobs');
      if (resp.ok) {
        const data = await resp.json();
        const fresh = (data.jobs || []).filter(isWithin7d);
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

  const fetchStatus = async () => {
    try {
      const resp = await fetch(apiUrl('/api/status'));
      if (resp.ok) setStatus(await resp.json());
    } catch (err) {
      console.error('Failed to fetch status:', err);
    }
  };

  const startStreaming = () => {
    if (eventSourceRef.current) eventSourceRef.current.close();
    const es = new EventSource(apiUrl('/api/stream'));
    eventSourceRef.current = es;
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setLogs(prev => [...prev, data.message]);
        if (typeof data.message === 'string' && data.message.includes('Database now holds')) {
          fetchJobs();
        }
      } catch (err) { console.error('Failed to parse log message:', err); }
    };
    es.onerror = () => { console.log('SSE closed'); es.close(); };
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
          }),
        });
        if (resp.ok) {
          setStatus({ status: 'running', query: queryValue });
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

    return () => { if (eventSourceRef.current) eventSourceRef.current.close(); };
  }, []);

  // health check
  useEffect(() => {
    const check = async () => {
      try {
        const resp = await fetch(apiUrl('/api/health'));
        setHealthStatus(resp.ok ? 'ok' : 'error');
      } catch { setHealthStatus('error'); }
    };
    check();
    const interval = setInterval(check, 5000);
    return () => clearInterval(interval);
  }, []);

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
              const resp = await apiFetch('/api/pull', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: q }) });
              if (resp.ok) { setStatus({ status: 'running', query: q }); setAgentStartTime(performance.now()); setConsoleOpen(true); startStreaming(); return { success: true, message: `Scraper initiated for '${q}'` }; }
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
          {/* Backend status */}
          <div className="header-status-corner">
            <div className={`health-pill ${healthStatus}`} title={`Backend ${healthStatus === 'ok' ? 'online' : healthStatus === 'error' ? 'offline' : 'checking'}`}>
              <div className="health-dot" />
              <span>{healthStatus === 'ok' ? 'Online' : healthStatus === 'error' ? 'Offline' : '…'}</span>
            </div>
          </div>

          {/* nav link — styled differently from action buttons */}
          <Link to="/resume/optimizer" className="btn nav-link" title="Resume Optimizer">
            <FileText size={16} />
            <span className="header-btn-label">Resume Optimizer</span>
          </Link>

          <Link to="/analytics" className="btn nav-link" title="Analytics">
            <BarChart3 size={16} />
            <span className="header-btn-label">Analytics</span>
          </Link>

          <Link to="/settings" className="btn nav-link" title="Settings">
            <SettingsIcon size={16} />
            <span className="header-btn-label">Settings</span>
          </Link>

          {/* Action buttons */}
          <ExportButton format="csv" />
          <ThemeToggle />

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
        <div className="stat-card" id="stat-card-fulltime" title="Full-time roles from Workday, LinkedIn, and Glassdoor">
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
            <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--border)' }}>
              <label className="control-label" style={{ display: 'block', marginBottom: '0.5rem' }}>Job Type</label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {['fulltime', 'remote', 'contract'].map((type) => (
                  <label key={type} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.95rem' }}>
                    <input
                      id={`filter-jobtype-${type}`}
                      type="checkbox"
                      checked={jobTypes.has(type)}
                      onChange={(e) => {
                        const newTypes = new Set(jobTypes);
                        if (e.target.checked) {
                          newTypes.add(type);
                        } else {
                          newTypes.delete(type);
                        }
                        setJobTypes(newTypes);
                      }}
                      disabled={status.status === 'running'}
                      style={{ cursor: 'pointer' }}
                    />
                    <span style={{ textTransform: 'capitalize' }}>
                      {type === 'fulltime' ? 'Full-Time' : type === 'remote' ? 'Remote' : 'Contract'}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {/* Time Period Slider */}
            <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--border)' }}>
              <label htmlFor="time-period-slider" className="control-label" style={{ display: 'block', marginBottom: '0.5rem' }}>
                Posted Within: <span style={{ fontWeight: 'bold', color: 'var(--primary)' }}>{timePeriodDays} days</span>
              </label>
              <input
                id="time-period-slider"
                type="range"
                min="7"
                max="90"
                value={timePeriodDays}
                onChange={(e) => setTimePeriodDays(parseInt(e.target.value, 10))}
                disabled={status.status === 'running'}
                style={{
                  width: '100%',
                  height: '6px',
                  borderRadius: '3px',
                  background: 'var(--border)',
                  outline: 'none',
                  cursor: status.status === 'running' ? 'not-allowed' : 'pointer',
                }}
              />
              <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                {[7, 14, 30, 90].map((days) => (
                  <button
                    key={days}
                    type="button"
                    onClick={() => setTimePeriodDays(days)}
                    disabled={status.status === 'running'}
                    style={{
                      padding: '0.4rem 0.8rem',
                      fontSize: '0.85rem',
                      border: `1px solid ${timePeriodDays === days ? 'var(--primary)' : 'var(--border)'}`,
                      backgroundColor: timePeriodDays === days ? 'var(--primary-glow)' : 'transparent',
                      color: timePeriodDays === days ? 'var(--primary)' : 'var(--text-muted)',
                      borderRadius: '4px',
                      cursor: status.status === 'running' ? 'not-allowed' : 'pointer',
                      transition: 'all 0.2s',
                    }}
                  >
                    {days === 7 ? '1 Week' : days === 14 ? '2 Weeks' : days === 30 ? '1 Month' : '3 Months'}
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
            </h2>

            <form
              id="filter-form"
              onSubmit={handleFilterFormSubmit}
              style={{ position: 'relative', width: '260px' }}
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
            <div className="jobs-grid" id="jobs-grid">
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
              <div className="jobs-grid" id="jobs-grid">
                {paginatedJobs.map((job, localIdx) => {
                  const absoluteIdx = (currentPage - 1) * JOBS_PER_PAGE + localIdx;
                  return (
                    <div
                      key={job.id || absoluteIdx}
                      id={`job-card-${absoluteIdx}`}
                      className={`job-card ${job.applied ? 'applied' : ''}`}
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
                        <span className="job-meta-item"><Calendar size={12} />{job.date_posted}</span>
                      </div>

                      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                        <span className="badge badge-source">{job.source}</span>
                        {job.applied && (
                          <span className="badge" style={{ backgroundColor: 'var(--success-glow)', color: 'var(--success)', border: '1px solid rgba(42,126,79,0.2)', display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                            <CheckCircle2 size={10} /> Applied
                          </span>
                        )}
                      </div>

                      <p className="job-desc-preview">{job.description}</p>

                      <div className="job-requirements">
                        {job.key_requirements.slice(0, 4).map((req, rIdx) => (
                          <span key={rIdx} className="requirement-tag">{req}</span>
                        ))}
                        {job.key_requirements.length > 4 && (
                          <span className="requirement-tag">+{job.key_requirements.length - 4} more</span>
                        )}
                      </div>
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
                  ? 'Enter a search target and trigger the agent to find full-time roles on Workday, LinkedIn, and Glassdoor.'
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
                  <Calendar size={11} />Found: {selectedJob.date_posted}
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
                      {selectedJob.url && (
                        <div className="contact-item"><LinkIcon size={14} /><a href={selectedJob.url} target="_blank" rel="noopener noreferrer">View Original Posting</a></div>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                      {selectedJob.url && (
                        <a
                          href={selectedJob.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="btn btn-primary"
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

              <div className="modal-section" id="modal-section-description">
                <span className="modal-section-title">Job Description</span>
                <p className="modal-desc-text">{selectedJob.description}</p>
              </div>

              <div className="modal-section" id="modal-section-requirements">
                <span className="modal-section-title">Required Technical Stack</span>
                <div className="job-requirements" style={{ gap: '0.5rem', marginTop: '0.25rem' }}>
                  {selectedJob.key_requirements.map((req, idx) => (
                    <span key={idx} className="requirement-tag" style={{ padding: '0.3rem 0.65rem', fontSize: '0.8rem' }}>{req}</span>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}
      </dialog>
    </div>
  );
}

export default Dashboard;
