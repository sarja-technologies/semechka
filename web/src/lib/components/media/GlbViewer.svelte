<script lang="ts">
  // A small, dependency-free viewer for a binary glTF (GLB) result: drag to turn it, scroll or pinch to zoom, double-click to reset.
  // It reads what ComfyUI's SaveGLB writes (positions, triangle indices, optional UVs + embedded PNG texture, optional vertex colours) and nothing else:
  // a file with features it does not draw is still shown as far as it can be, and the download is always the original file.
  import { onMount } from 'svelte';

  let { src, label = '3D result' }: { src: string; label?: string } = $props();
  let canvas: HTMLCanvasElement | undefined = $state();
  let phase = $state<'loading' | 'ready' | 'error'>('loading'), err = $state(''), stats = $state(''), mode = $state<'texture' | 'shape'>('texture');
  let hasColour = $state(false);
  let redraw: () => void = () => {};

  type Prim = { pos: Float32Array; idx: Uint32Array | null; uv: Float32Array | null; col: Float32Array | null; colSize: number; nrm: Float32Array; tex: ImageBitmap | null };

  const COMP: Record<number, any> = { 5126: Float32Array, 5125: Uint32Array, 5123: Uint16Array, 5121: Uint8Array, 5122: Int16Array, 5120: Int8Array };
  const SIZE: Record<string, number> = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };

  function parseGlb(buf: ArrayBuffer): { json: any; bin: DataView | null } {
    const dv = new DataView(buf);
    if (buf.byteLength < 20 || dv.getUint32(0, true) !== 0x46546c67) throw new Error('This is not a binary glTF (GLB) file.');
    if (dv.getUint32(4, true) !== 2) throw new Error('Only glTF 2.0 files can be shown.');
    let off = 12, json: any = null, bin: DataView | null = null;
    while (off + 8 <= buf.byteLength) {
      const len = dv.getUint32(off, true), type = dv.getUint32(off + 4, true);
      if (type === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, off + 8, len)));
      else if (type === 0x004e4942) bin = new DataView(buf, off + 8, len);
      off += 8 + len;
    }
    if (!json) throw new Error('The file has no glTF description.');
    return { json, bin };
  }

  function accessor(json: any, bin: DataView, i: number): { data: any; size: number } {
    const a = json.accessors[i], bv = json.bufferViews[a.bufferView], T = COMP[a.componentType], n = SIZE[a.type];
    if (!T || !n) throw new Error('The file uses a data layout this viewer does not read.');
    const el = T.BYTES_PER_ELEMENT, stride = bv.byteStride || el * n, start = bin.byteOffset + (bv.byteOffset || 0) + (a.byteOffset || 0);
    const out = new T(a.count * n);
    if (stride === el * n && start % el === 0) out.set(new T(bin.buffer, start, a.count * n));
    else {
      const src = new DataView(bin.buffer);
      const rd = (o: number) => (T === Float32Array ? src.getFloat32(o, true) : T === Uint32Array ? src.getUint32(o, true) : T === Uint16Array ? src.getUint16(o, true) : T === Int16Array ? src.getInt16(o, true) : T === Int8Array ? src.getInt8(o) : src.getUint8(o));
      for (let k = 0; k < a.count; k++) for (let c = 0; c < n; c++) out[k * n + c] = rd(start + k * stride + c * el);
    }
    if (a.normalized && T !== Float32Array) { const f = new Float32Array(out.length), mx = T === Uint8Array ? 255 : T === Uint16Array ? 65535 : 1; for (let k = 0; k < out.length; k++) f[k] = out[k] / mx; return { data: f, size: n }; }
    return { data: out, size: n };
  }

  function normals(pos: Float32Array, idx: Uint32Array | null): Float32Array {
    const n = new Float32Array(pos.length), tri = idx ?? Uint32Array.from({ length: pos.length / 3 }, (_, i) => i);
    for (let t = 0; t + 2 < tri.length; t += 3) {
      const a = tri[t] * 3, b = tri[t + 1] * 3, c = tri[t + 2] * 3;
      const ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2], vx = pos[c] - pos[a], vy = pos[c + 1] - pos[a + 1], vz = pos[c + 2] - pos[a + 2];
      const x = uy * vz - uz * vy, y = uz * vx - ux * vz, z = ux * vy - uy * vx;
      for (const p of [a, b, c]) { n[p] += x; n[p + 1] += y; n[p + 2] += z; }
    }
    for (let i = 0; i < n.length; i += 3) { const l = Math.hypot(n[i], n[i + 1], n[i + 2]) || 1; n[i] /= l; n[i + 1] /= l; n[i + 2] /= l; }
    return n;
  }

  async function load(url: string): Promise<Prim[]> {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(r.status === 404 ? 'The file is no longer available.' : `The file could not be loaded (${r.status}).`);
    const { json, bin } = parseGlb(await r.arrayBuffer());
    if (!bin) throw new Error('The file has no geometry data.');
    const prims: Prim[] = [];
    const texCache = new Map<number, ImageBitmap | null>();
    async function texture(mi: number | undefined): Promise<ImageBitmap | null> {
      const m = mi != null ? json.materials?.[mi] : null, ti = m?.pbrMetallicRoughness?.baseColorTexture?.index;
      if (ti == null) return null;
      if (texCache.has(ti)) return texCache.get(ti)!;
      const img = json.images?.[json.textures?.[ti]?.source];
      let bmp: ImageBitmap | null = null;
      if (img?.bufferView != null) {
        const bv = json.bufferViews[img.bufferView];
        const bytes = new Uint8Array(bin!.buffer, bin!.byteOffset + (bv.byteOffset || 0), bv.byteLength);
        try { bmp = await createImageBitmap(new Blob([bytes as unknown as BlobPart], { type: img.mimeType || 'image/png' })); } catch { bmp = null; }
      }
      texCache.set(ti, bmp);
      return bmp;
    }
    for (const mesh of json.meshes ?? []) for (const p of mesh.primitives ?? []) {
      if ((p.mode ?? 4) !== 4 || p.attributes?.POSITION == null) continue;   // triangles only
      const pos = accessor(json, bin, p.attributes.POSITION).data as Float32Array;
      const idx = p.indices != null ? Uint32Array.from(accessor(json, bin, p.indices).data) : null;
      const uv = p.attributes.TEXCOORD_0 != null ? (accessor(json, bin, p.attributes.TEXCOORD_0).data as Float32Array) : null;
      const c = p.attributes.COLOR_0 != null ? accessor(json, bin, p.attributes.COLOR_0) : null;
      prims.push({ pos, idx, uv, col: c ? Float32Array.from(c.data) : null, colSize: c?.size ?? 0, nrm: normals(pos, idx), tex: uv ? await texture(p.material) : null });
    }
    if (!prims.length) throw new Error('The file has no triangles to show.');
    return prims;
  }

  // ---- tiny matrix helpers (column-major, like WebGL)
  const persp = (fov: number, asp: number, n: number, f: number) => { const t = 1 / Math.tan(fov / 2); return new Float32Array([t / asp, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) / (n - f), -1, 0, 0, (2 * f * n) / (n - f), 0]); };
  function lookAt(e: number[], c: number[]): Float32Array {
    let zx = e[0] - c[0], zy = e[1] - c[1], zz = e[2] - c[2]; const zl = Math.hypot(zx, zy, zz) || 1; zx /= zl; zy /= zl; zz /= zl;
    let xx = zz, xy = 0, xz = -zx; const xl = Math.hypot(xx, xy, xz) || 1; xx /= xl; xy /= xl; xz /= xl;       // right = up(0,1,0) x z
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    return new Float32Array([xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0, -(xx * e[0] + xy * e[1] + xz * e[2]), -(yx * e[0] + yy * e[1] + yz * e[2]), -(zx * e[0] + zy * e[1] + zz * e[2]), 1]);
  }
  const mul = (a: Float32Array, b: Float32Array) => { const o = new Float32Array(16); for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) { let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k]; o[i * 4 + j] = s; } return o; };

  const VS = `attribute vec3 p; attribute vec3 n; attribute vec2 t; attribute vec4 c; uniform mat4 m; varying vec3 vn; varying vec2 vt; varying vec4 vc;
    void main() { gl_Position = m * vec4(p, 1.0); vn = n; vt = t; vc = c; }`;
  const FS = `precision mediump float; varying vec3 vn; varying vec2 vt; varying vec4 vc; uniform sampler2D s; uniform int k; uniform vec3 l;
    void main() {
      if (k == 0) { gl_FragColor = vec4(texture2D(s, vt).rgb, 1.0); return; }
      if (k == 1) { gl_FragColor = vec4(vc.rgb, 1.0); return; }
      float d = abs(dot(normalize(vn), l)); gl_FragColor = vec4(vec3(0.18 + 0.72 * d), 1.0);
    }`;

  onMount(() => {
    let stop = false, raf = 0, gl: WebGLRenderingContext | null = null;
    let yaw = 0, pitch = 0, dist = 2.5, center = [0, 0, 0], radius = 1;
    const reset = () => { yaw = 0; pitch = 0; dist = 2.6; draw(); };
    let draw = () => {};
    const ptrs = new Map<number, { x: number; y: number }>();
    let pinch = 0;
    const cv = canvas!;
    function down(e: PointerEvent) { cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY }); }
    function move(e: PointerEvent) {
      const p = ptrs.get(e.pointerId); if (!p) return;
      if (ptrs.size === 1) { yaw -= (e.clientX - p.x) * 0.008; pitch = Math.max(-1.5, Math.min(1.5, pitch + (e.clientY - p.y) * 0.008)); }
      p.x = e.clientX; p.y = e.clientY;
      if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y); if (pinch) dist = Math.max(0.3, Math.min(12, dist * (pinch / d))); pinch = d; }
      draw();
    }
    function up(e: PointerEvent) { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = 0; }
    function wheel(e: WheelEvent) { e.preventDefault(); dist = Math.max(0.3, Math.min(12, dist * Math.exp(e.deltaY * 0.001))); draw(); }
    function key(e: KeyboardEvent) {
      const k = e.key; let used = true;
      if (k === 'ArrowLeft') yaw += 0.15; else if (k === 'ArrowRight') yaw -= 0.15; else if (k === 'ArrowUp') pitch = Math.max(-1.5, pitch - 0.15);
      else if (k === 'ArrowDown') pitch = Math.min(1.5, pitch + 0.15); else if (k === '+' || k === '=') dist = Math.max(0.3, dist * 0.9); else if (k === '-') dist = Math.min(12, dist * 1.1);
      else if (k === '0' || k === 'Home') reset(); else used = false;
      if (used) { e.preventDefault(); draw(); }
    }
    cv.addEventListener('pointerdown', down); cv.addEventListener('pointermove', move); cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('wheel', wheel, { passive: false }); cv.addEventListener('dblclick', reset); cv.addEventListener('keydown', key);
    const ro = new ResizeObserver(() => draw()); ro.observe(cv);

    (async () => {
      try {
        const prims = await load(src);
        if (stop) return;
        gl = (cv.getContext('webgl', { antialias: true, alpha: true, premultipliedAlpha: true, preserveDrawingBuffer: false }) as WebGLRenderingContext | null);
        if (!gl) throw new Error('This browser cannot show 3D (WebGL is off). Download the file to open it in a 3D program.');
        const g = gl;
        const big = prims.some((p) => p.pos.length / 3 > 65535);
        if (big && !g.getExtension('OES_element_index_uint')) throw new Error('This browser cannot draw a mesh this large. Download the file to open it in a 3D program.');
        const sh = (t: number, s: string) => { const o = g.createShader(t)!; g.shaderSource(o, s); g.compileShader(o); if (!g.getShaderParameter(o, g.COMPILE_STATUS)) throw new Error('3D drawing failed to start.'); return o; };
        const prog = g.createProgram()!; g.attachShader(prog, sh(g.VERTEX_SHADER, VS)); g.attachShader(prog, sh(g.FRAGMENT_SHADER, FS)); g.linkProgram(prog); g.useProgram(prog);
        const loc = { p: g.getAttribLocation(prog, 'p'), n: g.getAttribLocation(prog, 'n'), t: g.getAttribLocation(prog, 't'), c: g.getAttribLocation(prog, 'c'),
          m: g.getUniformLocation(prog, 'm'), k: g.getUniformLocation(prog, 'k'), l: g.getUniformLocation(prog, 'l'), s: g.getUniformLocation(prog, 's') };
        const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
        let tris = 0, verts = 0, texInfo = '';
        const objs = prims.map((p) => {
          for (let i = 0; i < p.pos.length; i += 3) for (let a = 0; a < 3; a++) { const v = p.pos[i + a]; if (Number.isFinite(v)) { lo[a] = Math.min(lo[a], v); hi[a] = Math.max(hi[a], v); } }
          verts += p.pos.length / 3; tris += (p.idx ? p.idx.length : p.pos.length / 3) / 3;
          const buf = (d: ArrayBufferView, t: number = g.ARRAY_BUFFER) => { const b = g.createBuffer()!; g.bindBuffer(t, b); g.bufferData(t, d, g.STATIC_DRAW); return b; };
          let tex: WebGLTexture | null = null;
          if (p.tex) {
            tex = g.createTexture(); g.bindTexture(g.TEXTURE_2D, tex);
            g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL, false);
            g.texImage2D(g.TEXTURE_2D, 0, g.RGBA, g.RGBA, g.UNSIGNED_BYTE, p.tex);
            for (const [k, v] of [[g.TEXTURE_MIN_FILTER, g.LINEAR], [g.TEXTURE_MAG_FILTER, g.LINEAR], [g.TEXTURE_WRAP_S, g.CLAMP_TO_EDGE], [g.TEXTURE_WRAP_T, g.CLAMP_TO_EDGE]]) g.texParameteri(g.TEXTURE_2D, k, v);
            texInfo = `texture ${p.tex.width}×${p.tex.height}`;
          }
          return { p, pb: buf(p.pos), nb: buf(p.nrm), tb: p.uv ? buf(p.uv) : null, cb: p.col ? buf(p.col) : null,
            ib: p.idx ? buf(big ? p.idx : Uint16Array.from(p.idx), g.ELEMENT_ARRAY_BUFFER) : null, count: p.idx ? p.idx.length : p.pos.length / 3, tex };
        });
        center = [0, 1, 2].map((a) => (lo[a] + hi[a]) / 2);
        radius = Math.max(1e-6, Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2);
        hasColour = objs.some((o) => o.tex || o.cb);
        if (!hasColour) mode = 'shape';
        stats = [`${Math.round(verts).toLocaleString()} points`, `${Math.round(tris).toLocaleString()} triangles`, texInfo].filter(Boolean).join(' · ');
        g.enable(g.DEPTH_TEST);
        draw = () => {
          if (stop || !gl) return;
          cancelAnimationFrame(raf);
          raf = requestAnimationFrame(() => {
            const dpr = Math.min(2, window.devicePixelRatio || 1), w = Math.max(1, Math.round(cv.clientWidth * dpr)), h = Math.max(1, Math.round(cv.clientHeight * dpr));
            if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
            g.viewport(0, 0, w, h);
            g.clearColor(0, 0, 0, 0); g.clear(g.COLOR_BUFFER_BIT | g.DEPTH_BUFFER_BIT);   // transparent: the canvas's own (theme) background shows through
            const d = dist * radius, eye = [center[0] + d * Math.sin(yaw) * Math.cos(pitch), center[1] + d * Math.sin(pitch), center[2] + d * Math.cos(yaw) * Math.cos(pitch)];
            const mvp = mul(persp(0.75, w / h, Math.max(1e-4, d - radius * 2), d + radius * 2), lookAt(eye, center));
            g.uniformMatrix4fv(loc.m, false, mvp);
            const ex = eye[0] - center[0], ey = eye[1] - center[1], ez = eye[2] - center[2], el = Math.hypot(ex, ey, ez) || 1;
            g.uniform3f(loc.l, ex / el, ey / el, ez / el);
            for (const o of objs) {
              const k = mode === 'shape' ? 2 : o.tex ? 0 : o.cb ? 1 : 2;
              g.uniform1i(loc.k, k);
              const attr = (l: number, b: WebGLBuffer | null, n: number, fallback: number[]) => {
                if (l < 0) return;
                if (b) { g.bindBuffer(g.ARRAY_BUFFER, b); g.enableVertexAttribArray(l); g.vertexAttribPointer(l, n, g.FLOAT, false, 0, 0); }
                else { g.disableVertexAttribArray(l); g.vertexAttrib4f(l, fallback[0], fallback[1], fallback[2] ?? 0, fallback[3] ?? 1); }
              };
              attr(loc.p, o.pb, 3, [0, 0, 0]); attr(loc.n, o.nb, 3, [0, 0, 1]); attr(loc.t, o.tb, 2, [0, 0]); attr(loc.c, o.cb, o.p.colSize || 3, [0.7, 0.7, 0.7, 1]);
              if (o.tex) { g.activeTexture(g.TEXTURE0); g.bindTexture(g.TEXTURE_2D, o.tex); g.uniform1i(loc.s, 0); }
              if (o.ib) { g.bindBuffer(g.ELEMENT_ARRAY_BUFFER, o.ib); g.drawElements(g.TRIANGLES, o.count, big ? g.UNSIGNED_INT : g.UNSIGNED_SHORT, 0); }
              else g.drawArrays(g.TRIANGLES, 0, o.count);
            }
          });
        };
        redraw = draw;
        phase = 'ready';
        reset();
      } catch (e) { if (!stop) { err = e instanceof Error ? e.message : 'The 3D file could not be shown.'; phase = 'error'; } }
    })();

    return () => {
      stop = true; cancelAnimationFrame(raf); ro.disconnect();
      cv.removeEventListener('pointerdown', down); cv.removeEventListener('pointermove', move); cv.removeEventListener('pointerup', up); cv.removeEventListener('pointercancel', up);
      cv.removeEventListener('wheel', wheel); cv.removeEventListener('dblclick', reset); cv.removeEventListener('keydown', key);
      (gl?.getExtension('WEBGL_lose_context'))?.loseContext();
    };
  });
  $effect(() => { void mode; redraw(); });
