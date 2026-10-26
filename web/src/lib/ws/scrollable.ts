// A horizontally scrollable region (a wide table on a phone) must be reachable with the keyboard, and must have a name.
// Applied at runtime so the markup stays a plain wrapper; the name comes from the table's own (visually hidden) caption.
export function scrollable(node: HTMLElement) {
  node.tabIndex = 0;
  node.setAttribute('role', 'region');
  const label = node.querySelector('caption')?.textContent?.trim();
  if (label && !node.hasAttribute('aria-label')) node.setAttribute('aria-label', label);
}
