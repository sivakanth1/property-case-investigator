import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  envPrefix: ['VITE_', 'NEXT_PUBLIC_'],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.PCI_BACKEND_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
