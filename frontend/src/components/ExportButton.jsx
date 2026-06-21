import { Download } from 'lucide-react';
import { useState } from 'react';
import { apiFetch } from '../auth';

export default function ExportButton({ format = 'csv' }) {
  const [loading, setLoading] = useState(false);

  const handleExport = async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(`/api/jobs/export?format=${format}`);
      if (!resp.ok) {
        throw new Error('Export failed');
      }

      const data = await resp.json();

      if (format === 'csv') {
        // Download CSV
        const blob = new Blob([data.csv], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = data.filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
      } else if (format === 'json') {
        // Download JSON
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `jobs_export_${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.error('Export error:', err);
      alert('Failed to export jobs');
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={handleExport}
      disabled={loading}
      title={`Export as ${format.toUpperCase()}`}
      className="btn btn-secondary"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        padding: '0.5rem 1rem',
        opacity: loading ? 0.6 : 1,
      }}
    >
      <Download size={16} />
      {loading ? 'Exporting...' : `Export ${format.toUpperCase()}`}
    </button>
  );
}
