import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiClient, EXPORT_FORMATS } from './client';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('ApiClient request contracts', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('adds bearer auth and serializes structured work filters', async () => {
    const client = new ApiClient('http://api.test', 'token-123');

    await client.listWorks({
      q: 'attention',
      readingStatus: 'reading',
      shelfId: 'shelf-1',
      rackId: 'rack-1',
      tagId: 'tag-1',
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url);

    expect(parsed.pathname).toBe('/api/v1/works');
    expect(parsed.searchParams.get('q')).toBe('attention');
    expect(parsed.searchParams.get('reading_status')).toBe('reading');
    expect(parsed.searchParams.get('shelf_id')).toBe('shelf-1');
    expect(parsed.searchParams.get('rack_id')).toBe('rack-1');
    expect(parsed.searchParams.get('tag_id')).toBe('tag-1');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-123');
  });

  it('does not attach bearer auth to login requests', async () => {
    fetchMock = vi.fn(async () => jsonResponse({ access_token: 'server-token' }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient('http://api.test', 'old-token');

    const token = await client.login('owner', 'secret');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe('http://api.test/api/v1/auth/login');
    expect(headers.Authorization).toBeUndefined();
    expect(token).toBe('server-token');
  });

  it('uploads PDFs as FormData without forcing a JSON content type', async () => {
    fetchMock = vi.fn(async () => jsonResponse({ id: 'batch-1' }, 201));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient('http://api.test', 'token-123');
    const file = new File(['%PDF-1.4\n'], 'paper.pdf', { type: 'application/pdf' });

    await client.uploadPdf(file);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe('http://api.test/api/v1/imports/upload');
    expect(headers.Authorization).toBe('Bearer token-123');
    expect(headers['Content-Type']).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
  });

  it('serializes citationGraph work_ids and collapse_versions', async () => {
    fetchMock = vi.fn(async () => jsonResponse({ nodes: [], edges: [], summary: {} }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient('http://api.test', 'token-123');

    await client.citationGraph({
      scopeType: 'selected_papers',
      workIds: ['w1', 'w2'],
      nodeMode: 'local_only',
      collapseVersions: true,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://api.test/api/v1/graphs/citation');
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      scope: { type: 'selected_papers', id: null, work_ids: ['w1', 'w2'] },
      node_mode: 'local_only',
      collapse_versions: true,
    });
  });

  it('requests the import-batches list from the right URL', async () => {
    const client = new ApiClient('http://api.test', 'token-123');

    await client.listImportBatches();

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://api.test/api/v1/imports/batches');
  });

  it('keeps the supported citation export format set stable', () => {
    expect(EXPORT_FORMATS.map((format) => format.value).sort()).toEqual([
      'biblatex',
      'bibtex',
      'csl-json',
      'html',
      'markdown',
      'ris',
      'styled',
      'text',
    ]);
  });
});
