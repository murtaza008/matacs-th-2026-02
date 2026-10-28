"""Proposal triage prototype for MATACS-TH-2026-02.

Runs the flow shown in diagram/process-flow.png:
  admin criteria -> extract facts (Claude, or offline fixtures) -> injection scan
  -> evidence check -> mandatory gate -> project limits -> score -> rank -> outputs + audit log

  python prototype/triage.py --offline           hand-labelled fixtures, no API key needed
  python prototype/triage.py                     calls Claude on prototype/samples/A.txt ... E.txt
  python prototype/triage.py --offline --check   also compares the result with ../ranking.json
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_repair_message, build_user_message

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROPOSALS = ["A", "B", "C", "D", "E"]

# Phrases that speak to the evaluator instead of describing the work. A match never
# changes a score; it only holds the proposal for a person to look at.
INJECTION_PATTERNS = [
    r"\b(note|instruction)s? (to|for) (the )?(automated|ai|llm|model|review)",
    r"\bautomated review",
    r"\bignore (all |any |the )?(previous|above|prior|other)",
    r"\b(rank|score|rate|place) (it|this|us|this proposal) (first|highest|top)",
    r"\b(full|maximum|top) (marks|score|points)",
    r"\bpre-?approved\b",
    r"\byou are (an? )?(ai|assistant|language model)",
]

WORD_NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}

VALUE_TYPES = {"number": "number", "boolean": "boolean", "text": "string"}


class ExtractionError(Exception):
    pass


# ---------- setup ----------

def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def rule_fields(rule):
    if rule["type"] == "linear":
        return [rule["field"]]
    if rule["type"] == "average":
        return [f for part in rule["parts"] for f in rule_fields(part)]
    if rule["type"] == "checklist":
        return [item["field"] for item in rule["items"]]
    raise ValueError(f"Unknown rule type {rule['type']}")


def load_config(path):
    """The same checks the admin screen would run before allowing a save."""
    config = json.loads(path.read_text(encoding="utf-8"))
    total = sum(c["weight"] for c in config["weighted"])
    if total != 100:
        sys.exit(f"Criteria config error: weights add up to {total}, not 100.")
    known = {f["key"] for f in config["fields"]}
    used = [chk["field"] for m in config["mandatory"] for chk in m["checks"]]
    used += [lim["field"] for lim in config["limits"]]
    for crit in config["weighted"]:
        used += rule_fields(crit["rule"])
    undefined = sorted(set(used) - known)
    if undefined:
        sys.exit(f"Criteria config error: rules use undefined fields {undefined}.")
    return config


# ---------- AI step: extraction ----------

def build_schema(config):
    """JSON schema generated from the admin's field list and enforced by the API."""
    field_props = {}
    for f in config["fields"]:
        field_props[f["key"]] = {
            "type": "object",
            "properties": {
                "value": {"anyOf": [{"type": VALUE_TYPES[f["type"]]}, {"type": "null"}]},
                "status": {"type": "string", "enum": ["stated", "not_stated", "unclear"]},
                "evidence": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["value", "status", "evidence", "note"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": field_props,
                "required": list(field_props),
                "additionalProperties": False,
            },
            "suspicious_content": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["fields", "suspicious_content"],
        "additionalProperties": False,
    }


def extract_live(config, pid, text, sha, model):
    """One Claude call per proposal, plus at most one repair call. Results are cached by
    file hash + prompt version + model + criteria version, so a re-run reuses them."""
    cache_file = HERE / "cache" / f"{pid}_{sha[:12]}_{PROMPT_VERSION}_{model}_v{config['version']}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8")), {"cached": True, "model": model}

    import anthropic  # only needed for live runs

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": build_user_message(config, pid, f"{pid}.txt", sha, text)}]
    meta = {"cached": False, "model": model, "calls": 0}
    for _ in range(2):
        try:
            response = client.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=messages,
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": build_schema(config)},
                },
                # If the main model declines, the API retries on a suitable fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("could not reach the API") from exc
        meta["calls"] += 1
        meta["served_by"] = response.model
        if response.stop_reason in ("refusal", "max_tokens"):
            raise ExtractionError(f"model stopped early ({response.stop_reason})")
        raw = next(block.text for block in response.content if block.type == "text")
        extraction = json.loads(raw)
        errors = verify_evidence(config, extraction, text)
        if not errors:
            break
        messages += [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": build_repair_message(errors)},
        ]
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(extraction, indent=2), encoding="utf-8")
    return extraction, meta


