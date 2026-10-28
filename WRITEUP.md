# Write-up: AI Proposal Triage Design

**In short:** AI pulls quoted facts from each proposal. Code checks the quotes, applies the admin's rules and formulas, and ranks. A reviewer confirms, and people award. See the [diagram](diagram/process-flow.png).

**Assumptions** (stated here rather than waiting for a reply):
- The $260,000 ceiling and the 16-week schedule are hard limits. Going over either makes a proposal ineligible. A proposal that gives no committed number is flagged so the reviewer can ask.
- For each weighted criterion, the admin sets a fixed scale as well as a weight. For this category: experience 0→15 installs, price $260k→$200k, timeline 16→8 weeks, warranty averages panel (/25 years) and workmanship (/15 years), and team is a 4-item checklist ([`criteria.json`](prototype/criteria.json)).
- "Monitoring plan" means monitoring the installed system. Construction progress reports don't count.
- "Last 5 years" means 2021 onward.

## 1. AI vs. rules

**AI is used in one step: reading.** It turns free text into structured facts: price, weeks, warranty years, and whether the licence belongs to the bidder or a subcontractor. Bidders word things differently ("all-in", "six-person crew"), and language models handle that variety well.

**Everything else is code:** PDF-to-text, the injection scan, quote checking, the mandatory pass/fail checks, the budget and schedule limits, scoring, weighting, ranking and the reason text.

**Why the line is there:** the remaining steps are arithmetic or policy. Code does them the same way every time, can be tested, and can't be argued with. If the AI also scored, a sentence inside a proposal could influence its own score, and proposal C tries exactly that. Here the AI never sees the weights or produces a score.

## 2. Consistency and explainability

- **Checked facts.** The AI's output must match a JSON schema, and each value needs an exact quote. Code confirms the quote appears in the proposal and that its number matches. If anything fails, the AI is asked to repair it once; if it still fails, a person checks.
- **Stored facts.** Extractions are saved under the file hash, prompt version, model and criteria version. Ranking the same files again reuses those facts, so **the result is identical**. The design doesn't rely on the AI being repeatable.
- **Fixed formulas and scales.** A proposal's score never depends on the other proposals, so adding a sixth bid changes no one else's score. Ties are broken by experience points, then by price.
- **Visible reasoning.** For each proposal the reviewer sees the status, the total, and a table of fact → points → weight → contribution, with each fact linked to its quote in the PDF (see [`report.md`](prototype/output/report.md)). The one-line reason is generated from that table, so it can't disagree with the score.

## 3. Human in the loop

- **Admin:** sets the criteria, weights and scales. The tool won't save weights that don't add up to 100%, and every save creates a new version.
- **Reviewer, before anything is final:**
  - clears or upholds every flag (manipulation, missing evidence, missing information, unclear answers)
  - confirms every ineligible result
  - corrects any fact; code then re-scores without asking the AI again
  - may override a status or position, but only with a written reason
  - approves the final list
- **Award:** decided by people outside the tool. The tool only ranks.
- **Audit log** (append-only), per run: who and when, file hashes, criteria version, prompt version and model, raw AI output, evidence checks, every rule outcome and score calculation, every flag and its resolution, every human edit (old value, new value, who, why), and the approved list. Any result can be rebuilt from the log.

## 4. Changing criteria

- Criteria are versioned. A run is locked to the version it started with, so an edit creates v2 and leaves v1 results unchanged.
- Results from different versions are never mixed in one ranking. Older results are labelled "criteria changed since scoring".
- **Weight, scale or threshold change:** code re-scores the stored facts. This is instant, costs nothing, and adds no AI uncertainty.
- **New or reworded fact:** the AI extracts only that field, and the reviewer confirms it.
- The reviewer sees which positions changed. The edit requires a reason and both versions are logged. Whether changing criteria after bids are opened is fair is a procurement decision: the tool makes the change visible.

## 5. Risks and safeguards

| Risk | Guard in this design |
|---|---|
| **Wrong extraction** (for example, "$5M" read as 5) | Schema, quote check, and number-in-quote check. The reviewer sees each fact beside its quote. |
| **Made-up facts** | No quote, no value. Quotes must exist in the text. Missing means "not stated" and is never guessed. |
| **Manipulation** (C's note to automated reviewers) | Proposal text is marked as untrusted data, pattern rules and the AI both report suspicious text, and the AI has no scores to give. The proposal is flagged and a person decides. |
| **Responsibility gaps** (B's licence belongs to a subcontractor) | The question asks specifically about the bidder. Mandatory fails are confirmed by a person. |
| **Missing information** (D's schedule) | Flagged and never silently ranked. D's provisional score is 54.1, and a commitment of 14 weeks or less would place it above E, so the tool asks rather than guesses. |
| **Bias** (polished writing, big names, local firms) | The AI doesn't judge quality, and company names don't feed into scoring. Scales are fixed before bids are opened, and marketing claims are ignored. |
| **Poor PDFs** (scans, tables) | OCR fallback. Low text yield is flagged. |
| **Cost and outages** | One cached AI call per proposal. Re-scoring is free. A failed call queues the proposal; it is never ranked by default. |
| **Over-trusting the tool** | Flags and ineligible results can't be skipped, and evidence appears beside every number. |
| **Data privacy** | An AI provider whose terms rule out training on our data. No keys in code. |

**On the samples:** B has the lowest price. Without the licence gate it would score 58.3 and rank 2nd, which is why the mandatory check runs before scoring. C's note gains it nothing: on its facts it scores 48.4.
