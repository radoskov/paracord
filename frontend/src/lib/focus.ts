// Svelte action: focus (and optionally select) a node shortly after it mounts. Used to put the
// cursor in a popup's main input when it opens (batch10 #6). The timeout lets any modal transition
// / DOM insertion settle first. Pass `false` to disable without removing the directive.
export function focusOnMount(
  node: HTMLElement,
  options: boolean | { enabled?: boolean; select?: boolean } = true,
) {
  const opts = typeof options === 'boolean' ? { enabled: options } : options;
  if (opts.enabled === false) return {};
  const id = setTimeout(() => {
    node.focus();
    if (opts.select && (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement)) {
      node.select();
    }
  }, 0);
  return {
    destroy() {
      clearTimeout(id);
    },
  };
}
