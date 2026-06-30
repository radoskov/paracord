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
  confirmed_fields?: string[];
  keywords?: string[];
  // The user who created this paper (null for system/loose-imported papers). Drives the
  // "can I modify this paper" gate: a contributor may only edit their own papers.
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface RelatedWork {
  work: Work;
  score: number;
  shared_keywords: string[];
  reason: string;
}

export interface SemanticSearchItem {
  work_id: string;
  title: string | null;
  year: number | null;
  score: number;
}

export interface SemanticSearchResponse {
  query: string;
  items: SemanticSearchItem[];
}

export type SummaryType = 'abstract' | 'extractive';

export interface Summary {
  id: string;
  entity_type: string;
  entity_id: string;
  summary_type: string;
  text: string;
  model_name: string | null;
  prompt_version: string | null;
  created_at: string;
}

export interface Topic {
  topic_id: number;
  keywords: string[];
  work_count: number;
}

export interface TopicModelResponse {
  model_id: string;
  scope_type: string;
  scope_id: string | null;
  work_count: number;
  topics: Topic[];
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

export type ExportScopeType = 'work' | 'shelf' | 'rack' | 'library' | 'selection' | 'search';
export type ExportFormat =
  | 'bibtex'
  | 'biblatex'
  | 'ris'
  | 'csl-json'
  | 'markdown'
  | 'html'
  | 'text'
  | 'styled';

export const EXPORT_FORMATS: { value: ExportFormat; label: string }[] = [
  { value: 'bibtex', label: 'BibTeX' },
  { value: 'biblatex', label: 'BibLaTeX' },
  { value: 'ris', label: 'RIS' },
  { value: 'csl-json', label: 'CSL JSON' },
  { value: 'markdown', label: 'Markdown' },
  { value: 'html', label: 'HTML' },
  { value: 'text', label: 'Plain text' },
  { value: 'styled', label: 'Styled (APA/IEEE/…)' },
];

export const CITATION_STYLES = ['apa', 'ieee', 'chicago'] as const;

export interface ExportResponse {
  filename: string;
  content_type: string;
  content: string;
}

// Access level governing who may see / modify a rack or shelf (and, transitively, papers).
export type AccessLevel = 'open' | 'visible' | 'private';

export interface Shelf {
  id: string;
  name: string;
  description: string | null;
  status: string;
  access_level: AccessLevel;
  created_at: string;
  updated_at: string;
}

export interface Rack {
  id: string;
  name: string;
  description: string | null;
  status: string;
  access_level: AccessLevel;
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

// A merged server-folder import root: yaml-fixed (read-only) or DB-managed (owner-removable).
export interface ServerImportRoot {
  alias: string;
  path: string;
  source: 'yaml' | 'db';
  removable: boolean;
  id: string | null;
  exists: boolean;
}

// A merged find-on-web allowed download host: built-in default (locked) or DB-managed (removable).
export interface WebFindAllowedHost {
  host: string;
  source: 'default' | 'db';
  removable: boolean;
  id: string | null;
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
  pdf_coordinates: PdfCoordinateBox[] | null;
  pdf_x: number | null;
  pdf_y: number | null;
  pdf_w: number | null;
  pdf_h: number | null;
  source_tei_id: string | null;
}

export interface PdfCoordinateBox {
  page: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface MetadataAssertion {
  id: string;
  field_name: string;
  value: string;
  source: string;
  confidence: number | null;
  selected_as_canonical: boolean;
}

export interface FieldReview {
  field_name: string;
  canonical_value: string | null;
  has_conflict: boolean;
  confirmed?: boolean;
  assertions: MetadataAssertion[];
}

export interface WorkFile {
  id: string;
  sha256: string;
  size_bytes: number;
  original_filename: string | null;
  page_count: number | null;
  text_layer_quality: string;
  status: string;
  content_available: boolean;
}

export interface WebCandidate {
  candidate_id: string;
  source: string;
  sources: string[];
  title: string | null;
  authors: string[];
  year: number | null;
  doi: string | null;
  pdf_url: string | null;
  landing_url: string | null;
  // Final URL after following the redirect chain of `pdf_url || landing_url` (nullable).
  resolved_url: string | null;
  // Host to display (e.g. `sciencedirect.com`) — resolved final host, else the original
  // landing/pdf host (nullable).
  platform: string | null;
  is_oa: boolean;
  score: number;
}

export interface WebFindResponse {
  candidates: WebCandidate[];
  degraded_sources: string[];
  queried_sources: string[];
}

export interface WebFindDownloadItem {
  candidate_id: string;
  url: string;
  source: string;
  // Re-sent as true to proceed past a `needs_confirmation` result (unrestricted mode, unknown host).
  confirmed?: boolean;
}

export type WebFindDownloadStatus =
  | 'attached'
  | 'deduped'
  | 'manual_upload_needed'
  | 'error'
  | 'blocked'
  | 'needs_confirmation';

export interface WebFindDownloadResult {
  candidate_id: string;
  status: WebFindDownloadStatus;
  reason: string | null;
  // For `needs_confirmation`: the URL that would be fetched (echo it back with confirmed:true).
  url?: string | null;
  file: WorkFile | null;
}

export interface WebFindDownloadResponse {
  results: WebFindDownloadResult[];
}

// Streaming search NDJSON events (POST .../find-on-web/stream). One object per line.
export interface WebFindSourceEvent {
  type: 'source';
  source: string;
  status: 'querying' | 'done' | 'failed';
  count?: number;
}

export interface WebFindResultEvent {
  type: 'result';
  candidates: WebCandidate[];
  degraded_sources: string[];
  queried_sources: string[];
}

export type WebFindStreamEvent = WebFindSourceEvent | WebFindResultEvent;

// Find-on-web download policy (owner-only).
export type WebFindDownloadPolicy = 'restricted' | 'careful' | 'unrestricted';

export interface WebFindDownloadPolicyResponse {
  policy: WebFindDownloadPolicy;
  allowed: string[];
}

export interface ReferenceRecord {
  id: string;
  title: string | null;
  raw_citation: string | null;
  doi: string | null;
  arxiv_id: string | null;
  year: number | null;
  resolution_status: string;
  resolved_work_id: string | null;
  shorthand: string | null;
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
  | 'split_file'
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

// Sort keys the backend list_works endpoint accepts (SAFE allowlist, mirrored server-side).
export type WorkSortKey =
  | 'title'
  | 'year'
  | 'venue'
  | 'added_at'
  | 'updated_at'
  | 'reading_status';

export interface WorkQuery {
  q?: string;
  readingStatus?: string;
  shelfId?: string;
  rackId?: string;
  tagId?: string;
  hasPdf?: boolean;
  hasReferences?: boolean;
  missing?: string[];
  sort?: WorkSortKey;
  order?: 'asc' | 'desc';
}

// Per-user UI preferences (durable copy of the library column choices; see lib/columns.ts).
export interface LibraryColumnPrefs {
  order: string[];
  visible: string[];
  sort: { key: string; order: 'asc' | 'desc' };
}

export interface UserPreferences {
  library_columns?: LibraryColumnPrefs;
}

export interface IdentifierImportResponse {
  work_id: string;
  created: boolean;
  enriched_sources: string[];
}

// Batch citation import (Phase J item 5).
export type EngineKind = 'lookup' | 'grobid';
export type BatchMatchStatus = 'matched' | 'title_only' | 'no_match';

export interface DraftCandidate {
  title: string | null;
  authors: string[];
  year: number | null;
  doi: string | null;
  venue: string | null;
  source: string;
  sources: string[];
  confidence: number;
}

export interface ParsedDraft {
  line_index: number;
  raw_line: string;
  engine: EngineKind;
  suggested_title: string | null;
  suggested_authors: string[];
  suggested_year: number | null;
  suggested_doi: string | null;
  suggested_venue: string | null;
  suggested_abstract: string | null;
  match_status: BatchMatchStatus;
  candidates: DraftCandidate[];
}

export interface BatchPreviewResponse {
  drafts: ParsedDraft[];
  degraded: boolean;
  grobid_unavailable: boolean;
}

export interface BatchCommitDraft {
  title: string | null;
  authors: string[];
  year: number | null;
  doi: string | null;
  venue: string | null;
  abstract: string | null;
  include: boolean;
}

export interface ScopeSummaryResponse {
  entity_type: string;
  entity_id: string;
  summary_type: string;
  text: string;
  model_name: string | null;
  prompt_version: string | null;
  work_count: number;
}

export interface AiConfig {
  embedding_provider: string;
  embedding_model: string | null;
  summary_provider: string;
  summary_model: string;
  topic_backend: string;
  topic_embedding_model: string | null;
  ollama_url: string;
}

export interface AiProviderInfo {
  available: boolean;
  note: string | null;
}

export interface AiProviders {
  embedding: Record<string, AiProviderInfo>;
  summary: Record<string, AiProviderInfo>;
  topic: Record<string, AiProviderInfo>;
  ollama_reachable: boolean;
}

export interface AiModel {
  provider: string;
  name: string;
  size_bytes: number | null;
}

// Privilege ladder, highest first: owner > admin > librarian > editor > contributor > reader.
export type UserRole =
  | 'owner'
  | 'admin'
  | 'librarian'
  | 'editor'
  | 'contributor'
  | 'reader';

// --- Access control: groups, grants, default grants and access settings (admin-or-owner) ---
export interface Group {
  id: string;
  name: string;
  is_personal: boolean;
  personal_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface GroupMember {
  id: string;
  username: string;
  role: string;
  display_name: string | null;
}

export type GrantTargetType = 'rack' | 'shelf';

export interface Grant {
  id: string;
  group_id: string;
  target_type: GrantTargetType;
  target_id: string;
  created_at: string;
}

export interface DefaultGrant {
  id: string;
  target_type: GrantTargetType;
  target_id: string;
  created_at: string;
}

export interface AccessSettings {
  default_access_level: AccessLevel;
  allowed: AccessLevel[];
}

export interface AdminUser {
  id: string;
  username: string;
  role: string;
  created_at: string;
  disabled_at: string | null;
  // The single immutable owner account (provisioned by `make bootstrap-admin`). Never disablable,
  // deletable or role-changeable; only the owner can manage admins.
  is_bootstrap: boolean;
}

export interface CurrentUser {
  id: string;
  username: string;
  role: UserRole;
  display_name: string | null;
  email: string | null;
  created_at: string | null;
  last_login_at: string | null;
}

export interface AgentRecord {
  id: string;
  name: string;
  status: string;
  can_index: boolean;
  can_extract: boolean;
  can_teleport: boolean;
  can_be_requested: boolean;
  processing_visibility: boolean;
  server_status_visibility: boolean;
}

export type AgentPrivilege =
  | 'can_index'
  | 'can_extract'
  | 'can_teleport'
  | 'can_be_requested'
  | 'processing_visibility'
  | 'server_status_visibility';

export interface AuditEvent {
  id: string;
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  actor_user_id: string | null;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface EnrollTokenOut {
  token: string;
  expires_at: string;
}

export interface AgentFileRecord {
  id: string;
  local_file_id: string;
  sha256: string;
  size_bytes: number;
  display_path: string | null;
  teleport_status: string;
  file_id: string | null;
}

export interface JobRecord {
  id: string;
  task: string;
  status: string;
  enqueued_at: string | null;
  ended_at: string | null;
  error: string | null;
  target_kind?: string | null;
  target_id?: string | null;
  paper_title?: string | null;
  paper_sha256?: string | null;
}

export interface QueueStatus {
  available: boolean;
  error?: string;
  workers: number;
  counts: Record<string, number>;
  jobs: JobRecord[];
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string | null = null,
    // Called when an *authenticated* request is rejected with 401 (session expired, signed out,
    // or the account was disabled). The argument is the server's human-readable explanation.
    private readonly onUnauthorized?: (detail: string) => void,
  ) {}

