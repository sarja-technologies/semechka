import { sveltekit } from '@sveltejs/kit/vite';
import adapter from '@sveltejs/adapter-static';
import { defineConfig } from 'vite';

const process_env = (k: string) => (globalThis as any).process?.env?.[k];
export default defineConfig({
  plugins: [sveltekit({ adapter: adapter({ pages: 'build', assets: 'build', fallback: 'index.html', strict: false }), alias: { $lib: 'src/lib' } })],
  // SEMECHKA_FAKE_GOOSE: point the Work Mode API at web/dev/fake_goose.py (a mock) while developing the Work screen
  server: { port: 5173, proxy: { ...(process_env('SEMECHKA_FAKE_GOOSE') ? { '/api/work/goose': { target: process_env('SEMECHKA_FAKE_GOOSE') as string, changeOrigin: false } } : {}), '/api': { target: (globalThis as any).process?.env?.SEMECHKA_API ?? 'http://127.0.0.1:8780', changeOrigin: false } } }
});
