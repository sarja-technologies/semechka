// The attachments of the message being written: pictures, videos and files, in order, each with the part it plays.
// One tray per place a message is written (a conversation, or a new one); trays live for the whole session, so uploads keep going and
// attachments are still there after moving to another page and back, and after a reload (only references are kept, never file bytes).
import { ApiError } from '$lib/api';
import type { Attachment } from '$lib/chat/thread.svelte';
import { fetchAsset, isMem, type Asset } from './assets';
import { Upload, isMedia, precheck } from './upload.svelte';
import { autoAssign, type Slot } from './roles';

export type TrayItem = {
  key: string;
  kind: 'image' | 'video' | 'file';
  name: string;
  size: number;
  role: string | null;
  /** the role was chosen by the program, not the person, so it may change when the operation does */
  roleAuto?: boolean;
  upload?: Upload | null;
  asset?: Asset | null;
  /** a text file (or, where the library is not available, a picture) attached the older way */
  legacy?: Attachment | null;
  error?: string;
};

export type AddContext = {
  maxMb: number;
  ephemeral: boolean;
  /** pictures and videos go to the library; false where only the older attachments exist */
  media: boolean;
  /** text files and pictures, the older way. Absent when attachments are off. */
  legacy?: (f: File) => Promise<Attachment>;
  /** pictures go the older way (a no-save conversation reads them from memory) while videos still go to the library */
  legacyImages?: boolean;
  limit?: number;
  slots: () => Slot[];
};

let seq = 0;
const SAVE = 'sem.tray.';
const trays = new Map<string, Tray>();

export function trayFor(scope: string, userId: string): Tray {
  const k = `${userId}|${scope}`;
  let t = trays.get(k);
  if (!t) { t = new Tray(k); t.restore(); trays.set(k, t); }
  return t;
}

export class Tray {
  items = $state<TrayItem[]>([]);
  notes = $state<string[]>([]);
  private id: string;
  constructor(id: string) { this.id = id; }

  get ready(): TrayItem[] { return this.items.filter((i) => (i.asset || i.legacy) && !i.error && !i.upload); }
  get busy(): boolean { return this.items.some((i) => i.upload?.active); }
  get failed(): boolean { return this.items.some((i) => i.error || i.upload?.status === 'error'); }
  find(key: string) { return this.items.find((i) => i.key === key); }

  /** Add files: pictures and videos to the library (with progress), anything else the older way. Returns what was refused and why. */
  addFiles(files: Iterable<File>, cx: AddContext): string[] {
    const refused: string[] = [];
    for (const f of files) {
      if (this.items.length >= (cx.limit ?? 12)) { refused.push(`At most ${cx.limit ?? 12} files per message.`); break; }
      if (cx.media && isMedia(f) && !(cx.legacyImages && cx.legacy && (f.type || '').startsWith('image/'))) {
        const why = precheck(f, cx.maxMb);
        if (why) { refused.push(why); continue; }
        const up = new Upload(f, cx.ephemeral);
        const kind = (f.type || '').startsWith('video/') || /\.(mp4|m4v|mov|webm|mkv|avi)$/i.test(f.name) ? 'video' : 'image';
        const item: TrayItem = { key: `t${++seq}`, kind, name: f.name, size: f.size, role: null, roleAuto: true, upload: up };
        this.items.push(item);
        const live = this.items[this.items.length - 1];
        up.start().then((asset) => {
          if (up.status === 'cancelled') { this.drop(live.key); return; }
          if (asset) { live.asset = asset; live.upload = null; live.kind = asset.kind; live.name = asset.name ?? live.name; live.size = asset.size; autoAssign(this.items, cx.slots()); this.save(); }
          else live.error = up.error;
        });
        autoAssign(this.items, cx.slots());
      } else if (cx.legacy) {
        const isImage = (f.type || '').startsWith('image/');
        const limit = 10;
        if (f.size > limit * 1048576) { refused.push(`${f.name} is larger than ${limit} MB.`); continue; }
        const item: TrayItem = { key: `t${++seq}`, kind: isImage ? 'image' : 'file', name: f.name, size: f.size, role: null, roleAuto: true };
        this.items.push(item);
        const live = this.items[this.items.length - 1];
        const local = isImage ? URL.createObjectURL(f) : null;
        cx.legacy(f).then((a) => { live.legacy = a; live.size = a.size; live.name = a.name; if (local) URL.revokeObjectURL(local); autoAssign(this.items, cx.slots()); this.save(); })
          .catch((e) => { live.error = e instanceof ApiError ? e.message : 'The upload failed.'; if (local) URL.revokeObjectURL(local); });
      } else refused.push(`${f.name}: attachments are turned off for you.`);
    }
    this.save();
    return refused;
  }

