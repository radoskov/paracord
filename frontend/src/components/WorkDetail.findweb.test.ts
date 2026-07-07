import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { WebFindStreamEvent, Work } from '../api/client';
import { clearFindWebCache } from '../lib/findWebCache';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

const WORK: Work = {
  id: 'w1',
  canonical_title: 'Deep Residual Learning',
  abstract: null,
  doi: '10.1/src',
  arxiv_id: null,
  venue: 'CVPR',
  year: 2016,
  reading_status: 'unread',
  canonical_metadata_source: null,
  confirmed_fields: [],
  keywords: [],
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
} as unknown as Work;

const CANDIDATES = [
  {
    candidate_id: 'c1',
    source: 'openalex',
    sources: ['openalex'],
    title: 'Deep Residual Learning for Image Recognition',
    authors: ['Kaiming He'],
    year: 2016,
    doi: '10.1/x',
    arxiv_id: '1512.03385',
    pdf_url: 'https://arxiv.org/pdf/1512.03385.pdf',
    landing_url: 'https://arxiv.org/abs/1512.03385',
    resolved_url: 'https://arxiv.org/pdf/1512.03385.pdf',
    platform: 'arxiv.org',
    is_oa: true,
    score: 0.95,
  },
  {
    candidate_id: 'c2',
    source: 'crossref',
    sources: ['crossref'],
    title: 'Another Candidate',
    authors: [],
    year: 2015,
    doi: '10.1/y',
    arxiv_id: null,
    pdf_url: null,
    landing_url: 'https://doi.org/10.1/y',
    resolved_url: 'https://www.sciencedirect.com/science/article/pii/123',
    platform: 'sciencedirect.com',
    is_oa: false,
    score: 0.4,
  },
];

// A streamFindOnWeb mock that synchronously emits a realistic event sequence to onEvent.
function streamMock(events: WebFindStreamEvent[]) {
  return vi.fn(async (_id: string, onEvent: (e: WebFindStreamEvent) => void) => {
    for (const e of events) onEvent(e);
  });
}

const DEFAULT_EVENTS: WebFindStreamEvent[] = [
  { type: 'source', source: 'openalex', status: 'querying' },
  { type: 'source', source: 'crossref', status: 'querying' },
  { type: 'source', source: 'semanticscholar', status: 'querying' },
  { type: 'source', source: 'openalex', status: 'done', count: 1 },
  { type: 'source', source: 'crossref', status: 'done', count: 1 },
  { type: 'source', source: 'semanticscholar', status: 'failed' },
  {
    type: 'result',
    candidates: CANDIDATES,
    degraded_sources: ['semanticscholar'],
    queried_sources: ['openalex', 'crossref', 'semanticscholar'],
  },
];

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
    streamFindOnWeb: streamMock(DEFAULT_EVENTS),
    findOnWeb: vi.fn(),
    downloadWebCandidates: vi.fn(),
    getJobs: vi.fn().mockResolvedValue({ available: false, workers: 0, counts: {}, jobs: [] }),
    getWork: vi.fn().mockResolvedValue(WORK),
    ...overrides,
  };
}

