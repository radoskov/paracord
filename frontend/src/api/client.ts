import type { Theme, ThemeMode } from "../lib/theme/types";

export type ReadingStatus =
  "unread" | "skimmed" | "reading" | "read" | "important" | "revisit";

// Custom themes (Theming P4). A picker-list summary and the admin upload result. The full resolved
// theme object returned by getTheme() reuses the frontend `Theme` type (same shape as bundled ones).
export interface CustomThemeSummary {
  id: string;
  name: string;
  mode: ThemeMode;
  temperature: string;
  swatch: { surface: string; primary: string; accents: string[] };
}

export interface ThemeUploadResult {
  id: string;
  name: string;
  mode: string;
  temperature: string;
  // Advisory readability warnings; the theme is still accepted/stored when non-empty.
  warnings: string[];
}

export interface Work {
  id: string;
  canonical_title: string | null;
  abstract: string | null;
  doi: string | null;
  arxiv_id: string | null;
  venue: string | null;
  year: number | null;
  reading_status: ReadingStatus;
  // Per-paper processing error (F2): "<stage>: <reason>" when a background enrich/keyword/topic job
  // failed for this paper; null when clear. Drives a "processing failed" indicator.
  processing_error?: string | null;
  // Origin marker; "agent_index_only" on a not-yet-extracted local-agent stub (B6).
  canonical_metadata_source?: string | null;
  // The file to open by default (the "main" file); null falls back to the first attached file.
  main_file_id?: string | null;
  confirmed_fields?: string[];
  keywords?: string[];
  // Per-paper representative topic terms (Phase K); shown separately from keywords.
  topics?: string[];
  // The user who created this paper (null for system/loose-imported papers). Drives the
  // "can I modify this paper" gate: a contributor may only edit their own papers.
  created_by_user_id: string | null;
  // External citation-count snapshot (Track C P1); null for papers with no resolvable id. Carries
  // the source it came from and when it was last fetched, for an "as-of" display.
  citation_count?: number | null;
  citation_count_source?: string | null;
  citation_count_fetched_at?: string | null;
  created_at: string;
  updated_at: string;
  // Duplicate-merge shadow marker (Batch D): non-null on a hidden shadow. `has_reversible_shadow`
  // is only populated by getWork — true when this paper's most recent merge can be undone (drives
  // the Unmerge button).
  merged_into_id?: string | null;
  has_reversible_shadow?: boolean;
  // SEE-filtered shelves/racks for the Library columns (D32); only populated by listWorks.
  shelves?: WorkRef[];
  racks?: WorkRef[];
  // Library columns (batch10); only populated by listWorks. file_count = attached files;
  // tags = applied tags (with colour); badges = status tokens (extracted / extract_failed /
  // not_extracted / text_poor / text_none / ocr_added / conflicts) mapped to chips by the table.
  file_count?: number;
  tags?: WorkTagRef[];
  badges?: string[];
  // Reference/citation count columns (batch 12); only populated by listWorks. reference_count =
  // references this paper cites; local_reference_count = distinct other local papers it references;
  // local_citation_count = distinct other local papers that reference it. (External citation_count
  // is above.)
  reference_count?: number;
  local_reference_count?: number;
  local_citation_count?: number;
}

// A tag applied to a paper (Library "Tags" column).
export interface WorkTagRef {
  id: string;
  name: string;
  color?: string | null;
}

// A lightweight {id, name} reference (a paper's shelf or rack in the Library columns).
export interface WorkRef {
  id: string;
  name: string;
}

// Server-controlled Library pagination envelope (D18).
export interface PaginatedWorks {
  items: Work[];
  total: number;
  page: number;
  pages: number;
  per_page: number;
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
  mode?: string;
  items: SemanticSearchItem[];
  // Provider provenance (Phase B2): the embedding provider actually used vs the one configured,
  // and whether the search silently degraded to the built-in baseline. Null in lexical mode.
  embedding_provider_used?: string | null;
  embedding_provider_requested?: string | null;
  degraded?: boolean;
  degraded_reason?: string | null;
}

// Unified hybrid search (HS5): lexical (BM25F+), semantic (dense), or hybrid (RRF fusion).
export type SearchMode = "lexical" | "semantic" | "hybrid";

export interface HybridSearchItem {
  work_id: string;
  title: string | null;
  year: number | null;
  score: number;
  // Normalised 0..1 relevance for display as a % (distinct from the raw fusion `score`).
  relevance?: number | null;
  // Best-matching passage + its section (semantic/hybrid); null in lexical mode / doc fallback.
  passage?: string | null;
  section?: string | null;
  // Which engine surfaced the paper (1-based rank), null if that engine didn't.
  lexical_rank?: number | null;
  semantic_rank?: number | null;
}

export interface HybridSearchResponse {
  query: string;
  mode: string;
  items: HybridSearchItem[];
  embedding_provider_used?: string | null;
  embedding_provider_requested?: string | null;
  degraded?: boolean;
  degraded_reason?: string | null;
}

// Registered embedding models (admin) — powers the Search embedding-model selector and the
// AI panel's model list.
export interface EmbeddingModelInfo {
  model_name: string;
  provider: string;
  dim: number;
  slug: string;
  // Whether the model's provider is installed/usable in this deployment; false for a seeded model
  // whose provider (e.g. sentence-transformers) isn't in the image.
  available?: boolean;
}

export interface EmbeddingModelsResponse {
  models: EmbeddingModelInfo[];
  max_models: number;
  multimode_available: boolean;
}

// 'auto' lets the server pick the configured provider (local LLM if selected, else extractive);
// 'abstract'/'extractive' force a specific engine.
export type SummaryType = "auto" | "abstract" | "extractive";

export interface Summary {
  id: string;
  entity_type: string;
  entity_id: string;
  summary_type: string;
  text: string;
  model_name: string | null;
  prompt_version: string | null;
  // Provider provenance (Phase B2): what was requested vs actually ran, and whether it degraded
  // to the extractive fallback (with a short reason). Absent on older/stored summaries.
  provider_requested?: string | null;
  provider_used?: string | null;
  fallback?: boolean;
  fallback_reason?: string | null;
  created_at: string;
}

export interface Topic {
  topic_id: number;
  keywords: string[];
  work_count: number;
  // C4: quality signals the modeler always computes — closest-to-centroid papers + cluster tightness.
  representative_work_ids: string[];
  coherence_score: number | null;
}

export interface TopicModelResponse {
  model_id: string | null;
  scope_type: string;
  scope_id: string | null;
  work_count: number;
  topics: Topic[];
  // Papers whose embedding fit no topic cluster (C4).
  outlier_work_ids: string[];
  // S15: the scope was too large to run inline — poll the job; assignments land in the topic graph.
  queued?: boolean;
  job_id?: string | null;
}

export type GraphScopeType =
  | "library"
  | "shelf"
  | "rack"
  | "search_result"
  | "selected_papers"
  | "import_batch"
  | "saved_filter";
export type GraphNodeMode = "local_only" | "include_external";
// §8.9 depth (Track C P5b). Node sizing is a client re-style — all three metrics ship on every node.
export type GraphSizeBy = "degree" | "pagerank" | "betweenness";
export type GraphColorBy =
  | "none"
  | "shelf"
  | "tag"
  | "topic"
  | "status"
  | "year";

export interface GraphNode {
  id: string;
  label: string;
  type: "local" | "external";
  work_id: string | null;
  year: number | null;
  doi: string | null;
  // Optional venue for hover tooltips (#8); not always populated by the backend.
  venue?: string | null;
  // §8.9 depth encodings (present when the graph endpoint computed metrics; default 0/null/false).
  degree?: number;
  pagerank?: number;
  betweenness?: number;
  color_group?: string | null;
  warning?: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
  resolution: string;
}

export interface CitationGraphResponse {
  // L-a: set when the scope was routed to a background job — poll getJobResult(job_id).
  queued?: boolean;
  job_id?: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  summary: Record<string, number>;
}

// Topic (embedding-similarity) graph (#6): nodes are papers, edges weighted by similarity.
export interface TopicGraphNode {
  id: string;
  label: string;
  work_id: string | null;
  year: number | null;
  venue?: string | null;
  doi?: string | null;
}

export interface TopicGraphEdge {
  source: string;
  target: string;
  weight: number;
}

export interface TopicGraphResponse {
  nodes: TopicGraphNode[];
  edges: TopicGraphEdge[];
  summary: {
    node_count: number;
    edge_count: number;
    used_embeddings: boolean;
    embedding_model?: string | null;
    note?: string | null;
  };
}

// D38 visualization module (Track C P2). Normalized payload every renderer consumes; see
// backend app.services.visualization.VizPayload.
export interface VizAxis {
  key: string;
  label: string;
}

export interface VizNode {
  id: string;
  x: number | null;
  y: number | null;
  size: number | null;
  color_group: string | null;
  shape: string;
  label: string;
  meta: Record<string, unknown>;
}

export interface VizEdge {
  source: string;
  target: string;
  weight: number;
}

// P5a stacked time-series (topic river): one share per year, aligned to `years`.
export interface VizSeries {
  years: number[];
  topics: { label: string; values: number[] }[];
}

// P5a labelled square matrix (similarity heatmap): row/column order matches `labels`/`ids`.
export interface VizMatrix {
  labels: string[];
  ids: string[];
  values: number[][];
}

export interface VizPayload {
  view_type: string;
  nodes: VizNode[];
  axes: { x: VizAxis; y: VizAxis } | null;
  edges: VizEdge[] | null;
  // `layout` (embedding_cluster only) is the projection actually used (pca/umap) so the UI can
  // reflect a UMAP→PCA fallback when umap-learn is absent.
  legend: { color_by: string; groups: string[]; layout?: string } | null;
  notes: string[];
  axis_options: VizAxis[] | null;
  // P5a chart carriers; null for scatter/network views. See backend VizPayload.
  series: VizSeries | null;
  matrix: VizMatrix | null;
  // B2: some papers aren't indexed for the model — split into reindexable vs needs-a-PDF, with the
  // specific file-less papers listed so the user can open + extract them.
  reindex_hint?: {
    reindexable: number;
    needs_text: { work_id: string; title: string }[];
  } | null;
}

export interface VizParams {
  scopeType?: GraphScopeType;
  scopeId?: string | null;
  workIds?: string[];
  xAxis?: string;
  yAxis?: string;
  sizeBy?: string;
  colorBy?: string;
  edgeContext?: string;
  focusWorkId?: string | null;
  includeEdges?: boolean;
  // B3: suppress the temporal-map citation-edge overlay above this many placed papers.
  edgeMaxNodes?: number;
  embeddingModel?: string;
  // embedding_cluster projection: 'pca' (default) | 'umap' (opt-in, needs the AI extra image).
  layout?: string;
  currentYear?: number;
  maxNodes?: number;
}

// D38 Track C P4: scoped citation summaries (SPEC §8.11). See backend
// app.services.citation_summary.CitationSummary.
export interface RankedWork {
  work_id: string;
  title: string;
  year: number | null;
  doi: string | null;
  score: number;
}

