// Help content for the Visualizations tab (B1): a short always-visible description per view, a
// deeper "About this view" write-up with per-parameter help, and a stated requirement so users can
// pick the right view. Kept as data (not markup) so it's unit-testable and reused by both the
// inline description and the "Visualization types" overview.

export interface VizParamHelp {
  name: string;
  help: string;
}

export interface VizViewHelp {
  key: string;
  name: string;
  /** One or two sentences, always shown under the view picker. */
  short: string;
  /** Deeper explanation for the "About this view" popup. */
  about: string;
  /** What the view needs to be meaningful (shown in About + the types overview); null if none. */
  requirements: string | null;
  /** Per-parameter help for the settings this view exposes. */
  params: VizParamHelp[];
}

const SCOPE_PARAM: VizParamHelp = {
  name: "Scope",
  help: "Which papers to include — the whole library, a shelf, a rack, or the current search results.",
};

// Per-axis-option help for the temporal map's X / Y selectors: what each option is and how to read
// it. Keyed by the axis `key` returned in payload.axis_options (with sensible built-in defaults).
// Kept as data so it's unit-testable and can be rendered wherever the axis options are shown.
export const AXIS_OPTION_HELP: Record<string, string> = {
  year: "Publication year. Read left→right as older→newer — pair it with a metric on the other axis to watch that metric evolve over time.",
  citation_count:
    "Total times the paper has been cited across all literature (from stored metadata). Higher means more widely influential overall.",
  local_degree:
    "How many papers within the current scope cite or are cited by it — its connectedness inside your library, not the wider world. Higher means more central to your collection.",
  citation_velocity:
    "Citations gained per year since publication (citation count ÷ years since published). Surfaces fast-rising papers regardless of age.",
  similarity_to_focus:
    "Cosine similarity of the paper's embedding to the chosen focus paper (1 = most alike, 0 = unrelated). Needs a focus paper and embeddings.",
  topic_similarity_to_focus:
    "How strongly the paper shares the focus paper's topics (from extracted topics). Higher means more topical overlap. Needs a focus paper and topics.",
};

/** Help for one axis option, or a safe fallback for an unknown/new axis key. */
export function axisOptionHelp(key: string): string {
  return AXIS_OPTION_HELP[key] ?? "A value plotted on this axis.";
}

export const VIEW_HELP: Record<string, VizViewHelp> = {
  temporal_map: {
    key: "temporal_map",
    name: "Temporal map",
    short:
      "Plots each paper as a point — publication year on one axis and a metric you choose on the other. Citation edges link papers that cite each other, so clusters of related work stand out over time.",
    about:
      "A Litmaps-style scatter of your papers. Both axes are configurable; point size and colour encode extra dimensions, and an optional overlay draws the citation links among the papers in scope. Papers that land on the same spot merge into one marker showing the count — hover it to list them and open any one. Use the two-handle range sliders below/beside the plot to clamp each axis (handles at the end stops = auto); scroll to zoom; shift-click a legend entry to solo it; Show all resets the zoom windows and Reset view repaints everything.",
    requirements:
      'Year and citation axes work from stored metadata. The "similarity to focus" and "topic similarity to focus" axes need a focus paper plus, respectively, embeddings or extracted topics.',
    params: [
      SCOPE_PARAM,
      {
        name: "X / Y axis",
        help: "The value plotted on each axis (year, citation count, local citation degree, citation velocity, or similarity to a focus paper).",
      },
      {
        name: "Size",
        help: "What point size encodes — local citation degree or citation count.",
      },
      {
        name: "Colour",
        help: "What point colour encodes — e.g. reading status.",
      },
      {
        name: "Focus paper",
        help: "The reference paper the similarity axes compare against.",
      },
      {
        name: "Citation edges",
        help: "Overlay lines between papers that cite each other (on by default).",
      },
      {
        name: "Edge limit",
        help: "Above this many papers the edges are hidden to keep the map readable; raise it, then Build/Refresh, to force them.",
      },
    ],
  },
  embedding_cluster: {
    key: "embedding_cluster",
    name: "Embedding clusters",
    short:
      "Places papers by semantic similarity (a 2-D projection of their embeddings) and colours them by cluster, so papers about the same thing sit together.",
    about:
      "Each paper is embedded, the vectors are projected to two dimensions (PCA by default, UMAP optionally), and papers are grouped into clusters. Nearby points are semantically similar; the cluster colour is a rough topic grouping. The two-handle range sliders clamp each axis (end stops = auto); Show all resets the zoom windows, Reset view repaints everything.",
    requirements:
      "Meaningful clusters need a real embedding model (set one in AI & Models and reindex). With only the built-in baseline it still renders, but the layout is coarse.",
    params: [
      SCOPE_PARAM,
      {
        name: "Layout",
        help: "The 2-D projection: PCA (built-in) or UMAP (needs the AI extra image; falls back to PCA if absent).",
      },
      {
        name: "Size",
        help: "What point size encodes — local citation degree or citation count.",
      },
    ],
  },
  co_citation: {
    key: "co_citation",
    name: "Co-citation network",
    short:
      "A network of papers linked by how they cite (or are co-cited by) each other, revealing tightly-connected groups of related work.",
    about:
      "Nodes are papers; edges connect papers that share a citation relationship (bibliographic coupling or co-citation, selectable). Densely-linked clusters point to sub-topics or research lineages in your library.",
    requirements:
      "Needs extracted references — run extraction on the papers first.",
    params: [
      SCOPE_PARAM,
      {
        name: "Edge",
        help: "How two papers are considered linked — bibliographic coupling or co-citation.",
      },
      { name: "Colour", help: "What node colour encodes." },
    ],
  },
  topic_river: {
    key: "topic_river",
    name: "Topic river",
    short:
      "Shows how the share of papers in each topic changes across publication years — a stacked stream of your library’s themes over time.",
    about:
      "Papers are grouped into topics (from embedding clusters) and the proportion of each topic is charted per year, so you can see themes rise and fall across your collection’s timeline.",
    requirements:
      "Needs topics (derived from embeddings) and publication years on the papers.",
    params: [SCOPE_PARAM],
  },
  similarity_heatmap: {
    key: "similarity_heatmap",
    name: "Similarity heatmap",
    short:
      "A matrix of pairwise similarity between papers — warmest where two papers are most alike — for a small, focused selection.",
    about:
      "Every pair of papers in the (small) scope is scored by cosine similarity of their embeddings and shown as a colour-coded matrix. Best for comparing a handful of papers closely.",
    requirements:
      "Best with a real embedding model. Capped at ~50 papers (the most recent are kept for larger scopes).",
    params: [SCOPE_PARAM],
  },
};

/** Help for a view, or a safe generic fallback for an unknown/new view type. */
export function helpForView(key: string): VizViewHelp {
  return (
    VIEW_HELP[key] ?? {
      key,
      name: key,
      short: "Visualization of your library.",
      about: "No description available for this view yet.",
      requirements: null,
      params: [SCOPE_PARAM],
    }
  );
}
