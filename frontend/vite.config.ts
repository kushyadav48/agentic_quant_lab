import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Fixed loopback target. Credentials cannot be redirected by a browser setting.
export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], restoreMocks: true },
});
