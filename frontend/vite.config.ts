import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Served at the domain root on Cloudflare Pages / Netlify / Vercel, but under
// /<repo>/ on GitHub Pages. Set VITE_BASE=/tg_tl_first_tick_zerodha/ for that one.
// It is read from the environment rather than passed as `vite build --base=...`
// because a leading-slash CLI argument gets rewritten into a Windows path by
// MSYS/Git Bash, which silently produces a broken bundle.
const base = process.env.VITE_BASE || '/'

export default defineConfig({
  base,
  plugins: [react()],
  server: { port: 5173 },
  build: { outDir: 'dist', sourcemap: false },
})
