import { render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { AiStatus } from '../api/client';
import AiModelsPanel from './AiModelsPanel.svelte';

// A minimal getAiStatus fixture; overrides let a test flip the topic backend / availability.
function makeStatus(overrides: Partial<AiStatus> = {}): AiStatus {
  return {
    config: {
      embedding_provider: 'hash_bow',
      embedding_model: null,
      summary_provider: 'extractive',
      summary_model: 'llama3',
      topic_backend: 'tfidf',
      topic_embedding_model: null,
      ollama_url: 'http://localhost:11434',
    },
    allowed: {
      embedding_provider: ['hash_bow', 'sentence_transformers', 'ollama'],
      summary_provider: ['extractive', 'local_llm'],
      topic_backend: ['tfidf', 'embedding', 'bertopic'],
    },
    providers: {
      embedding: {
        hash_bow: { available: true, note: 'Default, dependency-free.' },
        ollama: { available: false, note: 'Start the Ollama profile (make up-ai) and set its URL.' },
      },
      summary: { extractive: { available: true, note: 'Default, dependency-free.' } },
      topic: {
        tfidf: { available: true, note: 'Default, dependency-free.' },
        embedding: { available: true, note: 'Built-in deterministic TF-IDF clustering (does not use embedding vectors yet).' },
        bertopic: { available: true, note: 'BERTopic is not installed — using the built-in deterministic TF-IDF topic model (same results as \'tfidf\', with richer metadata).' },
      },
      ollama_reachable: false,
    },
    reindex: { model_name: 'hash-bow-v1', indexed: 3, total: 5 },
    ollama_reachable: false,
    bertopic_installed: false,
    sentence_transformers_installed: false,
    active: {
      embedding: { selected: 'hash_bow', available: true, note: 'Default, dependency-free.' },
      summary: { selected: 'extractive', available: true, note: 'Default, dependency-free.' },
      topic: { selected: 'tfidf', available: true, note: 'Default, dependency-free.' },
    },
    ...overrides,
  };
}

function makeClient(status: AiStatus) {
  return {
    getAiStatus: vi.fn().mockResolvedValue(status),
    listAiModels: vi.fn().mockResolvedValue({ models: [] }),
  };
}

describe('AiModelsPanel', () => {
  it('renders a capability card per feature with what-it-does + used-for help', async () => {
    const client = makeClient(makeStatus());
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());

    expect(await screen.findByText('Semantic search & related papers')).toBeTruthy();
    expect(screen.getByText('Topic modeling')).toBeTruthy();
    expect(screen.getByText('Scope summaries')).toBeTruthy();
    expect(screen.getByText('Keyword extraction')).toBeTruthy();
    // The used-for help line renders.
    expect(screen.getByText(/Used for: the semantic search box/i)).toBeTruthy();
    // A status badge with a reason renders (green baseline for the always-on hash-BOW default).
    expect(screen.getAllByText('Built-in baseline').length).toBeGreaterThan(0);
  });

  it('shows the BERTopic honesty banner when the topic backend pretends to be advanced', async () => {
    const status = makeStatus();
    status.config.topic_backend = 'bertopic';
    const client = makeClient(status);
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());

    expect(await screen.findByText(/BERTopic isn't installed; this uses the built-in TF-IDF topic model/i)).toBeTruthy();
  });

  it('surfaces the reason when the active embedding provider is unavailable', async () => {
    const status = makeStatus();
    status.config.embedding_provider = 'ollama';
    status.active.embedding = {
      selected: 'ollama',
      available: false,
      note: 'Start the Ollama profile (make up-ai) and set its URL.',
    };
    const client = makeClient(status);
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());

    // The red "falls back" badge + its reason are shown.
    expect(await screen.findByText('Falls back to hash-BOW')).toBeTruthy();
    expect(screen.getAllByText(/Start the Ollama profile/i).length).toBeGreaterThan(0);
  });
});
