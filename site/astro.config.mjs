import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import tailwind from '@astrojs/tailwind';
import vercel from '@astrojs/vercel';

// Static output (Astro 5 default): every page is prerendered, and a route
// opts into server rendering with `export const prerender = false`.
// Used by /api/contribute.
export default defineConfig({
  output: 'static',
  adapter: vercel(),
  integrations: [react(), tailwind()],
  vite: {
    resolve: {
      dedupe: ['three'],
    },
    ssr: {
      noExternal: ['maplibre-gl', '@react-three/fiber', '@react-three/drei'],
    },
  },
});