# ---------- rule steps ----------

def normalise(s):
    s = s.lower()
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def numbers_in(s):
    found = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(m\b|million\b|k\b)?", s.lower()):
        multiplier = {"m": 1e6, "million": 1e6, "k": 1e3}.get(m.group(2) or "", 1)
        found.append(float(m.group(1).replace(",", "")) * multiplier)
    found += [float(WORD_NUMBERS[w]) for w in re.findall(r"[a-z]+", s.lower()) if w in WORD_NUMBERS]
    return found


def scan_for_injection(text):
    hits = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if any(re.search(p, sentence, re.IGNORECASE) for p in INJECTION_PATTERNS):
            hits.append(sentence.strip())
    return hits


def verify_evidence(config, extraction, text):
    """Every reported value must be backed by a quote that is really in the proposal."""
    errors = []
    source = normalise(text)
    types = {f["key"]: f["type"] for f in config["fields"]}
    for key, item in extraction["fields"].items():
        if item["value"] is None:
            continue
        quote = normalise(item["evidence"])
        if not quote:
            errors.append(f"{key}: value given without a quote")
        elif quote not in source:
            errors.append(f"{key}: quote not found in the proposal")
        elif types[key] == "number" and item["value"] not in numbers_in(item["evidence"]):
            errors.append(f"{key}: {item['value']} does not appear in its quote")
    return errors


def check(op, value, target=None):
    if op == "is_true":
        return value is True
    if op == ">=":
        return value >= target
    if op == "<=":
        return value <= target
    if op == ">":
        return value > target
    raise ValueError(f"Unknown operator {op}")


def mandatory_gate(config, fields):
    """Pass/fail per mandatory criterion. 'Not stated' is a fail because the admin
    requires it to be stated in the proposal; 'unclear' goes to a person."""
    results = []
    for crit in config["mandatory"]:
        outcome, details = "pass", []
        for chk in crit["checks"]:
            item = fields[chk["field"]]
            if item["status"] == "unclear":
                outcome = "fail" if outcome == "fail" else "unclear"
                details.append(f"{chk['field']} unclear: {item['note']}")
            elif item["value"] is None:
                outcome = "fail"
                details.append(f"{chk['field']} not stated")
            elif not check(chk["op"], item["value"], chk.get("value")):
                outcome = "fail"
                details.append(item["note"] or f"{chk['field']} = {item['value']}")
        results.append({"id": crit["id"], "name": crit["name"], "result": outcome, "detail": "; ".join(details)})
    return results


def limit_checks(config, fields):
    results = []
    for lim in config["limits"]:
        item = fields[lim["field"]]
        if item["value"] is None or item["status"] == "unclear":
            outcome = "missing"
        else:
            outcome = "pass" if check(lim["op"], item["value"], lim["value"]) else "fail"
        results.append({"id": lim["id"], "name": lim["name"], "result": outcome,
                        "value": item["value"], "detail": item["note"]})
    return results


def score_rule(rule, fields):
    """Points from 0 to 100 for one rule. A missing value scores 0 and says so."""
    if rule["type"] == "linear":
        value = fields[rule["field"]]["value"]
        if value is None:
            return 0.0, f"{rule['field']} not stated"
        share = (value - rule["zero_at"]) / (rule["full_at"] - rule["zero_at"])
        return 100 * min(max(share, 0.0), 1.0), f"{rule['field']} = {value}"
    if rule["type"] == "average":
        parts = [score_rule(part, fields) for part in rule["parts"]]
        return sum(p for p, _ in parts) / len(parts), "; ".join(basis for _, basis in parts)
    if rule["type"] == "checklist":
        met, missed = [], []
        for item in rule["items"]:
            value = fields[item["field"]]["value"]
            ok = value is not None and check(item["op"], value, item.get("value"))
            (met if ok else missed).append(item["field"])
        basis = f"{len(met)}/{len(rule['items'])} met" + (f"; missing {', '.join(missed)}" if missed else "")
        return 100 * len(met) / len(rule["items"]), basis
    raise ValueError(f"Unknown rule type {rule['type']}")


