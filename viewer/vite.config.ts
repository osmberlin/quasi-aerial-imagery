import { readFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join } from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin, searchForWorkspaceRoot } from 'vite'

/**
 * MapLibre starts its vector tile worker from a file next to its own bundle (import.meta.url plus a
 * fixed file name). Vite's build does not emit that file, so copy it into the assets folder.
 */
function maplibreWorkerAsset(): Plugin {
  const workerFile = new URL(
    './node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs',
    import.meta.url,
  )
  return {
    name: 'maplibre-worker-asset',
    apply: 'build',
    async generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'assets/maplibre-gl-worker.mjs',
        source: await readFile(workerFile, 'utf8'),
      })
    },
  }
}

export default defineConfig({
  // GitHub Pages serves the app under /<repository>/; set VITE_BASE=/<repository>/ for that build.
  base: process.env.VITE_BASE ?? '/',
  plugins: [react({ compiler: true }), tailwindcss(), maplibreWorkerAsset()],
  optimizeDeps: {
    // Pre-bundling moves maplibre-gl away from its worker file, so the dev server must serve it unbundled.
    exclude: ['maplibre-gl'],
  },
  server: {
    port: 5199,
    strictPort: true,
    fs: {
      // Bun's global store links packages into ~/.bun/install/cache/links. Extend the defaults, do not replace them.
      allow: [searchForWorkspaceRoot(process.cwd()), join(homedir(), '.bun/install/cache/links')],
    },
  },
  build: {
    // public/data is a symlink to the large export. Do not copy it into dist/.
    copyPublicDir: false,
    // maplibre-gl alone is about 1 MB minified, so the default 500 kB warning is noise here.
    chunkSizeWarningLimit: 1200,
  },
})
