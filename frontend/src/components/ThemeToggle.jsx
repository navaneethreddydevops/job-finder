import { Moon, Sun } from 'lucide-react';
import { useDarkMode } from '../hooks/useDarkMode';

export default function ThemeToggle() {
  const [isDark, setIsDark] = useDarkMode();

  return (
    <button
      className="btn-theme-toggle"
      onClick={() => setIsDark(!isDark)}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      style={{
        background: 'transparent',
        border: '1px solid var(--border)',
        color: 'var(--text-secondary)',
        cursor: 'pointer',
        padding: '0.5rem 0.65rem',
        borderRadius: 'var(--radius-md)',
        display: 'flex',
        alignItems: 'center',
        transition: 'all 0.2s',
      }}
      onMouseEnter={(e) => {
        e.target.style.backgroundColor = 'var(--bg-elevated)';
        e.target.style.borderColor = 'var(--primary)';
        e.target.style.color = 'var(--text-primary)';
      }}
      onMouseLeave={(e) => {
        e.target.style.backgroundColor = 'transparent';
        e.target.style.borderColor = 'var(--border)';
        e.target.style.color = 'var(--text-secondary)';
      }}
    >
      {isDark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}
