import React, { useState, useEffect, useMemo, useRef } from 'react';
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
  Cpu
} from 'lucide-react';

function App() {
  const [jobs, setJobs] = useState([]);
  const [status, setStatus] = useState({ status: 'idle', query: null });
  const [query, setQuery] = useState('C2C Data Engineer');
  const [logs, setLogs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [activeAgentTool, setActiveAgentTool] = useState(null);
  
  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedC2C, setSelectedC2C] = useState('All');
  const [selectedSource, setSelectedSource] = useState('All');
  const [selectedLocation, setSelectedLocation] = useState('All');
  const [selectedApplied, setSelectedApplied] = useState('All');

  const consoleEndRef = useRef(null);
  const eventSourceRef = useRef(null);
  const dialogRef = useRef(null);
  const stateRef = useRef({});
  
  // Create a mutable ref holding all reactive state variables.
  // This lets the WebMCP tools access fresh state without trigger re-registrations.
  stateRef.current = {
    jobs,
    filteredJobs: [], // will be populated in render / memo
    query,
    status,
    logs,
    searchTerm,
    selectedC2C,
    selectedLocation,
    selectedSource,
    selectedApplied,
    selectedJob
  };

  // Fetch jobs list
  const fetchJobs = async () => {
    try {
      const resp = await fetch('/api/jobs');
      if (resp.ok) {
        const data = await resp.json();
        setJobs(data.jobs || []);
      }
    } catch (err) {
      console.error("Failed to fetch jobs:", err);
    }
  };

  const handleToggleApplied = async (jobId, currentApplied) => {
    try {
      const newApplied = !currentApplied;
      const resp = await fetch(`/api/jobs/${jobId}/apply`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ applied: newApplied })
      });
      if (resp.ok) {
        setJobs(prevJobs => prevJobs.map(job => 
          job.id === jobId ? { ...job, applied: newApplied } : job
        ));
        if (stateRef.current.selectedJob && stateRef.current.selectedJob.id === jobId) {
          setSelectedJob(prev => ({ ...prev, applied: newApplied }));
        }
      } else {
        console.error("Failed to update applied status");
      }
    } catch (err) {
      console.error("Error toggling applied status:", err);
    }
  };

  // Fetch backend agent status
  const fetchStatus = async () => {
    try {
      const resp = await fetch('/api/status');
      if (resp.ok) {
        const data = await resp.json();
        setStatus(data);
      }
    } catch (err) {
      console.error("Failed to fetch status:", err);
    }
  };

  // SSE streaming listener
  const startStreaming = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const eventSource = new EventSource('/api/stream');
    eventSourceRef.current = eventSource;

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setLogs((prev) => [...prev, data.message]);
      } catch (err) {
        console.error("Failed to parse log message:", err);
      }
    };

    eventSource.onerror = (err) => {
      console.log("SSE Connection closed or encountered error, closing stream.");
      eventSource.close();
    };
  };

  // Trigger job pulling (FastAPI agent backend)
  const handlePullJobs = async (e) => {
    e.preventDefault();
    
    // Get query value directly from form in case React state hasn't updated (agent submissions)
    const formData = new FormData(e.currentTarget || e.target);
    const queryValue = formData.get('query')?.toString() || query;

    if (!queryValue.trim()) return;

    setQuery(queryValue);

    const runPromise = (async () => {
      try {
        setLogs([]); // Clear previous logs
        const resp = await fetch('/api/pull', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: queryValue })
        });
        
        if (resp.ok) {
          setStatus({ status: 'running', query: queryValue });
          startStreaming();
          return `Successfully started backend job pull for query: "${queryValue}"`;
        } else {
          const errorData = await resp.json();
          const errText = errorData.detail || "Failed to trigger job pull.";
          if (!e.agentInvoked) alert(errText);
          return `Error triggering agent: ${errText}`;
        }
      } catch (err) {
        console.error("Error triggering job pull:", err);
        return `Network error: ${err.message}`;
      }
    })();

    if (e.agentInvoked && typeof e.respondWith === 'function') {
      e.respondWith(runPromise);
    }
  };

  // Handle local filter form submission (WebMCP agent submissions)
  const handleFilterFormSubmit = (e) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget || e.target);
    const term = formData.get('searchTerm')?.toString() || '';
    setSearchTerm(term);
    if (e.agentInvoked && typeof e.respondWith === 'function') {
      e.respondWith(Promise.resolve(`Applied search query: "${term}"`));
    }
  };

  // Initial load
  useEffect(() => {
    fetchJobs();
    fetchStatus();

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  // Poll status when agent is running
  useEffect(() => {
    let interval;
    if (status.status === 'running') {
      if (!eventSourceRef.current || eventSourceRef.current.readyState === EventSource.CLOSED) {
        startStreaming();
      }
      
      interval = setInterval(async () => {
        try {
          const resp = await fetch('/api/status');
          if (resp.ok) {
            const data = await resp.json();
            setStatus(data);
            if (data.status === 'idle') {
              clearInterval(interval);
              fetchJobs();
              if (eventSourceRef.current) {
                eventSourceRef.current.close();
              }
            }
          }
        } catch (err) {
          console.error("Failed polling status:", err);
        }
      }, 3000);
    }
    return () => clearInterval(interval);
  }, [status.status]);

  // Auto-scroll terminal console
  useEffect(() => {
    if (consoleEndRef.current) {
      consoleEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs]);

  // Compute available sources dynamically from actual data
  const sources = useMemo(() => {
    const list = new Set();
    jobs.forEach(job => {
      if (job.source) list.add(job.source);
    });
    return ['All', ...Array.from(list)];
  }, [jobs]);

  // Filtered jobs list
  const filteredJobs = useMemo(() => {
    return jobs.filter(job => {
      const searchLower = searchTerm.toLowerCase();
      const matchesSearch = 
        job.title.toLowerCase().includes(searchLower) ||
        job.company.toLowerCase().includes(searchLower) ||
        job.description.toLowerCase().includes(searchLower) ||
        job.key_requirements.some(req => req.toLowerCase().includes(searchLower));
      
      let matchesC2C = true;
      if (selectedC2C !== 'All') {
        matchesC2C = job.c2c_viability === selectedC2C;
      }

      let matchesSource = true;
      if (selectedSource !== 'All') {
        matchesSource = job.source === selectedSource;
      }

      let matchesLocation = true;
      if (selectedLocation !== 'All') {
        const isRemote = job.location.toLowerCase().includes('remote');
        if (selectedLocation === 'Remote') {
          matchesLocation = isRemote;
        } else if (selectedLocation === 'Onsite/Hybrid') {
          matchesLocation = !isRemote;
        }
      }

      let matchesApplied = true;
      if (selectedApplied !== 'All') {
        matchesApplied = selectedApplied === 'Applied' ? job.applied : !job.applied;
      }

      return matchesSearch && matchesC2C && matchesSource && matchesLocation && matchesApplied;
    });
  }, [jobs, searchTerm, selectedC2C, selectedSource, selectedLocation, selectedApplied]);

  // Push filtered list to stateRef so WebMCP tools retrieve the correct indexes
  stateRef.current.filteredJobs = filteredJobs;

  // Sync native dialog overlay
  useEffect(() => {
    if (selectedJob) {
      dialogRef.current?.showModal();
    } else {
      dialogRef.current?.close();
    }
  }, [selectedJob]);

  // Stats Computations
  const stats = useMemo(() => {
    const total = jobs.length;
    const confirmedC2C = jobs.filter(j => j.c2c_viability === 'Confirmed C2C').length;
    const remote = jobs.filter(j => j.location.toLowerCase().includes('remote')).length;
    const applied = jobs.filter(j => j.applied).length;
    return { total, confirmedC2C, remote, applied };
  }, [jobs]);

  // WebMCP Imperative API registration
  useEffect(() => {
    const modelContext = document.modelContext || navigator.modelContext;
    if (modelContext && typeof modelContext.registerTool === 'function') {
      const controller = new AbortController();
      const signal = controller.signal;

      try {
        // Tool 1: Get jobs list
        modelContext.registerTool({
          name: "get_jobs_list",
          description: "Retrieve all jobs matching the current search parameters and filters in the dashboard.",
          inputSchema: {
            type: "object",
            properties: {}
          },
          execute() {
            const current = stateRef.current;
            return {
              total_database_count: current.jobs.length,
              filtered_display_count: current.filteredJobs.length,
              active_filters: {
                searchTerm: current.searchTerm,
                c2cViability: current.selectedC2C,
                location: current.selectedLocation,
                source: current.selectedSource,
                applied: current.selectedApplied
              },
              jobs: current.filteredJobs.map((j, idx) => ({
                index: idx,
                id: j.id,
                title: j.title,
                company: j.company,
                location: j.location,
                c2c_viability: j.c2c_viability,
                source: j.source,
                applied: j.applied,
                key_requirements: j.key_requirements
              }))
            };
          },
          annotations: { readOnlyHint: true }
        }, { signal });

        // Tool 2: Filter display list
        modelContext.registerTool({
          name: "filter_jobs",
          description: "Apply text search and filter selections in the dashboard viewport.",
          inputSchema: {
            type: "object",
            properties: {
              searchTerm: { type: "string", description: "Search query for title, company or skills" },
              c2cViability: { type: "string", enum: ["All", "Confirmed C2C", "Likely C2C", "Not Specified"], description: "C2C confidence filter" },
              location: { type: "string", enum: ["All", "Remote", "Onsite/Hybrid"], description: "Location type filter" },
              source: { type: "string", description: "Source portal filter" },
              applied: { type: "string", enum: ["All", "Applied", "Not Applied"], description: "Applied status filter" }
            }
          },
          execute(input) {
            if (input.searchTerm !== undefined) setSearchTerm(input.searchTerm);
            if (input.c2cViability !== undefined) setSelectedC2C(input.c2cViability);
            if (input.location !== undefined) setSelectedLocation(input.location);
            if (input.source !== undefined) setSelectedSource(input.source);
            if (input.applied !== undefined) setSelectedApplied(input.applied);
            return {
              success: true,
              message: "Dashboard viewport filters applied successfully"
            };
          }
        }, { signal });

        // Tool 3: View job description modal
        modelContext.registerTool({
          name: "view_job_details",
          description: "Open the details drawer modal for a job using its list index.",
          inputSchema: {
            type: "object",
            properties: {
              index: { type: "integer", description: "0-based index of the job in the current filtered jobs list" }
            },
            required: ["index"]
          },
          execute(input) {
            const current = stateRef.current;
            if (input.index >= 0 && input.index < current.filteredJobs.length) {
              const job = current.filteredJobs[input.index];
              setSelectedJob(job);
              return {
                success: true,
                message: `Opened job details for "${job.title}" at "${job.company}"`,
                job: job
              };
            }
            return {
              success: false,
              error: `Invalid index: ${input.index}. List bounds are 0 to ${current.filteredJobs.length - 1}`
            };
          }
        }, { signal });

        // Tool 4: Trigger backend crawler agent
        modelContext.registerTool({
          name: "trigger_agent_run",
          description: "Trigger the backend scraping agent to run a live job crawl with the specified query.",
          inputSchema: {
            type: "object",
            properties: {
              query: { type: "string", description: "The scraper query (e.g. 'C2C Data Engineer')" }
            },
            required: ["query"]
          },
          async execute(input) {
            const q = input.query.trim();
            if (!q) {
              return { success: false, error: "Search query string is required" };
            }
            setQuery(q);
            setLogs([]);
            try {
              const resp = await fetch('/api/pull', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: q })
              });
              
              if (resp.ok) {
                setStatus({ status: 'running', query: q });
                startStreaming();
                return {
                  success: true,
                  message: `Job search scraper successfully initiated for '${q}'`
                };
              } else {
                const errorData = await resp.json();
                return {
                  success: false,
                  error: errorData.detail || "Scraper call failed"
                };
              }
            } catch (err) {
              return { success: false, error: err.message };
            }
          }
        }, { signal });

        // Tool 5: Toggle applied status
        modelContext.registerTool({
          name: "toggle_job_applied",
          description: "Toggle the applied status of a job posting.",
          inputSchema: {
            type: "object",
            properties: {
              jobId: { type: "integer", description: "The unique database ID of the job" },
              applied: { type: "boolean", description: "Whether the job should be marked as applied" }
            },
            required: ["jobId", "applied"]
          },
          async execute(input) {
            try {
              const resp = await fetch(`/api/jobs/${input.jobId}/apply`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ applied: input.applied })
              });
              if (resp.ok) {
                setJobs(prevJobs => prevJobs.map(job => 
                  job.id === input.jobId ? { ...job, applied: input.applied } : job
                ));
                if (stateRef.current.selectedJob && stateRef.current.selectedJob.id === input.jobId) {
                  setSelectedJob(prev => ({ ...prev, applied: input.applied }));
                }
                return {
                  success: true,
                  message: `Successfully set applied=${input.applied} for job ID ${input.jobId}`
                };
              } else {
                return { success: false, error: "Failed to update applied status on backend" };
              }
            } catch (err) {
              return { success: false, error: err.message };
            }
          }
        }, { signal });

        // Tool 6: Clear database
        modelContext.registerTool({
          name: "clear_database",
          description: "Reset and clear the current jobs database list in the UI.",
          inputSchema: {
            type: "object",
            properties: {}
          },
          async execute() {
            try {
              const resp = await fetch('/api/jobs/clear', { method: 'POST' });
              if (resp.ok) {
                setJobs([]);
                setSelectedJob(null);
                return {
                  success: true,
                  message: "Local jobs database has been cleared."
                };
              } else {
                return { success: false, error: "Failed to clear database on backend" };
              }
            } catch (err) {
              return { success: false, error: err.message };
            }
          }
        }, { signal });

      } catch (err) {
        console.warn("Failed to register WebMCP tool:", err);
      }

      return () => {
        controller.abort();
      };
    }
  }, [sources]); // Recalculate only if sources list changes

  // WebMCP Interaction event hook handlers
  useEffect(() => {
    const handleActivated = (e) => {
      const toolName = e.toolName || e.detail?.toolName || "WebMCP Tool";
      setActiveAgentTool(toolName);
    };
    const handleCancel = () => {
      setActiveAgentTool(null);
    };

    window.addEventListener('toolactivated', handleActivated);
    window.addEventListener('toolcancel', handleCancel);
    return () => {
      window.removeEventListener('toolactivated', handleActivated);
      window.removeEventListener('toolcancel', handleCancel);
    };
  }, []);

  // Format log strings for terminal
  const formatLog = (logText) => {
    if (logText.startsWith('[Tool Call]')) {
      return <span className="log-tool">{logText}</span>;
    } else if (logText.startsWith('[Tool Complete]') || logText.startsWith('[Backend]')) {
      return <span className="log-system">{logText}</span>;
    } else if (logText.startsWith('[Backend Error]') || logText.startsWith('Error:')) {
      return <span className="log-error">{logText}</span>;
    } else {
      return <span className="log-thought">{logText}</span>;
    }
  };

  return (
    <div className="app-container" id="app-root-container">
      {/* Header */}
      <header className="app-header" id="dashboard-header">
        <div className="logo-section">
          <Briefcase className="logo-icon" size={28} />
          <div>
            <span className="logo-text">AI C2C Job Finder</span>
            <span className="logo-badge">Gemini-Powered</span>
          </div>
        </div>

        {activeAgentTool && (
          <div className="logo-badge" style={{ background: 'linear-gradient(135deg, var(--accent), var(--danger))', display: 'flex', alignItems: 'center', gap: '0.4rem', animation: 'pulse-slow 1s infinite' }}>
            <Cpu size={12} />
            <span>Agent Active: {activeAgentTool}</span>
          </div>
        )}

        <div className="header-actions">
          <button 
            id="sync-db-btn"
            className={`btn ${status.status === 'running' ? 'disabled' : ''}`}
            onClick={fetchJobs}
            disabled={status.status === 'running'}
            title="Refresh current local database"
          >
            <RefreshCw size={16} className={status.status === 'running' ? 'spin' : ''} />
            Sync Database
          </button>
        </div>
      </header>

      {/* Stats Cards */}
      <section className="stats-grid" id="stats-summary-panel" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
        <div className="stat-card" id="stat-card-total">
          <div className="stat-icon-wrapper primary">
            <Layers size={22} />
          </div>
          <div className="stat-info">
            <span className="stat-label">Total Jobs Found</span>
            <span className="stat-value">{stats.total}</span>
          </div>
        </div>
        <div className="stat-card" id="stat-card-confirmed">
          <div className="stat-icon-wrapper success">
            <CheckCircle2 size={22} />
          </div>
          <div className="stat-info">
            <span className="stat-label">Confirmed C2C</span>
            <span className="stat-value">{stats.confirmedC2C}</span>
          </div>
        </div>
        <div className="stat-card" id="stat-card-remote">
          <div className="stat-icon-wrapper warning">
            <Sparkles size={22} />
          </div>
          <div className="stat-info">
            <span className="stat-label">Remote Roles</span>
            <span className="stat-value">{stats.remote}</span>
          </div>
        </div>
        <div className="stat-card" id="stat-card-applied">
          <div className="stat-icon-wrapper success" style={{ color: 'var(--success)', backgroundColor: 'var(--success-glow)', borderColor: 'rgba(16, 185, 129, 0.2)' }}>
            <CheckCircle2 size={22} />
          </div>
          <div className="stat-info">
            <span className="stat-label">Applied Jobs</span>
            <span className="stat-value">{stats.applied}</span>
          </div>
        </div>
      </section>

      {/* Main Grid: Controls + Content */}
      <div className={`dashboard-grid ${status.status === 'running' || logs.length > 0 ? 'with-console' : ''}`}>
        
        {/* Controls Sidebar */}
        <aside className="sidebar-panel" id="agent-controls-panel">
          <div className="sidebar-title">
            <Sparkles size={18} className="text-primary" />
            Agent Controls
          </div>
          
          {/* Declarative WebMCP Form for Triggering Backend Scraper Agent */}
          <form 
            id="agent-run-form"
            onSubmit={handlePullJobs} 
            className="control-group"
            toolname="trigger_agent_run_form"
            tooldescription="Trigger a backend web scraper agent run to search for C2C job postings matching a specified search query"
            toolautosubmit
          >
            <label htmlFor="agent-query-input" className="control-label">Search Target</label>
            <input 
              id="agent-query-input"
              name="query"
              type="text" 
              className="input-text" 
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. C2C Data Engineer"
              disabled={status.status === 'running'}
              toolparamdescription="The search query for C2C jobs, for example 'C2C Data Engineer' or 'Corp-to-Corp Data Architect'"
              required
            />
            <button 
              id="trigger-agent-btn"
              type="submit" 
              className="btn btn-primary"
              disabled={status.status === 'running' || !query.trim()}
              style={{ marginTop: '0.5rem' }}
            >
              <RefreshCw size={16} className={status.status === 'running' ? 'spin' : ''} />
              {status.status === 'running' ? 'Agent Pulling...' : 'Trigger Agent Run'}
            </button>
          </form>

          {/* Active Logs Console */}
          {(status.status === 'running' || logs.length > 0) && (
            <div className="agent-console-panel" id="active-logs-console">
              <div className="console-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <Terminal size={14} />
                  Agent Thought Console
                </div>
                {status.status === 'running' && <span className="logo-badge" style={{ animation: 'pulse-slow 1s infinite' }}>live</span>}
              </div>
              <div className="console-body">
                {logs.map((log, index) => (
                  <span key={index} className="console-log-text">
                    {formatLog(log)}
                  </span>
                ))}
                {status.status === 'running' && (
                  <span className="console-log-text log-system" style={{ borderLeft: '2px solid var(--accent)', paddingLeft: '4px', animation: 'pulse-slow 1s infinite' }}>
                    Agent is processing... ▋
                  </span>
                )}
                <div ref={consoleEndRef} />
              </div>
            </div>
          )}

          {/* Filters Section */}
          <div className="sidebar-panel" id="filters-panel" style={{ border: 'none', padding: '0', boxShadow: 'none' }}>
            <div className="control-group" style={{ borderTop: '1px solid var(--border)', paddingTop: '1.25rem' }}>
              <span className="control-label">Application Status</span>
              <div className="filter-pills">
                {['All', 'Applied', 'Not Applied'].map((appliedOption) => (
                  <button
                    key={appliedOption}
                    id={`filter-applied-${appliedOption.replace(/\s+/g, '-').toLowerCase()}`}
                    className={`filter-pill ${selectedApplied === appliedOption ? 'active' : ''}`}
                    onClick={() => setSelectedApplied(appliedOption)}
                  >
                    {appliedOption}
                  </button>
                ))}
              </div>
            </div>

            <div className="control-group" style={{ marginTop: '1rem' }}>
              <span className="control-label">C2C Viability</span>
              <div className="filter-pills">
                {['All', 'Confirmed C2C', 'Likely C2C', 'Not Specified'].map((c2cOption) => (
                  <button
                    key={c2cOption}
                    id={`filter-c2c-${c2cOption.replace(/\s+/g, '-').toLowerCase()}`}
                    className={`filter-pill ${selectedC2C === c2cOption ? 'active' : ''}`}
                    onClick={() => setSelectedC2C(c2cOption)}
                  >
                    {c2cOption}
                  </button>
                ))}
              </div>
            </div>

            <div className="control-group" style={{ marginTop: '1rem' }}>
              <span className="control-label">Location Type</span>
              <div className="filter-pills">
                {['All', 'Remote', 'Onsite/Hybrid'].map((locOption) => (
                  <button
                    key={locOption}
                    id={`filter-location-${locOption.replace(/\//g, '-').toLowerCase()}`}
                    className={`filter-pill ${selectedLocation === locOption ? 'active' : ''}`}
                    onClick={() => setSelectedLocation(locOption)}
                  >
                    {locOption}
                  </button>
                ))}
              </div>
            </div>

            {sources.length > 1 && (
              <div className="control-group" style={{ marginTop: '1rem' }}>
                <span className="control-label">Job Source</span>
                <div className="filter-pills">
                  {sources.map((srcOption) => (
                    <button
                      key={srcOption}
                      id={`filter-source-${srcOption.replace(/\s+/g, '-').toLowerCase()}`}
                      className={`filter-pill ${selectedSource === srcOption ? 'active' : ''}`}
                      onClick={() => setSelectedSource(srcOption)}
                    >
                      {srcOption}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </aside>

        {/* Content Area */}
        <main className="main-content-panel" id="jobs-database-panel">
          <div className="panel-header">
            <h2 className="panel-title">Jobs Database ({filteredJobs.length} visible)</h2>
            
            {/* Declarative WebMCP Form for Filtering/Searching local job results */}
            <form 
              id="filter-form"
              onSubmit={handleFilterFormSubmit} 
              style={{ position: 'relative', width: '260px' }}
              toolname="search_jobs_form"
              tooldescription="Search and filter the currently loaded jobs in the local dashboard UI"
              toolautosubmit
            >
              <input 
                id="local-search-input"
                name="searchTerm"
                type="text" 
                className="input-text" 
                style={{ paddingLeft: '2.25rem' }}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search jobs..."
                toolparamdescription="Text query to search within titles, companies, or requirements"
              />
              <Search 
                size={16} 
                className="text-muted" 
                style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)' }} 
              />
            </form>
          </div>

          {/* Job List Container */}
          {filteredJobs.length > 0 ? (
            <div className="jobs-grid" id="jobs-grid">
              {filteredJobs.map((job, idx) => (
                <div 
                  key={idx} 
                  id={`job-card-${idx}`}
                  className={`job-card ${job.applied ? 'applied' : ''}`}
                  onClick={() => setSelectedJob(job)}
                >
                  <div className="job-card-header">
                    <div style={{ flex: 1 }}>
                      <h3 className="job-title">{job.title}</h3>
                      <span className="job-company">{job.company}</span>
                    </div>
                    <button
                      className={`btn-toggle-applied ${job.applied ? 'applied' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleToggleApplied(job.id, job.applied);
                      }}
                      title={job.applied ? "Mark as Not Applied" : "Mark as Applied"}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: job.applied ? 'var(--success)' : 'var(--text-muted)',
                        cursor: 'pointer',
                        padding: '0.25rem',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        marginTop: '-0.25rem',
                        marginRight: '-0.25rem'
                      }}
                    >
                      <CheckCircle2 size={18} fill={job.applied ? 'var(--success-glow)' : 'transparent'} />
                    </button>
                  </div>

                  <div className="job-meta-row">
                    <span className="job-meta-item">
                      <MapPin size={12} />
                      {job.location}
                    </span>
                    <span className="job-meta-item">
                      <Calendar size={12} />
                      {job.date_posted}
                    </span>
                  </div>

                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <span className={`badge ${
                      job.c2c_viability === 'Confirmed C2C' ? 'badge-c2c-confirmed' :
                      job.c2c_viability === 'Likely C2C' ? 'badge-c2c-likely' : 'badge-c2c-unknown'
                    }`}>
                      {job.c2c_viability}
                    </span>
                    <span className="badge badge-source">
                      {job.source}
                    </span>
                    {job.applied && (
                      <span className="badge" style={{ backgroundColor: 'var(--success-glow)', color: 'var(--success)', border: '1px solid rgba(16, 185, 129, 0.2)', textTransform: 'none', display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                        <CheckCircle2 size={10} />
                        Applied
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
              ))}
            </div>
          ) : (
            <div className="empty-state" id="empty-state-view">
              <Briefcase size={48} className="empty-state-icon" />
              <h3 className="empty-state-title">No Jobs Found</h3>
              <p className="empty-state-desc">
                {jobs.length === 0 
                  ? "The local database is empty. Provide a search query and trigger the Antigravity Agent to scour the web for Corp-to-Corp positions."
                  : "No jobs in the local database match your active search terms and filters."
                }
              </p>
              {jobs.length === 0 && (
                <button 
                  id="empty-state-default-btn"
                  className="btn btn-primary"
                  onClick={() => setQuery('C2C Data Engineer')}
                  disabled={status.status === 'running'}
                  style={{ marginTop: '0.5rem' }}
                >
                  Use Default Query
                </button>
              )}
            </div>
          )}
        </main>
      </div>

      {/* Accessible Native HTML5 Dialog for Job details */}
      <dialog 
        ref={dialogRef} 
        className="job-details-dialog"
        onClose={() => setSelectedJob(null)}
        id="job-details-dialog"
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
                <span className={`badge ${
                  selectedJob.c2c_viability === 'Confirmed C2C' ? 'badge-c2c-confirmed' :
                  selectedJob.c2c_viability === 'Likely C2C' ? 'badge-c2c-likely' : 'badge-c2c-unknown'
                }`}>
                  {selectedJob.c2c_viability}
                </span>
                <span className="badge badge-source">
                  {selectedJob.source}
                </span>
                <span className="badge badge-c2c-unknown" style={{ textTransform: 'none', display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                  <Calendar size={11} />
                  Found: {selectedJob.date_posted}
                </span>
                {selectedJob.applied && (
                  <span className="badge" style={{ backgroundColor: 'var(--success-glow)', color: 'var(--success)', border: '1px solid rgba(16, 185, 129, 0.2)', textTransform: 'none', display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                    <CheckCircle2 size={11} />
                    Applied
                  </span>
                )}
              </div>
            </div>

            <div className="modal-body">
              {/* Contact Info (if available) */}
              {(selectedJob.contact_email || selectedJob.contact_phone || selectedJob.url) && (
                <div className="modal-section" id="modal-section-contact">
                  <span className="modal-section-title">Application & Contact</span>
                  <div className="contact-row" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem' }}>
                      {selectedJob.contact_email && (
                        <div className="contact-item">
                          <Mail size={14} />
                          Email: <a href={`mailto:${selectedJob.contact_email}`}>{selectedJob.contact_email}</a>
                        </div>
                      )}
                      {selectedJob.contact_phone && (
                        <div className="contact-item">
                          <Phone size={14} />
                          Phone: {selectedJob.contact_phone}
                        </div>
                      )}
                      {selectedJob.url && (
                        <div className="contact-item">
                          <LinkIcon size={14} />
                          <a href={selectedJob.url} target="_blank" rel="noopener noreferrer" style={{ display: 'flex', alignItems: 'center', gap: '0.2rem' }}>
                            View Original Posting
                            <Sparkles size={11} />
                          </a>
                        </div>
                      )}
                    </div>

                    <div style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                      {selectedJob.url && (
                        <a 
                          href={selectedJob.url} 
                          target="_blank" 
                          rel="noopener noreferrer" 
                          className="btn btn-primary"
                          onClick={() => {
                            if (!selectedJob.applied) {
                              handleToggleApplied(selectedJob.id, selectedJob.applied);
                            }
                          }}
                          style={{ textDecoration: 'none' }}
                        >
                          <LinkIcon size={16} />
                          Apply Now & Mark Applied
                        </a>
                      )}
                      <button
                        className={`btn ${selectedJob.applied ? 'btn-success-active' : ''}`}
                        onClick={() => handleToggleApplied(selectedJob.id, selectedJob.applied)}
                      >
                        <CheckCircle2 size={16} fill={selectedJob.applied ? 'var(--success-glow)' : 'transparent'} />
                        {selectedJob.applied ? "Applied (Click to Undo)" : "Mark as Applied"}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Description */}
              <div className="modal-section" id="modal-section-description">
                <span className="modal-section-title">Job Description & C2C Analysis</span>
                <p className="modal-desc-text">{selectedJob.description}</p>
              </div>

              {/* Requirements */}
              <div className="modal-section" id="modal-section-requirements">
                <span className="modal-section-title">Required Technical Stack</span>
                <div className="job-requirements" style={{ gap: '0.5rem', marginTop: '0.25rem' }}>
                  {selectedJob.key_requirements.map((req, idx) => (
                    <span 
                      key={idx} 
                      className="requirement-tag"
                      style={{ padding: '0.3rem 0.65rem', fontSize: '0.8rem' }}
                    >
                      {req}
                    </span>
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

export default App;