  withToken(token: string | null): ApiClient {
    return new ApiClient(this.baseUrl, token, this.onUnauthorized);
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
    if (query.hasPdf !== undefined) params.set('has_pdf', String(query.hasPdf));
    if (query.hasReferences !== undefined) params.set('has_references', String(query.hasReferences));
    if (query.missing?.length) params.set('missing', query.missing.join(','));
    if (query.sort) params.set('sort', query.sort);
    if (query.order) params.set('order', query.order);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return this.request<Work[]>(`/api/v1/works${suffix}`);
  }

  async getWork(workId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}`);
  }

  async getPreferences(): Promise<UserPreferences> {
    return this.request<UserPreferences>('/api/v1/preferences');
  }

  async putPreferences(prefs: UserPreferences): Promise<UserPreferences> {
    return this.request<UserPreferences>('/api/v1/preferences', { method: 'PUT', body: prefs });
  }

  async importReferenceAsWork(referenceId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/from-reference/${referenceId}`, { method: 'POST' });
  }

  async getRelatedWorks(workId: string, limit = 8): Promise<RelatedWork[]> {
    return this.request<RelatedWork[]>(`/api/v1/works/${workId}/related?limit=${limit}`);
  }

  async acceptTopicAsTag(
    topicModelId: string,
    topicId: number,
    name: string,
  ): Promise<{ tag_id: string; tagged: number }> {
    return this.request('/api/v1/ai/topics/accept-as-tag', {
      method: 'POST',
      body: { topic_model_id: topicModelId, topic_id: topicId, name },
    });
  }

