# ai-agent-feed-mirror

Mirrors a few public AI/agent-engineering feeds into JSON so a sandboxed agent
can read them over `raw.githubusercontent.com`.

Exists because the Claude Code cloud sandbox's egress proxy blocks
`simonwillison.net`, `latent.space`, `interconnects.ai` and `hn.algolia.com`,
but allows GitHub. Without this mirror the weekly digest can only see search
snippets — and it fabricated a statistic the first time it tried.

## Files

| File | What |
|---|---|
| `feeds/last7days.json` | Pre-filtered 7-day digest across all sources. **Read this one.** |
| `feeds/<source>.json` | Rolling 60-day archive per source, deduped by URL |
| `feeds/index.json` | Per-source success/failure of the last run |

## Sources

- Simon Willison — `simonwillison.net/atom/everything/`
- Latent Space — `latent.space/feed` (carries the `[AINews]` daily digests)
- Interconnects — `interconnects.ai/feed`
- Hacker News — Algolia API, several agent/LLM queries, >60 points

## How it runs

`.github/workflows/mirror.yml` runs `scripts/fetch.py` daily at 03:41 UTC and
commits `feeds/` when anything changed. Stdlib only, no dependencies.

The mirror is deliberately inclusive — filtering for relevance is the reader's
job, not the mirror's.
