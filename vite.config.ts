import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const LOOPBACK_HOST = '127.0.0.1'
const LAN_HOST = '0.0.0.0'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'MUSEFORGE_')
  const allowLan = env.MUSEFORGE_ALLOW_LAN === 'true'

  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            react: ['react', 'react-dom', 'react-router', 'zustand'],
            canvas: ['konva', 'react-konva', 'use-image'],
          },
        },
      },
    },
    server: {
      host: allowLan ? LAN_HOST : LOOPBACK_HOST,
      port: 33020,
      strictPort: true,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:38120',
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: allowLan ? LAN_HOST : LOOPBACK_HOST,
      port: 33020,
      strictPort: true,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:38120',
          changeOrigin: true,
        },
      },
    },
  }
})
