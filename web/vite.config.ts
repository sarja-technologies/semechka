import { sveltekit } from '@sveltejs/kit/vite';
import adapter from '@sveltejs/adapter-static';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit({ adapter: adapter({ pages: 'build', assets: 'build', fallback: 'index.html', strict: false }), alias: { $lib: 'src/lib' } })],
  server: { port: 5173, proxy: { '/api': { target: (globalThis as any).process?.env?.SEMECHKA_API ?? 'http://127.0.0.1:8780', changeOrigin: false } } }
});
