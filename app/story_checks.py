"""Deep-analysis checks of a story against its code and its tests.

* **Implementation**: do the code changes in the linked GitHub PRs implement
  each acceptance criterion? (``app.pr_code``)
* **Test coverage**: do the linked TestRail test cases, their latest results
  and attached evidence cover each acceptance criterion? (``app.testrail``)

Each check has its prompt section and response format for the LLM, a
rule-based fallback (keyword matching, clearly labelled as such) and a merge
that prefers the LLM answer when it is usable.
"""

from __future__ import annotations

import re
from typing import Any

from app.pr_code import code_prompt_payload

IMPLEMENTATION_STATUSES = ("implemented", "partial", "not_found", "unclear")
COVERAGE_STATUSES = ("covered", "partial", "not_covered")

IMPLEMENTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "assessment": {"type": "string"},
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "status": {"type": "string", "enum": list(IMPLEMENTATION_STATUSES)},
                    "evidence": {"type": "string"},
                },
                "required": ["criterion", "status", "evidence"],
            },
        },
        "unrelated_changes": {"type": "array", "items": {"type": "string"}},
        "missing_tests": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "assessment", "criteria", "unrelated_changes", "missing_tests"],
}
TEST_COVERAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "assessment": {"type": "string"},
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "status": {"type": "string", "enum": list(COVERAGE_STATUSES)},
                    "test_cases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["criterion", "status", "test_cases"],
            },
        },
        "tests_without_criteria": {"type": "array", "items": {"type": "string"}},
        "evidence_issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "assessment", "criteria", "tests_without_criteria", "evidence_issues"],
}

CODE_PROMPT = """
CODE CHECK: the input has "code_changes": the diffs of the pull requests linked to the story
(files marked is_test are tests; some files may be truncated or not shown). For EACH acceptance
criterion decide whether the code implements it: implemented, partial, not_found, or unclear
(e.g. the relevant code is not in the diff). Give evidence: the file and what the change does.
List changes unrelated to the story, and criteria with no test changes. Return:
 "implementation": {"score": int, "assessment": str,
   "criteria": [{"criterion": str, "status": "implemented"|"partial"|"not_found"|"unclear", "evidence": str}],
   "unrelated_changes": [str], "missing_tests": [str]}"""

TESTS_PROMPT = """
TEST CHECK: the input has "test_cases": TestRail cases linked to the story, with steps, expected
results, their latest result (status, comment, tester) and attachment names (evidence). For EACH
acceptance criterion decide whether the tests cover it: covered, partial or not_covered, naming the
case ids (e.g. "C123"). List test cases that match no criterion, and evidence problems: failed,
blocked or untested cases, passes without evidence, evidence that does not show the expected result.
Return:
 "test_coverage": {"score": int, "assessment": str,
   "criteria": [{"criterion": str, "status": "covered"|"partial"|"not_covered", "test_cases": [str]}],
   "tests_without_criteria": [str], "evidence_issues": [str]}"""

_STOP = set(
    "given when then should with that this from into have will must able user users story shall which there their "
    "about after before while where every other also only each both".split()
)


def criteria_lines(text: str) -> list[str]:
    lines = [line.strip(" -*•\t") for line in (text or "").splitlines()]
    return [line for line in lines if len(line.split()) >= 3]


