"""Entity resolution step 4: an LLM judge for the pairs scoring left unsure.

For each pair in the review queue the model sees both records (names, websites,
countries, funds, descriptions) and answers match / no_match / unsure with a
confidence and quotes from the records. The answer is checked, not trusted:

  * match    needs high confidence, a substantive quote from EACH record, and
             no website, country or same-page conflict -> linked / merged
  * no_match needs high or medium confidence and a substantive quote -> recorded;
             a mention whose candidates are all rejected becomes a new actor
  * anything else stays unsure, now with the model's reasoning, for a person

A quote is verified only if it appears in one of that record's values (never in
the prompt's own labels such as "none known"), and substantive only if it comes
from a description or a website: names, countries, types and funds are shared
by different companies, so quoting them proves nothing. Decisions are
stored with method 'llm': later score runs leave them alone and a person's
decision replaces them (resolution.METHOD_RANK). The model never sees the web;
looking things up is step 6.
"""

from __future__ import annotations

from typing import Any

import psycopg

from src.core.structured_llm import Classifier, quote_found

from .loader import link_mention
from .resolution import (
    Profile, _retire_open_decisions, _survivor, actor_profile, canonical, finish_rejected_mention, merge_actors,
    mention_profile, record_decision,
)

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["match", "no_match", "unsure"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "record": {"type": "string", "enum": ["A", "B"]}, 
                    "quote": {"type": "string"}
                },
                "required": ["record", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decision", "confidence", "reason", "evidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You decide whether two records describe the same organization, for a database of the
Argentine deep tech ecosystem. Records come from investment funds' portfolio pages.

Use only the two records. Never guess.
- The same or a similar name is NOT enough: different companies share names
  (e.g. "Kigui", retail AI software, and "Kigüi", food logistics in Mexico).
- Different funds describe the same company in different words. Compare what the
  company does, its website, country and type, not the wording.
- Different websites, or two records listed separately on the same page, are strong
  signs of two different organizations.
- A record with little information means "unsure", not "match".

Answer:
- decision: match, no_match or unsure.
- confidence: high only when the records leave no real doubt.
- reason: one or two sentences.
- evidence: the exact words from the records that support your decision, each with the
  record it comes from (A or B). Copy them exactly; do not paraphrase."""


def record_lines(p: Profile) -> list[str]:
    """What the model sees of one side, one fact per line (also what its quotes are checked against)."""
    kind = ("a single record, from one fund's page" if p.kind == "mention"
            else f"an organization known from {p.mentions} record(s)")
    lines = [f"Kind: {kind}", f"Name: {p.display}"]
    if p.aliases:
        lines.append(f"Also written as: {', '.join(p.aliases)}")
    lines.append(f"Websites: {', '.join(sorted(p.domains)) or 'none known'}")
    lines.append(f"Country: {', '.join(sorted(p.countries)) or 'not stated'}")
    if p.types:
        lines.append(f"Type: {', '.join(sorted(p.types))}")
    if p.funds:
        lines.append(f"Listed by: {', '.join(sorted(p.funds))}")
    if p.descriptions:
        lines.append("Descriptions:")
        lines += [f'- "{d}"' for d in p.descriptions]
    else:
        lines.append("Descriptions: none")
    return lines


def build_messages(left: Profile, right: Profile, automatic: str) -> list[dict[str, str]]:
    user = ("Record A\n" + "\n".join(record_lines(left)) + "\n\nRecord B\n" + "\n".join(record_lines(right))
            + f"\n\nAutomatic name comparison: {automatic or 'none'}")
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def evidence_texts(p: Profile) -> tuple[list[str], list[str], list[str]]:
    """(every value of the record, the substantive ones: descriptions and websites, its names)."""
    substantive, names = [*p.descriptions, *sorted(p.domains)], [p.display, *p.aliases]
    return [*names, *sorted(p.countries), *sorted(p.types), *sorted(p.funds), *substantive], substantive, names


def interpret(answer: dict[str, Any], left: Profile, right: Profile,
              features: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
    """(decision to record, verified quotes, reason) from the model's answer and the pair's features."""
    texts = {"A": evidence_texts(left), "B": evidence_texts(right)}
    verified, invented = [], []
    for item in answer.get("evidence") or []:
        record, quote = (item.get("record"), item.get("quote")) if isinstance(item, dict) else (None, None)
        if record in texts and isinstance(quote, str) and quote_found(quote, texts[record][0]):
            _, substantive_texts, names = texts[record]
            # 'Kilimo' is in kilimo.com too, but quoting a name proves nothing
            verified.append({"record": record, "quote": quote,
                             "substantive": quote_found(quote, substantive_texts) and not quote_found(quote, names)})
        else:
            invented.append(item)
    substantive = {q["record"] for q in verified if q["substantive"]}

    said, confidence = answer.get("decision"), answer.get("confidence")
    reason = f"LLM ({said}, {confidence} confidence): {(answer.get('reason') or '').strip()}"
    if invented:
        reason += f" [{len(invented)} quote(s) not found in the records]"
    against = [name for name, present in (
        ("different websites", features.get("domain_conflict")),
        ("listed on the same page", features.get("same_page")),
        ("different countries", features.get("country") == "conflict"),
    ) if present]

    if said == "match":
        both_sides = substantive == {"A", "B"}
        if confidence == "high" and both_sides and not against:
            return "match", verified, reason
        missing = ([] if confidence == "high" else ["high confidence"]) + \
                  ([] if both_sides else ["a quote from each record's description or website"])
        why = "; ".join(filter(None, [f"needs {' and '.join(missing)}" if missing else "", ", ".join(against)]))
        return "unsure", verified, f"{reason} [not applied: {why}]"
    if said == "no_match":
        if confidence in ("high", "medium") and substantive:
            return "no_match", verified, reason
        return "unsure", verified, (f"{reason} [not applied: needs medium or high confidence and a quote "
                                    f"from a description or website]")
    return "unsure", verified, reason


def pending_pairs(conn: psycopg.Connection, limit: int | None = None) -> list[int]:
    """Review-queue decisions not yet seen by the judge (or a person), best scores first."""
    rows = conn.execute(
        """SELECT id FROM match_decisions
           WHERE is_current AND decision = 'unsure' AND method IN ('rule', 'score')
           ORDER BY score DESC NULLS LAST, id""").fetchall()
    ids = [r[0] for r in rows]
    return ids[:limit] if limit is not None else ids


def judge_decision(conn: psycopg.Connection, decision_id: int, classify: Classifier,
                   model: str) -> tuple[str, str]:
    """Ask the model about one review pair and carry out a checked answer.
    Returns (what happened, the recorded reason)."""
    mention_id, actor_id, candidate_id, score, features, reason, is_current, method = conn.execute(
        """SELECT mention_id, actor_id, candidate_actor_id, score, features, reason, is_current, method
           FROM match_decisions WHERE id = %s""", (decision_id,)).fetchone()
    if not is_current or method not in ("rule", "score"):
        return "skipped", ""            # decided since it was listed (a person, a merge, another run)
    features = features or {}
    candidate = canonical(conn, candidate_id)
    if mention_id is not None:
        if conn.execute("SELECT actor_id FROM mentions WHERE id = %s", (mention_id,)).fetchone()[0] is not None:
            return "skipped", ""
        left = mention_profile(conn, mention_id)
    else:
        if canonical(conn, actor_id) == candidate:
            return "skipped", ""
        left = actor_profile(conn, canonical(conn, actor_id))
    right = actor_profile(conn, candidate)

    answer = classify(build_messages(left, right, reason or ""), JUDGE_SCHEMA)
    decision, evidence, llm_reason = interpret(answer, left, right, features)
    record_decision(conn, mention_id=mention_id, actor_id=actor_id, candidate_id=candidate_id, decision=decision,
                    method="llm", score=score, features={**features, "llm_answer": answer}, reason=llm_reason,
                    decided_by=model, evidence=evidence)

    if decision == "match":
        if mention_id is not None:
            link_mention(conn, mention_id, candidate, "llm", score)
            _retire_open_decisions(conn, mention_id)
            return "linked", llm_reason
        keep, drop = _survivor(conn, canonical(conn, actor_id), candidate)
        merge_actors(conn, keep, drop)
        return "merged", llm_reason
    if decision == "no_match":
        if mention_id is not None and finish_rejected_mention(conn, mention_id):
            return "new actor", llm_reason
        return "rejected", llm_reason
    return "unsure", llm_reason
