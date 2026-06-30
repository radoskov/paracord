# AI providers & models (Admin → AI & Models)

PaRacORD's semantic search, summaries, and topic modeling run on **dependency-free lexical
baselines by default** (hash-BOW embeddings, extractive summaries, TF-IDF topics). Heavier local
providers are **opt-in and configured from the web UI** (Admin → AI & Models), not from a config
file. Owner only.

## What you can choose (Admin → AI & Models)

| Engine | Default | Heavier options |
|--------|---------|-----------------|
| Embeddings (semantic search) | `hash_bow` | `sentence_transformers`, `ollama` |
| Summaries | `extractive` | `local_llm` (Ollama) |
| Topics | `tfidf` | `embedding` / `bertopic` (deterministic embedding clustering) |

Each provider shows **available** or a one-line hint for how to enable it. Changing the **embedding
model** automatically queues a **reindex** (vectors are stored per provider+model); the panel shows
`indexed / total` coverage for the active model and has a **Reindex** button.

## Enabling the heavier providers

Two independent things: the **runtime** (a Python package or a daemon) and the **model weights**.

### Ollama (recommended — fully GUI-drivable)

Ollama needs **no Python dependency**, only a reachable daemon. It powers both embedding
(`ollama`) and `local_llm` summaries.

```bash
make up-ai          # starts the Ollama service (compose `ai` profile)
```

Then in Admin → AI & Models: set **Ollama URL** (default `http://ollama:11434` in compose, or
`http://localhost:11434`), **Pull model** (e.g. `nomic-embed-text` for embeddings, `qwen3:4b` for
summaries), select the provider, and **Save**. Pulls run as background jobs — watch the Jobs tab.

### sentence-transformers (Python package)

The Python package is **not** in the base image (immutable images; no runtime `pip install`). Enable
it by rebuilding with the AI extra (uncomment `sentence-transformers` in
`backend/requirements.txt`, or use an `ai` build target), then redeploy. Once importable, the panel
marks it **available**; selecting a model downloads its weights into the model-cache on first use /
via **Pull model** (`provider = sentence_transformers`).

## How the config is applied

The choices are stored in the single-row `ai_config` table (migration `0018`) and overlaid on the
static `Settings` defaults at request time (`app/services/ai_config.py`). An empty table reproduces
the exact out-of-the-box baseline behavior, so the GUI never has to be touched to get a working
system — it only *upgrades* the engines when you choose to.
