import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiClient } from './client';

const jsonResponse = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  });

const fetchMock = () => fetch as unknown as ReturnType<typeof vi.fn>;

describe('ApiClient request contracts', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('serializes structured library filters and attaches bearer auth', async () => {
    fetchMock().mockResolvedValue(
      jsonResponse({ items: [], total: 0, page: 2, pages: 0, per_page: 50 }),
    );

    const client = new ApiClient('http://api.example', 'token-123');
    await client.listWorks({
      q: 'graph retrieval',
      readingStatus: 'reading',
      shelfId: 'shelf-1',
      rackId: 'rack-1',
      tagId: 'tag-1',
      hasPdf: true,
      hasReferences: false,
      missing: ['doi', 'abstract'],
      sort: 'updated_at',
      order: 'desc',
      page: 2,
      perPage: 50,
    });

    const [url, init] = fetchMock().mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url);

    expect(parsed.pathname).toBe('/api/v1/works');
    expect(parsed.searchParams.get('q')).toBe('graph retrieval');
    expect(parsed.searchParams.get('reading_status')).toBe('reading');
    expect(parsed.searchParams.get('shelf_id')).toBe('shelf-1');
    expect(parsed.searchParams.get('rack_id')).toBe('rack-1');
    expect(parsed.searchParams.get('tag_id')).toBe('tag-1');
    expect(parsed.searchParams.get('has_pdf')).toBe('true');
    expect(parsed.searchParams.get('has_references')).toBe('false');
    expect(parsed.searchParams.get('missing')).toBe('doi,abstract');
    expect(parsed.searchParams.get('sort')).toBe('updated_at');
    expect(parsed.searchParams.get('order')).toBe('desc');
    expect(parsed.searchParams.get('page')).toBe('2');
    expect(parsed.searchParams.get('per_page')).toBe('50');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-123');
  });

  it('serializes a multi-column sort as one comma-joined key:order param', async () => {
    fetchMock().mockResolvedValue(
      jsonResponse({ items: [], total: 0, page: 1, pages: 0, per_page: 50 }),
    );
    const client = new ApiClient('http://api.example', 'token-123');
    await client.listWorks({
      sorts: [
        { key: 'year', order: 'desc' },
        { key: 'title', order: 'asc' },
      ],
      // `sort`/`order` are ignored when `sorts` is present.
      sort: 'updated_at',
      order: 'desc',
    });
    const [url] = fetchMock().mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url);
    expect(parsed.searchParams.get('sort')).toBe('year:desc,title:asc');
    expect(parsed.searchParams.get('order')).toBeNull();
  });

  it('serializes the advanced multi-tag filter as repeated params', async () => {
    fetchMock().mockResolvedValue(
      jsonResponse({ items: [], total: 0, page: 1, pages: 0, per_page: 50 }),
    );
    const client = new ApiClient('http://api.example', 'token-123');
    await client.listWorks({
      tagAny: ['ml', 'nlp'],
      tagAll: ['must'],
      tagNone: ['old'],
    });
    const [url] = fetchMock().mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url);
    expect(parsed.searchParams.getAll('tag_any')).toEqual(['ml', 'nlp']);
    expect(parsed.searchParams.getAll('tag_all')).toEqual(['must']);
    expect(parsed.searchParams.getAll('tag_none')).toEqual(['old']);
  });

  it('does not attach stale bearer auth to login', async () => {
    fetchMock().mockResolvedValue(jsonResponse({ access_token: 'new-token' }));

    const client = new ApiClient('http://api.example', 'stale-token');
    const token = await client.login('user', 'pass');

    const [_url, init] = fetchMock().mock.calls[0] as [string, RequestInit];
    expect(token).toBe('new-token');
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(init.method).toBe('POST');
  });

  it('routes queue-full responses through the configured callback', async () => {
    const onQueueFull = vi.fn();
    fetchMock().mockResolvedValue(jsonResponse({ detail: 'The processing queue is full' }, { status: 429 }));

    const client = new ApiClient('http://api.example', 'token-123', undefined, onQueueFull);

    await expect(client.createWork({ canonical_title: 'queued paper' })).rejects.toThrow(
      /queue is full/i,
    );
    expect(onQueueFull).toHaveBeenCalledWith('The processing queue is full');
  });

  it('uploads PDFs as FormData without forcing a JSON content type', async () => {
    fetchMock().mockResolvedValue(jsonResponse({ id: 'batch-1' }));

    const client = new ApiClient('http://api.example', 'token-123');
    const file = new File(['%PDF-1.4\n%%EOF\n'], 'paper.pdf', { type: 'application/pdf' });

    await client.uploadPdf(file, 'shelf-1');

    const [url, init] = fetchMock().mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;

    expect(url).toBe('http://api.example/api/v1/imports/upload');
    expect(init.method).toBe('POST');
    expect(headers.Authorization).toBe('Bearer token-123');
    expect(headers['Content-Type']).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
  });
});
