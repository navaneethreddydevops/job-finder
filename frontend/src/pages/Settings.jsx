import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { apiFetch, apiUrl } from '../auth';
import { AlertCircle, Trash2, Copy, Eye, EyeOff } from 'lucide-react';

export default function Settings() {
  const [activeTab, setActiveTab] = useState('email');
  const [emailPrefs, setEmailPrefs] = useState(null);
  const [apiKeys, setApiKeys] = useState([]);
  const [webhooks, setWebhooks] = useState([]);
  const [integrations, setIntegrations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      const [emailRes, keysRes, webhooksRes, intRes] = await Promise.all([
        apiFetch(apiUrl('/api/email-preferences')),
        apiFetch(apiUrl('/api/api-keys')),
        apiFetch(apiUrl('/api/webhooks')),
        apiFetch(apiUrl('/api/integrations')),
      ]);

      if (emailRes.ok) setEmailPrefs(await emailRes.json());
      if (keysRes.ok) setApiKeys((await keysRes.json()).keys);
      if (webhooksRes.ok) setWebhooks((await webhooksRes.json()).webhooks);
      if (intRes.ok) setIntegrations((await intRes.json()).integrations);
      setLoading(false);
    } catch (error) {
      console.error('Failed to load settings:', error);
      setLoading(false);
    }
  };

  const handleEmailPrefsChange = async (updates) => {
    setSaving(true);
    try {
      const res = await apiFetch(apiUrl('/api/email-preferences'), {
        method: 'PATCH',
        body: JSON.stringify(updates),
        headers: { 'Content-Type': 'application/json' },
      });

      if (res.ok) {
        setSuccessMsg('Email preferences saved');
        setTimeout(() => setSuccessMsg(''), 3000);
        fetchSettings();
      }
    } catch (error) {
      console.error('Failed to save email preferences:', error);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="settings-container"><p>Loading settings...</p></div>;
  }

  return (
    <div className="settings-container">
      <Link to="/" className="back-link" style={{ display: 'inline-block', marginBottom: 16, color: 'var(--primary)', textDecoration: 'none', fontWeight: 500 }}>← Back to Dashboard</Link>
      <h1>Settings & Preferences</h1>

      {successMsg && <div className="toast success">{successMsg}</div>}

      {/* Tabs */}
      <div className="settings-tabs">
        <button
          className={`tab ${activeTab === 'email' ? 'active' : ''}`}
          onClick={() => setActiveTab('email')}
        >
          Email
        </button>
        <button
          className={`tab ${activeTab === 'api' ? 'active' : ''}`}
          onClick={() => setActiveTab('api')}
        >
          API Keys
        </button>
        <button
          className={`tab ${activeTab === 'webhooks' ? 'active' : ''}`}
          onClick={() => setActiveTab('webhooks')}
        >
          Webhooks
        </button>
        <button
          className={`tab ${activeTab === 'integrations' ? 'active' : ''}`}
          onClick={() => setActiveTab('integrations')}
        >
          Integrations
        </button>
      </div>

      {/* Email Tab */}
      {activeTab === 'email' && emailPrefs && (
        <div className="tab-content">
          <h2>Email Digest Settings</h2>

          <div className="settings-form">
            <label>
              <input
                type="checkbox"
                checked={emailPrefs.digest_enabled}
                onChange={(e) =>
                  handleEmailPrefsChange({ digest_enabled: e.target.checked })
                }
              />
              Enable Email Digest
            </label>

            <div className="form-group">
              <label htmlFor="digest-freq">Digest Frequency</label>
              <select
                id="digest-freq"
                value={emailPrefs.digest_frequency}
                onChange={(e) =>
                  handleEmailPrefsChange({ digest_frequency: e.target.value })
                }
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </select>
            </div>

            <label>
              <input
                type="checkbox"
                checked={emailPrefs.receive_new_jobs}
                onChange={(e) =>
                  handleEmailPrefsChange({ receive_new_jobs: e.target.checked })
                }
              />
              Receive Notifications for New Jobs
            </label>

            <div className="info-box">
              <AlertCircle size={16} />
              <p>Unsubscribe Token: {emailPrefs.unsubscribe_token}</p>
            </div>
          </div>
        </div>
      )}

      {/* API Keys Tab */}
      {activeTab === 'api' && (
        <div className="tab-content">
          <h2>API Keys</h2>

          <button
            className="btn btn-primary"
            onClick={() => {
              const name = prompt('Enter API key name:');
              if (name) createApiKey(name);
            }}
          >
            Create API Key
          </button>

          <div className="items-list">
            {apiKeys.length === 0 ? (
              <p className="empty-state">No API keys yet. Create one to get started.</p>
            ) : (
              apiKeys.map((key) => (
                <div key={key.id} className="list-item">
                  <div>
                    <h3>{key.name}</h3>
                    <p className="mono">{key.key}</p>
                    <p className="detail">
                      Created: {new Date(key.created_at).toLocaleDateString()}
                      {key.last_used_at && (
                        <> • Last used: {new Date(key.last_used_at).toLocaleDateString()}</>
                      )}
                    </p>
                  </div>
                  <div className="actions">
                    <button
                      className="btn-icon"
                      onClick={() => copyToClipboard(key.key)}
                      title="Copy key"
                    >
                      <Copy size={16} />
                    </button>
                    <button
                      className="btn-icon btn-danger"
                      onClick={() => deleteApiKey(key.id)}
                      title="Delete key"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Webhooks Tab */}
      {activeTab === 'webhooks' && (
        <div className="tab-content">
          <h2>Webhooks</h2>

          <button
            className="btn btn-primary"
            onClick={() => {
              const url = prompt('Enter webhook URL:');
              if (url) createWebhook(url);
            }}
          >
            Add Webhook
          </button>

          <div className="items-list">
            {webhooks.length === 0 ? (
              <p className="empty-state">No webhooks configured.</p>
            ) : (
              webhooks.map((webhook) => (
                <div key={webhook.id} className="list-item">
                  <div>
                    <h3>{webhook.url}</h3>
                    <p className="detail">
                      Events: {webhook.events.join(', ')} •
                      {webhook.is_active ? ' Active' : ' Inactive'}
                    </p>
                  </div>
                  <div className="actions">
                    <button
                      className="btn btn-small"
                      onClick={() => testWebhook(webhook.id)}
                    >
                      Test
                    </button>
                    <button
                      className="btn-icon btn-danger"
                      onClick={() => deleteWebhook(webhook.id)}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Integrations Tab */}
      {activeTab === 'integrations' && (
        <div className="tab-content">
          <h2>Slack & Discord Integrations</h2>

          <button
            className="btn btn-primary"
            onClick={() => {
              const type = prompt('Enter integration type (slack or discord):');
              if (type === 'slack' || type === 'discord') {
                const url = prompt('Enter webhook URL:');
                if (url) createIntegration(type, url);
              }
            }}
          >
            Add Integration
          </button>

          <div className="items-list">
            {integrations.length === 0 ? (
              <p className="empty-state">No integrations configured.</p>
            ) : (
              integrations.map((int) => (
                <div key={int.id} className="list-item">
                  <div>
                    <h3>{int.type.toUpperCase()}</h3>
                    <p className="detail">
                      Channel: {int.channel_name || 'N/A'} •
                      {int.is_active ? ' Active' : ' Inactive'}
                      {int.filter_min_score && (
                        <> • Min Score: {int.filter_min_score}</>
                      )}
                    </p>
                  </div>
                  <div className="actions">
                    <button
                      className="btn btn-small"
                      onClick={() => testIntegration(int.id)}
                    >
                      Test
                    </button>
                    <button
                      className="btn-icon btn-danger"
                      onClick={() => deleteIntegration(int.id)}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );

  async function createApiKey(name) {
    try {
      const res = await apiFetch(apiUrl('/api/api-keys'), {
        method: 'POST',
        body: JSON.stringify({ name, rate_limit: 1000 }),
        headers: { 'Content-Type': 'application/json' },
      });
      if (res.ok) fetchSettings();
    } catch (error) {
      console.error('Failed to create API key:', error);
    }
  }

  async function deleteApiKey(keyId) {
    if (!confirm('Delete this API key?')) return;
    try {
      const res = await apiFetch(apiUrl(`/api/api-keys/${keyId}`), { method: 'DELETE' });
      if (res.ok) fetchSettings();
    } catch (error) {
      console.error('Failed to delete API key:', error);
    }
  }

  async function createWebhook(url) {
    try {
      const res = await apiFetch(apiUrl('/api/webhooks'), {
        method: 'POST',
        body: JSON.stringify({ url, events: ['job.new'] }),
        headers: { 'Content-Type': 'application/json' },
      });
      if (res.ok) fetchSettings();
    } catch (error) {
      console.error('Failed to create webhook:', error);
    }
  }

  async function deleteWebhook(webhookId) {
    if (!confirm('Delete this webhook?')) return;
    try {
      const res = await apiFetch(apiUrl(`/api/webhooks/${webhookId}`), { method: 'DELETE' });
      if (res.ok) fetchSettings();
    } catch (error) {
      console.error('Failed to delete webhook:', error);
    }
  }

  async function testWebhook(webhookId) {
    try {
      const res = await apiFetch(apiUrl(`/api/webhooks/${webhookId}/test`), { method: 'POST' });
      if (res.ok) alert('Webhook test successful!');
    } catch (error) {
      alert('Webhook test failed');
    }
  }

  async function createIntegration(type, url) {
    try {
      const res = await apiFetch(apiUrl('/api/integrations'), {
        method: 'POST',
        body: JSON.stringify({ type, webhook_url: url, channel_name: '' }),
        headers: { 'Content-Type': 'application/json' },
      });
      if (res.ok) fetchSettings();
    } catch (error) {
      console.error('Failed to create integration:', error);
    }
  }

  async function deleteIntegration(intId) {
    if (!confirm('Delete this integration?')) return;
    try {
      const res = await apiFetch(apiUrl(`/api/integrations/${intId}`), { method: 'DELETE' });
      if (res.ok) fetchSettings();
    } catch (error) {
      console.error('Failed to delete integration:', error);
    }
  }

  async function testIntegration(intId) {
    try {
      const res = await apiFetch(apiUrl(`/api/integrations/${intId}/test`), { method: 'POST' });
      if (res.ok) alert('Integration test successful!');
    } catch (error) {
      alert('Integration test failed');
    }
  }

  function copyToClipboard(text) {
    navigator.clipboard.writeText(text);
    alert('Copied to clipboard!');
  }
}
