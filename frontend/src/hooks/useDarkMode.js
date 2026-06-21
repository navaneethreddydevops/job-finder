import { useState, useEffect } from 'react';

export function useDarkMode() {
  const [isDark, setIsDark] = useState(() => {
    // Check localStorage first
    const saved = localStorage.getItem('jf_theme');
    if (saved) return saved === 'dark';

    // Then check system preference
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (isDark) {
      root.setAttribute('data-theme', 'dark');
      localStorage.setItem('jf_theme', 'dark');
    } else {
      root.removeAttribute('data-theme');
      localStorage.setItem('jf_theme', 'light');
    }
  }, [isDark]);

  return [isDark, setIsDark];
}
