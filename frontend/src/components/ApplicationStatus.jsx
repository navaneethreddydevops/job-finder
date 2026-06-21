import { ChevronDown, AlertCircle } from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import { apiFetch } from '../auth';

const APPLICATION_STATUSES = [
  { value: 'draft', label: 'Draft', color: '#9b8c7e' },
  { value: 'applied', label: 'Applied', color: '#2383e2' },
  { value: 'interviewing', label: 'Interviewing', color: '#a965c7' },
  { value: 'offer', label: 'Offer', color: '#34a853' },
  { value: 'rejected', label: 'Rejected', color: '#ea4335' },
];

export default function ApplicationStatus({ jobId, applicationId, currentStatus, onStatusChange, onError }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const dropdownRef = useRef(null);

  const statusObj = APPLICATION_STATUSES.find(s => s.value === currentStatus) || APPLICATION_STATUSES[0];

  const handleStatusUpdate = async (newStatus) => {
    if (newStatus === currentStatus) {
      setOpen(false);
      return;
    }

    setLoading(true);
    try {
      let appId = applicationId;

      // If no application exists yet, create one
      if (!appId) {
        const createResp = await apiFetch('/api/applications', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            job_id: jobId,
            status: newStatus,
          }),
        });

        if (!createResp.ok) {
          onError?.('Failed to create application');
          setLoading(false);
          return;
        }

        const createData = await createResp.json();
        appId = createData.id;
      } else {
        // Update existing application
        const updateResp = await apiFetch(`/api/applications/${appId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            status: newStatus,
          }),
        });

        if (!updateResp.ok) {
          onError?.('Failed to update application status');
          setLoading(false);
          return;
        }
      }

      onStatusChange?.(newStatus, appId);
      setOpen(false);
    } catch (err) {
      console.error('Error updating status:', err);
      onError?.('Error updating status');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false);
      }
    };

    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [open]);

  return (
    <div
      ref={dropdownRef}
      className="application-status-dropdown"
      style={{ position: 'relative', display: 'inline-block' }}
    >
      <button
        onClick={() => setOpen(!open)}
        disabled={loading}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '6px 10px',
          backgroundColor: `${statusObj.color}20`,
          border: `1px solid ${statusObj.color}`,
          borderRadius: '6px',
          color: statusObj.color,
          fontSize: '0.85rem',
          fontWeight: '500',
          cursor: 'pointer',
          whiteSpace: 'nowrap',
          transition: 'all 0.15s',
          opacity: loading ? 0.6 : 1,
        }}
        title={`Status: ${statusObj.label}`}
      >
        {statusObj.label}
        <ChevronDown size={14} />
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: '4px',
            backgroundColor: '#ffffff',
            border: '1px solid rgba(55,53,47,0.15)',
            borderRadius: '8px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
            zIndex: 100,
            minWidth: '140px',
            overflow: 'hidden',
          }}
        >
          {APPLICATION_STATUSES.map((status) => (
            <button
              key={status.value}
              onClick={() => handleStatusUpdate(status.value)}
              disabled={loading}
              style={{
                display: 'block',
                width: '100%',
                padding: '10px 12px',
                textAlign: 'left',
                backgroundColor: currentStatus === status.value ? `${status.color}15` : 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: status.color,
                fontSize: '0.9rem',
                fontWeight: currentStatus === status.value ? '600' : '400',
                transition: 'background-color 0.1s',
              }}
              onMouseEnter={(e) => {
                e.target.style.backgroundColor = `${status.color}10`;
              }}
              onMouseLeave={(e) => {
                e.target.style.backgroundColor = currentStatus === status.value ? `${status.color}15` : 'transparent';
              }}
            >
              <span
                style={{
                  display: 'inline-block',
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  backgroundColor: status.color,
                  marginRight: '8px',
                  verticalAlign: 'middle',
                }}
              />
              {status.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
