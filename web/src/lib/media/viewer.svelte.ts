// The one full-screen viewer (and editor) any part of the interface can open: pass the asset and, if the caller can use an edited result
// (replace an attachment, start from it), say how. The viewer itself is mounted once, in the app layout.
import type { Asset } from './assets';

export type ViewerOptions = {
  /** start in edit mode (the pencil on an attachment) */
  edit?: boolean;
  /** pictures and videos to step through with the arrow keys */
  siblings?: Asset[];
  /** label of the button that uses an edited result in place, e.g. "Use as source"; without it only "Save copy" is offered */
  useLabel?: string;
  /** called with the new asset when the person chose to use it */
  onuse?: (a: Asset) => void;
  /** called whenever an edited copy was saved to the library */
  onsaved?: (a: Asset) => void;
  /** a local preview to show while the server's copy is being fetched */
  local?: string | null;
};

export const viewer = $state<{ asset: Asset | null; opts: ViewerOptions }>({ asset: null, opts: {} });

export function openViewer(asset: Asset, opts: ViewerOptions = {}) { viewer.opts = opts; viewer.asset = asset; }
export function closeViewer() { viewer.asset = null; viewer.opts = {}; }
