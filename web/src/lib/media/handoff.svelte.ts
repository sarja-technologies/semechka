// A picture chosen somewhere else (the Library, a result) to be used as the reference on the Images or Videos screen it is about to open.
import type { Asset } from './assets';
export const handoff = $state<{ image: Asset | null; video: Asset | null }>({ image: null, video: null });
