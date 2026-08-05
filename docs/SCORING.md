# Paper Watch Scoring

Paper Watch expresses the relevance of each candidate paper to the lab's research on a scale from 0 to 100. It combines important terms, research themes, semantic similarity to multiple lab papers, authors, and journals instead of relying on one keyword or one reference PDF.

## Final Score

The final score `S` is:

\[
S = 45K + 35R + 10C + 5A + 5J
\]

Every component is normalized to the range from 0 to 1, so the final score remains between 0 and 100.

| Component | Normalization | Maximum contribution |
| --- | --- | ---: |
| Keyword relevance `K` | `min(keyword / 30, 1)` | 45 |
| RAG relevance `R` | calibrated and clamped to 0–1 | 35 |
| Theme combinations `C` | `min(combo / 6, 1)` | 10 |
| Priority author `A` | `min(author / 5, 1)` | 5 |
| Priority journal `J` | clamped to 0–1 | 5 |

## Term Score

The `term_score` stored in the database is a raw eligibility signal. It combines keyword relevance and theme-combination bonuses:

\[
\text{Term Score}
=
\max\left(0, \sum_{i=1}^{n} m_i d^{i-1} - P\right)
+ \min\left(\sum_j b_j, 6\right)
\]

- `m_i` is the contribution of a matched concept.
- `d` is the diminishing-return factor, `0.85` by default.
- `P` is the negative-profile penalty.
- `b_j` is a matched theme-combination bonus.

The raw `term_score` has no upper bound of 30. Only its keyword component reaches the normalized maximum used by the final score at 30.

### Keyword Matches

Every keyword has a weight:

- Title match: `weight × 1.5`
- Abstract-only match: `weight × 1.0`
- Match in both title and abstract: use the title contribution

Examples of built-in terms are:

| Term | Weight |
| --- | ---: |
| `persistent spin helix` | 9 |
| `spin helix` | 7 |
| `time-resolved kerr` / `trkr` | 6 |
| `crsbr` | 6 |
| `spin diffusion` | 5 |
| `magnon` | 5 |
| `rashba` | 4 |
| `dresselhaus` | 4 |
| `spin-orbit` | 4 |
| `wse2` / `ws2` | 3 |
| `gaas` / `ingaas` | 2 |

### Synonym Groups

Different spellings of one concept do not receive duplicate credit. For example, `spin-orbit`, `spin orbit`, `spin-orbit coupling`, `SOC`, and `SOI` belong to one group. Only the strongest contribution within the group is retained.

PSH, TRKR, 2DEG, two-dimensional magnetism, and spin excitons have similar groups. Profile-derived terms that are not in a predefined group remain independent concepts.

### Diminishing Returns

Matched concepts are ordered by contribution. Each concept after the first is multiplied by another factor of `0.85`.

For contributions of `9, 6, 4, 3`, the keyword score is:

\[
9 + 6(0.85) + 4(0.85^2) + 3(0.85^3) = 18.84
\]

This limits over-scoring from incidental keyword density. `PAPER_WATCH_KEYWORD_DECAY` controls the decay factor.

### Lab Profile

Every successful RAG ingest regenerates:

```text
rag_poc/index/lab_profile.json
rag_poc/index/lab_profile.md
```

By default, Paper Watch combines the built-in terms with up to 80 `weighted_terms` from `lab_profile.json`. When the same term appears in both sources, the higher weight wins.

Setting `PAPER_WATCH_TERMS` replaces both the built-in and generated terms completely. Normal production should leave it unset and use the generated lab profile.

### Theme Combinations

The `core_themes` and `theme_combinations` profile sections organize terms into:

- materials
- methods
- physics
- applications

A candidate receives a theme bonus when it matches at least two different categories in one rule. By default, each rule contributes at most 4 raw points and the combined theme score is capped at 6.

### Negative Profile

Terms in `negative_profile` subtract their weights from keyword relevance when they appear in the title or abstract. Keyword relevance cannot fall below zero.

## RAG Score

The RAG score measures semantic similarity between a candidate and the lab's indexed PDFs.

### 1. Reference-Chunk Selection

Paper Watch selects up to six chunks per PDF by default. It starts with one chunk, then adds chunks with low semantic overlap with those already selected. This gives broader coverage than taking only leading pages. At most 8,000 reference chunks are loaded by default.

