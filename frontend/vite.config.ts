import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The production bundle is written straight into the Python package so that
// `jiram-catalog gui` can serve it as static files from the same origin as
// the API; `base: '/'` because the app is always mounted at the root.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: '../src/jiram_catalog/webapp/dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
    chunkSizeWarningLimit: 4096,
    rollupOptions: {
      output: {
        // Keep the heavy libraries in their own chunks so the catalog view is
        // interactive before Plotly has been parsed.
        manualChunks(id: string) {
          if (id.includes('node_modules/plotly.js')) return 'plotly';
          if (id.includes('node_modules/@deck.gl') || id.includes('node_modules/@luma.gl')) return 'deck';
          if (id.includes('node_modules/apache-arrow')) return 'arrow';
          if (id.includes('node_modules/react') || id.includes('node_modules/scheduler')) return 'react';
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:5006', changeOrigin: true } },
  },
});