  addAsset(a: Asset, slots: Slot[]): boolean {
    if (this.items.some((i) => i.asset?.id === a.id)) return false;
    this.items.push({ key: `t${++seq}`, kind: a.kind, name: a.name ?? (a.kind === 'image' ? 'Picture' : 'Video'), size: a.size, role: null, roleAuto: true, asset: a });
    autoAssign(this.items, slots);
    this.save();
    return true;
  }

  /** Put an edited or newly uploaded asset where another one was, keeping its place and role. The old one stays in the library. */
  swap(key: string, a: Asset) {
    const it = this.find(key);
    if (!it) return;
    it.asset = a; it.upload = null; it.legacy = null; it.error = undefined;
    it.kind = a.kind; it.name = a.name ?? it.name; it.size = a.size;
    this.save();
  }

  replaceWithFile(key: string, f: File, cx: AddContext) {
    const old = this.find(key);
    if (!old) return [];
    const at = this.items.indexOf(old), role = old.role, auto = old.roleAuto;
    this.items.splice(at, 1);
    const before = this.items.length;
    const refused = this.addFiles([f], cx);
    if (this.items.length > before) {
      const made = this.items.pop()!;
      made.role = role; made.roleAuto = auto;
      this.items.splice(at, 0, made);
    } else this.items.splice(at, 0, old);
    this.save();
    return refused;
  }

  setRole(key: string, role: string | null) {
    const it = this.find(key);
    if (it) { it.role = role; it.roleAuto = false; this.save(); }
  }

  move(key: string, delta: number) {
    const i = this.items.findIndex((x) => x.key === key), j = i + delta;
    if (i < 0 || j < 0 || j >= this.items.length) return;
    const [it] = this.items.splice(i, 1);
    this.items.splice(j, 0, it);
    this.save();
  }

  reorder(from: string, before: string | null) {
    const i = this.items.findIndex((x) => x.key === from);
    if (i < 0 || from === before) return;
    const [it] = this.items.splice(i, 1);
    const j = before ? this.items.findIndex((x) => x.key === before) : this.items.length;
    this.items.splice(j < 0 ? this.items.length : j, 0, it);
    this.save();
  }

  /** Remove from the message. Cancels an upload still running. The library keeps what was already stored. */
  remove(key: string): TrayItem | undefined {
    const it = this.find(key);
    if (it?.upload?.active) it.upload.cancel();
    it?.upload?.release();
    return this.drop(key);
  }

  private drop(key: string) {
    const i = this.items.findIndex((x) => x.key === key);
    if (i < 0) return undefined;
    const [it] = this.items.splice(i, 1);
    this.save();
    return it;
  }

  retry(key: string) {
    const it = this.find(key);
    if (!it?.upload) return;
    it.error = undefined;
    it.upload.retry().then((asset) => {
      if (asset) { it.asset = asset; it.upload = null; it.kind = asset.kind; it.name = asset.name ?? it.name; this.save(); }
      else if (it.upload?.status !== 'cancelled') it.error = it.upload?.error;
    });
  }

  /** After a message is sent. Everything stays in the library; only the references go. */
  clear() {
    for (const it of this.items) { it.upload?.cancel(); it.upload?.release(); }
    this.items = []; this.notes = [];
    this.save();
  }

  // ---- surviving navigation and reloads ------------------------------------------------------------------------------
  save() {
    try {
      const keep = this.items.filter((i) => (i.asset || i.legacy) && !i.error).map((i) => ({ kind: i.kind, name: i.name, size: i.size, role: i.role, roleAuto: i.roleAuto, asset: i.asset, legacy: i.legacy }));
      if (keep.length) sessionStorage.setItem(SAVE + this.id, JSON.stringify(keep)); else sessionStorage.removeItem(SAVE + this.id);
    } catch { /* private mode or no storage: the tray still works for this page */ }
  }

  restore() {
    let saved: any[] = [];
    try { saved = JSON.parse(sessionStorage.getItem(SAVE + this.id) ?? '[]'); } catch { saved = []; }
    for (const s of saved) this.items.push({ key: `t${++seq}`, kind: s.kind, name: s.name, size: s.size, role: s.role ?? null, roleAuto: s.roleAuto, asset: s.asset ?? null, legacy: s.legacy ?? null });
    // anything that was removed or expired in the meantime is dropped, with a note, rather than failing at send time
    for (const it of [...this.items]) {
      if (!it.asset) continue;
      const a = it.asset;
      fetchAsset(a.id).then((fresh) => { const t = this.find(it.key); if (t) t.asset = { ...a, ...fresh }; })
        .catch((e) => {
          if (e instanceof ApiError && (e.status === 404 || e.status === 410)) {
            this.drop(it.key);
            this.notes.push(`“${it.name}” is no longer available${isMem(a.id) ? ' (no-save files are kept for a short time)' : ''} and was removed from the message.`);
          }
        });
    }
  }
}
