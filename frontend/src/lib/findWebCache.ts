// Session cache for find-on-web results, keyed by work id (#4). Reopening the find-on-web modal on
// the SAME paper restores the cached candidates/state without re-running the (slow) web search.
// The cache is reset only when a search is run on a different paper or the user hits "Reset".
// It lives in module scope so it survives WorkDetail re-mounts within a session; a page reload
// clears it, which is fine.
import type { WebCandidate, WebFindDownloadResult } from '../api/client';

export interface SourceProgress {
  source: string;
  status: 'querying' | 'done' | 'failed';
  count?: number;
}

export interface FindWebCacheEntry {
  findResults: WebCandidate[];
  degradedSources: string[];
  sourceProgress: SourceProgress[];
  selectedIds: string[];
  downloadStatus: Record<string, WebFindDownloadResult>;
}

const cache = new Map<string, FindWebCacheEntry>();

export function getFindWebCache(workId: string): FindWebCacheEntry | undefined {
  return cache.get(workId);
}

export function setFindWebCache(workId: string, entry: FindWebCacheEntry): void {
  cache.set(workId, entry);
}

export function clearFindWebCache(workId: string): void {
  cache.delete(workId);
}