  async createShelfFromTopic(
    topicModelId: string,
    topicId: number,
    name: string,
  ): Promise<{ shelf_id: string; added: number }> {
    return this.request('/api/v1/ai/topics/create-shelf', {
      method: 'POST',
      body: { topic_model_id: topicModelId, topic_id: topicId, name },
    });
  }

  async getReadingQueue(): Promise<Work[]> {
    return this.request<Work[]>('/api/v1/works/reading-queue');
  }

  async reorderReadingQueue(workIds: string[]): Promise<Work[]> {
    return this.request<Work[]>('/api/v1/works/reading-queue/reorder', {
      method: 'POST',
      body: { work_ids: workIds },
    });
  }

  async createWork(payload: Partial<Work>): Promise<Work> {
    return this.request<Work>('/api/v1/works', { method: 'POST', body: payload });
  }

  async updateWork(id: string, payload: Partial<Work>): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${id}`, { method: 'PATCH', body: payload });
  }

  async deleteWork(id: string): Promise<void> {
    await this.request<void>(`/api/v1/works/${id}`, { method: 'DELETE' });
  }

  async listWorkMetadata(workId: string): Promise<FieldReview[]> {
    return this.request<FieldReview[]>(`/api/v1/works/${workId}/metadata`);
  }

  async selectMetadataAssertion(workId: string, assertionId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}/metadata/select`, {
      method: 'POST',
      body: { assertion_id: assertionId },
    });
  }

