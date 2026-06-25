<script lang="ts">
  import type { CitationGraphResponse, GraphNodeMode } from '../api/client';

  export let label = '';
  export let disabled = false;
  export let load: (nodeMode: GraphNodeMode) => Promise<CitationGraphResponse> = async () => ({
    nodes: [],
    edges: [],
    summary: {},
  });

  let nodeMode: GraphNodeMode = 'local_only';
  let graph: CitationGraphResponse | null = null;
  let busy = false;

  async function build(): Promise<void> {
    busy = true;
    try {
      graph = await load(nodeMode);
    } finally {
      busy = false;
    }
  }

  function nodeLabel(id: string): string {
    return graph?.nodes.find((node) => node.id === id)?.label ?? id;
  }
</script>

<section>
  <div class="head">
    <h3>Citation graph {label}</h3>
    <div class="controls">
      <select bind:value={nodeMode} disabled={disabled || busy}>
        <option value="local_only">Local only</option>
        <option value="include_external">Include external</option>
      </select>
      <button type="button" on:click={build} disabled={disabled || busy}>Build graph</button>
    </div>
  </div>

  {#if graph}
    <p class="summary">
      {graph.summary.node_count ?? graph.nodes.length} nodes · {graph.summary.edge_count ??
        graph.edges.length} edges · {graph.summary.external_node_count ?? 0} external ·
      {graph.summary.unresolved_reference_count ?? 0} unresolved
    </p>
    {#if graph.edges.length === 0}
      <p class="empty">No citation edges in this scope yet.</p>
    {:else}
      <ul class="edges">
        {#each graph.edges as edge (edge.source + '->' + edge.target)}
          <li>
            <span>{nodeLabel(edge.source)}</span>
            <span class="arrow">→</span>
            <span>{nodeLabel(edge.target)}</span>
            <small>{edge.resolution}{edge.weight > 1 ? ` ·×${edge.weight}` : ''}</small>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</section>

<style>
  .head {
    align-items: center;
    display: flex;
    gap: 0.75rem;
    justify-content: space-between;
  }

  .controls {
    display: flex;
    gap: 0.5rem;
  }

  .summary {
    color: #64717f;
    font-size: 0.85rem;
  }

  .edges {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .edges li {
    align-items: baseline;
    display: flex;
    gap: 0.4rem;
  }

  .arrow {
    color: #64717f;
  }

  small {
    color: #64717f;
  }
</style>