describe('WorkDetail find-on-web picker (v2)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
    // Find-on-web results are cached per work id across the session (#4); clear between tests so
    // each starts with a fresh search rather than a prior test's cached candidates.
    clearFindWebCache(WORK.id);
  });

  it('shows the paper-info header, streaming progress, candidates, and gates Download by selection', async () => {
    const downloadWebCandidates = vi.fn().mockResolvedValue({
      results: [{ candidate_id: 'c1', status: 'attached', reason: null, file: null }],
    });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await waitFor(() => expect(client.streamFindOnWeb).toHaveBeenCalled());

    // Paper-info header surfaces the source paper's title + metadata.
    expect(screen.getByText(/Searching for this paper/i)).toBeTruthy();
    expect(screen.getByText(/doi:10\.1\/src/)).toBeTruthy();

    // Per-source progress rows render (done counts + a failed source).
    expect(screen.getAllByText(/✓ 1 match/).length).toBe(2);
    expect(screen.getByText(/✗ failed/)).toBeTruthy();

    // Candidates and the degraded-source note render.
    expect(await screen.findByText(/Deep Residual Learning for Image Recognition/)).toBeTruthy();
    expect(screen.getAllByText(/semanticscholar/).length).toBeGreaterThan(0);

    // Sticky-bar Download is disabled with nothing selected.
    const downloadBtn = screen.getByRole('button', { name: /download selected/i }) as HTMLButtonElement;
    expect(downloadBtn.disabled).toBe(true);

    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    await fireEvent.click(checkboxes[0]);
    expect(downloadBtn.disabled).toBe(false);
    expect(downloadBtn.textContent).toContain('(1)');

    await fireEvent.click(downloadBtn);
    // Downloads go one item at a time.
    await waitFor(() =>
      expect(downloadWebCandidates).toHaveBeenCalledWith('w1', [
        {
          candidate_id: 'c1',
          url: 'https://arxiv.org/pdf/1512.03385.pdf',
          source: 'openalex',
          doi: '10.1/x',
          arxiv_id: '1512.03385',
        },
      ]),
    );
    expect(await screen.findByText(/Attached/)).toBeTruthy();
    // Total progress advanced to 1/1.
    expect(screen.getByText('1/1 downloaded')).toBeTruthy();
  });

  it('renders platform labels, a View link, and enables Download for any candidate with a fetchable URL (pdf, resolved, or landing)', async () => {
    const client = makeClient();
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await screen.findByText(/Deep Residual Learning for Image Recognition/);

    // Platform badges render for both candidates ("via <host>").
    expect(screen.getByText(/via arxiv\.org/)).toBeTruthy();
    expect(screen.getByText(/via sciencedirect\.com/)).toBeTruthy();

    // The PDF-less candidate (c2) keeps a working View link (targets its resolved_url) and shows
    // the "no direct PDF — we'll try the landing page" reason.
    const viewLinks = screen.getAllByRole('link', { name: /View/i }) as HTMLAnchorElement[];
    expect(viewLinks.length).toBe(2);
    expect(
      viewLinks.some((a) => a.href === 'https://www.sciencedirect.com/science/article/pii/123'),
    ).toBe(true);
    expect(screen.getByText(/No direct PDF link/)).toBeTruthy();

    // item 4: c1 (pdf_url) AND c2 (resolved_url only, no pdf_url) are both attemptable → both
    // checkboxes enabled. c2's hint explains the publisher/landing-page fallback.
    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    expect(checkboxes[0].disabled).toBe(false);
    expect(checkboxes[1].disabled).toBe(false);
    expect(checkboxes[1].title).toMatch(/publisher\/landing page/i);
  });

  it('downloads a PDF-less candidate by attempting its resolved/landing URL', async () => {
    const downloadWebCandidates = vi.fn().mockResolvedValue({
      results: [
        { candidate_id: 'c2', status: 'manual_upload_needed', reason: 'login wall', file: null },
      ],
    });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await screen.findByText(/Deep Residual Learning for Image Recognition/);

    // Select the PDF-less candidate (c2) and download — it sends c2's resolved_url as the item URL.
    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    await fireEvent.click(checkboxes[1]);
    await fireEvent.click(screen.getByRole('button', { name: /download selected/i }));

    await waitFor(() =>
      expect(downloadWebCandidates).toHaveBeenCalledWith('w1', [
        {
          candidate_id: 'c2',
          url: 'https://www.sciencedirect.com/science/article/pii/123',
          source: 'crossref',
          doi: '10.1/y',
          arxiv_id: null,
        },
      ]),
    );
    // The publisher page needed a browser session → manual-upload fallback surfaces.
    expect(await screen.findByText(/Could not download automatically/)).toBeTruthy();
  });

  it('shows a manual-upload fallback when a download cannot complete', async () => {
    const downloadWebCandidates = vi.fn().mockResolvedValue({
      results: [{ candidate_id: 'c1', status: 'manual_upload_needed', reason: 'login wall', file: null }],
    });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await screen.findByText(/Deep Residual Learning for Image Recognition/);
    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    await fireEvent.click(checkboxes[0]);
    await fireEvent.click(screen.getByRole('button', { name: /download selected/i }));

    expect(await screen.findByText(/Could not download automatically/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Upload the PDF manually/i })).toBeTruthy();
  });

  it('prompts on needs_confirmation and re-sends the item with confirmed:true on confirm', async () => {
    const downloadWebCandidates = vi
      .fn()
      // First call: server asks for confirmation (unknown host).
      .mockResolvedValueOnce({
        results: [
          {
            candidate_id: 'c1',
            status: 'needs_confirmation',
            reason: 'Host not on the allow-list',
            url: 'https://arxiv.org/pdf/1512.03385.pdf',
            file: null,
          },
        ],
      })
      // Second call (after confirm): attached.
      .mockResolvedValueOnce({
        results: [{ candidate_id: 'c1', status: 'attached', reason: null, file: null }],
      });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await screen.findByText(/Deep Residual Learning for Image Recognition/);
    await fireEvent.click((screen.getAllByRole('checkbox') as HTMLInputElement[])[0]);
    await fireEvent.click(screen.getByRole('button', { name: /download selected/i }));

    // Confirmation dialog appears with the URL and reason.
    expect(await screen.findByText(/not on the allow-list and is not a known publisher/i)).toBeTruthy();
    await fireEvent.click(screen.getByRole('button', { name: /download anyway/i }));

    // Second send carried confirmed:true; the row ends attached.
    await waitFor(() =>
      expect(downloadWebCandidates).toHaveBeenNthCalledWith(2, 'w1', [
        {
          candidate_id: 'c1',
          url: 'https://arxiv.org/pdf/1512.03385.pdf',
          source: 'openalex',
          doi: '10.1/x',
          arxiv_id: '1512.03385',
          confirmed: true,
        },
      ]),
    );
    expect(await screen.findByText(/Attached/)).toBeTruthy();
  });

  it('shows a blocked row with its reason and offers no confirmation', async () => {
    const downloadWebCandidates = vi.fn().mockResolvedValue({
      results: [
        { candidate_id: 'c1', status: 'blocked', reason: 'Shadow library denylisted', file: null },
      ],
    });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await screen.findByText(/Deep Residual Learning for Image Recognition/);
    await fireEvent.click((screen.getAllByRole('checkbox') as HTMLInputElement[])[0]);
    await fireEvent.click(screen.getByRole('button', { name: /download selected/i }));

    expect(await screen.findByText(/Blocked: Shadow library denylisted/)).toBeTruthy();
    // A hard block never offers a "download anyway" confirmation and never re-sends.
    expect(screen.queryByRole('button', { name: /download anyway/i })).toBeNull();
    expect(downloadWebCandidates).toHaveBeenCalledTimes(1);
  });

  it('falls back to the non-streaming search if streaming errors', async () => {
    const client = makeClient({
      streamFindOnWeb: vi.fn().mockRejectedValue(new Error('no stream')),
      findOnWeb: vi.fn().mockResolvedValue({
        candidates: CANDIDATES,
        degraded_sources: [],
        queried_sources: ['openalex', 'crossref'],
      }),
    });
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await waitFor(() => expect(client.findOnWeb).toHaveBeenCalledWith('w1'));
    expect(await screen.findByText(/Deep Residual Learning for Image Recognition/)).toBeTruthy();
  });
});