def score(config, fields):
    rows, total = [], 0.0
    for crit in config["weighted"]:
        points, basis = score_rule(crit["rule"], fields)
        total += points * crit["weight"] / 100
        rows.append({"id": crit["id"], "name": crit["name"], "weight": crit["weight"],
                     "points": round(points, 1), "contribution": round(points * crit["weight"] / 100, 2),
                     "basis": basis})
    return rows, round(total, 1)


# ---------- pipeline ----------

def process(config, pid, offline, model):
    text_file = HERE / "samples" / f"{pid}.txt"
    text = text_file.read_text(encoding="utf-8") if text_file.exists() else None
    record = {
        "proposal": pid,
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        "flags": [],
        "to_verify": [],
    }

    if offline:
        extraction = json.loads((HERE / "fixtures" / f"{pid}.json").read_text(encoding="utf-8"))
        record["extraction"] = {"source": f"fixtures/{pid}.json (hand-labelled)"}
    else:
        if text is None:
            sys.exit(f"Missing {text_file}. See prototype/README.md.")
        try:
            extraction, meta = extract_live(config, pid, text, record["source_sha256"], model)
        except ExtractionError as exc:
            record.update(status="flagged", score=None, flags=[f"Extraction failed: {exc}"])
            return record
        record["extraction"] = {"source": "claude", "prompt_version": PROMPT_VERSION, **meta}
    record["extraction"]["output"] = extraction
    fields = extraction["fields"]

    suspicious = scan_for_injection(text) if text else []
    suspicious += [s for s in extraction["suspicious_content"] if s not in suspicious]
    if suspicious:
        record["flags"].append("Possible manipulation: text addressed to the evaluator was found and ignored")
        record["suspicious_content"] = suspicious

    if text:
        for err in verify_evidence(config, extraction, text):
            record["flags"].append(f"Evidence check failed: {err}")
    else:
        record["evidence_check"] = "skipped (offline fixtures carry no source text)"

    record["mandatory"] = mandatory_gate(config, fields)
    record["limits"] = limit_checks(config, fields)
    for g in record["mandatory"]:
        if g["result"] == "unclear":
            record["flags"].append(f"Mandatory check unclear: {g['name']} ({g['detail']})")
    for lim in record["limits"]:
        if lim["result"] == "missing":
            record["flags"].append(f"Cannot confirm '{lim['name']}': {lim['detail'] or 'not stated'}")
    for f in config["fields"]:
        item = fields[f["key"]]
        if item["status"] == "unclear" and item["value"] is not None:
            record["to_verify"].append(f"{f['key']} = {item['value']}: {item['note']}")

    if any(r["result"] == "fail" for r in record["mandatory"] + record["limits"]):
        record.update(status="ineligible", score=None)
        return record
    record["criteria"], record["score"] = score(config, fields)
    record["status"] = "flagged" if record["flags"] else "ranked"
    return record


def rank(records, config):
    ranked = [r for r in records if r["status"] == "ranked"]

    def sort_key(r):
        points = {c["id"]: c["points"] for c in r["criteria"]}
        return (-r["score"], *[-points[cid] for cid in config["tie_break"]], r["proposal"])

    for position, r in enumerate(sorted(ranked, key=sort_key), start=1):
        r["rank"] = position
    for r in records:
        r.setdefault("rank", None)


def reason(r):
    if r["status"] == "ineligible":
        failed = [c for c in r["mandatory"] + r["limits"] if c["result"] == "fail"]
        return "Not scored. Fails " + "; ".join(f"{c['name']} ({c['detail'].rstrip('.')})" for c in failed) + "."
    if r["status"] == "flagged":
        text = "Held for human review: " + " | ".join(f.rstrip(".") for f in r["flags"]) + "."
        if r.get("score") is not None:
            text += f" Provisional score {r['score']} (not ranked until a reviewer clears it)."
        return text
    best = max(r["criteria"], key=lambda c: c["points"])
    worst = min(r["criteria"], key=lambda c: c["points"])
    return (f"Passes all mandatory checks and project limits. {r['score']}/100, strongest on "
            f"{best['name'].lower()} ({best['points']}/100), weakest on {worst['name'].lower()} ({worst['points']}/100).")


def ordered(records):
    status_order = {"ranked": 0, "flagged": 1, "ineligible": 2}
    return sorted(records, key=lambda r: (status_order[r["status"]], r["rank"] or 0,
                                          -(r["score"] or 0), r["proposal"]))


