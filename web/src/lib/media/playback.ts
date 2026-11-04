// Only one video plays at a time across the interface: starting one pauses the one that was playing.
let current: HTMLVideoElement | null = null;
export function playing(v: HTMLVideoElement) {
  if (current && current !== v) current.pause();
  current = v;
}
export function stopped(v: HTMLVideoElement) {
  if (current === v) current = null;
}
