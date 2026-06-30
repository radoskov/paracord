<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient, type AuditEvent } from '../api/client';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let events: AuditEvent[] = [];
  let limit = 100;
  let loading = false;
  let message = '';
  let filter = '';

  // Reload whenever the authenticated client is (re)created (fixes blank-on-refresh, like Admin).
  let loadedFor: ApiClient | null = null;
  $: if (client && client !== loadedFor) {
    loadedFor = client;
    void load();
  }

  onMount(load);

  async function load(): Promise<void> {
    loading = true;
    message = '';
    try {
      events = await client.listAuditEvents(limit);
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
  }

  $: visible = filter
    ? events.filter((e) => e.event_type.toLowerCase().includes(filter.toLowerCase()))
    : events;
</script>

<section class="card">
  <div class="head">
    <h2>Events</h2>
    <div class="controls">
      <input bind:value={filter} placeholder="Filter by type (e.g. auth, paper, agent)" aria-label="Filter events" />
      <select bind:value={limit} on:change={load} aria-label="How many">
        <option value={50}>50</option>
        <option value={100}>100</option>
        <option value={250}>250</option>
      </select>
      <button type="button" class="secondary" on:click={load} disabled={loading}>Refresh</button>
    </div>
  </div>
  <p class="muted">
    The audit log: authentication, imports, extraction, enrichment, exports, views, teleports,
    agent and admin actions. Newest first.
  </p>
  {#if message}<p class="danger">{message}</p>{/if}

  {#if visible.length === 0}
    <p class="empty">{loading ? 'Loading…' : 'No matching events.'}</p>
  {:else}
    <ul class="events">
      {#each visible as event (event.id)}
        <li>
          <div class="row">
            <strong>{event.event_type}</strong>
            <small class="muted">{formatDate(event.created_at)}</small>
          </div>
          {#if event.entity_type}
            <small class="muted">
              {event.entity_type}{event.entity_id ? ` · ${event.entity_id.slice(0, 8)}` : ''}
            </small>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .head {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    justify-content: space-between;
  }
  h2 {
    font-size: 1.05rem;
    margin: 0;
  }
  .controls {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .events {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    list-style: none;
    margin: 0.6rem 0 0;
    padding: 0;
  }
  .events li {
    border-bottom: 1px solid #eef1f4;
    padding: 0.35rem 0;
  }
  .row {
    align-items: baseline;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }
</style>
