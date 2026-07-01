import { useState, useRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { User, LogOut, ChevronDown, Settings, Moon, Sun, SlidersHorizontal } from 'lucide-react';
import { useAuth } from '../auth.jsx';
import { useDarkMode } from '../hooks/useDarkMode';

/**
 * Rightmost account control: a single user tab that opens a dropdown
 * containing the profile link and logout action.
 */
export default function UserMenu() {
  const { user, logout } = useAuth();
  const [isDark, setIsDark] = useDarkMode();
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  const displayName = user?.full_name || user?.email || 'Account';
  const initial = (displayName || '?').trim().charAt(0).toUpperCase();

  useEffect(() => {
    if (!open) return;
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="user-menu" ref={menuRef} id="user-menu">
      <button
        type="button"
        className={`user-menu-trigger ${open ? 'open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        id="user-menu-trigger"
        title="Account"
      >
        <span className="user-avatar">{initial}</span>
        <span className="user-menu-name">{displayName}</span>
        <ChevronDown size={15} className={`user-menu-caret ${open ? 'open' : ''}`} />
      </button>

      {open && (
        <div
          className="user-menu-dropdown"
          role="menu"
          id="user-menu-dropdown"
          onKeyDown={(e) => {
            const items = menuRef.current?.querySelectorAll('[role="menuitem"]');
            if (!items) return;
            const idx = Array.from(items).indexOf(document.activeElement);
            if (e.key === 'ArrowDown') { e.preventDefault(); items[(idx + 1) % items.length]?.focus(); }
            if (e.key === 'ArrowUp') { e.preventDefault(); items[(idx - 1 + items.length) % items.length]?.focus(); }
            if (e.key === 'Tab') setOpen(false);
          }}
        >
          <div className="user-menu-head">
            <span className="user-avatar lg">{initial}</span>
            <div className="user-menu-head-text">
              <span className="user-menu-head-name">{user?.full_name || 'Account'}</span>
              {user?.email && <span className="user-menu-head-email">{user.email}</span>}
            </div>
          </div>
          <div className="user-menu-divider" />
          <Link
            to="/profile"
            className="user-menu-item"
            role="menuitem"
            onClick={() => setOpen(false)}
            id="user-menu-profile"
          >
            <User size={16} />
            Account & Profile
          </Link>
          <Link
            to="/settings"
            className="user-menu-item"
            role="menuitem"
            onClick={() => setOpen(false)}
            id="user-menu-settings"
          >
            <SlidersHorizontal size={16} />
            Settings
          </Link>
          <button
            type="button"
            className="user-menu-item"
            role="menuitem"
            onClick={() => setIsDark(!isDark)}
            id="user-menu-theme"
          >
            {isDark ? <Sun size={16} /> : <Moon size={16} />}
            {isDark ? 'Light mode' : 'Dark mode'}
          </button>
          <div className="user-menu-divider" />
          <button
            type="button"
            className="user-menu-item danger"
            role="menuitem"
            onClick={() => { setOpen(false); logout(); }}
            id="user-menu-logout"
          >
            <LogOut size={16} />
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
