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

  it('maps pagination params and returns the paginated envelope', async () => {
    fetchMock = vi.fn(async () =>
      jsonResponse({ items: [{ id: 'w1' }], total: 1, page: 2, pages: 5, per_page: 25 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient('http://api.test', 'token-123');

    const result = await client.listWorks({ page: 2, perPage: 25 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url);
    expect(parsed.searchParams.get('page')).toBe('2');
    expect(parsed.searchParams.get('per_page')).toBe('25');
    expect(result.total).toBe(1);
    expect(result.pages).toBe(5);
    expect(result.items).toHaveLength(1);
  });

  it('reads and updates the admin app-config (global max papers per page)', async () => {
    fetchMock = vi.fn(async () => jsonResponse({ max_papers_per_page: 300 }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient('http://api.test', 'token-123');

    const read = await client.getAppConfig();
    expect(read.max_papers_per_page).toBe(300);

    await client.updateAppConfig({ max_papers_per_page: 300 });
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new URL(url).pathname).toBe('/api/v1/admin/app-config');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ max_papers_per_page: 300 });
  });

  it('fires onQueueFull for a 429 "queue is full" rejection but not for a rate-limit 429', async () => {
    const onQueueFull = vi.fn();
    const client = new ApiClient('http://api.test', 'token-123', undefined, onQueueFull);

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'The processing queue is full (1000 pending). Please wait and retry.' }, 429),
    );
    await expect(client.extractFile('file-1')).rejects.toThrow(/queue is full/i);
    expect(onQueueFull).toHaveBeenCalledTimes(1);
    expect(onQueueFull.mock.calls[0][0]).toMatch(/queue is full/i);

    // A rate-limit 429 has a different detail and must NOT trigger the queue-full toast.
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'Rate limit exceeded; slow down and retry shortly.' }, 429),
    );
    await expect(client.extractFile('file-2')).rejects.toThrow(/Rate limit/);
    expect(onQueueFull).toHaveBeenCalledTimes(1);
  });

  it('clears the queue and resets workers via the admin job endpoints', async () => {
    fetchMock = vi.fn(async () => jsonResponse({ available: true, dropped: 4 }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient('http://api.test', 'token-123');

    const cleared = await client.clearQueue();
    expect(cleared.dropped).toBe(4);
    const [clearUrl, clearInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(clearUrl).pathname).toBe('/api/v1/jobs/clear-queue');
    expect(clearInit.method).toBe('POST');

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ available: true, requeued: 1, cleared_failed: 2, note: 'restart' }),
    );
    await client.resetWorkers();
    const [resetUrl, resetInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new URL(resetUrl).pathname).toBe('/api/v1/jobs/reset-workers');
    expect(resetInit.method).toBe('POST');
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
      color_by: 'none',
      max_external: 50,
      max_external_citing: 50,
      include_citing: true,
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
      'latex',
      'markdown',
      'pandoc',
      'ris',
      'styled',
      'text',
    ]);
  });
});
