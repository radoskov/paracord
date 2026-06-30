<script lang="ts">
  export let title = '';
  export let wide = false;
  export let onClose: () => void;

  function onKey(event: KeyboardEvent): void {
    if (event.key === 'Escape') onClose();
  }
</script>

<svelte:window on:keydown={onKey} />

<div class="backdrop" on:click|self={onClose} role="presentation">
  <div class="modal" class:wide role="dialog" aria-modal="true" aria-label={title}>
    <div class="modal-head">
      <h3>{title}</h3>
      <button type="button" class="secondary close" on:click={onClose} title="Close">✕</button>
    </div>
    <div class="modal-body">
      <slot />
    </div>
  </div>
</div>

<style>
  .backdrop {
    align-items: flex-start;
    background: rgba(15, 23, 42, 0.45);
    display: flex;
    inset: 0;
    justify-content: center;
    padding: 3rem 1rem;
    position: fixed;
    overflow: auto;
    z-index: 50;
  }

  .modal {
    background: #fbfcfd;
    border-radius: 10px;
    box-shadow: 0 20px 60px rgba(15, 23, 42, 0.35);
    display: flex;
    flex-direction: column;
    /* Never grow past the viewport (the backdrop's 3rem top/bottom padding leaves the gap);
       the body scrolls internally instead of the modal bottom being clipped off-screen. */
    max-height: calc(100vh - 6rem);
    max-width: 32rem;
    width: 100%;
  }

  .modal.wide {
    max-width: 72rem;
  }

  .modal-head {
    align-items: center;
    border-bottom: 1px solid #e1e7ee;
    display: flex;
    flex-shrink: 0;
    justify-content: space-between;
    padding: 0.75rem 1rem;
  }

  .modal-head h3 {
    font-size: 1.05rem;
    margin: 0;
  }

  .close {
    min-height: 1.9rem;
    padding: 0.2rem 0.55rem;
  }

  .modal-body {
    /* Take the remaining height and scroll within the modal so tall content (the reader) fits
       the viewport instead of clipping past the bottom when the browser page is zoomed. */
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 1rem;
  }
</style>