  async confirmMetadataField(workId: string, fieldName: string, confirmed: boolean): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}/metadata/confirm`, {
      method: 'POST',
      body: { field_name: fieldName, confirmed },
    });
  }

  async enrichWork(workId: string): Promise<{ job_id: string | null; status: string }> {
    return this.request(`/api/v1/works/${workId}/enrich`, { method: 'POST' });
  }

  async listWorkFiles(workId: string): Promise<WorkFile[]> {
    return this.request<WorkFile[]>(`/api/v1/works/${workId}/files`);
  }

  async findOnWeb(workId: string, sources?: string[]): Promise<WebFindResponse> {
    return this.request<WebFindResponse>(`/api/v1/works/${workId}/find-on-web`, {
      method: 'POST',
      body: sources ? { sources } : {},
    });
  }

  // Streaming search: POST the same find-on-web request but read the NDJSON ReadableStream,
  // invoking `onEvent` per parsed line. Reuses the ApiClient auth header + base URL (no bypass).
  // Resolves once the stream ends; throws on a non-OK response or missing stream support so the
  // caller can fall back to the non-streaming findOnWeb().
  async streamFindOnWeb(
    workId: string,
    onEvent: (event: WebFindStreamEvent) => void,
    sources?: string[],
  ): Promise<void> {
    const headers: Record<string, string> = {
      Accept: 'application/x-ndjson',
      'Content-Type': 'application/json',
    };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(`${this.baseUrl}/api/v1/works/${workId}/find-on-web/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(sources ? { sources } : {}),
    });
    if (!response.ok) {
      let detail = `Request failed: ${response.status}`;
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        /* keep status message */
      }
      if (response.status === 401 && this.token) this.onUnauthorized?.(detail);
      throw new Error(detail);
    }
    if (!response.body) throw new Error('Streaming not supported');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const flushLine = (line: string): void => {
      const trimmed = line.trim();
      if (!trimmed) return;
      onEvent(JSON.parse(trimmed) as WebFindStreamEvent);
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf('\n');
      while (newline !== -1) {
        flushLine(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf('\n');
      }
    }
    // Emit any trailing line that arrived without a closing newline.
    flushLine(buffer);
  }

  async downloadWebCandidates(
    workId: string,
    items: WebFindDownloadItem[],
  ): Promise<WebFindDownloadResponse> {
    return this.request<WebFindDownloadResponse>(`/api/v1/works/${workId}/find-on-web/download`, {
      method: 'POST',
      body: { items },
    });
  }

  async listWorkReferences(workId: string): Promise<ReferenceRecord[]> {
    return this.request<ReferenceRecord[]>(`/api/v1/works/${workId}/references`);
  }

  async uploadWorkFile(workId: string, file: File): Promise<WorkFile> {
    const form = new FormData();
    form.append('file', file);
    const headers: Record<string, string> = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(`${this.baseUrl}/api/v1/works/${workId}/files`, {
      method: 'POST',
      headers,
      body: form,
    });
    if (!response.ok) {
      let detail = `Upload failed: ${response.status}`;
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        /* keep status message */
      }
      throw new Error(detail);
    }
    return response.json() as Promise<WorkFile>;
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

  async deleteAnnotation(workId: string, annotationId: string): Promise<void> {
    await this.request<void>(`/api/v1/works/${workId}/annotations/${annotationId}`, {
      method: 'DELETE',
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

  async createShelf(payload: {
    name: string;
    description?: string;
    access_level?: AccessLevel;
  }): Promise<Shelf> {
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

  async createRack(payload: {
    name: string;
    description?: string;
    access_level?: AccessLevel;
  }): Promise<Rack> {
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

  // --- Server import roots (owner-only; merged yaml + DB whitelist for the "Server folder" import) ---
  async listServerImportRoots(): Promise<ServerImportRoot[]> {
    return this.request<ServerImportRoot[]>('/api/v1/admin/import-roots');
  }

  async addServerImportRoot(payload: { alias: string; path: string }): Promise<ServerImportRoot> {
    return this.request<ServerImportRoot>('/api/v1/admin/import-roots', {
      method: 'POST',
      body: payload,
    });
  }

  async removeServerImportRoot(rootId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/import-roots/${rootId}`, { method: 'DELETE' });
  }

  // --- Find-on-web allowed download hosts (admin-or-owner; merged defaults + DB allowlist) ---
  async listWebFindAllowedHosts(): Promise<WebFindAllowedHost[]> {
    return this.request<WebFindAllowedHost[]>('/api/v1/admin/web-find/allowed-hosts');
  }

  async addWebFindAllowedHost(payload: { host: string }): Promise<WebFindAllowedHost> {
    return this.request<WebFindAllowedHost>('/api/v1/admin/web-find/allowed-hosts', {
      method: 'POST',
      body: payload,
    });
  }

  async removeWebFindAllowedHost(hostId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/web-find/allowed-hosts/${hostId}`, {
      method: 'DELETE',
    });
  }

  // --- Find-on-web download policy (owner-only; restricted | careful | unrestricted) ---
  async getWebFindDownloadPolicy(): Promise<WebFindDownloadPolicyResponse> {
    return this.request<WebFindDownloadPolicyResponse>('/api/v1/admin/web-find/download-policy');
  }

  async setWebFindDownloadPolicy(
    policy: WebFindDownloadPolicy,
  ): Promise<WebFindDownloadPolicyResponse> {
    return this.request<WebFindDownloadPolicyResponse>('/api/v1/admin/web-find/download-policy', {
      method: 'PUT',
      body: { policy },
    });
  }

  async importFolder(sourceId: string): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/folder', {
      method: 'POST',
      body: { source_id: sourceId, recursive: true },
    });
  }

  async importBibtex(content: string, targetShelfId?: string | null): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/bibtex', {
      method: 'POST',
      body: { content, target_shelf_id: targetShelfId ?? null },
    });
  }

  async importRis(content: string, targetShelfId?: string | null): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/ris', {
      method: 'POST',
      body: { content, target_shelf_id: targetShelfId ?? null },
    });
  }

  async importCsl(content: string, targetShelfId?: string | null): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/csl', {
      method: 'POST',
      body: { content, target_shelf_id: targetShelfId ?? null },
    });
  }

  async uploadPdf(file: File, targetShelfId?: string | null): Promise<ImportBatch> {
    const form = new FormData();
    form.append('file', file);
    if (targetShelfId) form.append('target_shelf_id', targetShelfId);
    const headers: Record<string, string> = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(`${this.baseUrl}/api/v1/imports/upload`, {
      method: 'POST',
      headers,
      body: form,
    });
    if (!response.ok) {
      let detail = `Upload failed: ${response.status}`;
      try {
        const payload = await response.json();
        detail = payload.detail ?? detail;
      } catch { /* keep status message */ }
      throw new Error(detail);
    }
    return response.json() as Promise<ImportBatch>;
  }

  async importByIdentifier(
    identifierType: 'arxiv' | 'doi',
    value: string,
    targetShelfId?: string | null,
  ): Promise<IdentifierImportResponse> {
    return this.request<IdentifierImportResponse>('/api/v1/imports/identifier', {
      method: 'POST',
      body: { identifier_type: identifierType, value, target_shelf_id: targetShelfId ?? null },
    });
  }

  async batchImportPreview(
    lines: string[],
    engine: EngineKind,
  ): Promise<BatchPreviewResponse> {
    return this.request<BatchPreviewResponse>('/api/v1/imports/batch/preview', {
      method: 'POST',
      body: { lines, engine },
    });
  }

  async batchImportCommit(
    drafts: BatchCommitDraft[],
    options: { engine: EngineKind; targetShelfId?: string | null; enrich?: boolean },
  ): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/batch/commit', {
      method: 'POST',
      body: {
        drafts,
        engine: options.engine,
        target_shelf_id: options.targetShelfId ?? null,
        enrich: options.enrich ?? true,
      },
    });
  }

  async createScopeScope(
    scopeType: 'library' | 'shelf' | 'rack',
    scopeId: string | null,
  ): Promise<ScopeSummaryResponse> {
    return this.request<ScopeSummaryResponse>('/api/v1/ai/summaries', {
      method: 'POST',
      body: { scope_type: scopeType, scope_id: scopeId ?? null },
    });
  }

  async listAdminUsers(): Promise<AdminUser[]> {
    return this.request<AdminUser[]>('/api/v1/admin/users');
  }

  async createAdminUser(username: string, password: string, role: UserRole): Promise<AdminUser> {
    return this.request<AdminUser>('/api/v1/admin/users', {
      method: 'POST',
      body: { username, password, role },
    });
  }

  async updateUserRole(userId: string, role: UserRole): Promise<AdminUser> {
    return this.request<AdminUser>(`/api/v1/admin/users/${userId}`, {
      method: 'PATCH',
      body: { role },
    });
  }

  async disableUser(userId: string): Promise<AdminUser> {
    return this.request<AdminUser>(`/api/v1/admin/users/${userId}/disable`, { method: 'POST' });
  }

  async enableUser(userId: string): Promise<AdminUser> {
    return this.request<AdminUser>(`/api/v1/admin/users/${userId}/enable`, { method: 'POST' });
  }

  async deleteUser(userId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/users/${userId}`, { method: 'DELETE' });
  }

  async listAgents(): Promise<AgentRecord[]> {
    return this.request<AgentRecord[]>('/api/v1/admin/agents');
  }

  async approveAgent(agentId: string): Promise<{ agent_id: string; status: string; agent_token: string }> {
    return this.request('/api/v1/admin/agents/' + agentId + '/approve', { method: 'POST' });
  }

  async issueEnrollToken(): Promise<EnrollTokenOut> {
    return this.request<EnrollTokenOut>('/api/v1/admin/agents/enroll-token', { method: 'POST' });
  }

  async listAgentFiles(agentId: string): Promise<AgentFileRecord[]> {
    return this.request<AgentFileRecord[]>(`/api/v1/admin/agents/${agentId}/files`);
  }

  async updateAgentPrivileges(
    agentId: string,
    privileges: Partial<Record<AgentPrivilege, boolean>>,
  ): Promise<AgentRecord> {
    return this.request<AgentRecord>(`/api/v1/admin/agents/${agentId}/privileges`, {
      method: 'PATCH',
      body: privileges,
    });
  }

  // --- AI provider config + model management (owner) ---
  async getAiConfig(): Promise<{ config: AiConfig; allowed: Record<string, string[]> }> {
    return this.request('/api/v1/admin/ai-config');
  }

  async updateAiConfig(
    changes: Partial<AiConfig>,
  ): Promise<{ config: AiConfig; reindex_job_id: string | null }> {
    return this.request('/api/v1/admin/ai-config', { method: 'PUT', body: changes });
  }

  async getAiProviders(): Promise<AiProviders> {
    return this.request('/api/v1/admin/ai/providers');
  }

  async listAiModels(): Promise<{ models: AiModel[] }> {
    return this.request('/api/v1/admin/ai/models');
  }

  async pullAiModel(provider: string, model: string): Promise<{ job_id: string; status: string }> {
    return this.request('/api/v1/admin/ai/models/pull', {
      method: 'POST',
      body: { provider, model },
    });
  }

  async deleteAiModel(provider: string, model: string): Promise<unknown> {
    return this.request('/api/v1/admin/ai/models', { method: 'DELETE', body: { provider, model } });
  }

  async reindexEmbeddings(): Promise<{ job_id: string; status: string }> {
    return this.request('/api/v1/admin/ai/reindex', { method: 'POST' });
  }

  async getReindexStatus(): Promise<{ model_name: string; indexed: number; total: number }> {
    return this.request('/api/v1/admin/ai/reindex/status');
  }

  async renameAgent(agentId: string, name: string): Promise<AgentRecord> {
    return this.request<AgentRecord>(`/api/v1/admin/agents/${agentId}`, {
      method: 'PATCH',
      body: { name },
    });
  }

  async deleteAgent(agentId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/agents/${agentId}`, { method: 'DELETE' });
  }

  async requestTeleport(agentId: string, localFileId: string): Promise<void> {
    await this.request<void>('/api/v1/imports/teleport', {
      method: 'POST',
      body: { agent_id: agentId, local_file_id: localFileId },
    });
  }

  async searchAnnotations(q: string, annotationType?: string): Promise<Annotation[]> {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (annotationType) params.set('annotation_type', annotationType);
    return this.request<Annotation[]>(`/api/v1/works/annotations/search?${params.toString()}`);
  }

  async exportAnnotations(
    workId: string,
    format: 'markdown' | 'text' = 'markdown',
  ): Promise<{ filename: string; content_type: string; content: string }> {
    return this.request(`/api/v1/works/${workId}/annotations/export?format=${format}`);
  }

  async changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<{ status: string; sessions_revoked: number }> {
    return this.request('/api/v1/auth/change-password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    });
  }

  async getMe(): Promise<CurrentUser> {
    return this.request<CurrentUser>('/api/v1/auth/me');
  }

  async logout(): Promise<void> {
    await this.request<void>('/api/v1/auth/logout', { method: 'POST' });
  }

  async updateProfile(changes: {
    display_name?: string | null;
    email?: string | null;
  }): Promise<CurrentUser> {
    return this.request<CurrentUser>('/api/v1/auth/me', { method: 'PATCH', body: changes });
  }

  async resetUserPassword(
    userId: string,
    newPassword: string,
  ): Promise<{ status: string; sessions_revoked: number }> {
    return this.request(`/api/v1/admin/users/${userId}/reset-password`, {
      method: 'POST',
      body: { new_password: newPassword },
    });
  }

  // --- Access control: groups, members, grants, default grants, access settings (admin-or-owner) ---
  async listGroups(): Promise<Group[]> {
    return this.request<Group[]>('/api/v1/admin/groups');
  }

  async createGroup(name: string): Promise<Group> {
    return this.request<Group>('/api/v1/admin/groups', { method: 'POST', body: { name } });
  }

  async deleteGroup(groupId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/groups/${groupId}`, { method: 'DELETE' });
  }

  async listGroupMembers(groupId: string): Promise<GroupMember[]> {
    return this.request<GroupMember[]>(`/api/v1/admin/groups/${groupId}/members`);
  }

  async addGroupMember(groupId: string, userId: string): Promise<GroupMember> {
    return this.request<GroupMember>(`/api/v1/admin/groups/${groupId}/members`, {
      method: 'POST',
      body: { user_id: userId },
    });
  }

  async removeGroupMember(groupId: string, userId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/groups/${groupId}/members/${userId}`, {
      method: 'DELETE',
    });
  }

  async listGroupGrants(groupId: string): Promise<Grant[]> {
    return this.request<Grant[]>(`/api/v1/admin/groups/${groupId}/grants`);
  }

  async addGroupGrant(
    groupId: string,
    targetType: GrantTargetType,
    targetId: string,
  ): Promise<Grant> {
    return this.request<Grant>(`/api/v1/admin/groups/${groupId}/grants`, {
      method: 'POST',
      body: { target_type: targetType, target_id: targetId },
    });
  }

  async removeGrant(grantId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/grants/${grantId}`, { method: 'DELETE' });
  }

  async listDefaultGrants(): Promise<DefaultGrant[]> {
    return this.request<DefaultGrant[]>('/api/v1/admin/default-grants');
  }

  async addDefaultGrant(
    targetType: GrantTargetType,
    targetId: string,
  ): Promise<DefaultGrant> {
    return this.request<DefaultGrant>('/api/v1/admin/default-grants', {
      method: 'POST',
      body: { target_type: targetType, target_id: targetId },
    });
  }

  async removeDefaultGrant(defaultGrantId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/default-grants/${defaultGrantId}`, {
      method: 'DELETE',
    });
  }

  async getAccessSettings(): Promise<AccessSettings> {
    return this.request<AccessSettings>('/api/v1/admin/access-settings');
  }

  async setAccessSettings(defaultAccessLevel: AccessLevel): Promise<AccessSettings> {
    return this.request<AccessSettings>('/api/v1/admin/access-settings', {
      method: 'PUT',
      body: { default_access_level: defaultAccessLevel },
    });
  }

  async getJobs(limit = 25): Promise<QueueStatus> {
    return this.request<QueueStatus>(`/api/v1/jobs?limit=${limit}`);
  }

  async clearJobs(
    which: 'finished_failed' | 'failed' | 'finished' | 'all' = 'finished_failed',
  ): Promise<{ available: boolean; cleared: number; error?: string }> {
    return this.request(`/api/v1/jobs/clear?which=${which}`, { method: 'POST' });
  }

  async extractFile(fileId: string): Promise<{ job_id: string | null; status: string }> {
    return this.request(`/api/v1/files/${fileId}/extract`, { method: 'POST' });
  }

  async extractWork(
    workId: string,
  ): Promise<{ status: string; queued: number; job_ids?: string[] }> {
    return this.request(`/api/v1/works/${workId}/extract`, { method: 'POST' });
  }

  async listAuditEvents(limit = 50): Promise<AuditEvent[]> {
    // The endpoint returns a paginated envelope { items, total, ... }, not a bare array.
    const page = await this.request<{ items: AuditEvent[] }>(
      `/api/v1/admin/audit-events?limit=${limit}`,
    );
    return page.items ?? [];
  }

  async listFiles(): Promise<FileRecord[]> {
    return this.request<FileRecord[]>('/api/v1/files');
  }

  async getFileBlob(fileId: string): Promise<Blob> {
    return this.requestBlob(`/api/v1/files/${fileId}/stream`);
  }

  async semanticSearch(q: string, limit = 10): Promise<SemanticSearchResponse> {
    return this.request<SemanticSearchResponse>('/api/v1/search/semantic', {
      method: 'POST',
      body: { q, limit },
    });
  }

  async listSummaries(workId: string): Promise<Summary[]> {
    return this.request<Summary[]>(`/api/v1/works/${workId}/summaries`);
  }

  async createSummary(workId: string, summaryType: SummaryType): Promise<Summary> {
    return this.request<Summary>(`/api/v1/works/${workId}/summaries`, {
      method: 'POST',
      body: { summary_type: summaryType },
    });
  }

  async modelTopics(payload: {
    scopeType: GraphScopeType;
    scopeId?: string | null;
    maxTopics?: number;
  }): Promise<TopicModelResponse> {
    return this.request<TopicModelResponse>('/api/v1/ai/topics', {
      method: 'POST',
      body: {
        scope_type: payload.scopeType,
        scope_id: payload.scopeId ?? null,
        max_topics: payload.maxTopics ?? 5,
      },
    });
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
    scope_id?: string | null;
    work_ids?: string[];
    format: ExportFormat;
    style?: string;
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
      // A 401 on an authenticated request means the session ended (expired/signed out/disabled):
      // hand the explanation to the app so it can force a clean logout.
      if (response.status === 401 && options.auth !== false && this.token) {
        this.onUnauthorized?.(detail);
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
    if (!response.ok) {
      if (response.status === 401 && this.token) {
        let detail = 'Your session has ended. Please sign in again.';
        try {
          detail = (await response.json()).detail ?? detail;
        } catch {
          /* non-JSON body */
        }
        this.onUnauthorized?.(detail);
      }
      throw new Error(`Request failed: ${response.status}`);
    }
    return response.blob();
  }
}
