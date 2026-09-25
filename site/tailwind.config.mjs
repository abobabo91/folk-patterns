/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // Serif for tradition names / display; sans for meta.
        serif: ['"Cormorant Garamond"', 'Georgia', 'serif'],
        sans: ['Inter', '-apple-system', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      colors: {
        ink: '#0a0a0c',
        night: '#14141a',
        dusk: '#1e1e26',
        parchment: '#f4efe6',
        amber: {
          400: '#e0a94a',
          500: '#c4892a',
          600: '#a56d18',
        },
      },
      animation: {
        // Gentler pulse for tile skeletons — Tailwind's default is too twitchy.
        'pulse-slow': 'pulse-slow 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        'pulse-slow': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.55' },
        },
      },
    },
  },
};
