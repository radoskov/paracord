<!-- WorkDetail — the full single-paper detail panel: editable metadata/fields, files, references,
     citation contexts, annotations, summaries, tags, shelf membership, find-on-web search/download,
     and reference-graph modal. Props: client, work (the open paper), onUpdated/onClose/onDeleted/
     onImported/onSelectWork/onBack callbacks. Events/callbacks: calls onUpdated(work) after any
     server-confirmed edit, onSelectWork(workId) to navigate to a related/citing paper, onBack() to
     pop the host page's paper-view history. Non-obvious lifecycle/state: `$: if (work.id !==
     loadedId) loadDetail(work)` re-loads all paper sub-resources whenever the parent swaps in a
     different work; a poll loop (JOB_POLL_MS) watches the Jobs queue for extract/enrich/topic/
     keyword jobs targeting this work or its files and refetches once they settle; the PDF reader is
     gated solely on the nullable `readerUrl` (see inline note) to avoid a stale-boolean desync bug;
     find-on-web state is persisted to a per-work cache (findWebCache) so it survives closing/
     reopening the panel; onDestroy revokes the reader's object URL and stops job polling. -->
<script lang="ts">
  import { onDestroy } from 'svelte';

  import {
    ApiClient,
    type Annotation,
    type AnnotationCreate,
    type AppliedTag,
    type CitationContext,
    type CitingPapersResponse,
    type FieldReview,
    type JobRecord,
    type MergePaperPreview,
    type QueueStatus,
    type ReferenceRecord,
    type RelatedWork,
    type Summary,
    type SummaryDetail,
    type WebCandidate,
    type WebFindDownloadItem,
    type WebFindDownloadResult,
    type WebFindStreamEvent,
    type Work,
    type WorkFile,
    type WorkShelfMembership,
  } from '../api/client';
  import {
    clearFindWebCache,
    getFindWebCache,
    setFindWebCache,
    type SourceProgress,
  } from '../lib/findWebCache';
  import { ensureTags, refreshTags, tags } from '../lib/catalog';
  import { pendingImportText, pendingLibrarySearch } from '../lib/selection';
  import { canEdit, canManageStructure, canModifyWork, currentUser, INSUFFICIENT_ROLE } from '../lib/session';
  import { tameTitle } from '../lib/titleCase';
  import { errorMessage, formatBytes } from '../lib/ui';
  import { renderSummaryMath } from '../lib/renderMath';
  import Modal from './Modal.svelte';
  import PdfReader from './PdfReader.svelte';
  import ReferenceGraphModal from './ReferenceGraphModal.svelte';
  import ShelfPicker from './ShelfPicker.svelte';
  import WorkPicker from './WorkPicker.svelte';

  export let client: ApiClient;
  export let work: Work;
  export let onUpdated: (work: Work) => void = () => {};
  export let onClose: () => void = () => {};
  export let onDeleted: (workId: string) => void = () => {};
  export let onImported: () => void = () => {};
  // Switch the open paper to a related one (wired by the page hosting this detail panel).
  export let onSelectWork: (workId: string) => void = () => {};
  // Reopen the previously viewed paper (the host page keeps the history); null hides the button.
  export let onBack: (() => void) | null = null;

  const STATUSES = ['unread', 'skimmed', 'reading', 'read', 'important', 'revisit'];

  let loadedId = '';
  let loading = false;
  let message = '';

  // editable fields
  let form = { canonical_title: '', year: '', venue: '', doi: '', arxiv_id: '', abstract: '', notes: '', authors: '', reading_status: 'unread' };

  let fields: FieldReview[] = [];
  let files: WorkFile[] = [];
  let contexts: CitationContext[] = [];
  let references: ReferenceRecord[] = [];
  let annotations: Annotation[] = [];
  let summaries: Summary[] = [];
  let appliedTags: AppliedTag[] = [];
  let applyTagId = '';
  // 2026-07-16: tags offered for THIS paper (global + those scoped to its shelves/racks). null =
  // not loaded (or the client lacks the method) → fall back to all tags; [] = genuinely none offered.
  let assignableTags: Tag[] | null = null;
  $: tagOptions = assignableTags ?? $tags;
  // Inline "create tag" (make + apply without leaving the paper). Gated on canEdit (contributor).
  let creatingTag = false;
  let newTagName = '';
  let tagCreateBusy = false;
  let tagCreateError = '';
  let attachFile: File | null = null;

  // Find-on-web (#5 / v2): picker modal state.
  let showFindModal = false;
  let showRefGraph = false;
  let searching = false;
  let findResults: WebCandidate[] = [];
  let degradedSources: string[] = [];
  let selectedIds = new Set<string>();
  let downloadStatus: Record<string, WebFindDownloadResult> = {};
  let downloading = false;
  // Live per-source progress for the streaming search ('querying' | 'done' | 'failed', + count).
  // The `SourceProgress` type is shared with the per-work find-on-web cache (#4).
  let sourceProgress: SourceProgress[] = [];
  // Download progress shown in the sticky bar: how many actually attached (ok) and how many
  // were processed (done), out of the selected total. `done > ok` means some failed/were blocked.
  let downloadDone = 0;
  let downloadOk = 0;
  let downloadTotal = 0;
  // Pending needs_confirmation prompt: the item + the server's reason/url, awaiting the user's call.
  let confirmPrompt: { item: WebFindDownloadItem; reason: string | null; candidateTitle: string } | null =
    null;
  let confirmResolve: ((ok: boolean) => void) | null = null;

  let readerFile: WorkFile | null = null;
  // 2026-07-16: the reader is gated SOLELY on readerUrl (non-null = open). A separate showReader
  // boolean desynced from readerUrl and the false→true toggle got coalesced by Svelte → the reader
  // needed two clicks / stopped opening. One nullable variable can't desync; each open sets a fresh
  // URL (mounts / reloads), close nulls it (unmounts).
  let readerUrl: string | null = null;
  // When the reader is opened to jump straight to a reference's in-text mentions.
  let readerJumpReferenceId: string | null = null;
  // Reference entry to scroll-to + flash when a citation overlay is clicked in the reader.
  let flashRefId = '';

  $: if (work && work.id !== loadedId) void loadDetail(work);

  // Whether the signed-in user may modify THIS paper (contributor → own only; editor+ → any
  // visible paper). Gates every mutating affordance below; the server is the source of truth.
  $: canModify = canModifyWork($currentUser, work);

  // Authors: the Work has no author column, so take them from the 'authors' metadata-review field
  // (canonical value, else the selected/first assertion). Used by the find-on-web header and the
  // editable Authors row in the Details panel.
  function authorsFromFields(fs: FieldReview[]): string {
    const f = fs.find((x) => x.field_name === 'authors');
    if (!f) return '';
    const selected = f.assertions.find((a) => a.selected_as_canonical);
    return (f.canonical_value ?? selected?.value ?? f.assertions[0]?.value ?? '').trim();
  }
  $: searchedAuthors = authorsFromFields(fields);

  // Note counts per file (and overall) from the already-loaded annotations — no extra request.
  // Annotations with a null file_id (not bound to a specific PDF) count toward the work total and
  // are surfaced separately as "unattached" so they aren't silently lost.
  $: noteCountByFile = annotations.reduce<Record<string, number>>((m, a) => {
    if (a.file_id) m[a.file_id] = (m[a.file_id] ?? 0) + 1;
    return m;
  }, {});
  $: noteCount = annotations.length;
  $: unattachedNoteCount = annotations.filter((a) => !a.file_id).length;

  // GROBID extraction needs at least one file whose PDF bytes are on the server.
  $: hasReadableFile = files.some((f) => f.content_available);

  // The paper's main (default-to-open) file: work.main_file_id if set, else the first attached file
  // (#16). The quick-read button under the title opens this.
  $: mainFile =
    files.find((f) => f.id === work.main_file_id) ?? files[0] ?? null;

  // Reference ids that have at least one in-text mention with page coordinates — these can
  // be located in the reader via "Find in text".
  $: locatableReferenceIds = new Set(
    contexts
      .filter((c) => c.reference_id && (c.pdf_coordinates?.length ?? 0) > 0)
      .map((c) => c.reference_id),
  );

  async function run(fn: () => Promise<void>, ok?: string): Promise<void> {
    loading = true;
    message = '';
    try {
      await fn();
      if (ok) message = ok;
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  // Live-refresh: while a background extract/enrich/topic/keyword job is in flight for the OPEN
  // paper, poll the jobs queue and refetch the work once every relevant job has settled, so its
  // metadata updates without the user navigating away/back. Polls only while something is pending.
  const JOB_POLL_MS = 4000;
  const JOB_POLL_MAX_TICKS = 90; // ~6 min safety cap
  const IN_FLIGHT_STATUSES = new Set(['queued', 'started', 'deferred', 'scheduled']);
  let jobPollTimer: ReturnType<typeof setInterval> | undefined;
  let jobPollTicks = 0;
  let watchedJobIds = new Set<string>();
  let sawInFlightJob = false;

  $: workFileIds = new Set(files.map((f) => f.id));

  function jobMatchesOpenWork(job: JobRecord): boolean {
    if (job.target_kind === 'work' && job.target_id === work.id) return true;
    if (job.target_kind === 'file' && job.target_id && workFileIds.has(job.target_id)) return true;
    return false;
  }

  function stopJobPolling(): void {
    if (jobPollTimer) {
      clearInterval(jobPollTimer);
      jobPollTimer = undefined;
    }
    watchedJobIds = new Set();
    sawInFlightJob = false;
    jobPollTicks = 0;
  }

  async function refreshOpenWork(): Promise<void> {
    try {
      const fresh = await client.getWork(work.id);
      // Staleness guard (issue 2): a background job-poll refetch must never clobber a value the
      // user just applied (e.g. "Use this" on the title) with an in-flight, older read. Only apply
      // when the server copy is at least as new as the one we already hold.
      if (fresh.updated_at && work.updated_at && fresh.updated_at < work.updated_at) return;
      onUpdated(fresh);
      await loadDetail(fresh, false);
    } catch {
      // Best-effort refresh; keep the current view if the refetch fails.
    }
  }

  async function pollJobsForOpenWork(): Promise<void> {
    jobPollTicks += 1;
    let status: QueueStatus;
    try {
      status = await client.getJobs(60);
    } catch {
      stopJobPolling();
      return;
    }
    if (!status.available) {
      stopJobPolling();
      return;
    }
    const jobs = status.jobs ?? [];
    const byId = new Map(jobs.map((j) => [j.id, j]));
    let explicitPending = false;
    for (const id of watchedJobIds) {
      const j = byId.get(id);
      if (j && IN_FLIGHT_STATUSES.has(j.status)) explicitPending = true;
    }
    const matchedPending = jobs.some(
      (j) => jobMatchesOpenWork(j) && IN_FLIGHT_STATUSES.has(j.status),
    );
    if (matchedPending) sawInFlightJob = true;
    if (!explicitPending && !matchedPending) {
      const hadWork = watchedJobIds.size > 0 || sawInFlightJob;
      stopJobPolling();
      if (hadWork) await refreshOpenWork();
      return;
    }
    if (jobPollTicks >= JOB_POLL_MAX_TICKS) {
      stopJobPolling();
      await refreshOpenWork();
    }
  }

  // Begin (or extend) polling for the open paper. Pass the job ids returned by an action so a job
  // that finishes before the first poll is still detected; called with no args on open to pick up
  // a job already in flight (e.g. import extraction).
  function watchWorkJobs(jobIds: (string | null | undefined)[] = []): void {
    for (const id of jobIds) if (id) watchedJobIds.add(id);
    if (jobPollTimer) return;
    jobPollTicks = 0;
    jobPollTimer = setInterval(() => void pollJobsForOpenWork(), JOB_POLL_MS);
  }

  async function loadDetail(w: Work, watchJobs = true): Promise<void> {
    const switching = w.id !== loadedId;
    if (switching) stopJobPolling();
    loadedId = w.id;
    // Only tear the reader down on a genuine paper SWITCH. A same-paper background refresh (a
    // job-poll's refreshOpenWork) must NOT close the reader the user has open (2026-07-16).
    if (switching) clearReader();
    citing = null;
    citingLoaded = false;
    form = {
      canonical_title: w.canonical_title ?? '',
      year: w.year ? String(w.year) : '',
      venue: w.venue ?? '',
      doi: w.doi ?? '',
      arxiv_id: w.arxiv_id ?? '',
      abstract: w.abstract ?? '',
      notes: w.notes ?? '',
      authors: '',
      reading_status: w.reading_status,
    };
    await run(async () => {
      // Trailing ensureTags() primes the shared tag store in parallel; its result isn't destructured.
      // Citing papers load eagerly too (a cheap stored-links read), so the section header shows its
      // count without having to expand it first — same as the References section.
      [fields, files, contexts, references, annotations, summaries, appliedTags, citing] =
        await Promise.all([
          client.listWorkMetadata(w.id),
          client.listWorkFiles(w.id),
          client.listCitationContexts(w.id),
          client.listWorkReferences(w.id),
          client.listAnnotations(w.id),
          client.listSummaries(w.id),
          client.listWorkTags(w.id),
          // Best-effort (never blocks the detail load); Promise.resolve() also absorbs a partial
          // test client without the method.
          Promise.resolve()
            .then(() => client.getCitingPapers(w.id))
            .catch(() => null),
          ensureTags(client),
        ]);
      // Best-effort: which tags are offered for this paper's shelves/racks (2026-07-16).
      assignableTags = client.listAssignableTags
        ? await client.listAssignableTags(w.id).catch(() => [])
        : null;
      citingLoaded = citing != null;
    });
    // Seed the editable Authors field from the loaded 'authors' assertion (no Work column exists).
    form = { ...form, authors: authorsFromFields(fields) };
    // Pick up a job already in flight for this paper (e.g. extraction queued at import time).
    if (watchJobs) watchWorkJobs();
  }

  async function save(): Promise<void> {
    await run(async () => {
      const updated = await client.updateWork(work.id, {
        canonical_title: form.canonical_title || null,
        year: form.year ? Number(form.year) : null,
        venue: form.venue || null,
        doi: form.doi || null,
        arxiv_id: form.arxiv_id || null,
        abstract: form.abstract || null,
        notes: form.notes || null,
        reading_status: form.reading_status as Work['reading_status'],
      });
      onUpdated(updated);
      // Authors has no Work column — persist a manual change as a user-sourced 'authors' assertion.
      // setMetadataValue returns the refreshed field list, so only re-list otherwise.
      if (form.authors.trim() !== authorsFromFields(fields)) {
        fields = await client.setMetadataValue(work.id, 'authors', form.authors.trim());
      } else {
        fields = await client.listWorkMetadata(work.id);
      }
    }, 'Saved');
  }

  // UX batch 4 / 2026-07-16: short vs detailed summaries are stored separately; detailed has three
  // effort levels ("{provider}_detailed_{fast|section|deep}"). The panel shows the short one and the
  // detailed one for the SELECTED effort, each with its own generate/regenerate; a history popup
  // exposes every cached (effort × model) variant read-only.
  const isDetailedType = (t: string) => t.includes('_detailed');
  const DETAIL_EFFORTS: { value: SummaryDetail; label: string; hint: string }[] = [
    { value: 'detailed_fast', label: 'Fast', hint: 'Group sections into ~4 buckets (cheapest)' },
    { value: 'detailed_section', label: 'Section', hint: 'One pass per top-level section' },
    { value: 'detailed_deep', label: 'Deep', hint: 'One pass per subsection (most detail, slowest)' },
  ];
  let detailedEffort: SummaryDetail = 'detailed_section';
  let historyOpen = false;
  let historyView: Summary | null = null;
  // 2026-07-16: render summary maths with KaTeX ("fancy") or show raw text ("plain" fallback).
  let mathMode: 'fancy' | 'plain' = 'fancy';
  $: shortSummary = summaries.find((s) => !isDetailedType(s.summary_type)) ?? null;
  $: detailedSummary =
    summaries.find((s) => s.summary_type.endsWith(`_${detailedEffort}`)) ?? null;
  $: detailedHistory = summaries.filter((s) => isDetailedType(s.summary_type));
  let summarisingDetail: SummaryDetail | null = null;

  async function summariseDetail(detail: SummaryDetail): Promise<void> {
    summarisingDetail = detail;
    let backgrounded = false;
    await run(async () => {
      const res = await client.createSummary(work.id, 'auto', detail);
      if (res.queued && res.job_id) {
        // Detailed summaries run on the worker — poll the Jobs list, then reload (UX batch 4b).
        backgrounded = true;
        message = 'Generating the detailed summary in the background…';
        void pollSummaryJob(res.job_id).finally(() => (summarisingDetail = null));
        return;
      }
      summaries = await client.listSummaries(work.id);
    }, detail !== 'short' ? 'Detailed summary generated' : 'Summary generated');
    if (!backgrounded) summarisingDetail = null;
  }

  async function pollSummaryJob(jobId: string): Promise<void> {
    const active = new Set(['queued', 'started', 'deferred', 'scheduled']);
    for (let attempt = 0; attempt < 300; attempt += 1) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const status = await client.getJobs(100);
        const job = status.jobs.find((j) => j.id === jobId);
        if (job && job.status === 'failed') {
          message = `Summary job failed: ${job.error ?? 'see the Jobs tab'}`;
          return;
        }
        if (!job || !active.has(job.status)) {
          summaries = await client.listSummaries(work.id);
          message = 'Detailed summary ready.';
          return;
        }
      } catch {
        /* transient poll error — keep trying */
      }
    }
    message = 'Still generating — check the Jobs tab.';
  }

  // Citing papers (batch10 #8): external papers that cite this one, fetched on demand from
  // OpenAlex/Semantic Scholar. Loaded (cached list) on first panel open; (re)fetched via a button.
  let citing: CitingPapersResponse | null = null;
  let citingLoaded = false;
  async function loadCiting(): Promise<void> {
    if (citingLoaded) return;
    await run(async () => {
      citing = await client.getCitingPapers(work.id);
      citingLoaded = true;
    });
  }
  async function fetchCiting(): Promise<void> {
    await run(async () => {
      citing = await client.fetchCitingPapers(work.id);
      citingLoaded = true;
      message = citing.items.length
        ? `Fetched ${citing.items.length} citing paper(s)${citing.source ? ` via ${citing.source}` : ''}.`
        : 'No citing papers found (or none available from the open sources).';
    });
  }

  // Import a citing paper into the library (UX batch): works without a DOI too — the backend
  // creates the paper from the cached title/year/venue/authors and enriches when an identifier
  // is present.
  async function importCiter(citerId: string): Promise<void> {
    await run(async () => {
      await client.importCitingPaperAsWork(citerId);
      citing = await client.getCitingPapers(work.id);
      onImported();
    }, 'Citing paper imported into the library');
  }

  // Panel-header badges (UX batch): show at a glance that some references/citers need review
  // ("likely match"), are already held ("in library"), or are external — without expanding.
  $: refLikelyCount = references.filter(
    (r) => r.resolution_status === 'likely_match' && r.suggested_work_id,
  ).length;
  $: refInLibraryCount = references.filter((r) => r.resolved_work_id).length;
  $: refExternalCount = references.length - refInLibraryCount - refLikelyCount;
  $: citingInLibraryCount = citing?.items.filter((c) => c.resolved_work_id).length ?? 0;
  $: citingExternalCount = (citing?.items.length ?? 0) - citingInLibraryCount;

  // "Find & Import" (UX batch): prefill the batch-citation search with this entry (incl. the DOI
  // when present) and jump to the Import tab's Citations sub-tab, so the user reviews candidates
  // instead of importing blind. Same mechanism as the reference graph's import push.
  function findAndImport(title: string | null, year: number | null, doi: string | null): void {
    const base = (title ?? '').trim() || 'Untitled';
    let line = year != null ? `${base} (${year})` : base;
    if (doi) line += ` doi:${doi}`;
    pendingImportText.set(line);
    window.location.hash = '#import';
  }

  let related: RelatedWork[] = [];
  let relatedLoaded = false;
  async function loadRelated(): Promise<void> {
    await run(async () => {
      related = await client.getRelatedWorks(work.id, 8);
      relatedLoaded = true;
    });
  }

  // Batch D: papers explicitly LINKED to this one ("related / same work"), plus the Unmerge action.
  let relatedLinks: Work[] = [];
  let relatedLinksLoaded = false;
  async function loadRelatedLinks(): Promise<void> {
    await run(async () => {
      relatedLinks = await client.getRelatedLinks(work.id);
      relatedLinksLoaded = true;
    });
  }

  async function unmergePaper(): Promise<void> {
    if (!window.confirm('Undo the most recent merge into this paper? The merged paper is restored as a separate paper.'))
      return;
    await run(async () => {
      const updated = await client.unmergePaper(work.id);
      onUpdated(updated);
    }, 'Merge undone');
  }

  // Issue 4 — move an attached PDF to another paper. `moveFile` is the file being moved (opens the
  // picker modal); `null` when closed.
  let moveFile: WorkFile | null = null;
  function openMove(file: WorkFile): void {
    moveFile = file;
  }
  async function doMove(target: Work): Promise<void> {
    const file = moveFile;
    if (!file) return;
    await run(async () => {
      await client.moveWorkFile(work.id, file.id, target.id);
      files = await client.listWorkFiles(work.id);
      // The moved file may have been this paper's main file; reflect the server's cleared pointer.
      if (work.main_file_id === file.id) onUpdated({ ...work, main_file_id: null });
    }, `Moved “${file.original_filename ?? 'file'}” to “${target.canonical_title || 'the other paper'}”.`);
    moveFile = null;
  }

  // Issue 4 — merge another paper INTO this one. Two-step: pick a source → preview → confirm.
  let mergeOpen = false;
  let mergeSource: Work | null = null;
  let mergePreview: MergePaperPreview | null = null;
  function openMerge(): void {
    mergeOpen = true;
    mergeSource = null;
    mergePreview = null;
  }
  async function selectMergeSource(source: Work): Promise<void> {
    await run(async () => {
      mergeSource = source;
      mergePreview = await client.mergePaperPreview(work.id, source.id);
    });
  }
  async function doMerge(): Promise<void> {
    const source = mergeSource;
    if (!source) return;
    await run(async () => {
      const updated = await client.mergePaper(work.id, source.id);
      onUpdated(updated);
      await loadDetail(updated);
    }, `Merged “${source.canonical_title || 'the other paper'}” into this paper.`);
    mergeOpen = false;
    mergeSource = null;
    mergePreview = null;
  }

  // Organization / "Where is this?" — the shelves (and their racks) this paper sits in. Lazy-loaded
  // on first <details> open (like Related papers). Add/remove are STRUCTURE ops gated on the
  // librarian floor ($canManageStructure) + the per-shelf can_modify flag, NOT canModify.
  let locations: WorkShelfMembership[] = [];
  let locationsLoaded = false;
  let showPutInto = false;
  let putIntoShelfId = '';
  async function loadLocations(): Promise<void> {
    await run(async () => {
      locations = await client.listWorkShelves(work.id);
      locationsLoaded = true;
    });
  }

  async function addToShelf(): Promise<void> {
    if (!putIntoShelfId) return;
    const shelfId = putIntoShelfId;
    await run(async () => {
      await client.addWorkToShelf(shelfId, work.id);
      await loadLocations();
      showPutInto = false;
      putIntoShelfId = '';
    }, 'Added to shelf');
  }

  async function removeFromShelf(shelfId: string): Promise<void> {
    await run(async () => {
      await client.removeWorkFromShelf(shelfId, work.id);
      await loadLocations();
    }, 'Removed from shelf');
  }

  async function importReference(referenceId: string): Promise<void> {
    await run(async () => {
      await client.importReferenceAsWork(referenceId);
      references = await client.listWorkReferences(work.id);
      onImported();
    }, 'Reference imported into the library');
  }

  // Confirm / reject a reference's "likely local" match (batch 12). Feedback is shown inline on
  // the reference row — the shared `message` line sits at the top of a long modal, so an error
  // there reads as "nothing happened" (UX batch).
  let refActionPendingId: string | null = null;
  let refActionError: { id: string; text: string } | null = null;
  async function actOnReference(
    referenceId: string,
    action: 'link' | 'reject',
  ): Promise<void> {
    refActionPendingId = referenceId;
    refActionError = null;
    await run(async () => {
      try {
        await client.actOnReference(work.id, referenceId, action);
      } catch (error) {
        refActionError = { id: referenceId, text: errorMessage(error) };
        throw error;
      } finally {
        refActionPendingId = null;
      }
      references = await client.listWorkReferences(work.id);
    }, action === 'link' ? 'Match confirmed' : 'Marked as not a match');
  }

  // Re-run reference→library matching for this paper's bibliography (batch 12).
  async function rescanReferences(): Promise<void> {
    await run(async () => {
      const res = await client.rescanWorkReferences(work.id);
      references = await client.listWorkReferences(work.id);
      message = `Rescanned ${res.scanned} references — ${res.changed} updated`;
    });
  }

  async function exportNotes(): Promise<void> {
    await run(async () => {
      const r = await client.exportAnnotations(work.id, 'markdown');
      const url = URL.createObjectURL(new Blob([r.content], { type: r.content_type }));
      const a = document.createElement('a');
      a.href = url;
      a.download = r.filename;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  async function enrich(): Promise<void> {
    await run(async () => {
      const result = await client.enrichWork(work.id);
      watchWorkJobs([result.job_id]);
      message =
        `Enrichment ${result.status} (job ${(result.job_id ?? '').slice(0, 8) || 'n/a'}). ` +
        'It runs in the background worker — this paper refreshes automatically when it finishes.';
    });
  }

  async function extract(): Promise<void> {
    await run(async () => {
      const result = await client.extractWork(work.id);
      if (result.status === 'no_files') {
        message = 'No files attached — attach a PDF first, then extract.';
        return;
      }
      watchWorkJobs(result.job_ids ?? []);
      message =
        `Extraction queued for ${result.queued} file${result.queued === 1 ? '' : 's'}. ` +
        'It runs in the background worker — this paper refreshes automatically when it finishes.';
    });
  }

  async function topic(): Promise<void> {
    await run(async () => {
      const result = await client.topicWork(work.id);
      watchWorkJobs([result.job_id]);
      message =
        `Topic modeling ${result.status} (job ${(result.job_id ?? '').slice(0, 8) || 'n/a'}). ` +
        'It runs in the background worker — this paper refreshes automatically when it finishes.';
    });
  }

  async function keywords(): Promise<void> {
    await run(async () => {
      const result = await client.keywordsWork(work.id);
      watchWorkJobs([result.job_id]);
      message =
        `Keyword extraction ${result.status} (job ${(result.job_id ?? '').slice(0, 8) || 'n/a'}). ` +
        'It runs in the background worker — this paper refreshes automatically when it finishes.';
    });
  }

  // Open the find-on-web modal. Reuse cached results for THIS paper (#4) so reopening doesn't
  // re-run the slow web search; only run afresh when there's no cache for this paper.
  async function findOnWeb(): Promise<void> {
    showFindModal = true;
    message = '';
    const cached = getFindWebCache(work.id);
    if (cached) {
      findResults = cached.findResults;
      degradedSources = cached.degradedSources;
      sourceProgress = cached.sourceProgress;
      selectedIds = new Set(cached.selectedIds);
      downloadStatus = cached.downloadStatus;
      searching = false;
      return;
    }
    await runFindOnWeb();
  }

  // Persist the current find-on-web state for this paper so reopening restores it.
  function saveFindWebCache(): void {
    setFindWebCache(work.id, {
      findResults,
      degradedSources,
      sourceProgress,
      selectedIds: [...selectedIds],
      downloadStatus,
    });
  }

  // Force a fresh search on this paper, discarding any cached results (Reset button, #4).
  async function resetFindOnWeb(): Promise<void> {
    clearFindWebCache(work.id);
    await runFindOnWeb();
  }

  async function runFindOnWeb(): Promise<void> {
    findResults = [];
    degradedSources = [];
    selectedIds = new Set();
    downloadStatus = {};
    sourceProgress = [];
    downloadDone = 0;
    downloadOk = 0;
    downloadTotal = 0;
    searching = true;
    message = '';
    try {
      // Live progress: stream the search and render each source as it starts/finishes.
      await client.streamFindOnWeb(work.id, onStreamEvent);
    } catch {
      // Streaming unavailable/errored → fall back to the non-streaming search so the picker works.
      try {
        const result = await client.findOnWeb(work.id);
        findResults = result.candidates;
        degradedSources = result.degraded_sources;
        sourceProgress = result.queried_sources.map((s) => ({
          source: s,
          status: result.degraded_sources.includes(s) ? 'failed' : 'done',
        }));
      } catch (error) {
        message = errorMessage(error);
      }
    } finally {
      searching = false;
      saveFindWebCache();
    }
  }

  function onStreamEvent(event: WebFindStreamEvent): void {
    if (event.type === 'source') {
      const next = sourceProgress.filter((p) => p.source !== event.source);
      next.push({ source: event.source, status: event.status, count: event.count });
      sourceProgress = next;
    } else if (event.type === 'result') {
      findResults = event.candidates;
      degradedSources = event.degraded_sources;
    }
  }

  function toggleCandidate(id: string): void {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selectedIds = next;
    saveFindWebCache();
  }

  // The URL we attempt for a candidate: a direct PDF if it has one, else its resolved (final
  // post-redirect) URL, else its landing URL. The backend fetches whatever we send, %PDF-validates
  // it, and falls back to manual_upload_needed for an HTML/login/paywall page. `||` (not `??`):
  // some sources deliver "" where they mean null, and an empty string must not win the chain
  // (the backend rejects it as "missing download url").
  function fetchUrl(c: WebCandidate): string | null {
    return c.pdf_url || c.resolved_url || c.landing_url || null;
  }

  // A candidate is download-attemptable when it has ANY fetchable URL (find-on-web item 4): a
  // direct pdf_url attaches outright; a resolved/landing URL is tried, attaching when it serves a
  // real PDF and falling back to manual download via "View" when the site needs a browser session.
  $: downloadableCandidates = findResults.filter((c) => fetchUrl(c) !== null);
  $: allDownloadableSelected =
    downloadableCandidates.length > 0 &&
    downloadableCandidates.every((c) => selectedIds.has(c.candidate_id));

  function selectAll(): void {
    selectedIds = new Set(downloadableCandidates.map((c) => c.candidate_id));
    saveFindWebCache();
  }

  function selectNone(): void {
    selectedIds = new Set();
    saveFindWebCache();
  }

  // Ask the user before fetching from an off-allowlist host (unrestricted mode). Resolves to the
  // user's choice; the prompt UI calls resolveConfirm().
  function askConfirm(item: WebFindDownloadItem, reason: string | null, title: string): Promise<boolean> {
    confirmPrompt = { item, reason, candidateTitle: title };
    return new Promise<boolean>((resolve) => {
      confirmResolve = resolve;
    });
  }

  function resolveConfirm(ok: boolean): void {
    confirmResolve?.(ok);
    confirmResolve = null;
    confirmPrompt = null;
  }

  function recordResult(r: WebFindDownloadResult): void {
    downloadStatus = { ...downloadStatus, [r.candidate_id]: r };
    saveFindWebCache();
  }

  // Per-candidate "apply metadata" state (issue 9): 'applying' | 'applied'.
  let metadataStatus: Record<string, 'applying' | 'applied'> = {};

  async function applyCandidateMetadata(cand: WebCandidate): Promise<void> {
    metadataStatus = { ...metadataStatus, [cand.candidate_id]: 'applying' };
    try {
      // Adds the fetched values as candidate assertions; they surface in the metadata-review
      // section below for the user to promote with "Use this" (no silent overwrite).
      await client.applyWebCandidateMetadata(work.id, cand);
      metadataStatus = { ...metadataStatus, [cand.candidate_id]: 'applied' };
      // Refresh the paper: loadDetail re-fetches the metadata reviews (showing the new candidates)
      // and the header reflects any auto-filled arXiv id.
      const fresh = await client.getWork(work.id);
      onUpdated(fresh);
      await loadDetail(fresh, false);
    } catch (err) {
      message = err instanceof Error ? err.message : 'Could not apply metadata';
      const { [cand.candidate_id]: _dropped, ...rest } = metadataStatus;
      metadataStatus = rest;
    }
  }

  async function downloadSelected(): Promise<void> {
    const items: WebFindDownloadItem[] = findResults
      .filter((c) => selectedIds.has(c.candidate_id) && fetchUrl(c) !== null)
      .map((c) => ({
        candidate_id: c.candidate_id,
        url: fetchUrl(c) as string,
        source: c.source,
        doi: c.doi,
        arxiv_id: c.arxiv_id,
      }));
    if (items.length === 0) return;
    downloading = true;
    message = '';
    downloadDone = 0;
    downloadOk = 0;
    downloadTotal = items.length;
    let attachedAny = false;
    try {
      // One item at a time so each row updates live and the N/M total advances per download.
      for (const item of items) {
        const title =
          findResults.find((c) => c.candidate_id === item.candidate_id)?.title ?? '(untitled)';
        let result = await downloadOne(item);
        // needs_confirmation (unrestricted mode, unknown host) → ask, then re-send with confirmed.
        if (result.status === 'needs_confirmation') {
          const ok = await askConfirm(item, result.reason, title);
          if (!ok) {
            recordResult({
              candidate_id: item.candidate_id,
              status: 'error',
              reason: 'Skipped — download not confirmed.',
              file: null,
            });
            downloadDone += 1;
            continue;
          }
          result = await downloadOne({ ...item, confirmed: true });
        }
        recordResult(result);
        if (result.status === 'attached' || result.status === 'deduped') {
          attachedAny = true;
          downloadOk += 1;
        }
        downloadDone += 1;
      }
      if (attachedAny) {
        // The download backfills the work's arxiv_id/doi and queues extraction — refetch so the
        // view shows the new identifiers, and re-arm the job watch for the extraction.
        const fresh = await client.getWork(work.id);
        onUpdated(fresh);
        await loadDetail(fresh);
      }
    } catch (error) {
      message = errorMessage(error);
    } finally {
      downloading = false;
    }
  }

  // Download a single item and unwrap its one per-item result.
  async function downloadOne(item: WebFindDownloadItem): Promise<WebFindDownloadResult> {
    const response = await client.downloadWebCandidates(work.id, [item]);
    return (
      response.results.find((r) => r.candidate_id === item.candidate_id) ??
      response.results[0] ?? {
        candidate_id: item.candidate_id,
        status: 'error' as const,
        reason: 'No result returned',
        file: null,
      }
    );
  }

  function startManualUpload(): void {
    showFindModal = false;
    document.getElementById('attach-pdf-input')?.scrollIntoView({ behavior: 'smooth' });
    (document.getElementById('attach-pdf-input') as HTMLInputElement | null)?.focus();
  }

  // Split a download-status reason into plain text + clickable URLs (UX batch 4): the backend
  // lists the URLs it tried, and the user wants to open them directly.
  function linkifyReason(reason: string): { text?: string; url?: string }[] {
    const parts: { text?: string; url?: string }[] = [];
    let last = 0;
    const re = /https?:\/\/[^\s,)]+/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(reason)) !== null) {
      if (m.index > last) parts.push({ text: reason.slice(last, m.index) });
      parts.push({ url: m[0] });
      last = m.index + m[0].length;
    }
    if (last < reason.length) parts.push({ text: reason.slice(last) });
    return parts;
  }

  async function deletePaper(): Promise<void> {
    const title = form.canonical_title || 'this paper';
    if (!window.confirm(`Delete “${title}”? Its files stay in the library; links and notes are removed.`))
      return;
    const id = work.id;
    await run(async () => {
      await client.deleteWork(id);
      onDeleted(id);
    });
  }

  async function toggleLock(fieldName: string, confirmed: boolean): Promise<void> {
    await run(async () => {
      await client.confirmMetadataField(work.id, fieldName, confirmed);
      fields = await client.listWorkMetadata(work.id);
    }, confirmed ? 'Field locked' : 'Field unlocked');
  }

  async function selectCanonical(assertionId: string): Promise<void> {
    await run(async () => {
      const updated = await client.selectMetadataAssertion(work.id, assertionId);
      onUpdated(updated);
      await loadDetail(updated);
    }, 'Canonical value updated');
  }

  async function removeAssertion(assertionId: string): Promise<void> {
    if (!window.confirm('Remove this metadata entry? This deletes the assertion for this paper.')) return;
    await run(async () => {
      const updated = await client.deleteMetadataAssertion(work.id, assertionId);
      onUpdated(updated);
      await loadDetail(updated);
    }, 'Metadata entry removed');
  }

  async function upload(): Promise<void> {
    if (!attachFile) return;
    const file = attachFile;
    await run(async () => {
      await client.uploadWorkFile(work.id, file);
      attachFile = null;
      files = await client.listWorkFiles(work.id);
      watchWorkJobs();
    }, `Attached “${file.name}”; extraction queued — the paper refreshes when it finishes`);
  }

  // --- Attach from URL / server path (Files panel): paste a PDF's web URL (the server fetches it
  //     under the find-on-web download policy) or a path on the server machine (must be inside an
  //     allowed import root). One small modal serves both modes.
  let remoteAttachMode: 'url' | 'path' | null = null;
  let remoteAttachValue = '';
  let remoteAttachBusy = false;
  let remoteAttachMsg = ''; // success line → the primary button becomes "OK"
  let remoteAttachErr = '';
  // needs_confirmation step (unrestricted policy, unknown host): the reason to show; Proceed re-sends confirmed.
  let remoteAttachConfirm: string | null = null;

  function openRemoteAttach(mode: 'url' | 'path'): void {
    remoteAttachMode = mode;
    remoteAttachValue = '';
    remoteAttachMsg = '';
    remoteAttachErr = '';
    remoteAttachConfirm = null;
  }

  function closeRemoteAttach(): void {
    remoteAttachMode = null;
  }

  async function submitRemoteAttach(confirmed = false): Promise<void> {
    const value = remoteAttachValue.trim();
    if (!value || remoteAttachBusy) return;
    remoteAttachBusy = true;
    remoteAttachErr = '';
    remoteAttachMsg = '';
    try {
      if (remoteAttachMode === 'url') {
        const response = await client.downloadWebCandidates(work.id, [
          { candidate_id: 'manual-url', url: value, source: 'manual_url', confirmed },
        ]);
        const result = response.results[0];
        if (!result) {
          remoteAttachErr = 'No result returned.';
        } else if (result.status === 'needs_confirmation') {
          remoteAttachConfirm = result.reason ?? 'The host is not on the allowed-downloads list.';
        } else if (result.status === 'attached' || result.status === 'deduped') {
          remoteAttachConfirm = null;
          remoteAttachMsg =
            result.status === 'attached'
              ? 'PDF fetched and attached; extraction queued.'
              : 'That PDF is already in the library — linked it to this paper.';
          // The download can backfill doi/arxiv and queues extraction — refresh like find-on-web.
          const fresh = await client.getWork(work.id);
          onUpdated(fresh);
          await loadDetail(fresh);
          watchWorkJobs();
        } else {
          // manual_upload_needed / error / blocked → show the backend's reason verbatim.
          remoteAttachConfirm = null;
          remoteAttachErr =
            result.reason ??
            'No PDF could be fetched from that URL (it may need a login or serve HTML only).';
        }
      } else {
        const attached = await client.attachWorkFileFromPath(work.id, value);
        remoteAttachMsg = `Attached “${attached.original_filename ?? 'file'}”; extraction queued.`;
        files = await client.listWorkFiles(work.id);
        watchWorkJobs();
      }
    } catch (error) {
      remoteAttachErr = errorMessage(error);
    } finally {
      remoteAttachBusy = false;
    }
  }

  async function reextract(file: WorkFile): Promise<void> {
    await run(async () => {
      const result = await client.extractFile(file.id);
      files = await client.listWorkFiles(work.id);
      watchWorkJobs([result.job_id]);
      message =
        `Extraction ${result.status} (job ${(result.job_id ?? '').slice(0, 8) || 'n/a'}). ` +
        'It runs in the background worker — this paper refreshes automatically when it finishes.';
    });
  }

  // #22: force an OCR pass over a (likely scanned) file, then watch the Jobs tab.
  async function forceOcr(file: WorkFile): Promise<void> {
    await run(async () => {
      const result = await client.extractFile(file.id, true);
      files = await client.listWorkFiles(work.id);
      watchWorkJobs([result.job_id]);
      message =
        `OCR extraction queued (job ${(result.job_id ?? '').slice(0, 8) || 'n/a'}). ` +
        'It runs in the background worker — this paper refreshes automatically when it finishes.';
    });
  }

  // #16: make a file the paper's main (default-to-open) file.
  async function setMainFile(file: WorkFile): Promise<void> {
    await run(async () => {
      const updated = await client.setMainFile(work.id, file.id);
      onUpdated(updated);
    }, 'Set as main file');
  }

  // #17: detach a file from the paper (confirm first); refresh the list afterwards.
  async function removeFile(file: WorkFile): Promise<void> {
    if (!window.confirm(`Remove “${file.original_filename ?? 'this file'}” from this paper? The file itself stays in the library.`))
      return;
    await run(async () => {
      await client.deleteWorkFile(work.id, file.id);
      files = await client.listWorkFiles(work.id);
      // If it was the main file the backend clears the pointer; reflect that locally.
      if (work.main_file_id === file.id) onUpdated({ ...work, main_file_id: null });
    }, 'File removed');
  }

  // #22: human-readable OCR / text-layer status per file, with a hint whether OCR would help.
  function ocrStatus(file: WorkFile): { label: string; needsOcr: boolean } | null {
    switch (file.text_layer_quality) {
      case 'ocr_added':
        return { label: 'OCR added', needsOcr: false };
      case 'good':
        return { label: 'text layer: good', needsOcr: false };
      case 'poor':
        return { label: 'scanned — needs OCR', needsOcr: true };
      case 'none':
        return { label: 'no text layer — needs OCR', needsOcr: true };
      default:
        return null; // 'unknown' / unset — nothing useful to show
    }
  }

  function fileStatusLabel(status: string): string {
    return (
      {
        extracted: 'extracted ✓',
        extract_failed: 'extraction failed',
        available: 'not extracted',
        extracted_discarded: 'extracted — PDF not stored on server',
      }[status] ?? status
    );
  }

  async function copyHash(sha: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(sha);
      message = 'Content hash copied';
    } catch {
      message = sha;
    }
  }

  // Jump to the Library and search this PDF's content hash to reveal every paper it's attached to
  // (the hash-prefix search operator in the Library, batch10 duplicate-PDF badge).
  function findDuplicatePapers(sha: string): void {
    pendingLibrarySearch.set({ query: sha, mode: 'metadata' });
    if (typeof window !== 'undefined') window.location.hash = '#library';
    onClose();
  }

  // The reader open has its OWN busy flag — NOT the shared `loading`. Gating the Read button on the
  // global `loading` meant an unrelated in-flight op (notably the background job-poll's
  // refreshOpenWork→loadDetail→run) left the button disabled, so clicks did nothing until a paper
  // switch reset it ("Read works only once"). This flag is set only by openInReader (2026-07-16).
  let readerBusy = false;
  async function openInReader(file: WorkFile, jumpReferenceId: string | null = null): Promise<void> {
    if (readerBusy) return;
    readerBusy = true;
    try {
      const blob = await client.getFileBlob(file.id);
      const previous = readerUrl;
      readerFile = file;
      readerJumpReferenceId = jumpReferenceId;
      readerUrl = URL.createObjectURL(blob); // set LAST → mounts a fresh reader ({#key readerUrl})
      if (previous) URL.revokeObjectURL(previous);
    } catch (error) {
      message = errorMessage(error);
    } finally {
      readerBusy = false;
    }
  }

  // Reference "Find in text" → open the reader and jump to the reference's first in-text mention.
  // Item 2 (2026-07-13): the References panel used to force itself open on every paper
  // view/refresh (open={references.length > 0} is reactive), burying the Citing-papers panel
  // below it. Remember the user's last toggle instead (default: open).
  const REFS_PANEL_KEY = 'paracord_refs_panel_open';
  let refsPanelOpen =
    typeof localStorage === 'undefined' ? true : localStorage.getItem(REFS_PANEL_KEY) !== '0';
  function rememberRefsPanel(event: Event): void {
    refsPanelOpen = (event.currentTarget as HTMLDetailsElement).open;
    try {
      localStorage.setItem(REFS_PANEL_KEY, refsPanelOpen ? '1' : '0');
    } catch {
      // storage unavailable — session-only memory is fine
    }
  }

  function findReferenceInText(referenceId: string): void {
    const file = readerFile ?? files[0];
    if (file) void openInReader(file, referenceId);
  }

  // Citation overlay clicked in the reader → reveal + flash the matching reference entry.
  function navigateToReference(referenceId: string): void {
    const detailsEl = document.querySelector('details.references-block') as HTMLDetailsElement | null;
    if (detailsEl) detailsEl.open = true;
    flashRefId = referenceId;
    window.setTimeout(() => {
      const el = document.getElementById(`ref-${referenceId}`);
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }, 0);
    window.setTimeout(() => {
      if (flashRefId === referenceId) flashRefId = '';
    }, 2000);
  }

  async function openInNewTab(file: WorkFile): Promise<void> {
    await run(async () => {
      const blob = await client.getFileBlob(file.id);
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener,noreferrer');
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    });
  }

  function closeReader(): void {
    // Nulling readerUrl (in clearReader) drops the {#if readerUrl} block → the reader unmounts and a
    // later open re-mounts it cleanly.
    clearReader();
  }

  async function applyTag(): Promise<void> {
    if (!applyTagId) return;
    await run(async () => {
      await client.addTagLink(applyTagId, 'work', work.id);
      appliedTags = await client.listWorkTags(work.id);
    }, 'Tag applied');
  }

  async function removeTag(tagId: string): Promise<void> {
    if (!tagId) return;
    await run(async () => {
      await client.removeTagLink(tagId, 'work', work.id);
      appliedTags = await client.listWorkTags(work.id);
    }, 'Tag removed');
  }

  // Create a tag inline and select it in the dropdown (the shared store then updates every other
  // tag dropdown live). Kept separate from run() so its own busy/error state stays local.
  async function createTagInline(): Promise<void> {
    const name = newTagName.trim();
    if (!name || tagCreateBusy) return;
    tagCreateBusy = true;
    tagCreateError = '';
    try {
      const tag = await client.createTag({ name });
      await refreshTags(client);
      // The dropdown lists `assignableTags` (loaded once on open), so a newly-created tag — always
      // global, hence assignable here — must be added to it too, or the <select> would show a blank
      // value that isn't among its options (2026-07-16 fix).
      if (assignableTags) {
        assignableTags = [...assignableTags.filter((t) => t.id !== tag.id), tag].sort((a, b) =>
          a.name.localeCompare(b.name),
        );
      }
      applyTagId = tag.id;
      newTagName = '';
      creatingTag = false;
    } catch (e) {
      tagCreateError = e instanceof Error ? e.message : 'Could not create tag';
    } finally {
      tagCreateBusy = false;
    }
  }

  // 2026-07-16: annotation add/delete happen INSIDE the reader overlay, so they must NOT write the
  // paper-view `message` (it rendered under the title, appearing only after the reader closed —
  // "Annotation deleted" leaking into the paper view). Errors propagate to the caller (PdfReader),
  // which surfaces them in its own status line; success is silent (the highlight appearing/vanishing
  // is the feedback).
  async function createAnnotation(payload: AnnotationCreate): Promise<void> {
    await client.createAnnotation(work.id, {
      ...payload,
      file_id: readerFile?.id ?? files[0]?.id ?? null,
    });
    annotations = await client.listAnnotations(work.id);
  }

  async function deleteAnnotation(annotationId: string): Promise<void> {
    await client.deleteAnnotation(work.id, annotationId);
    annotations = await client.listAnnotations(work.id);
  }

  function clearReader(): void {
    if (readerUrl) URL.revokeObjectURL(readerUrl);
    readerUrl = null;
    readerFile = null;
    readerJumpReferenceId = null;
  }

  // Keyword chip clicked → run a semantic search for it in the Library tab.
  function searchKeyword(keyword: string): void {
    pendingLibrarySearch.set({ query: keyword, mode: 'semantic' });
    window.location.hash = '#library';
  }

  onDestroy(() => {
    clearReader();
    stopJobPolling();
  });
</script>

<div class="detail">
  <div class="bar">
    <!-- tameTitle: ALL-CAPS titles render in title case here; the edit form below shows the raw
         stored title untouched. -->
    <h2>{tameTitle(form.canonical_title) || 'Untitled paper'}
      {#if work.canonical_metadata_source === 'agent_index_only'}
        <span class="stub-badge" title="Indexed by the local agent but not yet extracted — attach/extract or teleport to fill in metadata and text">not extracted</span>
      {/if}
    </h2>
    <div class="bar-actions">
      {#if onBack}
        <button type="button" class="secondary small" on:click={onBack} data-testid="detail-back"
          title="Reopen the previously viewed paper">‹ Previous</button>
      {/if}
      <button type="button" class="secondary small" on:click={exportNotes} disabled={loading}
        title="Download this paper's annotations as Markdown">Export notes</button>
      <button type="button" class="secondary small" on:click={() => (showRefGraph = true)}
        title="Show a weighted citation graph of this paper's references (year × how heavily each is cited)">Reference graph</button>
      <button type="button" class="secondary small" on:click={openMerge} disabled={loading || !canModify}
        title={canModify ? 'Merge another paper into this one (moves its files/metadata here; reversible)' : INSUFFICIENT_ROLE}>Merge…</button>
      {#if work.has_reversible_shadow}
        <button type="button" class="secondary small" on:click={unmergePaper} disabled={loading || !canModify}
          title={canModify ? 'Undo the most recent merge into this paper (restores the merged paper)' : INSUFFICIENT_ROLE}>Unmerge</button>
      {/if}
      <button type="button" class="secondary small" on:click={onClose} title="Close detail panel">✕</button>
    </div>
    {#if appliedTags.length}
      <!-- Applied tags at a glance (also editable in the Tags section below). -->
      <div class="title-tags" aria-label="Tags on this paper">
        {#each appliedTags as tag (tag.id)}
          <span class="tag-chip" style={`--tag-color:${tag.color ?? 'var(--ink-muted)'}`}
            title={tag.description ?? tag.name}>
            <span class="dot"></span>{tag.name}
          </span>
        {/each}
      </div>
    {/if}
  </div>
  {#if message}<p class="muted">{message}</p>{/if}
  {#if mainFile}
    <!-- #16: quick-read the paper's main file right below the title (+ open in a new tab). -->
    <div class="quick-read">
      <button type="button" class="secondary small" on:click={() => mainFile && openInReader(mainFile)}
        disabled={readerBusy || !mainFile.content_available}
        title={mainFile.content_available ? 'Read the main file in the in-app reader' : 'The main file’s PDF is not on the server.'}>Read</button>
      <button type="button" class="secondary small" on:click={() => mainFile && openInNewTab(mainFile)}
        disabled={loading || !mainFile.content_available}
        title={mainFile.content_available ? 'Open the main file in a new browser tab' : 'The main file’s PDF is not on the server.'}>New tab ↗</button>
      <span class="hintline">Main file: {mainFile.original_filename ?? mainFile.id.slice(0, 8)}</span>
    </div>
  {:else if files.length === 0}
    <!-- No attached files, so the quick-read buttons can't render — explain why. -->
    <div class="quick-read">
      <span class="hintline muted">No file to read yet — attach a PDF below.</span>
    </div>
  {/if}
  {#if work.keywords && work.keywords.length}
    <div class="keywords">
      {#each work.keywords as kw}<button type="button" class="kw" on:click={() => searchKeyword(kw)}
        title="Search the library for this keyword">{kw}</button>{/each}
    </div>
  {/if}
  {#if work.topics && work.topics.length}
    <div class="topics">
      <span class="topics-label">Topics</span>
      <div class="topic-chips">
        {#each work.topics as t}<button type="button" class="topic" on:click={() => searchKeyword(t)}
          title="Search the library for this topic">{t}</button>{/each}
      </div>
    </div>
  {/if}

  <!-- External citation count (Track C P1): a cached snapshot from enrichment. Shows "—" when we
       have none yet (no resolvable id, or not enriched), with the source + as-of date otherwise. -->
  <div class="citations" data-testid="citation-count">
    <span class="citations-label">Citations</span>
    {#if typeof work.citation_count === 'number'}
      <span class="citations-value">{work.citation_count.toLocaleString()}</span>
      <span class="citations-meta">
        {#if work.citation_count_source}via {work.citation_count_source}{/if}
        {#if work.citation_count_fetched_at}· as of {new Date(work.citation_count_fetched_at).toLocaleDateString()}{/if}
      </span>
    {:else}
      <span class="citations-value muted" title="No external citation count yet — enrich a paper with a DOI or arXiv id to fetch one.">—</span>
    {/if}
  </div>

  <details open>
    <summary>Details</summary>
    <form class="fields" on:submit|preventDefault={save}>
      <label>Title<input bind:value={form.canonical_title} /></label>
      <label>Authors<input bind:value={form.authors}
        placeholder="Smith, J.; Doe, A."
        title="Authors for this paper (saved as a locked manual value; enrichment won't overwrite it)" /></label>
      <div class="two">
        <label>Year<input bind:value={form.year} inputmode="numeric" /></label>
        <label>Reading status
          <select bind:value={form.reading_status} title="Set your reading status for this paper (save to apply)">
            {#each STATUSES as s}<option value={s}>{s}</option>{/each}
          </select>
        </label>
      </div>
      <label>Venue<input bind:value={form.venue} /></label>
      <div class="two">
        <label>DOI<input bind:value={form.doi} placeholder="10.xxxx/…" /></label>
        <label>arXiv id<input bind:value={form.arxiv_id} placeholder="1706.03762" /></label>
      </div>
      <label>Abstract<textarea bind:value={form.abstract} rows="4"></textarea></label>
      <label>Notes<textarea bind:value={form.notes} rows="3"
        placeholder="Your notes on this paper (saved with the paper)"></textarea></label>
      <div class="actions actions-top">
        <button type="submit" disabled={loading || !canModify}
          title={canModify ? 'Save edits to this paper’s details' : INSUFFICIENT_ROLE}>Save changes</button>
        <button type="button" class="secondary find-on-web" on:click={findOnWeb} disabled={loading || !canModify}
          title={canModify
            ? 'Search legitimate scholarly sources for this paper’s PDF and attach it'
            : INSUFFICIENT_ROLE}>Find on web</button>
      </div>
      <div class="actions">
        <button type="button" class="secondary" on:click={enrich} disabled={loading || !canModify || (!form.doi && !form.arxiv_id)}
          title={!canModify
            ? INSUFFICIENT_ROLE
            : form.doi || form.arxiv_id
              ? 'Fetch external metadata & references'
              : 'Needs a DOI or arXiv id to enrich'}>Enrich</button>
        <button type="button" class="secondary" on:click={extract} disabled={loading || !canModify || !hasReadableFile}
          title={!canModify
            ? INSUFFICIENT_ROLE
            : hasReadableFile
              ? 'Run GROBID extraction on this paper’s PDFs (text, references, citations)'
              : 'Attach a PDF to extract'}>Extract</button>
        <button type="button" class="secondary" on:click={topic} disabled={loading || !canModify}
          title={canModify
            ? 'Extract representative topic terms for this paper'
            : INSUFFICIENT_ROLE}>Topic</button>
        <button type="button" class="secondary" on:click={keywords} disabled={loading || !canModify}
          title={canModify
            ? 'Re-extract keywords for this paper from its text'
            : INSUFFICIENT_ROLE}>Keyword</button>
        <button type="button" class="secondary" on:click={() => (showPutInto = true)}
          disabled={loading || !$canManageStructure}
          title={$canManageStructure ? 'Add this paper to a shelf' : INSUFFICIENT_ROLE}>Put into…</button>
      </div>
      <div class="actions">
        <button type="button" class="secondary danger-btn" on:click={deletePaper} disabled={loading || !canModify}
          title={canModify ? 'Delete this paper (files are kept)' : INSUFFICIENT_ROLE}>Delete</button>
      </div>
      {#if canModify && !form.doi && !form.arxiv_id}<p class="hintline">Add a DOI or arXiv id to enable “Enrich”.</p>{/if}
      {#if canModify && !hasReadableFile}<p class="hintline">Attach a PDF (Files section) to enable “Extract”.</p>{/if}
      {#if !canModify}<p class="hintline">{INSUFFICIENT_ROLE} — you can only edit papers you created (or any paper as an editor or higher).</p>{/if}
    </form>
  </details>

  <details>
    <summary>Metadata review {#if fields.some((f) => f.has_conflict)}<span class="conflict">conflicts</span>{/if}</summary>
    {#if fields.length === 0}
      <p class="empty">No metadata assertions yet. Enrich or extract to gather them.</p>
    {:else}
      <div class="reviews">
        {#each fields as field (field.field_name)}
          <div class="review" class:has-conflict={field.has_conflict}>
            <strong>{field.field_name}</strong>
            <button
              type="button"
              class="lock"
              class:locked={field.confirmed}
              on:click={() => toggleLock(field.field_name, !field.confirmed)}
              disabled={loading || !canModify}
              title={!canModify
                ? INSUFFICIENT_ROLE
                : field.confirmed
                  ? 'Locked — enrichment will not overwrite this field. Click to unlock.'
                  : 'Unlocked — click to lock so enrichment cannot overwrite it.'}
            >{field.confirmed ? '🔒 locked' : '🔓 lock'}</button>
            {#if field.has_conflict && field.match_pct != null}
              <span
                class="match-pct"
                title="How alike the conflicting values are, ignoring whitespace, line-break hyphenation, and case (100% = identical apart from formatting)."
              >{field.match_pct}% match</span>
            {/if}
            {#each field.assertions as a (a.id)}
              <div class="assertion">
                <span class="src">{a.source}</span>
                <span class="val">{a.value}</span>
                {#if a.selected_as_canonical}
                  <span class="canon">canonical</span>
                {:else}
                  <button type="button" class="secondary small" on:click={() => selectCanonical(a.id)} disabled={loading || !canModify}
                    title={canModify ? 'Use this value as the canonical one' : INSUFFICIENT_ROLE}>Use this</button>
                {/if}
                <button type="button" class="secondary small danger-btn" on:click={() => removeAssertion(a.id)} disabled={loading || !canModify}
                  title={canModify ? 'Remove this metadata entry (deletes the assertion for this paper)' : INSUFFICIENT_ROLE}>Remove</button>
              </div>
            {/each}
          </div>
        {/each}
      </div>
      {#if canModify}
        <p class="hintline">“Use this” makes an entry the canonical value; “Remove” deletes a wrong entry (if you remove the canonical one, the next most recent remaining entry becomes canonical).</p>
      {:else}
        <p class="hintline">{INSUFFICIENT_ROLE} — you can only change metadata on papers you created (or any paper as an editor or higher).</p>
      {/if}
    {/if}
  </details>

  <details>
    <summary>Files ({files.length})</summary>
    <div class="attach">
      <input id="attach-pdf-input" type="file" accept=".pdf,application/pdf" on:change={(e) => (attachFile = e.currentTarget.files?.[0] ?? null)} aria-label="Attach PDF" />
      <button type="button" on:click={upload} disabled={!attachFile || loading || !canModify}
        title={!canModify ? INSUFFICIENT_ROLE : attachFile ? 'Attach this PDF to the paper' : 'Choose a PDF to attach'}>Attach PDF</button>
      <button type="button" class="secondary" on:click={() => openRemoteAttach('url')} disabled={loading || !canModify}
        title={canModify ? 'Paste a PDF link — the server fetches and attaches it' : INSUFFICIENT_ROLE}>From URL…</button>
      <button type="button" class="secondary" on:click={() => openRemoteAttach('path')} disabled={loading || !canModify}
        title={canModify ? 'Attach a PDF already on the server machine by its file path (must be inside an allowed import folder)' : INSUFFICIENT_ROLE}>From server path…</button>
    </div>
    {#if files.length === 0}
      <p class="empty">No files attached. Attach a PDF above to read and extract it.</p>
    {:else}
      <ul class="files">
        {#each files as file (file.id)}
          <li class="entry-card" class:unavailable={!file.content_available}>
            <div class="file-row">
              <div class="file-main">
                <span class="fname">{file.original_filename ?? file.id.slice(0, 8)}</span>
                <small class="muted">{formatBytes(file.size_bytes)}</small>
                <span class="fstatus fstatus-{file.status}">{fileStatusLabel(file.status)}</span>
                {#if file.id === work.main_file_id}
                  <span class="fstatus fmain" title="This is the paper's main (default-to-open) file.">main</span>
                {/if}
                {#if file.extraction_degraded}
                  <span class="fstatus fdegraded"
                    title="GROBID could not process this PDF's full text (an internal parser error), so a fallback extracted the metadata, the bibliography, and plain body text. Section structure and in-text citation contexts are unavailable for this file.">degraded extraction</span>
                {/if}
                {#if file.also_in_count}
                  <button type="button" class="fstatus fdup"
                    on:click={() => findDuplicatePapers(file.sha256)}
                    title={`This exact PDF is already attached to ${file.also_in_count} other paper(s). Click to find them in the Library (by content hash). Extraction is keyed to the file's original paper, so metadata may not populate here.`}
                    >duplicate PDF{file.also_in_count > 1 ? ` — ${file.also_in_count} others` : ''}</button>
                {/if}
                {#if ocrStatus(file)}
                  {@const ocr = ocrStatus(file)}
                  <span class="fstatus focr" class:funavail={ocr?.needsOcr} title="Text-layer / OCR status for this file.">{ocr?.label}</span>
                {/if}
                {#if !file.content_available}
                  <span class="fstatus funavail" title="The PDF bytes are not stored on the server (extracted-only or the source location was removed).">file unavailable</span>
                {/if}
              </div>
              <span class="file-actions">
                <button type="button" class="secondary small" on:click={() => openInReader(file)}
                  disabled={readerBusy || !file.content_available}
                  title={file.content_available
                    ? 'Open in the in-app reader (annotations + citation overlay)'
                    : 'PDF not available on the server — it was extracted-only or its source was removed.'}>Read</button>
                <button type="button" class="secondary small" on:click={() => openInNewTab(file)}
                  disabled={loading || !file.content_available}
                  title={file.content_available
                    ? 'Open the raw PDF in a new browser tab'
                    : 'PDF not available on the server.'}>New tab ↗</button>
                {#if file.id !== work.main_file_id}
                  <button type="button" class="secondary small" on:click={() => setMainFile(file)} disabled={loading || !canModify}
                    title={canModify ? 'Make this the paper’s main (default-to-open) file' : INSUFFICIENT_ROLE}>Set as main</button>
                {/if}
                <button type="button" class="secondary small" on:click={() => reextract(file)} disabled={loading || !canModify}
                  title={canModify ? 'Queue GROBID extraction again for this file' : INSUFFICIENT_ROLE}>Re-extract</button>
                <button type="button" class="secondary small" on:click={() => forceOcr(file)} disabled={loading || !canModify || !file.content_available}
                  title={canModify ? 'Force an OCR pass (for scanned PDFs with no/poor text layer)' : INSUFFICIENT_ROLE}>Force OCR</button>
                <button type="button" class="secondary small" on:click={() => openMove(file)} disabled={loading || !canModify}
                  title={canModify ? 'Move this file to another paper (it leaves this one)' : INSUFFICIENT_ROLE}>Move…</button>
                <button type="button" class="secondary small danger-btn" on:click={() => removeFile(file)} disabled={loading || !canModify}
                  title={canModify ? 'Remove this file from the paper (the file stays in the library)' : INSUFFICIENT_ROLE}>Remove</button>
              </span>
            </div>
            <span class="note-count" title="Notes (annotations) on this file">
              {noteCountByFile[file.id] ?? 0} note{(noteCountByFile[file.id] ?? 0) === 1 ? '' : 's'}
            </span>
            <button
              type="button"
              class="hash"
              on:click={() => copyHash(file.sha256)}
              title="Content hash (SHA-256) — matches the agent's local file id. Click to copy; searchable in the Library search box."
            >{file.sha256}</button>
          </li>
        {/each}
      </ul>
      {#if unattachedNoteCount > 0}
        <p class="hintline">+{unattachedNoteCount} note{unattachedNoteCount === 1 ? '' : 's'} not attached to a specific file (counted in this paper's {noteCount} total).</p>
      {/if}
      <p class="hintline">
        The <strong>hash</strong> is the file's content hash — the same value the agent shows as its
        local file id. Click to copy, or paste it (or a prefix) into the Library search box to find
        the owning paper.
      </p>
    {/if}
  </details>

  <details on:toggle={(e) => e.currentTarget.open && !relatedLoaded && loadRelated()}>
    <summary>Related papers</summary>
    {#if !relatedLoaded}
      <p class="hintline">Open to find papers similar to this one (by embedding neighborhood).</p>
    {:else if related.length === 0}
      <p class="empty">No related papers found (build embeddings via Admin → AI &amp; Models → Reindex).</p>
    {:else}
      <ul class="refs">
        {#each related as r (r.work.id)}
          <li class="entry-card">
            <button type="button" class="related-link" on:click={() => onSelectWork(r.work.id)}
              title="Open this related paper">
              <span class="ref-title">{r.work.canonical_title ?? 'Untitled'}</span>
              <small class="muted">{r.work.year ?? ''}</small>
            </button>
            <small class="related-reason">{r.reason}</small>
          </li>
        {/each}
      </ul>
    {/if}
  </details>

  <details on:toggle={(e) => e.currentTarget.open && !relatedLinksLoaded && loadRelatedLinks()}>
    <summary>Linked papers</summary>
    {#if !relatedLinksLoaded}
      <p class="hintline">Open to see papers you linked to this one (related / same work).</p>
    {:else if relatedLinks.length === 0}
      <p class="empty">No linked papers. Use “Link” in Duplicate review to relate two papers.</p>
    {:else}
      <ul class="refs">
        {#each relatedLinks as l (l.id)}
          <li class="entry-card">
            <button type="button" class="related-link" on:click={() => onSelectWork(l.id)}
              title="Open this linked paper">
              <span class="ref-title">{l.canonical_title ?? 'Untitled'}</span>
              <small class="muted">{l.year ?? ''}</small>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </details>

  <details>
    <summary>Tags</summary>
    <div class="applied-tags" aria-label="Applied tags">
      {#if appliedTags.length === 0}
        <span class="muted">No tags applied yet.</span>
      {:else}
        {#each appliedTags as tag (tag.id)}
          <span class="tag-chip" style={`--tag-color:${tag.color ?? 'var(--ink-muted)'}`}>
            <span class="dot"></span>
            {tag.name}
            <button type="button" class="chip-remove" on:click={() => removeTag(tag.id)}
              disabled={loading || !canModify} aria-label={`Remove tag ${tag.name}`}
              title={canModify ? `Remove “${tag.name}” from this paper` : INSUFFICIENT_ROLE}>×</button>
          </span>
        {/each}
      {/if}
    </div>
    <div class="tags">
      <select bind:value={applyTagId} aria-label="Tag" title="Tags offered for this paper's shelves/racks (plus global tags)">
        <option value="">Choose a tag…</option>
        {#each tagOptions as tag (tag.id)}<option value={tag.id}>{tag.name}</option>{/each}
      </select>
      <button type="button" class="secondary" on:click={applyTag} disabled={!applyTagId || loading || !canModify}
        title={!canModify ? INSUFFICIENT_ROLE : applyTagId ? 'Apply the chosen tag to this paper' : 'Choose a tag first'}>Apply</button>
    </div>
    {#if $canEdit}
      {#if creatingTag}
        <form class="tag-create" on:submit|preventDefault={createTagInline}>
          <input type="text" bind:value={newTagName} placeholder="New tag name" aria-label="New tag name"
            disabled={tagCreateBusy} />
          <button type="submit" class="secondary" disabled={!newTagName.trim() || tagCreateBusy}>
            {tagCreateBusy ? 'Creating…' : 'Create'}</button>
          <button type="button" class="linkbtn" on:click={() => { creatingTag = false; tagCreateError = ''; newTagName = ''; }}
            disabled={tagCreateBusy}>Cancel</button>
        </form>
        {#if tagCreateError}<p class="hintline warn">{tagCreateError}</p>{/if}
      {:else}
        <button type="button" class="linkbtn" on:click={() => (creatingTag = true)}>+ New tag</button>
      {/if}
    {:else}
      <p class="hintline">Create tags on the Tags tab, then apply them here.</p>
    {/if}
  </details>

  <details on:toggle={(e) => e.currentTarget.open && !locationsLoaded && loadLocations()}>
    <summary>Organization — where is this?</summary>
    {#if !locationsLoaded}
      <p class="hintline">Open to see which shelves (and racks) this paper is on.</p>
    {:else if locations.length === 0}
      <p class="empty">This paper isn’t in any shelf you can see.</p>
    {:else}
      <ul class="locations">
        {#each locations as shelf (shelf.id)}
          {@const rowPrefix = (shelf.rows ?? []).map((r) => r.name).join(', ')}
          {#if shelf.racks.length}
            {#each shelf.racks as rack (rack.id)}
              <li class="entry-card location-row">
                <span class="loc-path">{#if rowPrefix}<span class="loc-row">{rowPrefix}</span> › {/if}<span class="loc-rack">{rack.name}</span> › <span class="loc-shelf">{shelf.name}</span></span>
                <button type="button" class="secondary small" on:click={() => removeFromShelf(shelf.id)}
                  disabled={loading || !shelf.can_modify}
                  title={shelf.can_modify ? 'Remove this paper from the shelf' : INSUFFICIENT_ROLE}>Remove</button>
              </li>
            {/each}
          {:else}
            <li class="entry-card location-row">
              <span class="loc-path">{#if rowPrefix}<span class="loc-row">{rowPrefix}</span> › {/if}<span class="loc-shelf">{shelf.name}</span></span>
              <button type="button" class="secondary small" on:click={() => removeFromShelf(shelf.id)}
                disabled={loading || !shelf.can_modify}
                title={shelf.can_modify ? 'Remove this paper from the shelf' : INSUFFICIENT_ROLE}>Remove</button>
            </li>
          {/if}
        {/each}
      </ul>
    {/if}
    {#if !$canManageStructure}
      <p class="hintline">Only librarians and admins can change a paper’s shelves.</p>
    {/if}
  </details>

  <details class="references-block" open={refsPanelOpen} on:toggle={rememberRefsPanel}>
    <summary>References ({references.length})
      {#if refLikelyCount}<span class="sum-badge sum-badge-warn"
          title="Fuzzy candidates awaiting your confirm/reject — expand to review them"
          >{refLikelyCount} likely match{refLikelyCount === 1 ? '' : 'es'}</span>{/if}
      {#if refInLibraryCount}<span class="sum-badge sum-badge-ok"
          title="References already linked to papers in your library">{refInLibraryCount} in library</span>{/if}
      {#if refExternalCount}<span class="sum-badge"
          title="References not (yet) in your library">{refExternalCount} external</span>{/if}
    </summary>
    {#if references.length === 0}
      <p class="empty">
        No references extracted yet. They appear after GROBID extraction runs on an attached PDF
        (watch the Jobs tab); a manually-created paper with no PDF won’t have any.
      </p>
    {:else}
      <div class="ref-actions refs-toolbar">
        <button type="button" class="secondary small" on:click={rescanReferences}
          disabled={loading || !canModify}
          title={canModify
            ? 'Re-check these references against the library for likely matches'
            : INSUFFICIENT_ROLE}>Rescan matches</button>
      </div>
      <ol class="refs">
        {#each references as ref (ref.id)}
          <li class="entry-card" id={`ref-${ref.id}`} class:flash-ref={flashRefId === ref.id}>
            <div class="ref-head">
              {#if ref.shorthand}<span class="ref-marker" title="In-text citation marker for this reference">{ref.shorthand}</span>{/if}
              <span class="ref-title">{ref.title ?? ref.raw_citation ?? 'Untitled reference'}</span>
            </div>
            {#if ref.authors && ref.authors.length}
              <small class="muted ref-authors" title="Reference authors">{ref.authors.join(', ')}</small>
            {/if}
            <small class="muted">
              {ref.year ?? ''}{ref.doi ? ` · doi:${ref.doi}` : ''}{ref.arxiv_id
                ? ` · arXiv:${ref.arxiv_id}`
                : ''}
              {#if ref.resolved_work_id}<button
                  type="button"
                  class="ref-badge ref-badge-link"
                  on:click={() => onSelectWork(ref.resolved_work_id)}
                  title={ref.resolution_status === 'confirmed_match'
                    ? 'Confirmed match — open this paper in the library'
                    : 'Open this paper in the library'}>in library{#if ref.resolution_status === 'confirmed_match'} ✓{/if}</button>{/if}
              {#if !ref.resolved_work_id && ref.resolution_status === 'likely_match' && ref.suggested_work_id}<button
                  type="button"
                  class="ref-badge ref-badge-likely"
                  on:click={() => onSelectWork(ref.suggested_work_id)}
                  title="Likely already in the library — click to view the candidate">likely match{#if ref.match_score} · {Math.round(ref.match_score)}%{/if}</button>{/if}
            </small>
            <div class="ref-actions">
              {#if locatableReferenceIds.has(ref.id)}
                <button type="button" class="secondary small" on:click={() => findReferenceInText(ref.id)}
                  title="Open the reader and jump to where this reference is cited">Find in text</button>
              {/if}
              {#if ref.resolution_status === 'likely_match' && ref.suggested_work_id}
                <button type="button" class="small" on:click={() => actOnReference(ref.id, 'link')}
                  disabled={loading || !canModify}
                  title={canModify ? 'Confirm this is the same paper (links it permanently)' : INSUFFICIENT_ROLE}
                  >{refActionPendingId === ref.id ? 'Confirming…' : 'Confirm match'}</button>
                <button type="button" class="secondary small" on:click={() => actOnReference(ref.id, 'reject')}
                  disabled={loading || !canModify}
                  title={canModify ? 'This is not the same paper' : INSUFFICIENT_ROLE}>Not a match</button>
              {/if}
              {#if !ref.resolved_work_id}
                <button type="button" class="secondary small"
                  on:click={() => findAndImport(ref.title ?? ref.raw_citation, ref.year, ref.doi)}
                  disabled={loading}
                  title="Open the Import tab with this citation prefilled — search for candidates and review before importing"
                  >Find &amp; Import</button>
                <button type="button" class="secondary small" on:click={() => importReference(ref.id)}
                  disabled={loading || !canModify}
                  title={!canModify
                    ? INSUFFICIENT_ROLE
                    : ref.doi || ref.arxiv_id
                      ? 'Create the paper from this reference now (identifier present — metadata can be enriched)'
                      : 'Create a paper record from the reference’s title/year/authors (no identifier available)'}
                  >{ref.doi || ref.arxiv_id ? 'Direct import' : 'Create paper'}</button>
              {/if}
            </div>
            {#if refActionError?.id === ref.id}
              <p class="ref-action-error" role="alert">⚠ {refActionError.text}</p>
            {/if}
          </li>
        {/each}
      </ol>
    {/if}
  </details>

  <details class="citing-block" on:toggle={(e) => e.currentTarget.open && loadCiting()}>
    <summary>Citing papers{#if citing}{' '}({citing.items.length}{#if citing.citation_count && citing.citation_count > citing.items.length} of {citing.citation_count}{/if}){/if}
      {#if citingInLibraryCount}<span class="sum-badge sum-badge-ok"
          title="Citing papers already in your library">{citingInLibraryCount} in library</span>{/if}
      {#if citingExternalCount}<span class="sum-badge"
          title="External citing papers not (yet) in your library">{citingExternalCount} external</span>{/if}
    </summary>
    <p class="muted small citing-lead">
      External papers that cite this one, fetched from OpenAlex (falling back to Semantic Scholar).
      {#if citing?.fetched_at}
        Showing {citing.items.length}{#if citing.citation_count && citing.citation_count > citing.items.length} of {citing.citation_count}{/if}
        {#if citing.source}· via {citing.source}{/if} · as of {new Date(citing.fetched_at).toLocaleDateString()}
      {/if}
    </p>
    <div class="ref-actions">
      <button type="button" class="secondary small" on:click={fetchCiting}
        disabled={loading || !canModify || (!work.doi && !work.arxiv_id)}
        title={!canModify
          ? INSUFFICIENT_ROLE
          : work.doi || work.arxiv_id
            ? 'Fetch (or refresh) the papers that cite this one'
            : 'Needs a DOI or arXiv id to look up citing papers'}
        >{citing && citing.items.length ? 'Refresh' : 'Fetch citing papers'}</button>
    </div>
    {#if citing && citing.items.length}
      <ol class="refs">
        {#each citing.items as c (c.id)}
          <li class="entry-card">
            <div class="ref-head"><span class="ref-title">{c.title ?? 'Untitled'}</span></div>
            <small class="muted">
              {c.authors ?? ''}{c.year ? ` · ${c.year}` : ''}{c.venue ? ` · ${c.venue}` : ''}
              {#if c.doi}<a class="ref-badge ref-badge-link" href={`https://doi.org/${c.doi}`}
                  target="_blank" rel="noopener noreferrer" title="Open at doi.org">doi:{c.doi}</a>{/if}
              {#if c.resolved_work_id}<button type="button" class="ref-badge ref-badge-link"
                  on:click={() => onSelectWork(c.resolved_work_id)}
                  title="This citing paper is already in the library — open it">in library</button>{/if}
            </small>
            {#if !c.resolved_work_id}
              <div class="ref-actions">
                <button type="button" class="secondary small"
                  on:click={() => findAndImport(c.title, c.year, c.doi)}
                  disabled={loading}
                  title="Open the Import tab with this citation prefilled — search for candidates and review before importing"
                  >Find &amp; Import</button>
                <button type="button" class="secondary small" on:click={() => importCiter(c.id)}
                  disabled={loading || !canModify}
                  title={!canModify
                    ? INSUFFICIENT_ROLE
                    : c.doi || c.arxiv_id
                      ? 'Create the paper from its identifier now (metadata is enriched in the background)'
                      : 'Create a paper record from the cached title/year/venue/authors (no identifier available)'}
                  >{c.doi || c.arxiv_id ? 'Direct import' : 'Create paper'}</button>
              </div>
            {/if}
          </li>
        {/each}
      </ol>
    {:else if citingLoaded && !loading}
      <p class="empty">
        {#if !work.doi && !work.arxiv_id}Add a DOI or arXiv id to look up citing papers.
        {:else}No citing papers fetched yet — use “Fetch citing papers”.{/if}
      </p>
    {/if}
  </details>

  {#if contexts.length}
    <details>
      <summary>In-text citations ({contexts.length})</summary>
      <p class="hintline">Open this paper with “Read”, then click a citation highlight to reveal its reference (or use a reference’s “Find in text”).</p>
      <ul class="ctx">
        {#each contexts as c (c.id)}
          <li class="entry-card">
            <strong class="ref-marker">{c.marker_text ?? '•'}</strong>
            <span>{c.context_sentence ?? c.reference_title ?? c.reference_raw_citation ?? ''}</span>
          </li>
        {/each}
      </ul>
    </details>
  {/if}

  <details open>
    <summary>Summaries</summary>
    <div class="math-toggle">
      <button type="button" class="linkish small" class:active={mathMode === 'fancy'}
        on:click={() => (mathMode = mathMode === 'fancy' ? 'plain' : 'fancy')}
        title="Toggle LaTeX math rendering (switch to plain if equations look garbled)"
        >{mathMode === 'fancy' ? '𝑓𝑥 fancy' : 'plain text'}</button>
    </div>
    <div class="summary-block">
      <div class="head">
        <h4>Short summary</h4>
        <button type="button" class="secondary small" on:click={() => summariseDetail('short')}
          disabled={loading || !canModify || summarisingDetail !== null}
          title={canModify ? 'One-paragraph AI summary (configured model, else extractive)' : INSUFFICIENT_ROLE}
          >{summarisingDetail === 'short' ? 'Summarizing…' : shortSummary ? 'Regenerate' : 'Summarize'}</button>
      </div>
      {#if shortSummary}
        {#if shortSummary.fallback}
          <p class="degraded-hint" role="status">Summarized with the extractive fallback (LLM unavailable).</p>
        {/if}
        <p class="muted small">Generated by {shortSummary.model_name ?? shortSummary.provider_used}{#if shortSummary.created_at} · {new Date(shortSummary.created_at).toLocaleDateString()}{/if}</p>
        {#if mathMode === 'fancy'}
          <p class="summary-text">{@html renderSummaryMath(shortSummary.text)}</p>
        {:else}
          <p class="summary-text">{shortSummary.text}</p>
        {/if}
      {:else}
        <p class="empty">No short summary yet.</p>
      {/if}
    </div>
    <div class="summary-block">
      <div class="head">
        <h4>Detailed summary</h4>
        <div class="effort-radios" role="radiogroup" aria-label="Detailed summary effort">
          {#each DETAIL_EFFORTS as e}
            <label class="effort" class:active={detailedEffort === e.value} title={e.hint}>
              <input type="radio" name="detail-effort" value={e.value} bind:group={detailedEffort} />
              {e.label}
            </label>
          {/each}
        </div>
        <button type="button" class="secondary small" on:click={() => summariseDetail(detailedEffort)}
          disabled={loading || !canModify || summarisingDetail !== null}
          title={canModify ? 'Detailed summary at the selected effort (runs as a background job)' : INSUFFICIENT_ROLE}
          >{summarisingDetail === detailedEffort ? 'Summarizing…' : detailedSummary ? 'Regenerate' : 'Generate detailed'}</button>
        {#if detailedHistory.length}
          <button type="button" class="linkish small" on:click={() => (historyOpen = !historyOpen)}
            title="View previously generated detailed summaries (other efforts / models)">History ({detailedHistory.length})</button>
        {/if}
      </div>
      {#if historyOpen}
        <ul class="summary-history">
          {#each detailedHistory as h}
            <li>
              <button type="button" class="linkish" on:click={() => (historyView = historyView === h ? null : h)}>
                {h.summary_type.replace(/^.*_detailed_/, '')} · {h.model_name ?? h.provider_used ?? 'local'}{#if h.created_at} · {new Date(h.created_at).toLocaleDateString()}{/if}
              </button>
              {#if historyView === h}
                <div class="history-text">{#each h.text.split(/\n\n+/) as para}{#if mathMode === 'fancy'}<p class="summary-text">{@html renderSummaryMath(para)}</p>{:else}<p class="summary-text">{para}</p>{/if}{/each}</div>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
      {#if detailedSummary}
        {#if detailedSummary.fallback}
          <p class="degraded-hint" role="status">Summarized with the extractive fallback (LLM unavailable).</p>
        {/if}
        <p class="muted small">Generated by {detailedSummary.model_name ?? detailedSummary.provider_used}{#if detailedSummary.created_at} · {new Date(detailedSummary.created_at).toLocaleDateString()}{/if}</p>
        {#each detailedSummary.text.split(/\n\n+/) as para}
          {#if mathMode === 'fancy'}<p class="summary-text">{@html renderSummaryMath(para)}</p>{:else}<p class="summary-text">{para}</p>{/if}
        {/each}
      {:else}
        <p class="empty">No {DETAIL_EFFORTS.find((e) => e.value === detailedEffort)?.label.toLowerCase()} detailed summary yet.</p>
      {/if}
    </div>
  </details>
</div>

{#if showRefGraph}
  <ReferenceGraphModal {client} workId={work.id} onClose={() => (showRefGraph = false)} />
{/if}

{#if moveFile}
  <Modal title="Move file to another paper" onClose={() => (moveFile = null)}>
    <p class="modal-lead">
      Move <strong>{moveFile.original_filename ?? 'this file'}</strong> to another paper. It will be
      detached from this one — pick the destination:
    </p>
    <WorkPicker {client} excludeId={work.id} onSelect={doMove} autofocusInput
      initialQuery={work.canonical_title ?? ''}
      placeholder="Search the destination paper by title, DOI, or identifier…" />
  </Modal>
{/if}

{#if remoteAttachMode}
  <Modal
    title={remoteAttachMode === 'url' ? 'Attach a PDF from a URL' : 'Attach a PDF from a server path'}
    onClose={closeRemoteAttach}>
    <p class="modal-lead">
      {#if remoteAttachMode === 'url'}
        Paste the PDF's web address (e.g. the link behind a site's “Download PDF” button). The
        server fetches it under the download policy and attaches it to this paper.
      {:else}
        Enter the file's absolute path <strong>on the server machine</strong>. Only paths inside an
        allowed import folder are accepted (owner: <em>Admin → Server import folders</em>).
      {/if}
    </p>
    <form class="remote-attach" on:submit|preventDefault={() => submitRemoteAttach(false)}>
      <input
        bind:value={remoteAttachValue}
        placeholder={remoteAttachMode === 'url'
          ? 'https://example.org/paper.pdf'
          : '/shared/papers/paper.pdf'}
        aria-label={remoteAttachMode === 'url' ? 'PDF URL' : 'Server file path'}
        disabled={remoteAttachBusy || !!remoteAttachMsg}
      />
      {#if remoteAttachConfirm}
        <p class="ra-warn" role="alert">⚠ {remoteAttachConfirm}</p>
      {/if}
      {#if remoteAttachMsg}<p class="ra-ok" role="status">{remoteAttachMsg}</p>{/if}
      {#if remoteAttachErr}
        <p class="ra-err" role="alert">
          {#each linkifyReason(remoteAttachErr) as part}
            {#if part.url}<a href={part.url} target="_blank" rel="noreferrer noopener">{part.url}</a>{:else}{part.text}{/if}
          {/each}
        </p>
      {/if}
      <div class="ra-actions">
        {#if remoteAttachMsg}
          <button type="button" on:click={closeRemoteAttach}>OK</button>
        {:else if remoteAttachConfirm}
          <button type="button" on:click={() => submitRemoteAttach(true)} disabled={remoteAttachBusy}
            title="The host is not on the allowed list — download from it anyway">Download anyway</button>
          <button type="button" class="secondary" on:click={closeRemoteAttach} disabled={remoteAttachBusy}>Cancel</button>
        {:else}
          <button type="submit" disabled={remoteAttachBusy || !remoteAttachValue.trim()}
            title={remoteAttachValue.trim()
              ? remoteAttachMode === 'url' ? 'Fetch the PDF and attach it' : 'Read the file and attach it'
              : remoteAttachMode === 'url' ? 'Paste a URL first' : 'Enter a path first'}>
            {remoteAttachBusy ? 'Working…' : 'Proceed'}</button>
          <button type="button" class="secondary" on:click={closeRemoteAttach} disabled={remoteAttachBusy}>Cancel</button>
        {/if}
      </div>
    </form>
  </Modal>
{/if}

{#if mergeOpen}
  <Modal title="Merge another paper into this one" onClose={() => (mergeOpen = false)}>
    {#if !mergeSource}
      <p class="modal-lead">
        Pick the paper to fold into <strong>{work.canonical_title || 'this paper'}</strong>. Its
        files, tags, shelves, references and metadata move here; it becomes a hidden shadow you can
        Unmerge later.
      </p>
      <WorkPicker {client} excludeId={work.id} onSelect={selectMergeSource} autofocusInput
        placeholder="Search the paper to merge in…" />
    {:else}
      <p class="modal-lead">
        Merge <strong>{mergeSource.canonical_title || 'the selected paper'}</strong> into
        <strong>{work.canonical_title || 'this paper'}</strong>?
      </p>
      {#if mergePreview}
        <ul class="merge-preview">
          <li>{mergePreview.file_count} file{mergePreview.file_count === 1 ? '' : 's'} moved here</li>
          {#if mergePreview.fill_fields.length > 0}
            <li>Fills empty fields: {mergePreview.fill_fields.join(', ')}</li>
          {/if}
          {#if mergePreview.conflict_fields.length > 0}
            <li class="warn">Conflicting fields recorded (not overwritten): {mergePreview.conflict_fields.join(', ')}</li>
          {/if}
          {#if mergePreview.incoming_reference_count > 0}
            <li>{mergePreview.incoming_reference_count} incoming reference(s) re-pointed here</li>
          {/if}
          {#if mergePreview.will_flatten}
            <li class="warn">This paper already has a reversible merge; that one becomes permanent.</li>
          {/if}
        </ul>
      {/if}
      <div class="modal-actions">
        <button type="button" class="secondary small" on:click={() => { mergeSource = null; mergePreview = null; }}
          disabled={loading} title="Pick a different paper">Back</button>
        <button type="button" class="small" on:click={doMerge} disabled={loading}
          title="Merge the selected paper into this one">Merge</button>
      </div>
    {/if}
  </Modal>
{/if}

{#if showFindModal}
  <Modal title="Find on web" wide onClose={() => (showFindModal = false)}>
    <div class="findwrap">
      <!-- Searched-paper header so the user can validate candidates against the source paper. -->
      <div class="searched-paper">
        <span class="searched-label">Searching for this paper</span>
        <strong class="searched-title">{form.canonical_title || 'Untitled paper'}</strong>
        {#if searchedAuthors}<div class="searched-authors">{searchedAuthors}</div>{/if}
        <div class="searched-meta">
          {#if form.year}<span>{form.year}</span>{/if}
          {#if form.venue}<span>· {form.venue}</span>{/if}
          {#if form.doi}<span>· doi:{form.doi}</span>{/if}
          {#if form.arxiv_id}<span>· arXiv:{form.arxiv_id}</span>{/if}
        </div>
      </div>

      <p class="hintline">
        Candidate matches from legitimate scholarly sources (Crossref, OpenAlex, arXiv, Unpaywall,
        Semantic Scholar). Select the papers to download and attach; failed downloads fall back to
        manual upload.
      </p>

      <!-- Results are cached per paper (#4): reopening this modal shows them without re-searching.
           Reset discards the cache and runs a fresh search. -->
      <div class="findweb-toolbar">
        <span class="hintline">Results are kept for this paper until you reset.</span>
        <button type="button" class="secondary small" on:click={resetFindOnWeb} disabled={searching}
          title="Discard the cached results and search the web again">Reset</button>
      </div>

      <!-- Live per-source search progress. -->
      {#if sourceProgress.length}
        <ul class="sources" aria-label="Search progress by source">
          {#each sourceProgress as p (p.source)}
            <li class="source-row source-{p.status}">
              <span class="source-name">{p.source}</span>
              {#if p.status === 'querying'}
                <span class="spinner" aria-hidden="true"></span><span class="source-state">querying…</span>
              {:else if p.status === 'done'}
                <span class="source-state ok">✓ {p.count ?? 0} match{(p.count ?? 0) === 1 ? '' : 'es'}</span>
              {:else}
                <span class="source-state err">✗ failed</span>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}

      {#if degradedSources.length}
        <p class="hintline warn">Some sources were unavailable and skipped: {degradedSources.join(', ')}.</p>
      {/if}
      {#if searching && findResults.length === 0}
        <p class="empty">Searching…</p>
      {:else if findResults.length === 0}
        <p class="empty">No candidate matches found. Try refining the title/year, or attach a PDF manually.</p>
      {:else}
        <!-- Sticky download bar pinned above the scrolling candidate list. -->
        <div class="download-bar">
          <div class="bar-left">
            <button type="button" class="secondary small" on:click={selectAll}
              disabled={allDownloadableSelected || downloadableCandidates.length === 0}
              title={downloadableCandidates.length === 0
                ? 'No downloadable candidates'
                : allDownloadableSelected
                  ? 'All downloadable candidates are selected'
                  : 'Select all downloadable candidates'}>Select all</button>
            <button type="button" class="secondary small" on:click={selectNone}
              disabled={selectedIds.size === 0}
              title={selectedIds.size === 0 ? 'Nothing selected' : 'Clear selection'}>Select none</button>
            <span class="sel-count">{selectedIds.size} selected</span>
          </div>
          <div class="bar-right">
            {#if downloading || downloadTotal > 0}
              <span
                class="dl-progress"
                class:has-failures={downloadDone > downloadOk}
                title="PDFs successfully attached out of the batch">
                {downloadOk}/{downloadTotal} downloaded{#if downloadDone > downloadOk} ({downloadDone - downloadOk} failed){/if}
              </span>
            {/if}
            <button type="button" on:click={downloadSelected} disabled={downloading || selectedIds.size === 0}
              title={selectedIds.size === 0 ? 'Select at least one candidate' : 'Download selected PDFs and attach them'}>
              {downloading ? 'Downloading…' : `Download selected (${selectedIds.size})`}
            </button>
          </div>
        </div>
        <ul class="candidates">
          {#each findResults as cand (cand.candidate_id)}
            <li class="candidate">
              <label class="pick">
                <input
                  type="checkbox"
                  checked={selectedIds.has(cand.candidate_id)}
                  on:change={() => toggleCandidate(cand.candidate_id)}
                  disabled={fetchUrl(cand) === null}
                  title={cand.pdf_url
                    ? 'Select this candidate to download its PDF and attach'
                    : fetchUrl(cand) !== null
                      ? 'Try to download from the publisher/landing page and attach; if the site needs a browser session it falls back to manual download via “View”'
                      : 'No link to fetch — attach the PDF manually'}
                />
              </label>
              <div class="cand-main">
                <div class="cand-head">
                  {#each cand.sources as s}<span class="badge src">{s}</span>{/each}
                  {#if cand.is_oa}<span class="badge oa">OA</span>{/if}
                  <span class="badge score" title="Match score (0–1)">{cand.score.toFixed(2)}</span>
                </div>
                <strong class="cand-title">{cand.title ?? '(untitled)'}</strong>
                <div class="cand-meta">
                  {#if cand.authors.length}<span>{cand.authors.slice(0, 4).join(', ')}{cand.authors.length > 4 ? ' et al.' : ''}</span>{/if}
                  {#if cand.year}<span>· {cand.year}</span>{/if}
                  {#if cand.platform}<span class="badge platform" title="Where this link leads">via {cand.platform}</span>{/if}
                  {#if cand.resolved_url || cand.landing_url}
                    {@const viewUrl = cand.resolved_url ?? (cand.landing_url as string)}
                    <a href={viewUrl} target="_blank" rel="noopener noreferrer" title={`Open ${viewUrl} in a new tab`}>View ↗</a>
                  {/if}
                  {#if canModify}
                    <button
                      type="button"
                      class="link"
                      disabled={metadataStatus[cand.candidate_id] === 'applying'}
                      on:click={() => applyCandidateMetadata(cand)}
                      title="Add this result's title/authors/year/DOI as metadata candidates you can then choose from below">
                      {metadataStatus[cand.candidate_id] === 'applying'
                        ? 'Applying…'
                        : metadataStatus[cand.candidate_id] === 'applied'
                          ? '✓ Metadata added'
                          : 'Use metadata'}
                    </button>
                  {/if}
                </div>
                {#if !cand.pdf_url && (cand.resolved_url || cand.landing_url)}
                  <span class="cand-status warn">No direct PDF link — we’ll try the publisher/landing page; if it needs a browser session, use “View” to download manually, then attach.</span>
                {:else if !(cand.pdf_url || cand.resolved_url || cand.landing_url)}
                  <span class="cand-status warn">No link to open — attach the PDF manually.</span>
                {/if}
                {#if downloadStatus[cand.candidate_id]}
                  {@const r = downloadStatus[cand.candidate_id]}
                  {#if r.status === 'attached'}
                    <span class="cand-status ok">✓ Attached</span>
                  {:else if r.status === 'deduped'}
                    <span class="cand-status ok">✓ Already attached (deduplicated)</span>
                  {:else if r.status === 'manual_upload_needed'}
                    <span class="cand-status warn">
                      Could not download automatically —
                      {#each linkifyReason(r.reason ?? '(login/paywall)') as part}{#if part.url}<a href={part.url} target="_blank" rel="noopener noreferrer">{part.url}</a>{:else}{part.text}{/if}{/each}.
                      <button type="button" class="link" on:click={startManualUpload}>Upload the PDF manually</button>.
                    </span>
                  {:else if r.status === 'blocked'}
                    <span class="cand-status err">Blocked:
                      {#each linkifyReason(r.reason ?? 'this host is not allowed for downloads') as part}{#if part.url}<a href={part.url} target="_blank" rel="noopener noreferrer">{part.url}</a>{:else}{part.text}{/if}{/each}</span>
                  {:else}
                    <span class="cand-status err">Error: {r.reason ?? 'download failed'}</span>
                  {/if}
                {/if}
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </Modal>
{/if}

{#if confirmPrompt}
  <Modal title="Confirm download from an unverified host" onClose={() => resolveConfirm(false)}>
    <div class="confirmwrap">
      <p>
        The download for <strong>{confirmPrompt.candidateTitle}</strong> resolves to a host that is
        <strong>not on the allow-list and is not a known publisher</strong>:
      </p>
      <p class="confirm-url"><code>{confirmPrompt.item.url}</code></p>
      {#if confirmPrompt.reason}<p class="hintline warn">{confirmPrompt.reason}</p>{/if}
      <p class="hintline">
        Only continue if you trust this source. The file will be fetched and attached to this paper.
      </p>
      <div class="confirm-actions">
        <button type="button" class="secondary" on:click={() => resolveConfirm(false)}
          title="Skip this download">Cancel</button>
        <button type="button" on:click={() => resolveConfirm(true)}
          title="Download from this host anyway">Download anyway</button>
      </div>
    </div>
  </Modal>
{/if}

{#if showPutInto}
  <Modal title="Put into a shelf" onClose={() => (showPutInto = false)}>
    <div class="putinto">
      <ShelfPicker {client} bind:value={putIntoShelfId} modifiableOnly excludeDefault autofocus />
      <div class="putinto-actions">
        <button type="button" class="secondary" on:click={() => (showPutInto = false)}
          title="Close without adding">Cancel</button>
        <button type="button" on:click={addToShelf} disabled={loading || !putIntoShelfId}
          title={putIntoShelfId ? 'Add this paper to the chosen shelf' : 'Choose a shelf first'}>Add</button>
      </div>
    </div>
  </Modal>
{/if}

{#if readerUrl}
  <!-- {#key readerUrl}: each open uses a fresh object URL, so this forces a brand-new Modal +
       PdfReader instance every time — no stale internal state (a corrupted zen portal, a
       hot-reload artifact, a half-torn-down pdf.js) can ever survive into a re-open (2026-07-16). -->
  {#key readerUrl}
    <Modal title={readerFile?.original_filename ?? 'PDF reader'} wide onClose={closeReader}>
      <PdfReader
        fileId={readerFile?.id ?? ''}
        fileName={readerFile?.original_filename ?? 'PDF'}
        fileUrl={readerUrl}
        canAnnotate={canModify}
        {contexts}
        {annotations}
        onCreateAnnotation={createAnnotation}
        onDeleteAnnotation={deleteAnnotation}
        onNavigateToReference={navigateToReference}
        initialJumpReferenceId={readerJumpReferenceId}
        onFetchText={readerFile ? () => client.getFileText(readerFile.id) : null}
      />
    </Modal>
  {/key}
{/if}

<style>
  .detail {
    display: grid;
    gap: 0.6rem;
  }

  .summary-block {
    margin-bottom: 0.8rem;
  }

  .math-toggle {
    display: flex;
    justify-content: flex-end;
    margin: 0.1rem 0 0.3rem;
  }
  .math-toggle .active {
    font-weight: 700;
  }

  .summary-block .head {
    align-items: center;
    display: flex;
    gap: 0.6rem;
    justify-content: space-between;
    flex-wrap: wrap;
  }

  /* 2026-07-16 detailed-summary effort selector + history */
  .effort-radios {
    display: inline-flex;
    gap: 0.15rem;
    border: 1px solid var(--border);
    border-radius: 0.375rem;
    overflow: hidden;
  }
  .effort-radios .effort {
    font-size: 0.78rem;
    padding: 0.12rem 0.45rem;
    cursor: pointer;
    user-select: none;
  }
  .effort-radios .effort.active {
    background: var(--accent-soft, rgba(120, 120, 255, 0.18));
    font-weight: 600;
  }
  .effort-radios .effort input {
    display: none;
  }
  .summary-history {
    list-style: none;
    margin: 0.3rem 0;
    padding: 0.3rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 0.375rem;
    max-height: 16rem;
    overflow-y: auto;
  }
  .summary-history li {
    margin: 0.15rem 0;
  }
  .history-text {
    margin: 0.2rem 0 0.4rem 0.6rem;
    padding-left: 0.5rem;
    border-left: 2px solid var(--border);
  }

  .summary-block h4 {
    font-size: 0.95rem;
    margin: 0.3rem 0;
  }

  .summary-text {
    margin: 0.3rem 0;
    white-space: pre-wrap;
  }

  .degraded-hint {
    margin: 0.25rem 0;
    padding: 0.4rem 0.6rem;
    border-radius: 0.375rem;
    background: var(--status-warning-bg);
    color: var(--status-warning);
    font-size: 0.85rem;
  }

  /* Stack the header vertically so each part gets its own row and nothing shifts between papers:
     title (wraps as needed) → action buttons → tag chips (if any). Status/keywords/topics follow
     as their own blocks below the bar. */
  .bar {
    align-items: stretch;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0;
    overflow-wrap: anywhere;
  }

  .stub-badge {
    background: var(--status-warning-bg);
    border-radius: 6px;
    color: var(--status-warning);
    font-size: 0.68rem;
    font-weight: 600;
    padding: 0.05rem 0.4rem;
    text-transform: uppercase;
    vertical-align: middle;
    white-space: nowrap;
  }


  details {
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    padding: 0.5rem 0.7rem;
  }

  summary {
    cursor: pointer;
    font-weight: 700;
  }

  .fields {
    display: grid;
    gap: 0.5rem;
    margin-top: 0.6rem;
  }

  .two {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: 1fr 1fr;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .actions button,
  .bar-actions button {
    white-space: nowrap;
  }

  .actions-top {
    justify-content: space-between;
  }

  .reviews {
    display: grid;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  .review {
    background: var(--surface-raised);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    padding: 0.5rem;
  }

  .review.has-conflict {
    border-color: var(--status-warning-border);
  }

  .assertion {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    margin-top: 0.3rem;
  }

  .src {
    background: var(--surface-sunken);
    border-radius: 0.25rem;
    font-size: 0.72rem;
    padding: 0.05rem 0.35rem;
  }

  .val {
    flex: 1;
    overflow-wrap: anywhere;
  }

  .canon {
    color: var(--status-success);
    font-size: 0.72rem;
    font-weight: 700;
  }

  .conflict {
    background: var(--status-warning-bg);
    border-radius: 0.25rem;
    color: var(--status-warning);
    font-size: 0.72rem;
    margin-left: 0.4rem;
    padding: 0.05rem 0.35rem;
  }

  .match-pct {
    background: var(--surface-muted, rgba(127, 127, 127, 0.12));
    border-radius: 0.25rem;
    color: var(--text-muted);
    cursor: help;
    font-size: 0.72rem;
    font-weight: 700;
    margin-left: 0.4rem;
    padding: 0.05rem 0.35rem;
  }

  .attach,
  .tags {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  .tag-create {
    align-items: center;
    display: flex;
    gap: 0.4rem;
    margin-top: 0.5rem;
  }

  .tag-create input {
    flex: 1;
    min-width: 0;
  }

  .linkbtn {
    background: none;
    border: none;
    color: var(--accent, var(--ink-muted));
    cursor: pointer;
    font-size: 0.8rem;
    margin-top: 0.4rem;
    padding: 0;
    text-decoration: underline;
  }

  .linkbtn:disabled {
    cursor: default;
    opacity: 0.5;
  }

  .applied-tags {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.5rem;
  }

  .title-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin: 0.15rem 0 0.3rem;
  }

  .tag-chip {
    align-items: center;
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 999px;
    display: inline-flex;
    font-size: 0.8rem;
    gap: 0.35rem;
    padding: 0.1rem 0.5rem;
  }

  .tag-chip .dot {
    background: var(--tag-color, var(--ink-muted));
    border-radius: 50%;
    height: 0.7rem;
    width: 0.7rem;
  }

  .chip-remove {
    background: none;
    border: none;
    color: var(--ink-muted);
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    min-height: auto;
    padding: 0 0.1rem;
  }

  .chip-remove:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  /* Rounded-rect card around each References / In-text-citation / File entry so it is
     unambiguous which Import button / text / hash belongs to which entry. */
  .entry-card {
    background: var(--surface-raised);
    border: 1px solid var(--border-normal);
    border-radius: 8px;
    padding: 0.5rem 0.6rem;
  }

  .files {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
  }

  .files li {
    display: grid;
    gap: 0.4rem;
  }

  .file-row {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    justify-content: space-between;
  }

  .files li.unavailable {
    background: var(--surface-overlay);
    border-style: dashed;
  }

  .file-main {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    min-width: 0;
  }

  .fname {
    overflow-wrap: anywhere;
  }

  .fstatus {
    border-radius: 0.25rem;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.05rem 0.35rem;
    text-transform: uppercase;
  }

  .fstatus-extracted {
    background: var(--status-success-bg);
    color: var(--status-success);
  }

  .fstatus-extract_failed {
    background: var(--status-danger-bg);
    color: var(--status-danger);
  }

  .fstatus-available {
    background: var(--surface-sunken);
    color: var(--ink-normal);
  }

  .fstatus-extracted_discarded {
    background: var(--surface-sunken);
    color: var(--ink-normal);
  }

  .funavail {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }

  .fmain {
    background: var(--status-info-bg);
    color: var(--status-info);
  }

  .focr {
    background: var(--surface-sunken);
    color: var(--ink-normal);
  }

  .fdegraded {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }

  /* Clickable "duplicate PDF" badge → Library hash search. Warning-tinted, button reset. */
  .fdup {
    background: var(--status-warning-bg);
    border: none;
    color: var(--status-warning);
    cursor: pointer;
    font-family: inherit;
  }

  .fdup:hover {
    text-decoration: underline;
  }

  .quick-read {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin: 0.2rem 0 0.6rem;
  }

  .quick-read .hintline {
    margin: 0;
  }

  .findweb-toolbar {
    align-items: center;
    display: flex;
    gap: 0.6rem;
    justify-content: space-between;
    margin: 0.2rem 0 0.4rem;
  }

  .findweb-toolbar .hintline {
    margin: 0;
  }

  .note-count {
    color: var(--ink-normal);
    font-size: 0.72rem;
    font-weight: 700;
  }

  .hash {
    background: var(--surface-sunken);
    border: 1px solid var(--border-strong);
    border-radius: 0.25rem;
    color: var(--ink-normal);
    cursor: pointer;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.72rem;
    min-height: auto;
    /* Full hash, selectable (browser Ctrl+F finds it) and wrapping so the row never scrolls. */
    overflow-wrap: anywhere;
    padding: 0.15rem 0.4rem;
    text-align: left;
    user-select: text;
    width: 100%;
  }

  .small {
    min-height: 1.9rem;
    padding: 0.2rem 0.5rem;
  }

  .file-actions {
    display: flex;
    /* Wrap into extra rows instead of overflowing the paper-view column when it is narrow —
       seven per-file buttons don't fit one line there (flex-shrink: 0 previously forced them
       out of the page). */
    flex-wrap: wrap;
    gap: 0.35rem;
    justify-content: flex-end;
    min-width: 0;
  }

  .bar-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }

  .danger-btn {
    border-color: var(--status-danger-border);
    color: var(--status-danger);
  }

  .keywords {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .kw {
    background: var(--surface-sunken);
    border: 1px solid var(--border-strong);
    border-radius: 10px;
    color: var(--ink-normal);
    cursor: pointer;
    font-size: 0.72rem;
    min-height: auto;
    padding: 0.05rem 0.45rem;
  }

  .kw:hover {
    background: var(--surface-hover);
  }

  /* Topics: same chip shape as keywords but a distinct accent + a labelled divider, so a paper's
     topics read as a separate block from its keywords. */
  .topics {
    align-items: baseline;
    border-top: 1px dashed var(--accent-note-border);
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.5rem;
    padding-top: 0.4rem;
  }

  .topics-label {
    color: var(--accent-note);
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .topic-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .citations {
    align-items: baseline;
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.5rem;
  }

  .citations-label {
    color: var(--accent-note);
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .citations-value {
    font-size: 0.85rem;
    font-weight: 600;
  }

  .citations-meta {
    color: var(--accent-note);
    font-size: 0.72rem;
  }

  .topic {
    background: var(--accent-note-bg);
    border: 1px solid var(--accent-note-border);
    border-radius: 10px;
    color: var(--accent-note);
    cursor: pointer;
    font-size: 0.72rem;
    min-height: auto;
    padding: 0.05rem 0.45rem;
  }

  .topic:hover {
    background: var(--accent-note-bg);
  }

  .lock {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 0.72rem;
    margin-left: 0.4rem;
    padding: 0;
  }

  .lock.locked {
    color: var(--status-success);
    font-weight: 700;
  }

  .refs {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin: 0.5rem 0 0;
    padding-left: 1.1rem;
  }

  .refs li {
    display: grid;
    gap: 0.1rem;
  }

  .ref-head {
    align-items: baseline;
    display: flex;
    gap: 0.4rem;
  }

  .related-link {
    background: none;
    border: none;
    color: var(--status-info);
    cursor: pointer;
    display: flex;
    gap: 0.4rem;
    min-height: auto;
    padding: 0;
    text-align: left;
  }

  .related-link:hover .ref-title {
    text-decoration: underline;
  }

  .related-reason {
    color: var(--ink-muted);
    display: block;
    font-size: 0.72rem;
    margin-top: 0.15rem;
  }

  .ref-marker {
    background: var(--status-info-bg);
    border-radius: 0.25rem;
    color: var(--status-info);
    flex-shrink: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.72rem;
    font-weight: 700;
    padding: 0.05rem 0.4rem;
    white-space: nowrap;
  }

  .ref-title {
    overflow-wrap: anywhere;
  }

  .ref-badge {
    background: var(--status-success-bg);
    border-radius: 0.25rem;
    color: var(--status-success);
    font-size: 0.68rem;
    margin-left: 0.3rem;
    padding: 0.03rem 0.3rem;
  }

  /* Clickable "in library" badge (issue 10) — navigates to the resolved paper. */
  .ref-badge-link {
    border: none;
    cursor: pointer;
    font: inherit;
    font-size: 0.68rem;
  }

  .ref-badge-link:hover {
    text-decoration: underline;
  }

  /* Panel-header badges (UX batch) — same shape as the metadata "conflicts" badge. */
  .sum-badge {
    background: var(--surface-sunken);
    border-radius: 0.25rem;
    color: var(--ink-muted);
    font-size: 0.72rem;
    margin-left: 0.4rem;
    padding: 0.05rem 0.35rem;
    white-space: nowrap;
  }

  .sum-badge-warn {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }

  .sum-badge-ok {
    background: var(--status-success-bg);
    color: var(--status-success);
  }

  /* Inline confirm/reject failure — shown on the row itself, not the far-away top message line. */
  .ref-action-error {
    color: var(--status-danger);
    font-size: 0.8rem;
    margin: 0.2rem 0 0;
  }

  /* "likely match" badge (batch 12) — a soft, unconfirmed candidate; warning-tinted, clickable. */
  .ref-badge-likely {
    background: var(--status-warning-bg);
    border: none;
    color: var(--status-warning);
    cursor: pointer;
    font: inherit;
    font-size: 0.68rem;
  }

  .ref-badge-likely:hover {
    text-decoration: underline;
  }

  .refs-toolbar {
    margin-bottom: 0.5rem;
  }

  .ref-authors {
    display: block;
    font-style: italic;
  }

  .ref-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.25rem;
  }

  .flash-ref {
    animation: flash-ref 0.6s ease-in-out 2;
  }

  @keyframes flash-ref {
    50% {
      background: rgba(255, 200, 90, 0.6);
    }
  }

  .ctx {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    list-style: none;
    margin: 0.4rem 0 0;
    max-height: 16rem;
    overflow: auto;
    padding: 0;
  }

  .ctx li {
    display: flex;
    gap: 0.4rem;
  }

  .ctx span {
    overflow-wrap: anywhere;
  }

  .findwrap {
    display: grid;
    gap: 0.6rem;
  }

  .hintline.warn {
    color: var(--status-warning);
  }

  .candidates {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 0.5rem;
  }

  .candidate {
    display: flex;
    gap: 0.6rem;
    border: 1px solid var(--border-normal);
    border-radius: 8px;
    padding: 0.5rem 0.7rem;
  }

  .candidate .cand-main {
    display: grid;
    gap: 0.25rem;
    min-width: 0;
  }

  .cand-head {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .badge {
    border-radius: 4px;
    font-size: 0.7rem;
    padding: 0.05rem 0.4rem;
    background: var(--accent-note-bg);
    color: var(--accent-note);
  }

  .badge.oa {
    background: var(--status-success-bg);
    color: var(--status-success);
  }

  .badge.score {
    background: var(--surface-sunken);
    color: var(--ink-normal);
  }

  .badge.platform {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }

  .cand-title {
    overflow-wrap: anywhere;
  }

  .cand-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    color: var(--ink-muted);
    font-size: 0.85rem;
  }

  .cand-status {
    font-size: 0.85rem;
  }

  .cand-status.ok {
    color: var(--status-success);
  }

  .cand-status.warn {
    color: var(--status-warning);
  }

  .cand-status.err {
    color: var(--status-danger);
  }

  .link {
    background: none;
    border: none;
    color: var(--accent-link);
    cursor: pointer;
    padding: 0;
    text-decoration: underline;
  }

  .searched-paper {
    background: var(--accent-note-bg);
    border: 1px solid var(--accent-note-border);
    border-left: 4px solid var(--accent-note);
    border-radius: 8px;
    display: grid;
    gap: 0.15rem;
    padding: 0.55rem 0.75rem;
  }

  .searched-label {
    color: var(--accent-note);
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
  }

  .searched-title {
    overflow-wrap: anywhere;
  }

  .searched-authors {
    color: var(--ink-normal);
    font-size: 0.85rem;
    margin-top: 0.15rem;
    overflow-wrap: anywhere;
  }

  .searched-meta {
    color: var(--ink-normal);
    display: flex;
    flex-wrap: wrap;
    font-size: 0.85rem;
    gap: 0.4rem;
  }

  .sources {
    display: grid;
    gap: 0.3rem;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .source-row {
    align-items: center;
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    display: flex;
    gap: 0.5rem;
    padding: 0.3rem 0.6rem;
  }

  .source-name {
    flex: 1;
    font-weight: 600;
  }

  .source-state {
    font-size: 0.82rem;
  }

  .source-state.ok {
    color: var(--status-success);
  }

  .source-state.err {
    color: var(--status-danger);
  }

  .spinner {
    animation: spin 0.7s linear infinite;
    border: 2px solid var(--border-normal);
    border-radius: 50%;
    border-top-color: var(--accent-note);
    display: inline-block;
    height: 0.85rem;
    width: 0.85rem;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* Pinned to the top of the modal's scrolling body so it never scrolls away with the list. */
  .download-bar {
    align-items: center;
    background: var(--surface-raised);
    border: 1px solid var(--border-normal);
    border-radius: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    justify-content: space-between;
    padding: 0.5rem 0.7rem;
    position: sticky;
    top: 0;
    z-index: 5;
  }

  .bar-left,
  .bar-right {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .sel-count {
    color: var(--ink-normal);
    font-size: 0.85rem;
    font-weight: 600;
  }

  .dl-progress {
    color: var(--status-info);
    font-size: 0.85rem;
    font-weight: 700;
  }

  .dl-progress.has-failures {
    color: var(--status-danger);
  }

  .modal-lead {
    margin: 0 0 0.75rem;
    font-size: 0.9rem;
  }
  .merge-preview {
    margin: 0 0 0.9rem;
    padding-left: 1.1rem;
    font-size: 0.88rem;
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .merge-preview .warn {
    color: var(--status-warning);
  }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }

  .locations {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
  }

  .location-row {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    justify-content: space-between;
  }

  .loc-path {
    overflow-wrap: anywhere;
  }

  .loc-rack {
    color: var(--ink-muted);
  }

  .loc-shelf {
    font-weight: 600;
  }

  .putinto {
    display: grid;
    gap: 0.7rem;
  }

  .putinto-actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
  }

  .confirmwrap {
    display: grid;
    gap: 0.6rem;
  }

  .confirm-url {
    overflow-wrap: anywhere;
  }

  .confirm-actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
  }

  /* Attach-from-URL / server-path modal form. */
  .remote-attach {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .remote-attach input {
    width: 100%;
  }
  .ra-actions {
    display: flex;
    gap: 0.5rem;
  }
  .ra-warn {
    background: var(--status-warning-bg);
    border: 1px solid var(--status-warning-border);
    border-radius: var(--radius-sm);
    color: var(--status-warning);
    margin: 0;
    padding: 0.4rem 0.6rem;
  }
  .ra-ok {
    color: var(--status-success);
    margin: 0;
  }
  .ra-err {
    color: var(--status-danger);
    margin: 0;
    overflow-wrap: anywhere;
  }
</style>
