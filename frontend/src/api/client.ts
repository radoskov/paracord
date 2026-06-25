export type ReadingStatus = 'unread' | 'skimmed' | 'reading' | 'read' | 'important' | 'revisit';

export interface Work {
  id: string;
  canonical_title: string | null;
  abstract: string | null;
  doi: string | null;
  arxiv_id: string | null;
  venue: string | null;
  year: number | null;
  reading_status: ReadingStatus;
  created_at: string;
  updated_at: string;
}

export type GraphScopeType = 'library' | 'shelf' | 'rack';
export type GraphNodeMode = 'local_only' | 'include_external';

export interface GraphNode {
  id: string;
  label: string;
  type: 'local' | 'external';
  work_id: string | null;
  year: number | null;
  doi: string | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
  resolution: string;
}

export interface CitationGraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  summary: Record<string, number>;
}

export type ExportScopeType = 'work' | 'shelf' | 'rack';
export type ExportFormat = 'bibtex' | 'biblatex' | 'ris' | 'csl-json' | 'markdown' | 'html' | 'text';

export const EXPORT_FORMATS: { value: ExportFormat; label: string }[] = [
  { value: 'bibtex', label: 'BibTeX' },
  { value: 'biblatex', label: 'BibLaTeX' },
  { value: 'ris', label: 'RIS' },
  { value: 'csl-json', label: 'CSL JSON' },
  { value: 'markdown', label: 'Markdown' },
  { value: 'html', label: 'HTML' },
  { value: 'text', label: 'Plain text' },
];

export interface ExportResponse {
  filename: string;
  content_type: string;
  content: string;
}

