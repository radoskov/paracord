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