export interface MissingWork {
  key: string;
  title: string;
  doi: string | null;
  year: number | null;
  cited_by_count: number;
  mention_count: number;
  reference_id: string | null;
  arxiv_id: string | null;
}

// On-demand external-reference preview (Track C C1). `available` is false when there is no
// identifier to query or no source returned anything (`message` explains).
export interface ExternalPreview {
  available: boolean;
  title: string | null;
  authors: string[];
  year: number | null;
  venue: string | null;
  abstract: string | null;
  doi: string | null;
  arxiv_id: string | null;
  sources: string[];
  message: string | null;
}

// A missing work's import/ignore decision (Track C C3a).
export type MissingDecision = "import" | "ignore";

export interface YearCount {
  year: number | null;
  work_count: number;
  // The year's papers (id + title) so the chart can list/open them on click.
  works: { work_id: string; title: string }[];
}

export interface CitationSummary {
  scope_work_count: number;
  // Library coverage (Track C C3c): held / total resolvable cited works, and the percentage.
  coverage_held: number;
  coverage_total: number;
  coverage_pct: number | null;
  most_cited_local: RankedWork[];
  most_cited_external: RankedWork[];
  frequently_cited_missing: MissingWork[];
  bridge_papers: RankedWork[];
  isolated_papers: RankedWork[];
  chronological: YearCount[];
  bridge_method: string;
  computed_at: string;
  version: string;
  notes: string[];
}

export interface CitationSummaryParams {
  scopeType?: GraphScopeType;
  scopeId?: string | null;
  workIds?: string[];
  limit?: number;
}

// Venue/author aggregation over a citation-summary scope (batch10 #7).
export interface VenueStat {
  name: string;
  count: number;
  pct: number;
  year_min: number | null;
  year_max: number | null;
  variants: string[];
}

export interface AuthorStat {
  name: string;
  count: number;
  pct: number;
  variants: string[];
}

export interface VenueAuthorSummary {
  scope_work_count: number;
  venues: VenueStat[];
  authors: AuthorStat[];
  papers_without_venue: number;
  papers_without_authors: number;
  distinct_venue_count: number;
  distinct_author_count: number;
  notes: string[];
}

export type ExportScopeType =
  | "work"
  | "shelf"
  | "rack"
  | "library"
  | "selection"
  | "search"
  | "saved_filter"
  | "import_batch"
  | "missing_references";
export type ExportFormat =
  | "bibtex"
  | "biblatex"
  | "ris"
  | "csl-json"
  | "markdown"
  | "html"
  | "text"
  | "styled"
  | "latex"
  | "pandoc";

export const EXPORT_FORMATS: { value: ExportFormat; label: string }[] = [
  { value: "bibtex", label: "BibTeX" },
  { value: "biblatex", label: "BibLaTeX" },
  { value: "ris", label: "RIS" },
  { value: "csl-json", label: "CSL JSON" },
  { value: "markdown", label: "Markdown" },
  { value: "html", label: "HTML" },
  { value: "text", label: "Plain text" },
  { value: "styled", label: "Styled (APA/IEEE/…)" },
  { value: "latex", label: "LaTeX (\\cite)" },
  { value: "pandoc", label: "Pandoc Markdown ([@key])" },
];

export interface CitationStyle {
  value: string;
  label: string;
}

// Fallback list used before the dynamic list loads (or if the styles endpoint is unavailable).
// The backend (GET /api/v1/exports/styles) is the source of truth; keep these in rough sync.
export const CITATION_STYLES: CitationStyle[] = [
  { value: "apa", label: "APA (7th edition)" },
  { value: "ieee", label: "IEEE" },
  { value: "chicago", label: "Chicago (author-date)" },
  { value: "mla", label: "MLA (9th edition)" },
  { value: "harvard", label: "Harvard (Cite Them Right)" },
  { value: "vancouver", label: "Vancouver" },
  { value: "nature", label: "Nature" },
];

export interface ExportResponse {
  filename: string;
  content_type: string;
  content: string;
}

// Access level governing who may see / modify a rack or shelf (and, transitively, papers).
export type AccessLevel = "open" | "visible" | "private";

export interface Shelf {
  id: string;
  name: string;
  description: string | null;
  status: string;
  access_level: AccessLevel;
  // Whether the requesting caller may modify this shelf's structure/membership (librarian floor +
  // grant). Defaulted server-side; used by ShelfPicker's `modifiableOnly` pre-filter.
  can_modify?: boolean;
  // Whether this is the ephemeral default/Inbox shelf (loose-paper fallback). Used by ShelfPicker's
  // `excludeDefault` pre-filter so it isn't offered as a "Put into" move-target.
  is_default?: boolean;
  created_at: string;
  updated_at: string;
}

// One rack containing a paper's shelf (from GET /works/{id}/shelves; SEE-filtered).
export interface WorkShelfRackRef {
  id: string;
  name: string;
}

// A shelf a paper belongs to, with the caller's modify flag and the racks that contain it.
export interface WorkShelfMembership {
  id: string;
  name: string;
  access_level: AccessLevel;
  // Whether the caller may add/remove papers on this shelf (librarian floor + grant). Gates the
  // per-shelf Remove button alongside the canManageStructure store.
  can_modify: boolean;
  racks: WorkShelfRackRef[];
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

// A tag applied to a paper (from GET /works/{id}/tags): just what the chip needs to render.
export interface AppliedTag {
  id: string;
  name: string;
  color: string | null;
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
  source: "yaml" | "db";
  removable: boolean;
  id: string | null;
  exists: boolean;
}

// A merged find-on-web allowed download host: built-in default (locked) or DB-managed (removable).
export interface WebFindAllowedHost {
  host: string;
  source: "default" | "db";
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
  created_by_user_id?: string | null;
  input_type: string;
  status: string;
  stats: Record<string, number> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  work_count?: number;
  // False when the import intended extraction but the processing queue was offline, so the jobs
  // were dropped; the recovery sweep will retry them (D7). Absent on older servers → treat as true.
  extraction_queued?: boolean;
}

// A reference to an existing paper a staged PDF collides with (same PDF / DOI / title).
export interface StagingDuplicateRef {
  work_id: string;
  title: string | null;
}

// One staged PDF in a multi-import batch (batch10 #1).
export interface StagingItem {
  id: string;
  filename: string;
  sha256: string | null;
  status: string; // pending | extracting | extracted | extract_failed | committed | skipped
  error: string | null;
  parsed: {
    title?: string | null;
    authors?: string[];
    year?: number | null;
    doi?: string | null;
    venue?: string | null;
    abstract?: string | null;
  } | null;
  duplicates: Partial<Record<"same_pdf" | "same_doi" | "same_title", StagingDuplicateRef[]>> | null;
  created_work_id: string | null;
}

export interface StagingBatch {
  id: string;
  mode: "preview" | "direct";
  status: string; // extracting | ready | committed | cancelled
  target_shelf_id: string | null;
  created_at: string;
  updated_at: string;
  items: StagingItem[];
  extraction_queued: boolean;
}

export interface StagingCommitResult {
  batch_id: string;
  created: number;
  skipped: number;
  created_work_ids: string[];
  warnings: string[];
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
  /** 0-100 similarity between the conflicting values (null when there is no conflict). */
  match_pct?: number | null;
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
  extraction_queued?: boolean;
  // How many OTHER papers this exact PDF is also attached to (batch10) — drives the duplicate badge.
  also_in_count?: number;
}

// What merging one paper into another would do (issue 4). Mirrors the backend MergePaperPreview.
export interface MergePaperPreview {
  base_work_id: string;
  source_work_id: string;
  fill_fields: string[];
  conflict_fields: string[];
  file_count: number;
  incoming_reference_count: number;
  will_flatten: boolean;
}

export interface WebCandidate {
  candidate_id: string;
  source: string;
  sources: string[];
  title: string | null;
  authors: string[];
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
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
  // Candidate identifiers, so the work's empty arxiv_id/doi are backfilled when the PDF attaches.
  doi?: string | null;
  arxiv_id?: string | null;
}

export type WebFindDownloadStatus =
  | "attached"
  | "deduped"
  | "manual_upload_needed"
  | "error"
  | "blocked"
  | "needs_confirmation";

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
  type: "source";
  source: string;
  status: "querying" | "done" | "failed";
  count?: number;
}

export interface WebFindResultEvent {
  type: "result";
  candidates: WebCandidate[];
  degraded_sources: string[];
  queried_sources: string[];
}

export type WebFindStreamEvent = WebFindSourceEvent | WebFindResultEvent;

// Find-on-web download policy (owner-only).
export type WebFindDownloadPolicy = "restricted" | "careful" | "unrestricted";

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
  // Parsed citation authors (batch 12); null for references extracted before authors were persisted.
  authors: string[] | null;
  resolution_status: string;
  resolved_work_id: string | null;
  // Unconfirmed fuzzy "likely local" candidate (batch 12): the work this reference probably is.
  suggested_work_id: string | null;
  match_score: number | null;
  shorthand: string | null;
}

export type ReferenceAction = "link" | "reject" | "import";

export interface ReferenceRescanResult {
  scanned: number;
  changed: number;
  queued: boolean;
  job_id: string | null;
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

export type DuplicateCandidateStatus =
  "open" | "accepted" | "rejected" | "ignored";
export type DuplicateCandidateAction =
  | "merge_works"
  | "link_as_version"
  | "mark_duplicate_file"
  | "split_file"
  | "keep_separate"
  | "ignore";

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
  // A full-library scan runs on the background worker (D15): the response is empty and `queued` is
  // true, with `job_id` naming the RQ job to poll for completion before reloading candidates.
  queued?: boolean;
  job_id?: string | null;
}

export interface DuplicateSplitSegment {
  title: string;
  page_start?: number;
  page_end?: number;
  label?: string;
}

// Preview of what merging a work/work candidate into a chosen base would do (Batch D).
export interface MergePreview {
  base_work_id: string;
  source_work_id: string;
  fill_fields: string[];
  conflict_fields: string[];
  file_count: number;
  incoming_reference_count: number;
  will_flatten: boolean;
}

// Sort keys the backend list_works endpoint accepts (SAFE allowlist, mirrored server-side).
export type WorkSortKey =
  | "title"
  | "year"
  | "venue"
  | "added_at"
  | "updated_at"
  | "reading_status"
  | "file_count"
  | "reference_count"
  | "citation_count"
  | "local_reference_count"
  | "local_citation_count";

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
  order?: "asc" | "desc";
  // Server-controlled pagination (D18). `perPage` overrides the user's saved preference.
  page?: number;
  perPage?: number;
}

// --- Saved filters (Phase B7): a per-user named Library query, usable as a graph/export scope ---
export interface SavedFilterParams {
  reading_status?: string | null;
  shelf_id?: string | null;
  rack_id?: string | null;
  tag_id?: string | null;
  has_pdf?: boolean | null;
  has_references?: boolean | null;
  missing?: string[];
}

