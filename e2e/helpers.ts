import { APIRequestContext, expect, Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Where the running dev stack lives. The frontend's default VITE_API_BASE_URL already points at the
// API, so specs drive the UI at BASE_URL and only use API_URL for setup/cleanup shortcuts.
export const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173';
export const API_URL = process.env.E2E_API_URL ?? 'http://127.0.0.1:8000';

// Must match scripts/ensure_e2e_user.py defaults (override both places together).
export const USERNAME = process.env.E2E_USERNAME ?? 'e2e_admin';
export const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-Passw0rd!';

/** A collision-proof title/name so parallel runs and reruns never clash. */
export function uniqueName(tag: string): string {
  return `E2E ${tag} ${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

/** Log in against the API and return a bearer token (used for setup + cleanup only). */
export async function apiLogin(request: APIRequestContext): Promise<string> {
  const res = await request.post(`${API_URL}/api/v1/auth/login`, {
    data: { username: USERNAME, password: PASSWORD },
  });
  expect(res.ok(), `API login failed: ${res.status()}`).toBeTruthy();
  return (await res.json()).access_token as string;
}

function auth(token: string) {
  return { Authorization: `Bearer ${token}` };
}

/** Create a paper via the API (setup shortcut for specs that only exercise later flows). */
export async function apiCreateWork(
  request: APIRequestContext,
  token: string,
  title: string,
): Promise<string> {
  const res = await request.post(`${API_URL}/api/v1/works`, {
    headers: auth(token),
    data: { canonical_title: title },
  });
  expect(res.ok(), `createWork failed: ${res.status()}`).toBeTruthy();
  return (await res.json()).id as string;
}

/** Delete every paper whose canonical_title exactly matches `title` (idempotent cleanup). */
export async function apiDeleteWorksByTitle(
  request: APIRequestContext,
  token: string,
  title: string,
): Promise<void> {
  // Narrow with the shared "E2E" prefix, then match the exact title so we never touch other data.
  const res = await request.get(`${API_URL}/api/v1/works?q=${encodeURIComponent('E2E')}`, {
    headers: auth(token),
  });
  if (!res.ok()) return;
  const works = (await res.json()) as Array<{ id: string; canonical_title: string | null }>;
  for (const w of works) {
    if ((w.canonical_title ?? '') === title) {
      await request.delete(`${API_URL}/api/v1/works/${w.id}`, { headers: auth(token) });
    }
  }
}

/** Archive every shelf whose name exactly matches `name` (there is no hard-delete for shelves). */
export async function apiArchiveShelvesByName(
  request: APIRequestContext,
  token: string,
  name: string,
): Promise<void> {
  const res = await request.get(`${API_URL}/api/v1/shelves`, { headers: auth(token) });
  if (!res.ok()) return;
  const shelves = (await res.json()) as Array<{ id: string; name: string }>;
  for (const s of shelves) {
    if (s.name === name) {
      await request.patch(`${API_URL}/api/v1/shelves/${s.id}`, {
        headers: auth(token),
        data: { status: 'archived' },
      });
    }
  }
}

/** Wait until the authenticated tab-nav is visible (i.e. we are signed in). */
export async function expectSignedIn(page: Page): Promise<void> {
  await expect(page.getByRole('link', { name: 'Library' })).toBeVisible({ timeout: 15_000 });
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Absolute path to the committed 1-page PDF fixture (valid PDF with the text used by the reader tests). */
export const SAMPLE_PDF = path.join(__dirname, 'fixtures', 'sample.pdf');

/**
 * A byte-unique variant of the sample PDF. Uploads are content-addressed and deduplicated by
 * SHA-256 (see storage.import_uploaded_pdf) — a new *work* is only minted when the bytes are new.
 * Appending a comment line AFTER the trailing `%%EOF` changes the hash without shifting any xref
 * offset, so the file still renders in pdf.js / parses in GROBID. Use this whenever a spec needs an
 * upload to create a fresh paper (so parallel specs + reruns never collide on the same content).
 */
export function uniqueSamplePdf(token: string): Buffer {
  const base = fs.readFileSync(SAMPLE_PDF);
  return Buffer.concat([base, Buffer.from(`\n% e2e-unique ${token}\n`)]);
}

/** Delete every shelf whose name exactly matches `name` via the hard-delete endpoint (idempotent). */
export async function apiDeleteShelvesByName(
  request: APIRequestContext,
  token: string,
  name: string,
): Promise<void> {
  const res = await request.get(`${API_URL}/api/v1/shelves`, { headers: auth(token) });
  if (!res.ok()) return;
  const shelves = (await res.json()) as Array<{ id: string; name: string }>;
  for (const s of shelves) {
    // Never touch the default "Inbox" shelf (it can't be deleted and is shared).
    if (s.name === name && s.name !== 'Inbox') {
      await request.delete(`${API_URL}/api/v1/shelves/${s.id}`, { headers: auth(token) });
    }
  }
}

/** Delete every paper whose canonical_title CONTAINS `needle` (for titles derived from a filename). */
export async function apiDeleteWorksByTitleContains(
  request: APIRequestContext,
  token: string,
  needle: string,
): Promise<void> {
  const res = await request.get(`${API_URL}/api/v1/works?q=${encodeURIComponent(needle)}`, {
    headers: auth(token),
  });
  if (!res.ok()) return;
  const works = (await res.json()) as Array<{ id: string; canonical_title: string | null }>;
  for (const w of works) {
    if ((w.canonical_title ?? '').includes(needle)) {
      await request.delete(`${API_URL}/api/v1/works/${w.id}`, { headers: auth(token) });
    }
  }
}

/** Archive every rack whose name exactly matches `name` (racks have no hard-delete). */
export async function apiArchiveRacksByName(
  request: APIRequestContext,
  token: string,
  name: string,
): Promise<void> {
  const res = await request.get(`${API_URL}/api/v1/racks`, { headers: auth(token) });
  if (!res.ok()) return;
  const racks = (await res.json()) as Array<{ id: string; name: string }>;
  for (const r of racks) {
    if (r.name === name) {
      await request.patch(`${API_URL}/api/v1/racks/${r.id}`, {
        headers: auth(token),
        data: { status: 'archived' },
      });
    }
  }
}

/** List a work's files via the API (used to poll async GROBID extraction status). */
export async function apiListWorkFiles(
  request: APIRequestContext,
  token: string,
  workId: string,
): Promise<Array<{ id: string; status: string; content_available: boolean }>> {
  const res = await request.get(`${API_URL}/api/v1/works/${workId}/files`, { headers: auth(token) });
  if (!res.ok()) return [];
  return (await res.json()) as Array<{ id: string; status: string; content_available: boolean }>;
}

/** Find a single work whose canonical_title contains `needle` (newest first), or null. */
export async function apiFindWorkByTitleContains(
  request: APIRequestContext,
  token: string,
  needle: string,
): Promise<{ id: string; canonical_title: string | null } | null> {
  const res = await request.get(`${API_URL}/api/v1/works?q=${encodeURIComponent(needle)}`, {
    headers: auth(token),
  });
  if (!res.ok()) return null;
  const works = (await res.json()) as Array<{ id: string; canonical_title: string | null }>;
  return works.find((w) => (w.canonical_title ?? '').includes(needle)) ?? null;
}
