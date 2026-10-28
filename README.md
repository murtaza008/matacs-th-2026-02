# AI Proposal Triage: Design Submission

| | |
|---|---|
| **Candidate** | Murtaza Mazhar |
| **Reference** | MATACS-TH-2026-02 |
| **Role** | Associate Software Developer |
| **Time actually spent** | **[FILL IN, e.g. about 2 hours: 75 min on the design, the rest on the optional prototype]** |

## Files

| File | What it contains |
|---|---|
| [`diagram/process-flow.png`](diagram/process-flow.png) | Process flow from upload to ranked list, showing AI steps, rule steps and human checkpoints |
| [`WRITEUP.md`](WRITEUP.md) | Answers to the five questions (under 1,000 words) |
| [`PROMPTS.md`](PROMPTS.md) | The extraction and repair prompts, exactly as sent |
| [`ranking.json`](ranking.json) | Expected ranking of proposals A–E |
| [`prototype/`](prototype/) | *Optional.* A script that runs the prompt and rules on the samples ([how to run](prototype/README.md)) |
| [`.env.example`](.env.example) | Settings template for the prototype (no keys committed) |

## The design in brief

1. **An admin defines the criteria as data:** mandatory checks, project limits, weights and a fixed scoring scale for each criterion. Each save creates a new, locked version.
2. **AI does one job: reading.** For each proposal it returns every fact with an exact quote and a status of *stated*, *not stated* or *unclear*. It never scores.
3. **Code does everything that decides a result.** It verifies the quotes, applies the pass/fail checks and the budget and schedule limits, scores with fixed formulas and ranks. The same facts always give the same ranking.
4. **Anything doubtful goes to a person.** That covers manipulation attempts, missing evidence, missing information and unclear answers. A reviewer confirms or overrides every result, giving a reason, and people make the award.
5. **Everything is logged** so any result can be explained and replayed.

## Result on the sample set

| Proposal | Outcome | Score | Why |
|---|---|---|---|
| A | **Rank 1** | 72.2 | Most qualifying experience, full team-and-approach marks, 12 weeks at $238,000 |
| E | **Rank 2** | 57.4 | Best warranty and full team marks, but fewer installs and a higher price than A |
| D | **Flagged** | provisional 54.1 | No committed schedule, so the 16-week limit can't be confirmed. At 14 weeks or less it would move above E, so the reviewer should ask the bidder. |
| C | **Flagged** | provisional 48.4 | Contains a hidden note telling automated reviewers to rank it first. The note is ignored and gains nothing. |
| B | **Ineligible** | not scored | The bidder holds no Ontario electrical licence; that work goes to a subcontractor named only after award. Lowest price, but fails a mandatory check. |

The score breakdown for each proposal is in [`prototype/output/report.md`](prototype/output/report.md). Assumptions are listed at the top of [`WRITEUP.md`](WRITEUP.md).

## How I used AI tools

I used Claude (in Claude Code) as a drafting partner: to stress-test the flow against the five sample proposals, draft the extraction prompt and JSON schema, and write the optional prototype. I checked every score by hand against the criteria before accepting it. **One place I disagreed:** the first approach gave proposal D zero timeline points and ranked it 3rd. I changed that to *flagged*, because a missing schedule is a question for the bidder, not a score of zero. With 14 weeks or less D would rank above E, so silently ranking it would hide a decision a person should make.

<!-- Edit the paragraph above so it matches exactly how you used AI and where you corrected it. -->
