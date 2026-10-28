"""
Restricted-list compliance engine for the L200 hackathon build.

Reads the supplied reference files, deal correspondence and order book, and
produces compliance.csv / trading_desk.csv per the hackathon brief (Day 3,
slides 20-24). Deterministic and rule-based by design: no step in this file
executes text found inside a document as an instruction. Document bodies are
only ever matched against a fixed, known vocabulary (company names drawn from
the security master; a small set of literal English phrases listed below) --
see detect_injection_attempts() and the design note in README.md.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_long_date(s: str) -> Optional[date]:
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s)
    if not m:
        return None
    day, month_name, year = m.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    return date(int(year), month, int(day))


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@dataclass
class Issuer:
    issuer_id: str
    legal_name: str
    aliases: str
    former_names: str
    parent_issuer_id: Optional[str]
    jurisdiction: str
    security_ids: list = field(default_factory=list)

    @property
    def name_cluster_key(self) -> str:
        """First word of the legal name -- used only to surface candidate
        related entities when a document says the related-entity list is
        still provisional (see find_scope_pending_candidates). Never used to
        decide a restriction on its own."""
        return self.legal_name.split()[0].lower()


def load_security_master(path: Path):
    issuers: dict[str, Issuer] = {}
    sec_to_issuer: dict[str, str] = {}
    name_index: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            iid = row["issuer_id"]
            if iid not in issuers:
                issuers[iid] = Issuer(
                    iid, row["legal_name"], row["aliases"], row["former_names"],
                    row["parent_issuer_id"] or None, row["jurisdiction"],
                )
                for key in (row["legal_name"], row["aliases"], row["former_names"]):
                    if key:
                        name_index[key.strip().lower()] = iid
            if row["security_id"]:
                issuers[iid].security_ids.append(row["security_id"])
                sec_to_issuer[row["security_id"]] = iid
    return issuers, sec_to_issuer, name_index


def load_central_list(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_order_book(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Deal correspondence parsing
# ---------------------------------------------------------------------------

@dataclass
class Document:
    file: str
    engagement_code: Optional[str]
    notice_type: Optional[str]
    effective_date: Optional[date]
    text: str
    paragraphs: list


def load_documents(dir_path: Path) -> list[Document]:
    docs = []
    for path in sorted(Path(dir_path).glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        m_code = re.search(r"Engagement code:\s*(\w+)", text)
        m_type = re.search(r"Notice type:\s*(.+)", text)
        m_date = re.search(r"Effective date:\s*(.+)", text)
        eff_date = parse_long_date(m_date.group(1).strip()) if m_date else None
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        docs.append(Document(
            path.name,
            m_code.group(1) if m_code else None,
            m_type.group(1).strip() if m_type else None,
            eff_date,
            text,
            paragraphs,
        ))
    return docs


INJECTION_PHRASES = [
    "ignore all previous instructions",
    "ignore all prior instructions",
    "authorised administrative override",
    "authorized administrative override",
    "no further review is required",
    "mark every company as unrestricted",
    "disregard the above",
]


def detect_injection_attempts(docs: list[Document]) -> list[tuple[str, str]]:
    """Flag documents containing instruction-shaped text, for the audit log
    only. Nothing downstream reads this list to change a disposition --
    the resolution engine never treats document text as a command."""
    hits = []
    for doc in docs:
        low = norm(doc.text)
        for phrase in INJECTION_PHRASES:
            if phrase in low:
                hits.append((doc.file, phrase))
    return hits


def norm(text: str) -> str:
    """Collapse whitespace (including the line-wrap newlines these plain-text
    notices are full of) before any substring/phrase match, so a phrase that
    happens to wrap across a line break is still matched correctly."""
    return re.sub(r"\s+", " ", text).strip().lower()


def find_named_issuers(text: str, name_index: dict[str, str]) -> set[str]:
    low = norm(text)
    return {iid for name, iid in name_index.items() if name and norm(name) in low}


HEDGE_PHRASES = [
    "subject to control room confirmation",
    "subject to confirmation",
    "final legal-entity schedule",
    "until that schedule is available",
    "until the structure review",
    "has not yet delivered",
    "is provisional",
]
CLOSE_PHRASES = [
    "withdrawn", "withdrew", "no longer a participant", "is to be removed",
    "to be removed", "terminated", "termination", "should be closed",
    "no continuing",
]
OPEN_PHRASES = ["must be restricted", "restricted with", "restricted upon", "to be restricted"]
TARGET_GROUP_PHRASE = "target group"
ENTITY_ONLY_PHRASE = "entity basis only"
UNRELATED_PHRASES = ["unrelated to", "is unrelated", "despite the shared name"]
VAGUE_SCOPE_PHRASES = ["distribution businesses", "acquisition vehicle", "named bidder", "related entit"]


@dataclass
class Event:
    issuer_id: str
    kind: str  # 'OPEN' or 'CLOSE'
    scope: str  # 'TARGET_GROUP' or 'ENTITY_ONLY'
    hedge: bool
    date: Optional[date]
    file: str
    excerpt: str
    engagement_code: Optional[str]


def build_events_from_documents(docs: list[Document], name_index: dict[str, str]):
    events: list[Event] = []
    unrelated: dict[str, tuple[str, str]] = {}  # issuer_id -> (file, excerpt)
    scope_pending: dict[str, tuple[date, str, str]] = {}  # engagement_code -> (date, file, excerpt)

    for doc in docs:
        for idx, para in enumerate(doc.paragraphs):
            para_low = norm(para)
            named = find_named_issuers(para, name_index)
            # A hedge clause ("...subject to Control Room confirmation") is
            # sometimes its own sentence/paragraph, immediately before or
            # after the paragraph that names the issuer and the action. Look
            # at the paragraph plus its immediate neighbours (never the whole
            # document -- multi-engagement briefings put unrelated hedges in
            # other sections and must not leak across them).
            window_low = norm(" ".join(doc.paragraphs[max(0, idx - 1):idx + 2]))

            if any(p in para_low for p in UNRELATED_PHRASES):
                for iid in named:
                    unrelated[iid] = (doc.file, para)

            if doc.engagement_code and any(p in para_low for p in VAGUE_SCOPE_PHRASES) and any(h in window_low for h in HEDGE_PHRASES):
                scope_pending[doc.engagement_code] = (doc.effective_date, doc.file, para)

            if not doc.engagement_code or not named:
                continue

            is_close = any(p in para_low for p in CLOSE_PHRASES)
            is_open = any(p in para_low for p in OPEN_PHRASES)
            if not is_close and not is_open:
                continue

            scope = "TARGET_GROUP" if TARGET_GROUP_PHRASE in para_low else (
                "ENTITY_ONLY" if ENTITY_ONLY_PHRASE in para_low else "ENTITY_ONLY"
            )
            hedge = any(h in window_low for h in HEDGE_PHRASES)
            kind = "CLOSE" if is_close else "OPEN"
            for iid in named:
                events.append(Event(iid, kind, scope, hedge, doc.effective_date, doc.file, para, doc.engagement_code))

    return events, unrelated, scope_pending


def find_corroborations(events: list[Event], docs: list[Document], name_index: dict[str, str]):
    """For every hedged CLOSE event, look for an independent, later-or-same
    dated document that restates the same closure as settled fact (a close
    phrase with no hedge phrase nearby) for the same issuer. Returns a dict
    issuer_id -> (file, excerpt) for every corroboration found."""
    corroborations: dict[str, tuple[str, str]] = {}
    hedged = [e for e in events if e.kind == "CLOSE" and e.hedge]
    for ev in hedged:
        for doc in docs:
            if doc.file == ev.file:
                continue
            if ev.date and doc.effective_date and doc.effective_date < ev.date:
                continue
            for para in doc.paragraphs:
                para_low = norm(para)
                named = find_named_issuers(para, name_index)
                if ev.issuer_id not in named:
                    continue
                has_close = any(p in para_low for p in CLOSE_PHRASES)
                has_hedge = any(h in para_low for h in HEDGE_PHRASES)
                if has_close and not has_hedge:
                    corroborations[ev.issuer_id] = (doc.file, para)
    return corroborations


@dataclass
class IssuerState:
    status: str  # 'ACTIVE' | 'PENDING' | 'CLOSED' | 'NONE'
    scope: str
    evidence: list  # list of (file_or_source, location, excerpt)
    deal_code: Optional[str]
    closure_corroborated: bool = False


def resolve_issuer_states(issuers, central_rows, events, corroborations, unrelated):
    states: dict[str, IssuerState] = {}

    for row in central_rows:
        if row["status"] != "ACTIVE":
            continue
        iid = row["issuer_id"]
        ev_list = [("central_restricted_list.csv", row["central_record_id"],
                    f"{row['legal_issuer_name']},{row['issuer_id']},{row['status']},{row['start_date']},{row['end_date']},{row['restriction_reason']},{row['source']}")]
        states[iid] = IssuerState("ACTIVE", "ENTITY_ONLY", ev_list, None)

    by_issuer: dict[str, list[Event]] = {}
    for ev in events:
        by_issuer.setdefault(ev.issuer_id, []).append(ev)

    for iid, ev_list in by_issuer.items():
        ev_list_sorted = sorted(ev_list, key=lambda e: (e.date or date.min))
        current = states.get(iid, IssuerState("NONE", "ENTITY_ONLY", [], None))
        status, scope, evidence, deal_code = current.status, current.scope, list(current.evidence), current.deal_code
        corroborated = False
        for ev in ev_list_sorted:
            evidence.append((ev.file, "document text", ev.excerpt))
            deal_code = deal_code or ev.engagement_code
            if ev.kind == "OPEN":
                status, scope = "ACTIVE", ev.scope
                corroborated = False
            elif ev.kind == "CLOSE":
                if ev.hedge and iid not in corroborations:
                    status = "PENDING"
                elif ev.hedge and iid in corroborations:
                    status, corroborated = "CLOSED", True
                    cfile, cexcerpt = corroborations[iid]
                    evidence.append((cfile, "document text (corroborating)", cexcerpt))
                else:
                    status = "CLOSED"
        states[iid] = IssuerState(status, scope, evidence, deal_code, corroborated)

    return states


def find_scope_pending_candidates(issuers: dict[str, Issuer], events: list[Event], scope_pending, unrelated):
    """For an engagement whose scope is flagged provisional (a widened,
    not-yet-finalised related-entity population), surface issuers that share
    a name-cluster with an already-confirmed target-group member of that
    engagement but are not themselves structurally linked (no parent_issuer_id
    match) and have not been explicitly disambiguated elsewhere. These are
    judgment-call candidates for REVIEW, not an automatic restriction."""
    confirmed_target_group: dict[str, set[str]] = {}
    for ev in events:
        if ev.kind == "OPEN" and ev.scope == "TARGET_GROUP":
            confirmed_target_group.setdefault(ev.engagement_code, set()).add(ev.issuer_id)
    # structural subsidiaries are also confirmed, once their parent is confirmed
    changed = True
    while changed:
        changed = False
        for code, members in confirmed_target_group.items():
            for iid, issuer in issuers.items():
                if issuer.parent_issuer_id in members and iid not in members:
                    members.add(iid)
                    changed = True

    candidates: dict[str, tuple[str, str, str]] = {}  # issuer_id -> (engagement_code, file, excerpt)
    for code, (pending_date, file, excerpt) in scope_pending.items():
        members = confirmed_target_group.get(code, set())
        clusters = {issuers[m].name_cluster_key for m in members if m in issuers}
        for iid, issuer in issuers.items():
            if iid in members or iid in unrelated:
                continue
            if issuer.name_cluster_key in clusters:
                candidates[iid] = (code, file, excerpt)
    return candidates


# ---------------------------------------------------------------------------
# Order resolution
# ---------------------------------------------------------------------------

@dataclass
class Disposition:
    order_id: str
    disposition: str
    rule_id: str
    deal_code: str
    evidence_file: str
    evidence_location: str
    evidence_excerpt: str


def join_evidence(items, key):
    return ";".join(str(i[key] if isinstance(i, dict) else i) for i in items) if items else ""


def resolve_order(order, issuers, sec_to_issuer, name_index, states, candidates, unrelated):
    order_id = order["order_id"]
    issuer_name = (order.get("issuer_name") or "").strip()
    security_id = (order.get("security_id") or "").strip()

    issuer_id = None
    identifier_gap_note = ""
    if security_id and security_id in sec_to_issuer:
        issuer_id = sec_to_issuer[security_id]
    elif issuer_name.lower() in name_index:
        issuer_id = name_index[issuer_name.lower()]
        if not security_id:
            identifier_gap_note = "security_id was blank on this order; issuer resolved by exact legal-name match. "

    if issuer_id is None:
        return Disposition(order_id, "REVIEW", "R10_UNRESOLVED_IDENTITY", "",
                            "orderbook.csv", order_id,
                            f"Could not resolve issuer '{issuer_name}' (security_id '{security_id}') against the security master by security_id or exact name/alias match.")

    issuer = issuers[issuer_id]
    own = states.get(issuer_id)
    parent = issuers.get(issuer.parent_issuer_id) if issuer.parent_issuer_id else None
    parent_state = states.get(issuer.parent_issuer_id) if issuer.parent_issuer_id else None

    def render(disp, rule, deal_code, ev_items):
        files = ";".join(e[0] for e in ev_items) if ev_items else ""
        locs = ";".join(e[1] for e in ev_items) if ev_items else ""
        excerpts = ";".join(e[2] for e in ev_items) if ev_items else ""
        excerpt_prefix = identifier_gap_note
        return Disposition(order_id, disp, rule, deal_code or "", files, locs, excerpt_prefix + excerpts)

    # 1. Direct restriction on the order's own issuer.
    if own and own.status == "ACTIVE":
        return render("HOLD", "R1_R2_RESTRICTED_ISSUER_ACTIVE", own.deal_code, own.evidence)
    if own and own.status == "PENDING":
        return render("REVIEW", "R7_CLOSURE_UNCORROBORATED_PENDING", own.deal_code, own.evidence)
    if own and own.status == "CLOSED":
        rule = "R6_CLOSURE_CORROBORATED" if own.closure_corroborated else "R6B_CLOSURE_STATED_UNHEDGED"
        return render("RELEASE", rule, own.deal_code, own.evidence)

    # 2. Target-group cascade from an actively restricted parent.
    if parent_state and parent_state.status == "ACTIVE" and parent_state.scope == "TARGET_GROUP":
        return render("HOLD", "R3_TARGET_GROUP_CASCADE", parent_state.deal_code, parent_state.evidence)
    if parent_state and parent_state.status == "ACTIVE" and parent_state.scope == "ENTITY_ONLY":
        ev = parent_state.evidence + [("security_master.csv", issuer_id, f"{issuer.legal_name} is a subsidiary of {parent.legal_name if parent else ''}, which is restricted on an entity basis only; the restriction does not cascade.")]
        return render("RELEASE", "R4_ENTITY_ONLY_NO_CASCADE", parent_state.deal_code, ev)
    if parent_state and parent_state.status == "PENDING":
        return render("REVIEW", "R7_CLOSURE_UNCORROBORATED_PENDING", parent_state.deal_code, parent_state.evidence)

    # 3. Provisional / widened scope naming this issuer as a candidate.
    if issuer_id in candidates:
        code, file, excerpt = candidates[issuer_id]
        return render("REVIEW", "R8_AMBIGUOUS_SCOPE_PENDING_SCHEDULE", code, [(file, "document text", excerpt)])

    # 4. Explicitly disambiguated name collision.
    if issuer_id in unrelated:
        file, excerpt = unrelated[issuer_id]
        return render("RELEASE", "R5_UNRELATED_NAME_COLLISION", "", [(file, "document text", excerpt)])

    # 5. Clear negative: checked, nothing applies.
    note = f"Checked central_restricted_list.csv (by issuer_id {issuer_id}) and all engagement-code deal correspondence by name/alias match for '{issuer.legal_name}'; no restriction found."
    return render("RELEASE", "R9_NO_RESTRICTION_FOUND", "", [("central_restricted_list.csv;deal correspondence", "n/a", note)])


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(run_name: str, repo_root: Path):
    issuers, sec_to_issuer, name_index = load_security_master(repo_root / "reference" / "reference" / "security_master.csv")
    central_rows = load_central_list(repo_root / "reference" / "reference" / "central_restricted_list.csv")
    orders = load_order_book(repo_root / "orderbook.csv")
    docs = load_documents(repo_root / f"artifacts_{run_name}")

    injection_hits = detect_injection_attempts(docs)

    events, unrelated, scope_pending = build_events_from_documents(docs, name_index)
    corroborations = find_corroborations(events, docs, name_index)
    states = resolve_issuer_states(issuers, central_rows, events, corroborations, unrelated)
    candidates = find_scope_pending_candidates(issuers, events, scope_pending, unrelated)

    dispositions = [resolve_order(o, issuers, sec_to_issuer, name_index, states, candidates, unrelated) for o in orders]

    out_dir = repo_root / "outputs" / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "compliance.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "disposition", "rule_id", "deal_code", "evidence_file", "evidence_location", "evidence_excerpt"])
        for d in dispositions:
            w.writerow([d.order_id, d.disposition, d.rule_id, d.deal_code, d.evidence_file, d.evidence_location, d.evidence_excerpt])

    with open(out_dir / "trading_desk.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "disposition"])
        for d in dispositions:
            w.writerow([d.order_id, d.disposition])

    print(f"[{run_name}] {len(orders)} orders processed -> {out_dir}")
    counts = {}
    for d in dispositions:
        counts[d.disposition] = counts.get(d.disposition, 0) + 1
    print(f"[{run_name}] dispositions: {counts}")
    if injection_hits:
        print(f"[{run_name}] prompt-injection phrases detected and ignored (no effect on output): {injection_hits}")
    return dispositions


if __name__ == "__main__":
    import sys
    repo_root = Path(__file__).resolve().parent.parent
    run_names = sys.argv[1:] or ["run1", "run2"]
    for rn in run_names:
        run(rn, repo_root)
