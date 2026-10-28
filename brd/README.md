# PaC ingestion — not run

`BRD.md` in this folder is the business requirements document the brief asks
for (slide 27): written from the two rules a real Day 2 presentation would
have defended, plus this build's own evidence from the supplied deal
correspondence. It is deliberately in plain prose/markdown, not a
policy-as-code format, because it was never ingested into an actual
Policy-as-Code tool — PaC is lab-VM tooling (OPA-based, per Masterclass 4) not
available in this environment.

What a real run would add: the generated policy files and the restriction
lifecycle state machine PaC produces from this BRD, plus the side-by-side
comparison the presentation asks for (one PaC-generated policy next to the
place this build's `solution/engine.py` enforces the same rule by hand). The
rule table in `../README.md` is the closest substitute available here — it
is, in effect, the state machine's transitions (`ACTIVE` → `PENDING` →
`CLOSED`, gated by corroboration) written out as code comments and a markdown
table instead of as PaC-generated artifacts.
