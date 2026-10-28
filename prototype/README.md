# Prototype (optional extra)

A small Python script that runs the design end to end on proposals A–E. It is not an app; it shows that the flow, the prompt and the scoring rules actually work together.

| File | What it is |
|---|---|
| [`criteria.json`](criteria.json) | What the admin screens would save: fields to extract, mandatory checks, project limits, weights and scoring scales |
| [`prompts.py`](prompts.py) | The extraction prompt (same text as [PROMPTS.md](../PROMPTS.md)), built from `criteria.json` |
| [`triage.py`](triage.py) | The pipeline: extract → injection scan → evidence check → mandatory gate → limits → score → rank |
| [`fixtures/`](fixtures/) | Hand-labelled expected extractions for A–E, so the rules can run without an API key |
| [`output/`](output/) | Result of the last offline run: `ranking.json`, a readable `report.md` with each score broken down, and `audit-log.jsonl` |

## Run without an API key (rules only)

```bash
python prototype/triage.py --offline --check
```

This uses the fixtures in place of the AI step, so it tests everything after extraction. `--check` confirms the result matches the top-level [`ranking.json`](../ranking.json). Python 3.10+, no packages needed.

## Run with Claude (full flow)

1. `pip install -r prototype/requirements.txt`
2. Copy `.env.example` to `.env` in the repository root and add your `ANTHROPIC_API_KEY`.
3. Create `prototype/samples/A.txt` … `E.txt` and paste each proposal's text from the brief. This folder is git-ignored, because the brief asks candidates not to commit the proposal text.
4. `python prototype/triage.py --check`

The live run calls Claude once per proposal (plus one repair call if a quote fails the evidence check) and caches each answer by file hash, prompt version, model and criteria version in `prototype/cache/` (also git-ignored). Running it again re-uses the cache, so the ranking comes out the same. The request also turns on the API's server-side fallback, so if the main model declines a request a fallback model answers it instead of the run failing.