export interface SavedFilter {
  id: string;
  name: string;
  search_mode: "metadata" | "semantic" | "hybrid";
  query_text: string | null;
  params: SavedFilterParams;
  created_at: string;
  updated_at: string;
}

export interface SavedFilterCreate {
  name: string;
  search_mode?: "metadata" | "semantic" | "hybrid";
  query_text?: string | null;
  params?: SavedFilterParams;
}

export type SavedFilterUpdate = Partial<SavedFilterCreate>;

// Per-user UI preferences (durable copy of the library column choices; see lib/columns.ts).
export interface LibraryColumnPrefs {
  order: string[];
  visible: string[];
  sort: { key: string; order: "asc" | "desc" };
}

export interface UserPreferences {
  library_columns?: LibraryColumnPrefs;
  // B7: per-user section weights for the reference graph's node sizing (bucket → weight).
  citation_section_weights?: Record<string, number>;
}

export interface ReferenceGraphNode {
  id: string;
  label: string;
  year: number | null;
  kind: "base" | "local" | "likely_local" | "external" | "citing";
  resolved_work_id: string | null;
  // A soft "likely local" candidate (batch 12): the work this reference probably is + score.
  suggested_work_id?: string | null;
  match_score?: number | null;
  // Parsed authors (batch 12): references + citing papers, for the tooltip.
  authors?: string[] | null;
  section_counts: Record<string, number>;
  mention_count: number;
  weighted: number;
  // Selectable-Y metrics (B7 v2); null for external refs / when unavailable.
  citation_count?: number | null;
  local_degree?: number | null;
  topic_similarity?: number | null;
  // 5d colour-by-venue (local: resolved work's venue) + 5g click-to-import prefill data.
  venue?: string | null;
  doi?: string | null;
}

export interface ReferenceGraph {
  base_work_id: string;
  nodes: ReferenceGraphNode[];
  edges: { source: string; target: string }[];
}

// External papers that cite a work (batch10 #8).
export interface CitingPaper {
  id: string;
  source: string;
  external_id: string | null;
  title: string | null;
  authors: string | null;
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  venue: string | null;
  // The library work this citing paper IS, when the local matcher recognizes it.
  resolved_work_id: string | null;
}

export interface CitingPapersResponse {
  items: CitingPaper[];
  source: string | null;
  fetched_at: string | null;
  citation_count: number | null;
  citation_count_source: string | null;
}

export interface IdentifierImportResponse {
  work_id: string;
  created: boolean;
  enriched_sources: string[];
  extraction_queued?: boolean;
}

// Batch citation import (Phase J item 5). "bibtex" drafts come from the BibTeX preview endpoint
// and commit through the same batch commit.
export type EngineKind = "lookup" | "grobid" | "bibtex";
export type BatchMatchStatus = "matched" | "title_only" | "no_match";

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
  // BibTeX-engine extras (absent/null for lookup/grobid drafts).
  suggested_arxiv_id?: string | null;
  suggested_work_type?: string | null;
  existing_work_id?: string | null;
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
  // BibTeX-engine passthrough (not editable in the review UI).
  arxiv_id?: string | null;
  work_type?: string | null;
}

export interface ScopeSummaryResponse {
  // Nullable because a large scope is answered with a queued job instead (S15).
  entity_type: string | null;
  entity_id: string | null;
  summary_type: string | null;
  text: string | null;
  model_name: string | null;
  prompt_version: string | null;
  work_count: number;
  // S15: the scope was too large to run inline — poll the job, then getLatestScopeSummary().
  queued?: boolean;
  job_id?: string | null;
  // Provider provenance (#10 / L4): what was requested vs used, and why it fell back. `provider_used`
  // is 'extractive' whenever the summary is the extractive fallback (no model configured, or the
  // model was unavailable), which the Insights UI surfaces as a hint.
  provider_requested?: string | null;
  provider_used?: string | null;
  fallback?: boolean;
  fallback_reason?: string | null;
}

export interface AiConfig {
  embedding_provider: string;
  embedding_model: string | null;
  summary_provider: string;
  summary_model: string;
  topic_backend: string;
  topic_embedding_model: string | null;
  // OCR / advanced-extraction backend (Phase B5): 'none' | 'ocrmypdf' | 'pymupdf'.
  ocr_backend: string;
  // OCR languages in tesseract syntax; supports multi like 'eng+spa'.
  ocr_language: string;
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
  extraction?: Record<string, AiProviderInfo>;
  ollama_reachable: boolean;
  bertopic_installed?: boolean;
  sentence_transformers_installed?: boolean;
}

// One capability's active selection + whether that selection can run right now (else it degrades
// to the dependency-free baseline).
export interface AiActiveCapability {
  selected: string;
  available: boolean;
  note: string | null;
}

// Everything the AI & Models tab needs in one call (GET /admin/ai/status).
export interface AiStatus {
  config: AiConfig;
  allowed: Record<string, string[]>;
  providers: AiProviders;
  reindex: { model_name: string; indexed: number; total: number };
  // Hybrid search (HS6): chunk-level ANN coverage for the active model + lexical index warmth.
  chunk_embeddings?: {
    model_name: string;
    column: string | null;
    indexed: number;
    total: number;
  };
  lexical_index?: {
    loaded: boolean;
    docs: number | null;
    stale?: boolean | null;
  };
  ollama_reachable: boolean;
  bertopic_installed: boolean;
  sentence_transformers_installed: boolean;
  active: {
    embedding: AiActiveCapability;
    summary: AiActiveCapability;
    topic: AiActiveCapability;
    // PDF text extraction / OCR (Phase B5); optional for older backends without the field.
    extraction?: AiActiveCapability;
  };
}

export interface AiModel {
  provider: string;
  name: string;
  size_bytes: number | null;
}

// Privilege ladder, highest first: owner > admin > librarian > editor > contributor > reader.
export type UserRole =
  "owner" | "admin" | "librarian" | "editor" | "contributor" | "reader";

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

export type GrantTargetType = "rack" | "shelf";

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
  // Preferred Library page size (D18); null falls back to the server default.
  papers_per_page: number | null;
  // Preferred GUI theme id (P3); null falls back to the boot default.
  theme: string | null;
}

// Runtime app configuration (admin-editable; D18 page-size clamp + D1 overload protection).
export interface AppConfig {
  max_papers_per_page: number;
  rate_limit_per_client_per_min: number;
  rate_limit_global_per_min: number;
  max_batch_items: number;
  rq_worker_count: number;
  max_queue_len: number;
  // Citing-papers fetch cap (S20): max external citers fetched+cached per paper.
  citing_papers_fetch_cap: number;
  // Per-surface analysis node caps (L-a).
  citation_graph_node_cap: number;
  topic_graph_node_cap: number;
  viz_node_cap: number;
  // AI scope-job threshold (S15/S16): scopes above this run topics/summaries as a background job.
  ai_scope_job_threshold: number;
  // Reference→library matching (batch 12): treat a fuzzy "likely local" match as a hard link.
  use_fuzzy_match_as_confirmed: boolean;
  // Reference→library matching (F3a): re-run a full library-wide reference rematch on startup.
  reference_rescan_on_startup: boolean;
}

// Reference-dupes review (S13/S14): pending contradiction groups + the last scan summary.
export interface ReferenceDupeEntry {
  id: string;
  title: string | null;
  doi: string | null;
  arxiv_id: string | null;
  year: number | null;
  resolution_status: string;
  resolved_work_id: string | null;
  resolved_work_title: string | null;
  suggested_work_id: string | null;
  suggested_work_title: string | null;
  citing_count: number;
  parked: boolean;
}

export interface ReferenceDupeGroup {
  dedup_key: string;
  references: ReferenceDupeEntry[];
}

export interface LastConsolidationScan {
  at: string | null;
  groups_scanned: number;
  folded: number;
  conflicts: number;
}

export interface ReferenceDupesResponse {
  last_scan: LastConsolidationScan | null;
  conflicts: ReferenceDupeGroup[];
}

export interface ReferenceDupesScanResponse {
  queued: boolean;
  job_id: string | null;
  result: LastConsolidationScan | null;
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
  | "can_index"
  | "can_extract"
  | "can_teleport"
  | "can_be_requested"
  | "processing_visibility"
  | "server_status_visibility";

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
  // F2: RQ retries remaining (null = no retry policy). A "scheduled" job with this set is a pending
  // automatic retry — the Jobs tab surfaces it so a retry looks like progress, not a stuck job.
  retries_left?: number | null;
}