</script>

<div class="glb" data-state={phase}>
  <canvas bind:this={canvas} tabindex="0" aria-roledescription="3D viewer" aria-label={`${label}. Drag to turn it, scroll or pinch to zoom, double-click to reset. Arrow keys turn it, plus and minus zoom, 0 resets.`}></canvas>
  {#if phase === 'loading'}<p class="ov muted small" role="status">Loading the 3D file…</p>{/if}
  {#if phase === 'error'}<div class="ov"><div class="notice bad" role="alert"><div>{err}</div></div></div>{/if}
  {#if phase === 'ready'}
    <div class="bar">
      {#if hasColour}
        <div class="seg" role="radiogroup" aria-label="Show">
          <button type="button" role="radio" aria-checked={mode === 'texture'} class:on={mode === 'texture'} onclick={() => (mode = 'texture')}>Picture</button>
          <button type="button" role="radio" aria-checked={mode === 'shape'} class:on={mode === 'shape'} onclick={() => (mode = 'shape')}>Shape</button>
        </div>
      {/if}
      <span class="tiny muted">{stats}</span>
    </div>
  {/if}
</div>

<style>
  .glb { position: relative; display: grid; gap: 6px; }
  canvas { width: 100%; aspect-ratio: 4 / 3; max-height: 70vh; display: block; border-radius: var(--radius); border: 1px solid var(--border); background: var(--bg-2); touch-action: none; cursor: grab; }
  canvas:active { cursor: grabbing; } canvas:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .ov { position: absolute; inset: 0 0 auto 0; aspect-ratio: 4 / 3; max-height: 70vh; display: grid; place-items: center; padding: 16px; pointer-events: none; }
  .ov :global(.notice) { pointer-events: auto; }
  .bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 2px; gap: 2px; }
  .seg button { all: unset; padding: 0 10px; height: 26px; display: inline-flex; align-items: center; border-radius: calc(var(--radius-sm) - 2px); cursor: pointer; font-size: 13px; }
  .seg button.on { background: var(--selected); font-weight: 600; } .seg button:focus-visible { outline: 2px solid var(--m-ring); }
</style>
