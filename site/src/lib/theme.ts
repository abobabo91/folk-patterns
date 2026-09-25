// Theme state stored on <html data-theme="dark|light"> AND localStorage.
// Default: dark. Read once at hydration, updated on toggle.

export type Theme = 'dark' | 'light';

export function getTheme(): Theme {
  if (typeof document === 'undefined') return 'dark';
  const t = document.documentElement.getAttribute('data-theme') as Theme | null;
  return t ?? 'dark';
}

export function setTheme(t: Theme) {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-theme', t);
  document.documentElement.classList.toggle('dark', t === 'dark');
  try {
    localStorage.setItem('folk-patterns-theme', t);
  } catch {}
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === 'dark' ? 'light' : 'dark';
  setTheme(next);
  return next;
}
