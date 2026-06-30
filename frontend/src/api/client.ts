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

export interface WorkQuery {
  q?: string;
  readingStatus?: string;
  shelfId?: string;
  rackId?: string;
  tagId?: string;
  hasPdf?: boolean;
  hasReferences?: boolean;
  missing?: string[];
}

export interface IdentifierImportResponse {
  work_id: string;
  created: boolean;
  enriched_sources: string[];
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

export type UserRole = 'owner' | 'editor' | 'reader';

export interface AdminUser {
  id: string;
  username: string;
  role: string;
  created_at: string;
  disabled_at: string | null;
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
    if (query.hasPdf !== undefined) params.set('has_pdf', String(query.hasPdf));
    if (query.hasReferences !== undefined) params.set('has_references', String(query.hasReferences));
    if (query.missing?.length) params.set('missing', query.missing.join(','));
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return this.request<Work[]>(`/api/v1/works${suffix}`);
  }

  async getWork(workId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}`);
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

  async importBibtex(content: string): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/bibtex', {
      method: 'POST',
      body: { content },
    });
  }

  async importRis(content: string): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/ris', { method: 'POST', body: { content } });
  }

  async importCsl(content: string): Promise<ImportBatch> {
    return this.request<ImportBatch>('/api/v1/imports/csl', { method: 'POST', body: { content } });
  }

  async uploadPdf(file: File): Promise<ImportBatch> {
    const form = new FormData();
    form.append('file', file);
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
  ): Promise<IdentifierImportResponse> {
    return this.request<IdentifierImportResponse>('/api/v1/imports/identifier', {
      method: 'POST',
      body: { identifier_type: identifierType, value },
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