### 2. Title and Abstract Embeddings

The title and abstract are embedded separately. For each lab PDF, Paper Watch finds the best title match and the best abstract match, then combines them as:

\[
q_p = 0.35 \times \text{title similarity}
    + 0.65 \times \text{abstract similarity}
\]

### 3. Multi-Paper Aggregation

The per-PDF values `q_p` are sorted, and the top three distinct papers are aggregated:

\[
R_{raw} = 0.6q_1 + 0.3q_2 + 0.1q_3
\]

Only one score from each PDF contributes, so several chunks from one paper cannot occupy all top positions.

### 4. Calibration

The fixed calibration maps a raw similarity of `0.75` to 0 and `0.90` to 1 by default.

When at least ten candidates are available, Paper Watch also creates adaptive bounds from the 10th and 95th percentiles of the current batch. The final RAG component blends 65% fixed calibration with 35% adaptive calibration.

This preserves cross-run comparability while still representing useful differences within one candidate batch. Paper Watch uses only fixed calibration when the batch size or score spread is insufficient.

## Authors and Journals

Author and journal priorities come from both `lab_profile.json` and explicit `.env` settings. If several values apply to one author or journal, the highest value wins.

- Author contribution: up to 5 final points
- Journal contribution: up to 5 final points
- If several authors match, only the largest author priority contributes
- If several journal labels match, only the largest journal priority contributes

Explicit settings use `PAPER_WATCH_PRIORITY_AUTHORS` and `PAPER_WATCH_PRIORITY_JOURNALS`.

## Stored Database Values

The primary scoring columns in `paper_watch.sqlite3` are:

| Column | Meaning |
| --- | --- |
| `score` | Final score from 0 to 100 |
| `term_score` | Raw keyword and theme-combination score |
| `rag_score` | Calibrated RAG score from 0 to 1 |
| `reasons_json` | Matched terms, themes, and penalties |
| `rag_source` | Closest indexed lab PDF |
| `rag_page_start` / `rag_page_end` | Page range of the representative chunk |

`term_score` and `score` use different scales. A `term_score` above 30 is valid, while the keyword contribution to the final score is capped at 45 points.

## Rescoring Existing Data

Preview and apply the current implementation and profile to existing records:

```bash
sudo ./scripts/run_paper_watch.sh --mode rescore --lookback-days 180 --dry-run
sudo ./scripts/run_paper_watch.sh --mode rescore --lookback-days 180
```

The write operation provides these safeguards:

- Creates a timestamped SQLite backup before updating records
- Does not change the database schema
- Targets unposted records by default
- Aborts before any write if the RAG index or Ollama request fails
- Preserves `posted_at`, `updated_at`, `first_seen_at`, and `last_seen_at`

Add `--include-posted` only when posted history must also be rescored.

## Main Configuration Values

| Environment variable | Default | Purpose |
| --- | ---: | --- |
| `PAPER_WATCH_PROFILE_TERM_LIMIT` | `80` | Maximum generated profile terms |
| `PAPER_WATCH_PROFILE_COMBO_LIMIT` | `30` | Maximum theme rules |
| `PAPER_WATCH_PROFILE_COMBO_SCORE_CAP` | `6` | Theme-combination cap |
| `PAPER_WATCH_KEYWORD_DECAY` | `0.85` | Diminishing-return factor |
| `PAPER_WATCH_RAG_CHUNKS_PER_SOURCE` | `6` | Reference chunks per PDF |
| `PAPER_WATCH_RAG_TOP_PAPERS` | `3` | Distinct lab PDFs to aggregate |
| `PAPER_WATCH_RAG_FLOOR` | `0.75` | Fixed-calibration lower bound |
| `PAPER_WATCH_RAG_CEILING` | `0.90` | Fixed-calibration upper bound |
| `PAPER_WATCH_RAG_ADAPTIVE_WEIGHT` | `0.35` | Adaptive-calibration blend |
| `PAPER_WATCH_RAG_MIN_TERM_SCORE` | `1` | Minimum Term score for RAG evaluation |

See [`.env.example`](../.env.example) for configuration and [`rag_poc/paper_watch.py`](../rag_poc/paper_watch.py) for the implementation.