def write_outputs(records, run, config):
    out = HERE / "output"
    out.mkdir(exist_ok=True)

    ranking = [{"proposal": r["proposal"], "rank": r["rank"], "status": r["status"],
                "score": r["score"] if r["status"] == "ranked" else None, "reason": r["reason"]}
               for r in ordered(records)]
    (out / "ranking.json").write_text(json.dumps(ranking, indent=2) + "\n", encoding="utf-8")

    with (out / "audit-log.jsonl").open("a", encoding="utf-8") as log:
        for r in records:
            log.write(json.dumps({"run": run, **r}) + "\n")

    lines = [
        f"# Triage report: {config['category']} (criteria v{config['version']})",
        "",
        f"Run {run['run_id']} · mode: {run['mode']} · prompt {run['prompt_version']} · "
        f"criteria sha256 {run['criteria_sha256'][:12]}",
        "",
        "| Proposal | Status | Rank | Score | Reason |",
        "|---|---|---|---|---|",
    ]
    for r in ordered(records):
        shown = r["score"] if r["score"] is not None else "-"
        if r["status"] == "flagged" and r["score"] is not None:
            shown = f"({r['score']} provisional)"
        lines.append(f"| {r['proposal']} | {r['status']} | {r['rank'] or '-'} | {shown} | {r['reason']} |")
    for r in ordered(records):
        lines += ["", f"## Proposal {r['proposal']}: {r['status']}", ""]
        for c in r.get("mandatory", []):
            lines.append(f"- Mandatory · {c['name']}: **{c['result']}**" + (f" ({c['detail']})" if c["detail"] else ""))
        for c in r.get("limits", []):
            lines.append(f"- Limit · {c['name']}: **{c['result']}** (value: {c['value']})")
        if r.get("criteria"):
            lines += ["", "| Criterion | Weight | Basis | Points /100 | Contribution |", "|---|---|---|---|---|"]
            lines += [f"| {c['name']} | {c['weight']}% | {c['basis']} | {c['points']} | {c['contribution']} |"
                      for c in r["criteria"]]
            lines += [f"| **Total** | 100% | | | **{r['score']}** |", ""]
        for flag in r["flags"]:
            lines.append(f"- Flag: {flag}")
        for note in r["to_verify"]:
            lines.append(f"- To verify (scored as stated): {note}")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compare_with_expected(records):
    expected = {e["proposal"]: e for e in json.loads((ROOT / "ranking.json").read_text(encoding="utf-8"))}
    problems = []
    for r in records:
        e = expected[r["proposal"]]
        got = {"rank": r["rank"], "status": r["status"], "score": r["score"] if r["status"] == "ranked" else None}
        for key, value in got.items():
            if e[key] != value:
                problems.append(f"{r['proposal']}.{key}: expected {e[key]}, got {value}")
    print("\nCheck against ranking.json: " + ("OK" if not problems else "MISMATCH"))
    for p in problems:
        print("  " + p)
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="use hand-labelled fixtures instead of calling Claude")
    parser.add_argument("--check", action="store_true", help="compare the result with ../ranking.json")
    args = parser.parse_args()

    load_env()
    config_path = HERE / "criteria.json"
    config = load_config(config_path)
    model = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
    now = datetime.datetime.now(datetime.timezone.utc)
    run = {
        "run_id": now.strftime("%Y%m%dT%H%M%SZ"),
        "mode": "offline fixtures" if args.offline else f"live ({model})",
        "category": config["category"],
        "criteria_version": config["version"],
        "criteria_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "prompt_version": PROMPT_VERSION,
    }

    records = [process(config, pid, args.offline, model) for pid in PROPOSALS]
    rank(records, config)
    for r in records:
        r["reason"] = reason(r)
    write_outputs(records, run, config)

    print(f"{'Proposal':<9}{'Status':<12}{'Rank':<6}{'Score':<8}")
    for r in ordered(records):
        print(f"{r['proposal']:<9}{r['status']:<12}{str(r['rank'] or '-'):<6}{str(r['score'] if r['score'] is not None else '-'):<8}")
    print("\nWritten: prototype/output/ranking.json, report.md, audit-log.jsonl")
    if args.check:
        sys.exit(compare_with_expected(records))


if __name__ == "__main__":
    main()
