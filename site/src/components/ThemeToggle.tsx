import { useEffect, useState } from 'react';
import { getTheme, setTheme, type Theme } from '../lib/theme';

export function ThemeToggle() {
  const [theme, setLocalTheme] = useState<Theme>('dark');

  useEffect(() => {
    setLocalTheme(getTheme());
  }, []);

  const flip = () => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    setLocalTheme(next);
  };

  return (
    <button
      onClick={flip}
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      className="flex h-8 w-8 items-center justify-center rounded-full border border-dusk bg-night/80 text-parchment/70 backdrop-blur hover:text-parchment dark:border-dusk dark:bg-night/80 [html:not(.dark)_&]:border-stone-300 [html:not(.dark)_&]:bg-white/80 [html:not(.dark)_&]:text-stone-600 [html:not(.dark)_&]:hover:text-stone-900"
    >
      {theme === 'dark' ? (
        // Sun icon (currently dark → click to go light)
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
        </svg>
      ) : (
        // Moon icon (currently light → click to go dark)
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      )}
    </button>
  );
}
