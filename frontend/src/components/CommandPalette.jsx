import React, { useState, useEffect } from 'react';
import { X, Search } from 'lucide-react';

export default function CommandPalette({ isOpen, onClose, onNavigate }) {
  const [input, setInput] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);

  const commands = [
    { id: 'dashboard', label: 'Dashboard', category: 'Navigation', shortcut: 'Ctrl+Shift+D', action: '/' },
    { id: 'analytics', label: 'Analytics', category: 'Navigation', shortcut: 'Ctrl+Shift+A', action: '/analytics' },
    { id: 'settings', label: 'Settings', category: 'Navigation', shortcut: 'Ctrl+Shift+S', action: '/settings' },
    { id: 'profile', label: 'Profile', category: 'Navigation', shortcut: 'Ctrl+Shift+P', action: '/profile' },
    { id: 'resume', label: 'Resume Optimizer', category: 'Navigation', shortcut: 'Ctrl+Shift+R', action: '/resume/optimizer' },
    { id: 'new-search', label: 'New Search', category: 'Action', shortcut: 'Ctrl+N', action: 'new-search' },
    { id: 'bookmarks', label: 'View Bookmarks', category: 'Action', shortcut: 'Ctrl+B', action: 'bookmarks' },
    { id: 'applications', label: 'View Applications', category: 'Action', shortcut: 'Ctrl+L', action: 'applications' },
    { id: 'help', label: 'Help & Shortcuts', category: 'Help', shortcut: '?', action: 'help' },
  ];

  const filteredCommands = commands.filter(cmd =>
    cmd.label.toLowerCase().includes(input.toLowerCase()) ||
    cmd.category.toLowerCase().includes(input.toLowerCase())
  );

  useEffect(() => {
    setSelectedIndex(0);
  }, [input]);

  const showHelp = () => {
    alert(
      `Keyboard Shortcuts:

Ctrl+K or Cmd+K - Open command palette
Ctrl+N - New search
Ctrl+B - Bookmarks
Ctrl+L - Applications
Ctrl+Shift+D - Dashboard
Ctrl+Shift+A - Analytics
Ctrl+Shift+S - Settings
? - Show help
ESC - Close palette`
    );
  };

  const executeCommand = (cmd) => {
    if (cmd.action.startsWith('/')) {
      onNavigate(cmd.action);
    } else if (cmd.action === 'new-search') {
      // Trigger new search in parent
      onNavigate('new-search');
    } else if (cmd.action === 'help') {
      showHelp();
    }
    onClose();
  };

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % filteredCommands.length);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (filteredCommands.length > 0) {
          executeCommand(filteredCommands[selectedIndex]);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, filteredCommands, selectedIndex]);

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="command-palette-backdrop"
        onClick={onClose}
      />

      {/* Palette */}
      <div className="command-palette">
        {/* Search Input */}
        <div className="command-palette-input-wrapper">
          <Search size={20} className="input-icon" />
          <input
            type="text"
            placeholder="Type a command or search..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            autoFocus
            className="command-palette-input"
          />
          <button className="palette-close-btn" onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        {/* Commands List */}
        <div className="command-palette-list">
          {filteredCommands.length === 0 ? (
            <div className="command-empty">
              <p>No commands found for "{input}"</p>
            </div>
          ) : (
            <>
              {filteredCommands.map((cmd, index) => (
                <div key={cmd.id}>
                  {index === 0 || filteredCommands[index - 1].category !== cmd.category ? (
                    <div className="command-category">{cmd.category}</div>
                  ) : null}
                  <button
                    className={`command-item ${index === selectedIndex ? 'active' : ''}`}
                    onClick={() => executeCommand(cmd)}
                    onMouseEnter={() => setSelectedIndex(index)}
                  >
                    <div className="command-item-label">
                      <span className="command-title">{cmd.label}</span>
                    </div>
                    <span className="command-shortcut">{cmd.shortcut}</span>
                  </button>
                </div>
              ))}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="command-palette-footer">
          <span>↑↓ Navigate</span>
          <span>↵ Execute</span>
          <span>Esc Close</span>
        </div>
      </div>
    </>
  );
}