def _terms(text: str) -> set[str]:
    """Words of 4+ letters; code identifiers are split (saved_card, requireCvv -> saved, card, require, cvv)."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "").replace("_", " ")
    return {word for word in re.findall(r"[a-z][a-z0-9]{3,}", text.lower()) if word not in _STOP}


def tests_payload(tests: dict[str, Any]) -> list[dict[str, Any]]:
    clip = lambda text, n: text if len(text or "") <= n else text[:n] + " …[truncated]"
    return [
        {
            "case": f"C{case['id']}",
            "title": case["title"],
            "linked_by": case["linked_by"],
            "preconditions": clip(case["preconditions"], 800),
            "steps": [{"step": clip(s["step"], 500), "expected": clip(s["expected"], 500)} for s in case["steps"][:20]],
            "case_attachments": case["attachments"][:20],
            "latest_result": None if not case["latest"] else {
                "status": case["latest"].get("status"),
                "run": case["latest"].get("run"),
                "comment": clip(case["latest"].get("comment") or "", 800),
                "attachments": case["latest"].get("attachments", [])[:20],
            },
        }
        for case in tests["cases"]
    ]


def extend_prompt(system: str, payload: dict[str, Any], code: list[dict[str, Any]] | None, tests: dict[str, Any] | None) -> str:
    if code is not None:
        system += CODE_PROMPT
        payload["code_changes"] = code_prompt_payload(code)
    if tests is not None:
        system += TESTS_PROMPT
        payload["test_cases"] = tests_payload(tests)
    return system


def extend_schema(schema: dict[str, Any], code: bool, tests: bool) -> dict[str, Any]:
    properties, required = dict(schema["properties"]), list(schema["required"])
    if code:
        properties["implementation"], required = IMPLEMENTATION_SCHEMA, required + ["implementation"]
    if tests:
        properties["test_coverage"], required = TEST_COVERAGE_SCHEMA, required + ["test_coverage"]
    return {**schema, "properties": properties, "required": required}


# ---------------------------------------------------------------------------
# Rule-based fallbacks (keyword matching; the page says so)
# ---------------------------------------------------------------------------


def heuristic_implementation(criteria_text: str, code: list[dict[str, Any]]) -> dict[str, Any]:
    criteria = criteria_lines(criteria_text)
    added = " ".join(
        line[1:] for change in code for f in change["files"] for line in f["patch"].splitlines() if line.startswith("+")
    )
    added_terms = _terms(added + " " + " ".join(f["filename"] for change in code for f in change["files"]))
    rows, hits = [], 0
    for criterion in criteria:
        terms = _terms(criterion)
        found = sorted(terms & added_terms)
        share = len(found) / len(terms) if terms else 0
        status = "partial" if share >= 0.5 else "unclear" if found else "not_found"
        hits += share >= 0.5
        rows.append({"criterion": criterion, "status": status,
                     "evidence": f"Words found in the added code: {', '.join(found[:8])}" if found else "None of its key words appear in the added code."})
    errors = [f"{c['repository']}#{c['number']}: {c['error']}" for c in code if c["error"]]
    tests_changed = any(f["is_test"] for change in code for f in change["files"])
    return {
        "score": round(100 * hits / len(criteria)) if criteria else None,
        "assessment": "Rule-based keyword match between the acceptance criteria and the added code (an LLM review reads the code itself)."
        + (f" Problems: {'; '.join(errors)}." if errors else ""),
        "criteria": rows,
        "unrelated_changes": [],
        "missing_tests": [] if tests_changed or not code else ["No test files were changed in the linked pull requests."],
    }


def heuristic_test_coverage(criteria_text: str, tests: dict[str, Any]) -> dict[str, Any]:
    criteria = criteria_lines(criteria_text)
    cases = tests["cases"]
    case_terms = {
        case["id"]: _terms(" ".join([case["title"] or "", case["preconditions"]] + [s["step"] + " " + s["expected"] for s in case["steps"]]))
        for case in cases
    }
    rows, used, covered = [], set(), 0
    for criterion in criteria:
        terms = _terms(criterion)
        matches = []
        for case in cases:
            share = len(terms & case_terms[case["id"]]) / len(terms) if terms else 0
            if share >= 0.3:
                matches.append((share, case["id"]))
        matches.sort(reverse=True)
        ids = [f"C{case_id}" for _, case_id in matches[:5]]
        used |= {case_id for _, case_id in matches}
        status = "covered" if matches and matches[0][0] >= 0.5 else "partial" if matches else "not_covered"
        covered += status == "covered"
        rows.append({"criterion": criterion, "status": status, "test_cases": ids})
    issues = []
    for case in cases:
        latest = case["latest"]
        if not latest:
            issues.append(f"C{case['id']} has no result in the recent test runs.")
        elif str(latest.get("status")).lower() in ("failed", "blocked", "retest", "untested"):
            issues.append(f"C{case['id']} latest result: {latest.get('status')} ({latest.get('run')}).")
        elif not (latest.get("attachments") or case["attachments"] or (latest.get("comment") or "").strip()):
            issues.append(f"C{case['id']} passed without evidence (no attachment or comment).")
    issues += tests.get("errors", [])
    return {
        "score": round(100 * covered / len(criteria)) if criteria else None,
        "assessment": "Rule-based keyword match between the acceptance criteria and the test case steps (an LLM review compares meaning).",
        "criteria": rows,
        "tests_without_criteria": [f"C{case['id']} {case['title']}" for case in cases if case["id"] not in used],
        "evidence_issues": issues,
    }


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []


def merge_check(llm_value: Any, fallback: dict[str, Any], statuses: tuple[str, ...], list_keys: tuple[str, ...], case_key: str | None) -> tuple[dict[str, Any], bool]:
    """The LLM's check if usable (score + assessment + criteria), else the fallback."""
    if not isinstance(llm_value, dict) or not isinstance(llm_value.get("assessment"), str) or not llm_value["assessment"].strip():
        return fallback, False
    try:
        score = max(0, min(100, int(round(float(llm_value.get("score"))))))
    except (TypeError, ValueError):
        score = fallback["score"]
    criteria = []
    for row in llm_value.get("criteria") or []:
        if not isinstance(row, dict) or not row.get("criterion"):
            continue
        entry = {"criterion": str(row["criterion"]), "status": row.get("status") if row.get("status") in statuses else statuses[-1]}
        if case_key:
            entry[case_key] = _strings(row.get(case_key))
        else:
            entry["evidence"] = str(row.get("evidence") or "")
        criteria.append(entry)
    merged = {"score": score, "assessment": llm_value["assessment"].strip(), "criteria": criteria or fallback["criteria"]}
    for key in list_keys:
        merged[key] = _strings(llm_value.get(key)) or ([] if key != "evidence_issues" else fallback.get(key, []))
    return merged, True


def merge_implementation(llm: dict[str, Any] | None, fallback: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    return merge_check((llm or {}).get("implementation"), fallback, IMPLEMENTATION_STATUSES, ("unrelated_changes", "missing_tests"), None)


def merge_test_coverage(llm: dict[str, Any] | None, fallback: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    return merge_check((llm or {}).get("test_coverage"), fallback, COVERAGE_STATUSES, ("tests_without_criteria", "evidence_issues"), "test_cases")
