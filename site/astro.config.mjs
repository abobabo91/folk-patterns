import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import tailwind from '@astrojs/tailwind';
import vercel from '@astrojs/vercel/serverless';

// Hybrid rendering — all Astro pages remain statically built (same site
// output as before), but individual routes can opt into server-rendered
// mode with `export const prerender = false`. Used by /api/contribute.
export default defineConfig({
  output: 'hybrid',
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