export interface Shelf {
  id: string;
  name: string;
  description: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Rack {
  id: string;
  name: string;
  description: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Tag {
  id: string;
  name: string;
  normalized_name: string;
  color: string | null;
  description: string | null;
  created_at: string;
}

export interface Source {
  id: string;
  type: string;
  name: string;
  path_alias: string | null;
  is_active: boolean;
}

export interface FileRecord {
  id: string;
  sha256: string;
  size_bytes: number;
  mime_type: string | null;
  original_filename: string | null;
  page_count: number | null;
  text_layer_quality: string;
  status: string;
  preview_text: string | null;
  created_at: string;
  last_seen_at: string | null;
}

export interface ImportBatch {
  id: string;
  source_id: string | null;
  input_type: string;
  status: string;
  stats: Record<string, number> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface CitationContext {
  id: string;
  reference_id: string;
  resolved_cited_work_id: string | null;
  reference_title: string | null;
  reference_raw_citation: string | null;
  reference_doi: string | null;
  marker_text: string | null;
  section_label: string | null;
  context_before: string | null;
  context_sentence: string | null;
  context_after: string | null;
  page: number | null;
  source_tei_id: string | null;
}

export interface Annotation {
  id: string;
  work_id: string;
  file_id: string | null;
  version_id: string | null;
  page: number | null;
  coordinates: Record<string, unknown> | null;
  selected_text: string | null;
  annotation_type: string;
  content_markdown: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnnotationCreate {
  annotation_type: string;
  file_id?: string | null;
  version_id?: string | null;
  page?: number | null;
  coordinates?: Record<string, unknown> | null;
  selected_text?: string | null;
  content_markdown?: string | null;
}

export type DuplicateCandidateStatus = 'open' | 'accepted' | 'rejected' | 'ignored';
export type DuplicateCandidateAction =
  | 'merge_works'
  | 'link_as_version'
  | 'mark_duplicate_file'
  | 'keep_separate'
  | 'ignore';

export interface DuplicateCandidate {
  id: string;
  candidate_type: string;
  entity_a_type: string;
  entity_a_id: string;
  entity_b_type: string;
  entity_b_id: string;
  score: number;
  signals: Record<string, unknown>;
  status: DuplicateCandidateStatus;
  created_at: string;
  resolved_by_user_id: string | null;
  resolved_at: string | null;
  entity_a_label: string | null;
  entity_b_label: string | null;
  suggested_target_work_id: string | null;
  summary: string | null;
}

export interface DuplicateScanResult {
  scanned_works: number;
  scanned_files: number;
  candidate_count: number;
  candidates: DuplicateCandidate[];
}

export interface DuplicateSplitSegment {
  title: string;
  page_start?: number;
  page_end?: number;
  label?: string;
}

export interface WorkQuery {
  q?: string;
  readingStatus?: string;
  shelfId?: string;
  rackId?: string;
  tagId?: string;
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string | null = null,
  ) {}

  withToken(token: string | null): ApiClient {
    return new ApiClient(this.baseUrl, token);
  }

  async login(username: string, password: string): Promise<string> {
    const response = await this.request<{ access_token: string }>('/api/v1/auth/login', {
      method: 'POST',
      body: { username, password },
      auth: false,
    });
    return response.access_token;
  }

  async listWorks(query: WorkQuery = {}): Promise<Work[]> {
    const params = new URLSearchParams();
    if (query.q) params.set('q', query.q);
    if (query.readingStatus) params.set('reading_status', query.readingStatus);
    if (query.shelfId) params.set('shelf_id', query.shelfId);
    if (query.rackId) params.set('rack_id', query.rackId);
    if (query.tagId) params.set('tag_id', query.tagId);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return this.request<Work[]>(`/api/v1/works${suffix}`);
  }

  async createWork(payload: Partial<Work>): Promise<Work> {
    return this.request<Work>('/api/v1/works', { method: 'POST', body: payload });
  }

  async updateWork(id: string, payload: Partial<Work>): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${id}`, { method: 'PATCH', body: payload });
  }

  async listCitationContexts(workId: string): Promise<CitationContext[]> {
    return this.request<CitationContext[]>(`/api/v1/works/${workId}/citation-contexts`);
  }

  async listAnnotations(workId: string): Promise<Annotation[]> {
    return this.request<Annotation[]>(`/api/v1/works/${workId}/annotations`);
  }

  async createAnnotation(workId: string, payload: AnnotationCreate): Promise<Annotation> {
    return this.request<Annotation>(`/api/v1/works/${workId}/annotations`, {
      method: 'POST',
      body: payload,
    });
  }

  async listDuplicateCandidates(
    status: DuplicateCandidateStatus | '' = 'open',
  ): Promise<DuplicateCandidate[]> {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return this.request<DuplicateCandidate[]>(`/api/v1/duplicates${suffix}`);
  }

  async scanDuplicateCandidates(
    payload: { work_id?: string; file_id?: string } = {},
  ): Promise<DuplicateScanResult> {
    return this.request<DuplicateScanResult>('/api/v1/duplicates/scan', {
      method: 'POST',
      body: payload,
    });
  }

  async updateDuplicateCandidate(
    id: string,
    status: DuplicateCandidateStatus,
  ): Promise<DuplicateCandidate> {
    return this.request<DuplicateCandidate>(`/api/v1/duplicates/${id}`, {
      method: 'PATCH',
      body: { status },
    });
  }

  async applyDuplicateCandidateAction(
    id: string,
    action: DuplicateCandidateAction,
    options: { targetWorkId?: string; splitSegments?: DuplicateSplitSegment[] } = {},
  ): Promise<DuplicateCandidate> {
    return this.request<DuplicateCandidate>(`/api/v1/duplicates/${id}`, {
      method: 'PATCH',
      body: {
        action,
        target_work_id: options.targetWorkId,
        split_segments: options.splitSegments,
      },
    });
  }

  async listShelves(): Promise<Shelf[]> {
    return this.request<Shelf[]>('/api/v1/shelves');
  }

  async createShelf(payload: { name: string; description?: string }): Promise<Shelf> {
    return this.request<Shelf>('/api/v1/shelves', { method: 'POST', body: payload });
  }

  async updateShelf(id: string, payload: Partial<Shelf>): Promise<Shelf> {
    return this.request<Shelf>(`/api/v1/shelves/${id}`, { method: 'PATCH', body: payload });
  }

  async listShelfWorks(shelfId: string): Promise<Work[]> {
    return this.request<Work[]>(`/api/v1/shelves/${shelfId}/works`);
  }

  async addWorkToShelf(shelfId: string, workId: string): Promise<void> {
    await this.request<void>(`/api/v1/shelves/${shelfId}/works`, {
      method: 'POST',
      body: { work_id: workId },
    });
  }

  async removeWorkFromShelf(shelfId: string, workId: string): Promise<void> {
    await this.request<void>(`/api/v1/shelves/${shelfId}/works/${workId}`, {
      method: 'DELETE',
    });
  }

  async listRacks(): Promise<Rack[]> {
    return this.request<Rack[]>('/api/v1/racks');
  }

  async createRack(payload: { name: string; description?: string }): Promise<Rack> {
    return this.request<Rack>('/api/v1/racks', { method: 'POST', body: payload });
  }

  async updateRack(id: string, payload: Partial<Rack>): Promise<Rack> {
    return this.request<Rack>(`/api/v1/racks/${id}`, { method: 'PATCH', body: payload });
  }

  async listRackShelves(rackId: string): Promise<Shelf[]> {
    return this.request<Shelf[]>(`/api/v1/racks/${rackId}/shelves`);
  }

  async addShelfToRack(rackId: string, shelfId: string): Promise<void> {
    await this.request<void>(`/api/v1/racks/${rackId}/shelves`, {
      method: 'POST',
      body: { shelf_id: shelfId },
    });
  }

  async removeShelfFromRack(rackId: string, shelfId: string): Promise<void> {
    await this.request<void>(`/api/v1/racks/${rackId}/shelves/${shelfId}`, {
      method: 'DELETE',
    });
  }

  async listTags(): Promise<Tag[]> {
    return this.request<Tag[]>('/api/v1/tags');
  }

  async createTag(payload: { name: string; color?: string; description?: string }): Promise<Tag> {
    return this.request<Tag>('/api/v1/tags', { method: 'POST', body: payload });
  }

  async addTagLink(tagId: string, entityType: string, entityId: string): Promise<void> {
    await this.request<void>(`/api/v1/tags/${tagId}/links`, {
      method: 'POST',
      body: { entity_type: entityType, entity_id: entityId },
    });
  }

  async removeTagLink(tagId: string, entityType: string, entityId: string): Promise<void> {
    const params = new URLSearchParams({ entity_type: entityType, entity_id: entityId });
    await this.request<void>(`/api/v1/tags/${tagId}/links?${params.toString()}`, {
      method: 'DELETE',
    });
  }

  async listSources(): Promise<Source[]> {
    return this.request<Source[]>('/api/v1/sources');
  }

  async createServerFolderSource(payload: { name: string; path_alias: string }): Promise<Source> {
    return this.request<Source>('/api/v1/sources/server-folder', {
      method: 'POST',
      body: payload,
    });
  }

  async importFolder(sourceId: string): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/folder', {
      method: 'POST',
      body: { source_id: sourceId, recursive: true },
    });
  }

  async listFiles(): Promise<FileRecord[]> {
    return this.request<FileRecord[]>('/api/v1/files');
  }

  async getFileBlob(fileId: string): Promise<Blob> {
    return this.requestBlob(`/api/v1/files/${fileId}/stream`);
  }

  async citationGraph(payload: {
    scopeType: GraphScopeType;
    scopeId?: string | null;
    nodeMode: GraphNodeMode;
  }): Promise<CitationGraphResponse> {
    return this.request<CitationGraphResponse>('/api/v1/graphs/citation', {
      method: 'POST',
      body: {
        scope: { type: payload.scopeType, id: payload.scopeId ?? null },
        node_mode: payload.nodeMode,
      },
    });
  }

  async exportCitations(payload: {
    scope_type: ExportScopeType;
    scope_id: string;
    format: ExportFormat;
  }): Promise<ExportResponse> {
    return this.request<ExportResponse>('/api/v1/exports', { method: 'POST', body: payload });
  }

  private async request<T>(
    path: string,
    options: { method?: string; body?: unknown; auth?: boolean } = {},
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.auth !== false && this.token) headers.Authorization = `Bearer ${this.token}`;

    const response = await fetch(`${this.baseUrl}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });

    if (!response.ok) {
      let detail = `Request failed: ${response.status}`;
      try {
        const payload = await response.json();
        detail = payload.detail ?? detail;
      } catch {
        // Keep the status-based message when the response is not JSON.
      }
      throw new Error(detail);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  private async requestBlob(path: string): Promise<Blob> {
    const headers: Record<string, string> = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(`${this.baseUrl}${path}`, { headers });
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return response.blob();
  }
}
