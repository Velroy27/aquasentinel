import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  server: {
    // The font packages resolve from the repository-level node_modules folder.
    // Permit that workspace root so Vite can serve the bundled font assets.
    fs: { allow: ['..'] },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/recharts/') || id.includes('/node_modules/d3-')) {
            return 'charts';
          }
          if (id.includes('/node_modules/three/') || id.includes('/node_modules/@react-three/')) {
            return 'three-vendor';
          }
          if (id.includes('/node_modules/react-dom/') || id.includes('/node_modules/react/')) {
            return 'react-vendor';
          }
          return undefined;
        },
      },
    },
  },
});
