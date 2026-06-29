/// <reference types="svelte" />

// Vite's `?url` import suffix returns the asset's final URL as a string. Used for the
// bundled PDF.js worker (see PdfReader.svelte).
declare module '*?url' {
  const url: string;
  export default url;
}

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