export interface QueueStatus {
  available: boolean;
  error?: string;
  workers: number;
  counts: Record<string, number>;
  jobs: JobRecord[];
  // D7 queue-health fields for the Jobs-tab semaphore. Optional for compatibility with an older
  // server; fall back to `available`/`workers` when absent.
  redis_reachable?: boolean;
  worker_count?: number;
  queued?: number;
  // E1: true when the server requires Redis (fail-closed). With redis_reachable=false this means
  // rate/queue limits are unavailable and Redis-dependent requests are being rejected with 503.
  require_redis?: boolean;
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string | null = null,
    // Called when an *authenticated* request is rejected with 401 (session expired, signed out,
    // or the account was disabled). The argument is the server's human-readable explanation.
    private readonly onUnauthorized?: (detail: string) => void,
    // Called when a job-creating request is rejected because the processing queue is full (D39:
    // HTTP 429 with a "queue is full" detail). The argument is the server's explanation. Lets the
    // app surface one consistent toast wherever an import/extract/reindex is triggered.
    private readonly onQueueFull?: (detail: string) => void,
  ) {}

  withToken(token: string | null): ApiClient {
    return new ApiClient(
      this.baseUrl,
      token,
      this.onUnauthorized,
      this.onQueueFull,
    );
  }

  async login(username: string, password: string): Promise<string> {
    const response = await this.request<{ access_token: string }>(
      "/api/v1/auth/login",
      {
        method: "POST",
        body: { username, password },
        auth: false,
      },
    );
    return response.access_token;
  }

  async listWorks(query: WorkQuery = {}): Promise<PaginatedWorks> {
    const params = new URLSearchParams();
    if (query.q) params.set("q", query.q);
    if (query.readingStatus) params.set("reading_status", query.readingStatus);
    if (query.shelfId) params.set("shelf_id", query.shelfId);
    if (query.rackId) params.set("rack_id", query.rackId);
    if (query.tagId) params.set("tag_id", query.tagId);
    if (query.hasPdf !== undefined) params.set("has_pdf", String(query.hasPdf));
    if (query.hasReferences !== undefined)
      params.set("has_references", String(query.hasReferences));
    if (query.missing?.length) params.set("missing", query.missing.join(","));
    if (query.sort) params.set("sort", query.sort);
    if (query.order) params.set("order", query.order);
    if (query.page !== undefined) params.set("page", String(query.page));
    if (query.perPage !== undefined)
      params.set("per_page", String(query.perPage));
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return this.request<PaginatedWorks>(`/api/v1/works${suffix}`);
  }

  async getWork(workId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}`);
  }

  // --- Custom themes (P4): read for any user; upload/delete owner+admin ---
  async listThemes(): Promise<CustomThemeSummary[]> {
    return this.request<CustomThemeSummary[]>("/api/v1/themes");
  }

  async getTheme(id: string): Promise<Theme> {
    return this.request<Theme>(`/api/v1/themes/${encodeURIComponent(id)}`);
  }

  // A custom theme's verbatim YAML source — used to prefill the admin editor as a template.
  async getThemeSource(id: string): Promise<{ id: string; yaml: string }> {
    return this.request<{ id: string; yaml: string }>(
      `/api/v1/themes/${encodeURIComponent(id)}/source`,
    );
  }

  async uploadTheme(yaml: string): Promise<ThemeUploadResult> {
    return this.request<ThemeUploadResult>("/api/v1/admin/themes", {
      method: "POST",
      body: { yaml },
    });
  }

  async deleteTheme(id: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/themes/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  }

  async referenceGraph(
    workId: string,
    opts: { includeRefEdges?: boolean; includeCiting?: boolean; maxExternal?: number } = {},
  ): Promise<ReferenceGraph> {
    const query = new URLSearchParams();
    if (opts.includeRefEdges) query.set("include_ref_edges", "true");
    if (opts.includeCiting) query.set("include_citing", "true");
    if (opts.maxExternal !== undefined) query.set("max_external", String(opts.maxExternal));
    const q = query.toString();
    return this.request<ReferenceGraph>(
      `/api/v1/works/${workId}/reference-graph${q ? `?${q}` : ""}`,
    );
  }

  /** Cached external papers that cite this work (batch10 #8). */
  async getCitingPapers(workId: string): Promise<CitingPapersResponse> {
    return this.request<CitingPapersResponse>(
      `/api/v1/works/${workId}/citing-papers`,
    );
  }

  /** Fetch/refresh the external citing papers for a work (OpenAlex → Semantic Scholar). */
  async fetchCitingPapers(workId: string): Promise<CitingPapersResponse> {
    return this.request<CitingPapersResponse>(
      `/api/v1/works/${workId}/citing-papers/fetch`,
      { method: "POST", timeoutMs: 45000 },
    );
  }

  async getPreferences(): Promise<UserPreferences> {
    return this.request<UserPreferences>("/api/v1/preferences");
  }

  async putPreferences(prefs: UserPreferences): Promise<UserPreferences> {
    return this.request<UserPreferences>("/api/v1/preferences", {
      method: "PUT",
      body: prefs,
    });
  }

  // --- Saved filters (per-user; usable as a Library filter and as a graph/export scope) ---
  async listSavedFilters(): Promise<SavedFilter[]> {
    return this.request<SavedFilter[]>("/api/v1/saved-filters");
  }

  async createSavedFilter(payload: SavedFilterCreate): Promise<SavedFilter> {
    return this.request<SavedFilter>("/api/v1/saved-filters", {
      method: "POST",
      body: payload,
    });
  }

  async updateSavedFilter(
    id: string,
    payload: SavedFilterUpdate,
  ): Promise<SavedFilter> {
    return this.request<SavedFilter>(`/api/v1/saved-filters/${id}`, {
      method: "PUT",
      body: payload,
    });
  }

  async deleteSavedFilter(id: string): Promise<void> {
    await this.request<void>(`/api/v1/saved-filters/${id}`, {
      method: "DELETE",
    });
  }

  async importReferenceAsWork(referenceId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/from-reference/${referenceId}`, {
      method: "POST",
    });
  }

  async importCitingPaperAsWork(externalPaperId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/from-citing/${externalPaperId}`, {
      method: "POST",
    });
  }

  async getRelatedWorks(workId: string, limit = 8): Promise<RelatedWork[]> {
    return this.request<RelatedWork[]>(
      `/api/v1/works/${workId}/related?limit=${limit}`,
    );
  }

  async acceptTopicAsTag(
    topicModelId: string,
    topicId: number,
    name: string,
  ): Promise<{ tag_id: string; tagged: number }> {
    return this.request("/api/v1/ai/topics/accept-as-tag", {
      method: "POST",
      body: { topic_model_id: topicModelId, topic_id: topicId, name },
    });
  }

  async createShelfFromTopic(
    topicModelId: string,
    topicId: number,
    name: string,
  ): Promise<{ shelf_id: string; added: number }> {
    return this.request("/api/v1/ai/topics/create-shelf", {
      method: "POST",
      body: { topic_model_id: topicModelId, topic_id: topicId, name },
    });
  }

  async getReadingQueue(): Promise<Work[]> {
    return this.request<Work[]>("/api/v1/works/reading-queue");
  }

  async reorderReadingQueue(workIds: string[]): Promise<Work[]> {
    return this.request<Work[]>("/api/v1/works/reading-queue/reorder", {
      method: "POST",
      body: { work_ids: workIds },
    });
  }

  async createWork(payload: Partial<Work>): Promise<Work> {
    return this.request<Work>("/api/v1/works", {
      method: "POST",
      body: payload,
    });
  }

  async updateWork(id: string, payload: Partial<Work>): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${id}`, {
      method: "PATCH",
      body: payload,
    });
  }

  async deleteWork(id: string): Promise<void> {
    await this.request<void>(`/api/v1/works/${id}`, { method: "DELETE" });
  }

  async listWorkMetadata(workId: string): Promise<FieldReview[]> {
    return this.request<FieldReview[]>(`/api/v1/works/${workId}/metadata`);
  }

  async selectMetadataAssertion(
    workId: string,
    assertionId: string,
  ): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}/metadata/select`, {
      method: "POST",
      body: { assertion_id: assertionId },
    });
  }

  async deleteMetadataAssertion(
    workId: string,
    assertionId: string,
  ): Promise<Work> {
    return this.request<Work>(
      `/api/v1/works/${workId}/metadata/${assertionId}`,
      {
        method: "DELETE",
      },
    );
  }

  // Set a metadata field to a user-entered value (manual correction). Writes a user-sourced
  // canonical assertion + locks the field; an empty value clears it. Returns the refreshed
  // field-review list. Used for editable authors (which has no Work column).
  async setMetadataValue(
    workId: string,
    fieldName: string,
    value: string,
  ): Promise<FieldReview[]> {
    return this.request<FieldReview[]>(`/api/v1/works/${workId}/metadata/set`, {
      method: "POST",
      body: { field_name: fieldName, value },
    });
  }

  async confirmMetadataField(
    workId: string,
    fieldName: string,
    confirmed: boolean,
  ): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}/metadata/confirm`, {
      method: "POST",
      body: { field_name: fieldName, confirmed },
    });
  }

  async enrichWork(
    workId: string,
  ): Promise<{ job_id: string | null; status: string }> {
    return this.request(`/api/v1/works/${workId}/enrich`, { method: "POST" });
  }

  async topicWork(
    workId: string,
  ): Promise<{ job_id: string | null; status: string }> {
    return this.request(`/api/v1/works/${workId}/topics`, { method: "POST" });
  }

  /** Queue a background citing-papers fetch (the Library batch action). */
  async fetchCitingPapersJob(
    workId: string,
  ): Promise<{ job_id: string | null; status: string }> {
    return this.request(`/api/v1/works/${workId}/citing-papers/fetch-job`, {
      method: "POST",
    });
  }

  /** Queue a background per-paper summary (the Library batch action). */
  async summarizeWorkJob(
    workId: string,
  ): Promise<{ job_id: string | null; status: string }> {
    return this.request(`/api/v1/works/${workId}/summaries/job`, {
      method: "POST",
    });
  }

  async keywordsWork(
    workId: string,
  ): Promise<{ job_id: string | null; status: string }> {
    return this.request(`/api/v1/works/${workId}/keywords`, { method: "POST" });
  }

  // Bulk "set one metadata field from the best available source" across selected papers (issue 3).
  async bulkApplyMetadata(
    workIds: string[],
    fieldName: string,
  ): Promise<{ field_name: string; applied: number; skipped: number }> {
    return this.request(`/api/v1/works/bulk-apply-metadata`, {
      method: "POST",
      body: { work_ids: workIds, field_name: fieldName },
    });
  }

  async listWorkFiles(workId: string): Promise<WorkFile[]> {
    return this.request<WorkFile[]>(`/api/v1/works/${workId}/files`);
  }

  async findOnWeb(
    workId: string,
    sources?: string[],
  ): Promise<WebFindResponse> {
    return this.request<WebFindResponse>(
      `/api/v1/works/${workId}/find-on-web`,
      {
        method: "POST",
        body: sources ? { sources } : {},
      },
    );
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
      Accept: "application/x-ndjson",
      "Content-Type": "application/json",
    };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(
      `${this.baseUrl}/api/v1/works/${workId}/find-on-web/stream`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(sources ? { sources } : {}),
      },
    );
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
    if (!response.body) throw new Error("Streaming not supported");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const flushLine = (line: string): void => {
      const trimmed = line.trim();
      if (!trimmed) return;
      onEvent(JSON.parse(trimmed) as WebFindStreamEvent);
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        flushLine(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    }
    // Emit any trailing line that arrived without a closing newline.
    flushLine(buffer);
  }

  async downloadWebCandidates(
    workId: string,
    items: WebFindDownloadItem[],
  ): Promise<WebFindDownloadResponse> {
    return this.request<WebFindDownloadResponse>(
      `/api/v1/works/${workId}/find-on-web/download`,
      {
        method: "POST",
        body: { items },
      },
    );
  }

  // Record a find-on-web result's metadata as reviewable candidate assertions (issue 9). Returns
  // the refreshed per-field review so the caller can show the new candidates immediately.
  async applyWebCandidateMetadata(
    workId: string,
    candidate: WebCandidate,
  ): Promise<FieldReview[]> {
    return this.request<FieldReview[]>(
      `/api/v1/works/${workId}/find-on-web/apply-metadata`,
      {
        method: "POST",
        body: {
          source: candidate.source,
          title: candidate.title,
          authors: candidate.authors,
          year: candidate.year,
          doi: candidate.doi,
          arxiv_id: candidate.arxiv_id,
        },
      },
    );
  }

  async listWorkReferences(workId: string): Promise<ReferenceRecord[]> {
    return this.request<ReferenceRecord[]>(
      `/api/v1/works/${workId}/references`,
    );
  }

  /** Confirm / reject / import a reference's "likely local" match (batch 12). */
  async actOnReference(
    workId: string,
    referenceId: string,
    action: ReferenceAction,
  ): Promise<ReferenceRecord> {
    return this.request<ReferenceRecord>(
      `/api/v1/works/${workId}/references/${referenceId}`,
      { method: "PATCH", body: JSON.stringify({ action }) },
    );
  }

  /** Re-run reference→library matching for one paper's bibliography (batch 12). */
  async rescanWorkReferences(workId: string): Promise<ReferenceRescanResult> {
    return this.request<ReferenceRescanResult>(
      `/api/v1/works/${workId}/references/rescan`,
      { method: "POST" },
    );
  }

  /** Re-run reference→library matching across the WHOLE library (queued; editor+). */
  async rescanAllReferences(): Promise<ReferenceRescanResult> {
    return this.request<ReferenceRescanResult>("/api/v1/works/references/rescan-all", {
      method: "POST",
    });
  }

  async uploadWorkFile(workId: string, file: File): Promise<WorkFile> {
    const form = new FormData();
    form.append("file", file);
    const headers: Record<string, string> = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(
      `${this.baseUrl}/api/v1/works/${workId}/files`,
      {
        method: "POST",
        headers,
        body: form,
      },
    );
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
    return this.request<CitationContext[]>(
      `/api/v1/works/${workId}/citation-contexts`,
    );
  }

  async listAnnotations(workId: string): Promise<Annotation[]> {
    return this.request<Annotation[]>(`/api/v1/works/${workId}/annotations`);
  }

  async createAnnotation(
    workId: string,
    payload: AnnotationCreate,
  ): Promise<Annotation> {
    return this.request<Annotation>(`/api/v1/works/${workId}/annotations`, {
      method: "POST",
      body: payload,
    });
  }

  async deleteAnnotation(workId: string, annotationId: string): Promise<void> {
    await this.request<void>(
      `/api/v1/works/${workId}/annotations/${annotationId}`,
      {
        method: "DELETE",
      },
    );
  }

  async listDuplicateCandidates(
    status: DuplicateCandidateStatus | "" = "open",
  ): Promise<DuplicateCandidate[]> {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return this.request<DuplicateCandidate[]>(`/api/v1/duplicates${suffix}`);
  }

  async scanDuplicateCandidates(
    payload: { work_id?: string; file_id?: string } = {},
  ): Promise<DuplicateScanResult> {
    return this.request<DuplicateScanResult>("/api/v1/duplicates/scan", {
      method: "POST",
      body: payload,
    });
  }

  async updateDuplicateCandidate(
    id: string,
    status: DuplicateCandidateStatus,
  ): Promise<DuplicateCandidate> {
    return this.request<DuplicateCandidate>(`/api/v1/duplicates/${id}`, {
      method: "PATCH",
      body: { status },
    });
  }

  async applyDuplicateCandidateAction(
    id: string,
    action: DuplicateCandidateAction,
    options: {
      targetWorkId?: string;
      splitSegments?: DuplicateSplitSegment[];
    } = {},
  ): Promise<DuplicateCandidate> {
    return this.request<DuplicateCandidate>(`/api/v1/duplicates/${id}`, {
      method: "PATCH",
      body: {
        action,
        target_work_id: options.targetWorkId,
        split_segments: options.splitSegments,
      },
    });
  }

  // Preview a merge of a work/work candidate into the chosen base (the surviving canonical paper).
  async getMergePreview(
    candidateId: string,
    baseWorkId: string,
  ): Promise<MergePreview> {
    return this.request<MergePreview>(
      `/api/v1/duplicates/${candidateId}/merge-preview?base_work_id=${baseWorkId}`,
      // Bound the request so a stalled preview can't hang the row on "Loading preview…" forever.
      { timeoutMs: 15000 },
    );
  }

  // Undo the most recent merge into a paper, restoring the hidden shadow to a standalone paper.
  async unmergePaper(workId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}/unmerge`, {
      method: "POST",
    });
  }

  // Move an attached PDF from one paper to another (issue 4): re-points the file's link.
  async moveWorkFile(
    workId: string,
    fileId: string,
    targetWorkId: string,
  ): Promise<WorkFile> {
    return this.request<WorkFile>(
      `/api/v1/works/${workId}/files/${fileId}/move`,
      { method: "POST", body: { target_work_id: targetWorkId } },
    );
  }

  // Preview merging `sourceWorkId` INTO `workId` (issue 4) — read-only.
  async mergePaperPreview(
    workId: string,
    sourceWorkId: string,
  ): Promise<MergePaperPreview> {
    return this.request<MergePaperPreview>(
      `/api/v1/works/${workId}/merge-preview?source_work_id=${sourceWorkId}`,
    );
  }

  // Merge another paper INTO this one (issue 4); the source becomes a reversible hidden shadow.
  async mergePaper(workId: string, sourceWorkId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}/merge`, {
      method: "POST",
      body: { source_work_id: sourceWorkId },
    });
  }

  // Papers bidirectionally LINKED to this one (Batch D "Link"; distinct from similarity /related).
  async getRelatedLinks(workId: string): Promise<Work[]> {
    return this.request<Work[]>(`/api/v1/works/${workId}/related-links`);
  }

  async listShelves(): Promise<Shelf[]> {
    return this.request<Shelf[]>("/api/v1/shelves");
  }

  async createShelf(payload: {
    name: string;
    description?: string;
    access_level?: AccessLevel;
  }): Promise<Shelf> {
    return this.request<Shelf>("/api/v1/shelves", {
      method: "POST",
      body: payload,
    });
  }

  async updateShelf(id: string, payload: Partial<Shelf>): Promise<Shelf> {
    return this.request<Shelf>(`/api/v1/shelves/${id}`, {
      method: "PATCH",
      body: payload,
    });
  }

  async deleteShelf(id: string): Promise<void> {
    // Hard delete: papers only on this shelf fall back to the default shelf (backend #1).
    await this.request<void>(`/api/v1/shelves/${id}`, { method: "DELETE" });
  }

  async listShelfWorks(shelfId: string): Promise<Work[]> {
    return this.request<Work[]>(`/api/v1/shelves/${shelfId}/works`);
  }

  // "Where is this?": the shelves (with containing racks) a paper belongs to that the caller can
  // SEE, each carrying a per-shelf can_modify flag for gating the Remove button.
  async listWorkShelves(workId: string): Promise<WorkShelfMembership[]> {
    return this.request<WorkShelfMembership[]>(
      `/api/v1/works/${workId}/shelves`,
    );
  }

  async addWorkToShelf(shelfId: string, workId: string): Promise<void> {
    await this.request<void>(`/api/v1/shelves/${shelfId}/works`, {
      method: "POST",
      body: { work_id: workId },
    });
  }

  async removeWorkFromShelf(shelfId: string, workId: string): Promise<void> {
    await this.request<void>(`/api/v1/shelves/${shelfId}/works/${workId}`, {
      method: "DELETE",
    });
  }

  async listRacks(): Promise<Rack[]> {
    return this.request<Rack[]>("/api/v1/racks");
  }

  async createRack(payload: {
    name: string;
    description?: string;
    access_level?: AccessLevel;
  }): Promise<Rack> {
    return this.request<Rack>("/api/v1/racks", {
      method: "POST",
      body: payload,
    });
  }

  async updateRack(id: string, payload: Partial<Rack>): Promise<Rack> {
    return this.request<Rack>(`/api/v1/racks/${id}`, {
      method: "PATCH",
      body: payload,
    });
  }

  async deleteRack(id: string, deleteShelves = false): Promise<void> {
    // Hard delete. When deleteShelves is true, associated shelves are also hard-deleted (papers
    // only on them fall back to the default shelf); otherwise the shelves just leave this rack.
    const suffix = deleteShelves ? "?delete_shelves=true" : "";
    await this.request<void>(`/api/v1/racks/${id}${suffix}`, {
      method: "DELETE",
    });
  }

  async listRackShelves(rackId: string): Promise<Shelf[]> {
    return this.request<Shelf[]>(`/api/v1/racks/${rackId}/shelves`);
  }

  async addShelfToRack(rackId: string, shelfId: string): Promise<void> {
    await this.request<void>(`/api/v1/racks/${rackId}/shelves`, {
      method: "POST",
      body: { shelf_id: shelfId },
    });
  }

  async removeShelfFromRack(rackId: string, shelfId: string): Promise<void> {
    await this.request<void>(`/api/v1/racks/${rackId}/shelves/${shelfId}`, {
      method: "DELETE",
    });
  }

  async listTags(): Promise<Tag[]> {
    return this.request<Tag[]>("/api/v1/tags");
  }

  async createTag(payload: {
    name: string;
    color?: string;
    description?: string;
  }): Promise<Tag> {
    return this.request<Tag>("/api/v1/tags", { method: "POST", body: payload });
  }

  async updateTag(
    id: string,
    payload: {
      name?: string;
      color?: string | null;
      description?: string | null;
    },
  ): Promise<Tag> {
    return this.request<Tag>(`/api/v1/tags/${id}`, {
      method: "PATCH",
      body: payload,
    });
  }

  async deleteTag(id: string): Promise<void> {
    // Removes the tag and every link to it; the tagged papers/shelves/racks just lose the tag.
    await this.request<void>(`/api/v1/tags/${id}`, { method: "DELETE" });
  }

  // The tags applied to one paper (id + name + colour), SEE-gated like the paper itself.
  async listWorkTags(workId: string): Promise<AppliedTag[]> {
    return this.request<AppliedTag[]>(`/api/v1/works/${workId}/tags`);
  }

  async addTagLink(
    tagId: string,
    entityType: string,
    entityId: string,
  ): Promise<void> {
    await this.request<void>(`/api/v1/tags/${tagId}/links`, {
      method: "POST",
      body: { entity_type: entityType, entity_id: entityId },
    });
  }

  async removeTagLink(
    tagId: string,
    entityType: string,
    entityId: string,
  ): Promise<void> {
    const params = new URLSearchParams({
      entity_type: entityType,
      entity_id: entityId,
    });
    await this.request<void>(
      `/api/v1/tags/${tagId}/links?${params.toString()}`,
      {
        method: "DELETE",
      },
    );
  }

  async listSources(): Promise<Source[]> {
    return this.request<Source[]>("/api/v1/sources");
  }

  async createServerFolderSource(payload: {
    name: string;
    path_alias: string;
  }): Promise<Source> {
    return this.request<Source>("/api/v1/sources/server-folder", {
      method: "POST",
      body: payload,
    });
  }

  // --- Server import roots (owner-only; merged yaml + DB whitelist for the "Server folder" import) ---
  async listServerImportRoots(): Promise<ServerImportRoot[]> {
    return this.request<ServerImportRoot[]>("/api/v1/admin/import-roots");
  }

  async addServerImportRoot(payload: {
    alias: string;
    path: string;
  }): Promise<ServerImportRoot> {
    return this.request<ServerImportRoot>("/api/v1/admin/import-roots", {
      method: "POST",
      body: payload,
    });
  }

  async removeServerImportRoot(rootId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/import-roots/${rootId}`, {
      method: "DELETE",
    });
  }

  // --- Find-on-web allowed download hosts (admin-or-owner; merged defaults + DB allowlist) ---
  async listWebFindAllowedHosts(): Promise<WebFindAllowedHost[]> {
    return this.request<WebFindAllowedHost[]>(
      "/api/v1/admin/web-find/allowed-hosts",
    );
  }

  async addWebFindAllowedHost(payload: {
    host: string;
  }): Promise<WebFindAllowedHost> {
    return this.request<WebFindAllowedHost>(
      "/api/v1/admin/web-find/allowed-hosts",
      {
        method: "POST",
        body: payload,
      },
    );
  }

  async removeWebFindAllowedHost(hostId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/web-find/allowed-hosts/${hostId}`, {
      method: "DELETE",
    });
  }

  // --- Find-on-web download policy (owner-only; restricted | careful | unrestricted) ---
  async getWebFindDownloadPolicy(): Promise<WebFindDownloadPolicyResponse> {
    return this.request<WebFindDownloadPolicyResponse>(
      "/api/v1/admin/web-find/download-policy",
    );
  }

  async setWebFindDownloadPolicy(
    policy: WebFindDownloadPolicy,
  ): Promise<WebFindDownloadPolicyResponse> {
    return this.request<WebFindDownloadPolicyResponse>(
      "/api/v1/admin/web-find/download-policy",
      {
        method: "PUT",
        body: { policy },
      },
    );
  }

  async importFolder(sourceId: string): Promise<ImportBatch> {
    return this.request<ImportBatch>("/api/v1/imports/folder", {
      method: "POST",
      body: { source_id: sourceId, recursive: true },
    });
  }

  async importBibtex(
    content: string,
    targetShelfId?: string | null,
  ): Promise<ImportBatch> {
    return this.request<ImportBatch>("/api/v1/imports/bibtex", {
      method: "POST",
      body: { content, target_shelf_id: targetShelfId ?? null },
    });
  }

  async bibtexImportPreview(content: string): Promise<BatchPreviewResponse> {
    return this.request<BatchPreviewResponse>("/api/v1/imports/bibtex/preview", {
      method: "POST",
      body: { content },
    });
  }

  async importRis(
    content: string,
    targetShelfId?: string | null,
  ): Promise<ImportBatch> {
    return this.request<ImportBatch>("/api/v1/imports/ris", {
      method: "POST",
      body: { content, target_shelf_id: targetShelfId ?? null },
    });
  }

  async importCsl(
    content: string,
    targetShelfId?: string | null,
  ): Promise<ImportBatch> {
    return this.request<ImportBatch>("/api/v1/imports/csl", {
      method: "POST",
      body: { content, target_shelf_id: targetShelfId ?? null },
    });
  }

  async uploadPdf(
    file: File,
    targetShelfId?: string | null,
  ): Promise<ImportBatch> {
    const form = new FormData();
    form.append("file", file);
    if (targetShelfId) form.append("target_shelf_id", targetShelfId);
    const headers: Record<string, string> = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(`${this.baseUrl}/api/v1/imports/upload`, {
      method: "POST",
      headers,
      body: form,
    });
    if (!response.ok) {
      let detail = `Upload failed: ${response.status}`;
      try {
        const payload = await response.json();
        detail = payload.detail ?? detail;
      } catch {
        /* keep status message */
      }
      throw new Error(detail);
    }
    return response.json() as Promise<ImportBatch>;
  }

  // Multi-PDF staging import (batch10 #1): upload N PDFs, each extracted before any paper is
  // created. `mode` "preview" returns a batch to review + commit; "direct" auto-creates non-blocked
  // papers. Multipart, so it uses raw fetch (like uploadPdf) rather than the JSON request helper.
  async uploadPdfsMulti(
    files: File[],
    mode: "preview" | "direct" = "preview",
    targetShelfId?: string | null,
  ): Promise<StagingBatch> {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    form.append("mode", mode);
    if (targetShelfId) form.append("target_shelf_id", targetShelfId);
    const headers: Record<string, string> = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(`${this.baseUrl}/api/v1/imports/upload-multi`, {
      method: "POST",
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
    return response.json() as Promise<StagingBatch>;
  }

  async getStagingBatch(batchId: string): Promise<StagingBatch> {
    return this.request<StagingBatch>(`/api/v1/imports/staging/${batchId}`, {
      timeoutMs: 15000,
    });
  }

  async commitStagingBatch(
    batchId: string,
    payload: { auto?: boolean; decisions?: { item_id: string; action: "accept" | "skip" }[] },
  ): Promise<StagingCommitResult> {
    return this.request<StagingCommitResult>(`/api/v1/imports/staging/${batchId}/commit`, {
      method: "POST",
      body: { auto: payload.auto ?? false, decisions: payload.decisions ?? [] },
    });
  }

  async importByIdentifier(
    identifierType: "arxiv" | "doi",
    value: string,
    targetShelfId?: string | null,
  ): Promise<IdentifierImportResponse> {
    return this.request<IdentifierImportResponse>(
      "/api/v1/imports/identifier",
      {
        method: "POST",
        body: {
          identifier_type: identifierType,
          value,
          target_shelf_id: targetShelfId ?? null,
        },
      },
    );
  }

  async batchImportPreview(
    lines: string[],
    engine: EngineKind,
  ): Promise<BatchPreviewResponse> {
    return this.request<BatchPreviewResponse>("/api/v1/imports/batch/preview", {
      method: "POST",
      body: { lines, engine },
    });
  }

  async batchImportCommit(
    drafts: BatchCommitDraft[],
    options: {
      engine: EngineKind;
      targetShelfId?: string | null;
      enrich?: boolean;
    },
  ): Promise<ImportBatch> {
    return this.request<ImportBatch>("/api/v1/imports/batch/commit", {
      method: "POST",
      body: {
        drafts,
        engine: options.engine,
        target_shelf_id: options.targetShelfId ?? null,
        enrich: options.enrich ?? true,
      },
    });
  }

  async createScopeScope(
    scopeType: "library" | "shelf" | "rack",
    scopeId: string | null,
  ): Promise<ScopeSummaryResponse> {
    return this.request<ScopeSummaryResponse>("/api/v1/ai/summaries", {
      method: "POST",
      body: { scope_type: scopeType, scope_id: scopeId ?? null },
    });
  }

  async getLatestScopeSummary(
    scopeType: "library" | "shelf" | "rack",
    scopeId: string | null,
  ): Promise<ScopeSummaryResponse> {
    const params = new URLSearchParams({ scope_type: scopeType });
    if (scopeId) params.set("scope_id", scopeId);
    return this.request<ScopeSummaryResponse>(`/api/v1/ai/summaries/latest?${params}`);
  }

  async listAdminUsers(): Promise<AdminUser[]> {
    return this.request<AdminUser[]>("/api/v1/admin/users");
  }

  async createAdminUser(
    username: string,
    password: string,
    role: UserRole,
  ): Promise<AdminUser> {
    return this.request<AdminUser>("/api/v1/admin/users", {
      method: "POST",
      body: { username, password, role },
    });
  }

  async updateUserRole(userId: string, role: UserRole): Promise<AdminUser> {
    return this.request<AdminUser>(`/api/v1/admin/users/${userId}`, {
      method: "PATCH",
      body: { role },
    });
  }

  async disableUser(userId: string): Promise<AdminUser> {
    return this.request<AdminUser>(`/api/v1/admin/users/${userId}/disable`, {
      method: "POST",
    });
  }

  async enableUser(userId: string): Promise<AdminUser> {
    return this.request<AdminUser>(`/api/v1/admin/users/${userId}/enable`, {
      method: "POST",
    });
  }

  async deleteUser(userId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/users/${userId}`, {
      method: "DELETE",
    });
  }

  async listAgents(): Promise<AgentRecord[]> {
    return this.request<AgentRecord[]>("/api/v1/admin/agents");
  }

  async approveAgent(
    agentId: string,
  ): Promise<{ agent_id: string; status: string; agent_token: string }> {
    return this.request("/api/v1/admin/agents/" + agentId + "/approve", {
      method: "POST",
    });
  }

  async issueEnrollToken(): Promise<EnrollTokenOut> {
    return this.request<EnrollTokenOut>("/api/v1/admin/agents/enroll-token", {
      method: "POST",
    });
  }

  async listAgentFiles(agentId: string): Promise<AgentFileRecord[]> {
    return this.request<AgentFileRecord[]>(
      `/api/v1/admin/agents/${agentId}/files`,
    );
  }

  async updateAgentPrivileges(
    agentId: string,
    privileges: Partial<Record<AgentPrivilege, boolean>>,
  ): Promise<AgentRecord> {
    return this.request<AgentRecord>(
      `/api/v1/admin/agents/${agentId}/privileges`,
      {
        method: "PATCH",
        body: privileges,
      },
    );
  }

  // --- AI provider config + model management (owner) ---
  async getAiConfig(): Promise<{
    config: AiConfig;
    allowed: Record<string, string[]>;
  }> {
    return this.request("/api/v1/admin/ai-config");
  }

  async updateAiConfig(
    changes: Partial<AiConfig>,
  ): Promise<{ config: AiConfig; reindex_job_id: string | null }> {
    return this.request("/api/v1/admin/ai-config", {
      method: "PUT",
      body: changes,
    });
  }

  async getAiProviders(): Promise<AiProviders> {
    return this.request("/api/v1/admin/ai/providers");
  }

  // One-shot status for the AI & Models tab: config + provider availability + reindex coverage +
  // capability flags + the active selection per capability.
  async getAiStatus(): Promise<AiStatus> {
    return this.request("/api/v1/admin/ai/status");
  }

  async listAiModels(): Promise<{ models: AiModel[] }> {
    return this.request("/api/v1/admin/ai/models");
  }

  async pullAiModel(
    provider: string,
    model: string,
  ): Promise<{ job_id: string; status: string }> {
    return this.request("/api/v1/admin/ai/models/pull", {
      method: "POST",
      body: { provider, model },
    });
  }

  /**
   * Validate a provider/model against the daemon (#2). `present`/`embeddings` are null when the
   * daemon (e.g. Ollama) is unreachable; `canonical` is the resolved effective model name.
   */
  async validateAiModel(
    provider: string,
    model: string,
  ): Promise<{
    present: boolean | null;
    embeddings: boolean | null;
    canonical: string;
    error: string | null;
  }> {
    return this.request("/api/v1/admin/ai/models/validate", {
      method: "POST",
      body: { provider, model },
    });
  }

  async deleteAiModel(provider: string, model: string): Promise<unknown> {
    return this.request("/api/v1/admin/ai/models", {
      method: "DELETE",
      body: { provider, model },
    });
  }

  async reindexEmbeddings(): Promise<{ job_id: string; status: string }> {
    return this.request("/api/v1/admin/ai/reindex", { method: "POST" });
  }

  async rebuildLexicalIndex(): Promise<{
    job_id: string | null;
    status: string;
  }> {
    return this.request("/api/v1/admin/ai/lexical-rebuild", { method: "POST" });
  }

  async getReindexStatus(): Promise<{
    model_name: string;
    indexed: number;
    total: number;
  }> {
    return this.request("/api/v1/admin/ai/reindex/status");
  }

  async renameAgent(agentId: string, name: string): Promise<AgentRecord> {
    return this.request<AgentRecord>(`/api/v1/admin/agents/${agentId}`, {
      method: "PATCH",
      body: { name },
    });
  }

  async deleteAgent(agentId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/agents/${agentId}`, {
      method: "DELETE",
    });
  }

  async requestTeleport(agentId: string, localFileId: string): Promise<void> {
    await this.request<void>("/api/v1/imports/teleport", {
      method: "POST",
      body: { agent_id: agentId, local_file_id: localFileId },
    });
  }

  async searchAnnotations(
    q: string,
    annotationType?: string,
  ): Promise<Annotation[]> {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (annotationType) params.set("annotation_type", annotationType);
    return this.request<Annotation[]>(
      `/api/v1/works/annotations/search?${params.toString()}`,
    );
  }

  async exportAnnotations(
    workId: string,
    format: "markdown" | "text" = "markdown",
  ): Promise<{ filename: string; content_type: string; content: string }> {
    return this.request(
      `/api/v1/works/${workId}/annotations/export?format=${format}`,
    );
  }

  async changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<{ status: string; sessions_revoked: number }> {
    return this.request("/api/v1/auth/change-password", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    });
  }

  async getMe(): Promise<CurrentUser> {
    return this.request<CurrentUser>("/api/v1/auth/me");
  }

  async logout(): Promise<void> {
    await this.request<void>("/api/v1/auth/logout", { method: "POST" });
  }

  async updateProfile(changes: {
    display_name?: string | null;
    email?: string | null;
    papers_per_page?: number | null;
    theme?: string | null;
  }): Promise<CurrentUser> {
    return this.request<CurrentUser>("/api/v1/auth/me", {
      method: "PATCH",
      body: changes,
    });
  }

  // --- Backups (S-batch 2026-07-13 item 1): admin export, owner-only restore ---
  async listBackups(): Promise<{
    backups: { archive: string; size_bytes: number; created_at: string }[];
    last_restore: Record<string, unknown> | null;
  }> {
    return this.request("/api/v1/admin/backups");
  }

  async createBackup(includePdfs: boolean): Promise<{ queued: boolean; job_id: string | null; archive: string | null }> {
    return this.request("/api/v1/admin/backups", {
      method: "POST",
      body: { include_pdfs: includePdfs },
    });
  }

  async downloadBackup(name: string): Promise<Blob> {
    return this.requestBlob(`/api/v1/admin/backups/${encodeURIComponent(name)}/download`);
  }

  async deleteBackup(name: string): Promise<void> {
    await this.request(`/api/v1/admin/backups/${encodeURIComponent(name)}`, { method: "DELETE" });
  }

  async uploadBackup(file: globalThis.File): Promise<Record<string, unknown>> {
    const form = new FormData();
    form.append("upload", file);
    const headers: Record<string, string> = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(`${this.baseUrl}/api/v1/admin/backups/upload`, {
      method: "POST",
      headers,
      body: form,
    });
    if (!response.ok) {
      let detail = `Upload failed: ${response.status}`;
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        // keep the generic message
      }
      throw new Error(detail);
    }
    return response.json();
  }

  async analyzeBackup(name: string): Promise<Record<string, unknown>> {
    return this.request(`/api/v1/admin/backups/${encodeURIComponent(name)}/analyze`);
  }

  async restoreBackup(
    name: string,
    payload: { mode: "merge" | "replace"; pdf_root_alias?: string | null; confirm?: string },
  ): Promise<{ queued: boolean; job_id: string | null; summary: Record<string, unknown> | null }> {
    return this.request(`/api/v1/admin/backups/${encodeURIComponent(name)}/restore`, {
      method: "POST",
      body: payload,
    });
  }

  async getReferenceDupes(): Promise<ReferenceDupesResponse> {
    return this.request<ReferenceDupesResponse>("/api/v1/admin/reference-dupes");
  }

  async scanReferenceDupes(): Promise<ReferenceDupesScanResponse> {
    return this.request<ReferenceDupesScanResponse>("/api/v1/admin/reference-dupes/scan", {
      method: "POST",
    });
  }

  async resolveReferenceDupe(winnerReferenceId: string): Promise<ReferenceDupesResponse> {
    return this.request<ReferenceDupesResponse>("/api/v1/admin/reference-dupes/resolve", {
      method: "POST",
      body: { winner_reference_id: winnerReferenceId },
    });
  }

  async getAppConfig(): Promise<AppConfig> {
    return this.request<AppConfig>("/api/v1/admin/app-config");
  }

  async updateAppConfig(changes: Partial<AppConfig>): Promise<AppConfig> {
    return this.request<AppConfig>("/api/v1/admin/app-config", {
      method: "PATCH",
      body: changes,
    });
  }

  async resetUserPassword(
    userId: string,
    newPassword: string,
  ): Promise<{ status: string; sessions_revoked: number }> {
    return this.request(`/api/v1/admin/users/${userId}/reset-password`, {
      method: "POST",
      body: { new_password: newPassword },
    });
  }

  // --- Access control: groups, members, grants, default grants, access settings (admin-or-owner) ---
  async listGroups(): Promise<Group[]> {
    return this.request<Group[]>("/api/v1/admin/groups");
  }

  async createGroup(name: string): Promise<Group> {
    return this.request<Group>("/api/v1/admin/groups", {
      method: "POST",
      body: { name },
    });
  }

  async deleteGroup(groupId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/groups/${groupId}`, {
      method: "DELETE",
    });
  }

  async listGroupMembers(groupId: string): Promise<GroupMember[]> {
    return this.request<GroupMember[]>(
      `/api/v1/admin/groups/${groupId}/members`,
    );
  }

  async addGroupMember(groupId: string, userId: string): Promise<GroupMember> {
    return this.request<GroupMember>(
      `/api/v1/admin/groups/${groupId}/members`,
      {
        method: "POST",
        body: { user_id: userId },
      },
    );
  }

  async removeGroupMember(groupId: string, userId: string): Promise<void> {
    await this.request<void>(
      `/api/v1/admin/groups/${groupId}/members/${userId}`,
      {
        method: "DELETE",
      },
    );
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
      method: "POST",
      body: { target_type: targetType, target_id: targetId },
    });
  }

  async removeGrant(grantId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/grants/${grantId}`, {
      method: "DELETE",
    });
  }

  async listDefaultGrants(): Promise<DefaultGrant[]> {
    return this.request<DefaultGrant[]>("/api/v1/admin/default-grants");
  }

  async addDefaultGrant(
    targetType: GrantTargetType,
    targetId: string,
  ): Promise<DefaultGrant> {
    return this.request<DefaultGrant>("/api/v1/admin/default-grants", {
      method: "POST",
      body: { target_type: targetType, target_id: targetId },
    });
  }

  async removeDefaultGrant(defaultGrantId: string): Promise<void> {
    await this.request<void>(`/api/v1/admin/default-grants/${defaultGrantId}`, {
      method: "DELETE",
    });
  }

  async getAccessSettings(): Promise<AccessSettings> {
    return this.request<AccessSettings>("/api/v1/admin/access-settings");
  }

  async setAccessSettings(
    defaultAccessLevel: AccessLevel,
  ): Promise<AccessSettings> {
    return this.request<AccessSettings>("/api/v1/admin/access-settings", {
      method: "PUT",
      body: { default_access_level: defaultAccessLevel },
    });
  }

  async getJobResult(
    jobId: string,
  ): Promise<{ status: string; result?: unknown; error?: string | null }> {
    return this.request(`/api/v1/jobs/${jobId}/result`);
  }

  async cancelJob(jobId: string): Promise<{ cancelled: boolean; job_id: string }> {
    return this.request(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" });
  }

  async getJobs(limit = 25): Promise<QueueStatus> {
    // 15s timeout: this is polled on a timer, so a stalled request must reject (and be retried on
    // the next tick) rather than hang and pile up behind subsequent polls.
    return this.request<QueueStatus>(`/api/v1/jobs?limit=${limit}`, {
      timeoutMs: 15000,
    });
  }

  async clearJobs(
    which:
      "finished_failed" | "failed" | "finished" | "all" = "finished_failed",
  ): Promise<{ available: boolean; cleared: number; error?: string }> {
    return this.request(`/api/v1/jobs/clear?which=${which}`, {
      method: "POST",
    });
  }

  /** Empty the pending job queue (admin). Running jobs are kept; returns how many were dropped. */
  async clearQueue(): Promise<{
    available: boolean;
    dropped: number;
    error?: string;
  }> {
    return this.request("/api/v1/jobs/clear-queue", { method: "POST" });
  }

  /** Recover stuck jobs (admin): requeue jobs stranded as started and clear failed history. */
  async resetWorkers(): Promise<{
    available: boolean;
    requeued: number;
    cleared_failed: number;
    note: string;
    error?: string;
  }> {
    return this.request("/api/v1/jobs/reset-workers", { method: "POST" });
  }

  async extractFile(
    fileId: string,
    forceOcr = false,
  ): Promise<{ job_id: string | null; status: string }> {
    const q = forceOcr ? "?force_ocr=true" : "";
    return this.request(`/api/v1/files/${fileId}/extract${q}`, {
      method: "POST",
    });
  }

  /** Set which attached file is the paper's main (default-to-open) file. Returns the updated work. */
  async setMainFile(workId: string, fileId: string): Promise<Work> {
    return this.request<Work>(`/api/v1/works/${workId}/main-file/${fileId}`, {
      method: "PUT",
    });
  }

  /** Detach a file from a paper (204). If it was the main file, the backend clears the pointer. */
  async deleteWorkFile(workId: string, fileId: string): Promise<void> {
    return this.request(`/api/v1/works/${workId}/files/${fileId}`, {
      method: "DELETE",
    });
  }

  async extractWork(
    workId: string,
  ): Promise<{ status: string; queued: number; job_ids?: string[] }> {
    return this.request(`/api/v1/works/${workId}/extract`, { method: "POST" });
  }

  async listAuditEvents(
    limit = 50,
    offset = 0,
  ): Promise<{ items: AuditEvent[]; total: number }> {
    // The endpoint returns a paginated envelope { items, total, ... }, not a bare array.
    const page = await this.request<{ items: AuditEvent[]; total: number }>(
      `/api/v1/admin/audit-events?limit=${limit}&offset=${offset}`,
    );
    return { items: page.items ?? [], total: page.total ?? 0 };
  }

  async listFiles(): Promise<FileRecord[]> {
    return this.request<FileRecord[]>("/api/v1/files");
  }

  async getFileBlob(fileId: string): Promise<Blob> {
    return this.requestBlob(`/api/v1/files/${fileId}/stream`);
  }

  // Server-extracted PDF text (native layer, else on-the-fly OCR). The reader uses this as a
  // fallback for search / copy-text when the in-browser pdf.js text layer is empty (scanned PDFs).
  async getFileText(fileId: string): Promise<{ text: string; source: string }> {
    return this.request<{ text: string; source: string }>(
      `/api/v1/files/${fileId}/text`,
    );
  }

  async semanticSearch(q: string, limit = 10): Promise<SemanticSearchResponse> {
    return this.request<SemanticSearchResponse>("/api/v1/search/semantic", {
      method: "POST",
      body: { q, limit },
    });
  }

  async search(
    q: string,
    mode: SearchMode = "hybrid",
    limit = 10,
    embeddingModel?: string,
  ): Promise<HybridSearchResponse> {
    return this.request<HybridSearchResponse>("/api/v1/search", {
      method: "POST",
      body: {
        q,
        mode,
        limit,
        // Omit for the default model; pass a registered model_name or "multimode" to fuse all.
        ...(embeddingModel ? { embedding_model: embeddingModel } : {}),
      },
    });
  }

  /** Registered embedding models + the model cap (admin-scoped; 403 for reader-only sessions). */
  async listEmbeddingModels(): Promise<EmbeddingModelsResponse> {
    return this.request<EmbeddingModelsResponse>(
      "/api/v1/admin/ai/embedding-models",
    );
  }

  // Warm the BM25F+ lexical index (call on library/insights open) so the first search is hot.
  async warmSearch(): Promise<{
    lexical_indexed_docs: number;
    status: string;
  }> {
    return this.request("/api/v1/search/warm", { method: "POST" });
  }

  async listSummaries(workId: string): Promise<Summary[]> {
    return this.request<Summary[]>(`/api/v1/works/${workId}/summaries`);
  }

  async createSummary(
    workId: string,
    summaryType: SummaryType,
  ): Promise<Summary> {
    return this.request<Summary>(`/api/v1/works/${workId}/summaries`, {
      method: "POST",
      body: { summary_type: summaryType },
    });
  }

  async modelTopics(payload: {
    scopeType: GraphScopeType;
    scopeId?: string | null;
    maxTopics?: number;
  }): Promise<TopicModelResponse> {
    return this.request<TopicModelResponse>("/api/v1/ai/topics", {
      method: "POST",
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
    workIds?: string[];
    nodeMode: GraphNodeMode;
    collapseVersions?: boolean;
    colorBy?: GraphColorBy;
    maxExternal?: number;
  }): Promise<CitationGraphResponse> {
    return this.request<CitationGraphResponse>("/api/v1/graphs/citation", {
      method: "POST",
      body: {
        scope: {
          type: payload.scopeType,
          id: payload.scopeId ?? null,
          work_ids: payload.workIds ?? null,
        },
        node_mode: payload.nodeMode,
        collapse_versions: payload.collapseVersions ?? false,
        color_by: payload.colorBy ?? "none",
        max_external: payload.maxExternal ?? 50,
      },
    });
  }

  /** 1-hop (or N-hop) local citation neighborhood of one focus paper (§8.9, Track C P5b). */
  async citationNeighborhood(
    workId: string,
    params: {
      hops?: number;
      nodeMode?: GraphNodeMode;
      colorBy?: GraphColorBy;
    } = {},
  ): Promise<CitationGraphResponse> {
    const query = new URLSearchParams();
    if (params.hops != null) query.set("hops", String(params.hops));
    if (params.nodeMode) query.set("node_mode", params.nodeMode);
    if (params.colorBy) query.set("color_by", params.colorBy);
    return this.request<CitationGraphResponse>(
      `/api/v1/works/${encodeURIComponent(workId)}/citation-neighborhood?${query}`,
    );
  }

  /** Topic (embedding-similarity) graph over the same scope family as the citation graph (#6). */
  async topicGraph(payload: {
    scopeType: GraphScopeType;
    scopeId?: string | null;
    workIds?: string[];
    embeddingModel?: string;
    k?: number;
    minSimilarity?: number;
  }): Promise<TopicGraphResponse> {
    return this.request<TopicGraphResponse>("/api/v1/graphs/topic", {
      method: "POST",
      body: {
        scope: {
          type: payload.scopeType,
          id: payload.scopeId ?? null,
          work_ids: payload.workIds ?? null,
        },
        ...(payload.embeddingModel
          ? { embedding_model: payload.embeddingModel }
          : {}),
        ...(payload.k != null ? { k: payload.k } : {}),
        ...(payload.minSimilarity != null
          ? { min_similarity: payload.minSimilarity }
          : {}),
      },
    });
  }

  /** Registered visualization view types (D38 P2; for the view-type selector). */
  async listVizViewTypes(): Promise<string[]> {
    return (await this.request<{ view_types: string[] }>("/api/v1/viz/"))
      .view_types;
  }

  /** Build a visualization payload for a view type over the chosen scope (D38 P2). */
  async visualization(
    viewType: string,
    params: VizParams = {},
  ): Promise<VizPayload> {
    const query = new URLSearchParams();
    query.set("scope_type", params.scopeType ?? "library");
    if (params.scopeId) query.set("scope_id", params.scopeId);
    for (const id of params.workIds ?? []) query.append("work_ids", id);
    if (params.xAxis) query.set("x_axis", params.xAxis);
    if (params.yAxis) query.set("y_axis", params.yAxis);
    if (params.sizeBy) query.set("size_by", params.sizeBy);
    if (params.colorBy) query.set("color_by", params.colorBy);
    if (params.edgeContext) query.set("edge_context", params.edgeContext);
    if (params.focusWorkId) query.set("focus_work_id", params.focusWorkId);
    if (params.includeEdges) query.set("include_edges", "true");
    if (params.edgeMaxNodes != null)
      query.set("edge_max_nodes", String(params.edgeMaxNodes));
    if (params.embeddingModel)
      query.set("embedding_model", params.embeddingModel);
    if (params.layout) query.set("layout", params.layout);
    if (params.currentYear != null)
      query.set("current_year", String(params.currentYear));
    if (params.maxNodes != null)
      query.set("max_nodes", String(params.maxNodes));
    return this.request<VizPayload>(
      `/api/v1/viz/${encodeURIComponent(viewType)}?${query}`,
    );
  }

  /** Scoped citation summary — the §8.11 analytics (D38 P4). Cached + versioned server-side. */
  async citationSummary(
    params: CitationSummaryParams = {},
  ): Promise<CitationSummary> {
    const query = new URLSearchParams();
    query.set("scope_type", params.scopeType ?? "library");
    if (params.scopeId) query.set("scope_id", params.scopeId);
    for (const id of params.workIds ?? []) query.append("work_ids", id);
    if (params.limit != null) query.set("limit", String(params.limit));
    return this.request<CitationSummary>(`/api/v1/citations/summary?${query}`);
  }

  /** Venue + author aggregation for a scope (batch10 #7). */
  async venueAuthorSummary(
    params: CitationSummaryParams = {},
  ): Promise<VenueAuthorSummary> {
    const query = new URLSearchParams();
    query.set("scope_type", params.scopeType ?? "library");
    if (params.scopeId) query.set("scope_id", params.scopeId);
    for (const id of params.workIds ?? []) query.append("work_ids", id);
    if (params.limit != null) query.set("limit", String(params.limit));
    return this.request<VenueAuthorSummary>(
      `/api/v1/citations/venue-author-summary?${query}`,
    );
  }

  /** On-demand preview of an external cited-but-missing reference (Track C C1; identifier-only). */
  async externalPreview(params: {
    doi?: string | null;
    arxiv?: string | null;
    referenceId?: string | null;
  }): Promise<ExternalPreview> {
    const query = new URLSearchParams();
    if (params.doi) query.set("doi", params.doi);
    if (params.arxiv) query.set("arxiv", params.arxiv);
    if (params.referenceId) query.set("reference_id", params.referenceId);
    return this.request<ExternalPreview>(
      `/api/v1/citations/external-preview?${query}`,
    );
  }

  /** The caller's frequently-cited-but-missing import/ignore decisions (Track C C3a). */
  async getWorklist(): Promise<Record<string, MissingDecision>> {
    const body = await this.request<{
      decisions: Record<string, MissingDecision>;
    }>("/api/v1/citations/worklist");
    return body.decisions;
  }

  /** Record (upsert) an import/ignore decision for a missing work; returns the updated map. */
  async setWorklistDecision(
    key: string,
    decision: MissingDecision,
  ): Promise<Record<string, MissingDecision>> {
    const body = await this.request<{
      decisions: Record<string, MissingDecision>;
    }>("/api/v1/citations/worklist", {
      method: "PUT",
      body: { key, decision },
    });
    return body.decisions;
  }

  /** Clear (undo) a missing-work decision; returns the updated map. */
  async clearWorklistDecision(
    key: string,
  ): Promise<Record<string, MissingDecision>> {
    const query = new URLSearchParams({ key });
    const body = await this.request<{
      decisions: Record<string, MissingDecision>;
    }>(`/api/v1/citations/worklist?${query}`, { method: "DELETE" });
    return body.decisions;
  }

  /** Export the scope's frequently-cited-but-missing list as BibTeX or CSV (Track C C3b). */
  async exportMissingWorks(params: {
    scopeType?: GraphScopeType;
    scopeId?: string | null;
    workIds?: string[];
    format: "bibtex" | "csv";
  }): Promise<ExportResponse> {
    const query = new URLSearchParams();
    query.set("scope_type", params.scopeType ?? "library");
    if (params.scopeId) query.set("scope_id", params.scopeId);
    for (const id of params.workIds ?? []) query.append("work_ids", id);
    query.set("format", params.format);
    return this.request<ExportResponse>(
      `/api/v1/citations/missing-export?${query}`,
    );
  }

  /** Import batches for the graph's import-batch scope picker (access-filtered, newest first). */
  async listImportBatches(): Promise<ImportBatch[]> {
    return this.request<ImportBatch[]>("/api/v1/imports/batches");
  }

  async exportCitations(payload: {
    scope_type: ExportScopeType;
    scope_id?: string | null;
    work_ids?: string[];
    format: ExportFormat;
    style?: string;
  }): Promise<ExportResponse> {
    return this.request<ExportResponse>("/api/v1/exports", {
      method: "POST",
      body: payload,
    });
  }

  /** Citation styles offered for the `styled` export format (backend is the source of truth). */
  async listCitationStyles(): Promise<CitationStyle[]> {
    return this.request<CitationStyle[]>("/api/v1/exports/styles");
  }

  private async request<T>(
    path: string,
    options: {
      method?: string;
      body?: unknown;
      auth?: boolean;
      // Abort the request after this many ms. Used by polling calls (e.g. getJobs) so a stalled
      // network request rejects instead of hanging forever and stacking behind the next poll.
      timeoutMs?: number;
    } = {},
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (options.body !== undefined)
      headers["Content-Type"] = "application/json";
    if (options.auth !== false && this.token)
      headers.Authorization = `Bearer ${this.token}`;

    const signal =
      options.timeoutMs &&
      typeof AbortSignal !== "undefined" &&
      typeof AbortSignal.timeout === "function"
        ? AbortSignal.timeout(options.timeoutMs)
        : undefined;
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: options.method ?? "GET",
      headers,
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal,
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
      // A "queue is full" rejection (D39: 429/503 from a job-creating action) is surfaced app-wide
      // as a toast. Keyed off the detail phrase so it doesn't fire on the rate-limit 429.
      if (
        (response.status === 429 || response.status === 503) &&
        /queue is full/i.test(detail)
      ) {
        this.onQueueFull?.(detail);
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
        let detail = "Your session has ended. Please sign in again.";
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
