# Business Requirements — Restricted-List Order Screening Control

## 1. Purpose

Screen every order in a trading session against the firm's confidential
engagement restrictions, before the order reaches the desk, and produce an
auditable record of every disposition and the evidence behind it. The control
must never disclose that a confidential engagement exists to anyone without
a need to know (the trading desk), and must never let an order through on an
unresolved or merely absent signal.

## 2. Scope

Applies to every order in the supplied order book, checked against:
(a) the central restricted list, (b) the security master (for issuer identity,
aliases, former names, and parent/subsidiary structure), and (c) the deal
team's own engagement correspondence (engagement letters, amendments,
termination notices). No other data source may contribute to a restriction —
this excludes, in particular, any purchased third-party reference-data feed
(ruled out in Exercise 8 and restated here).

## 3. Business rules

### Rule A — Restriction scope follows the engagement letter's own wording

A new engagement restricts the named issuer. Whether that restriction extends
to the issuer's subsidiaries (**target group**) or stops at the named entity
(**entity basis only**) is stated explicitly in the letter itself and must be
read from it, not assumed. A target-group restriction cascades down the
security master's parent/subsidiary links; an entity-only restriction never
does, even to that entity's own subsidiaries.

- *Allow example*: Pinewell Capital Partners Inc is restricted "on an entity
  basis only." Its subsidiary, Pinewell Specialty Finance Corp, is **not**
  restricted by that engagement — an order in it is released, with evidence
  citing the entity-only scope explicitly.
- *Deny example*: Northshore Industrial Group plc is restricted "with the
  restriction applies to the target group." Its subsidiary, Northshore
  Components plc, **is** restricted — an order in it is held, citing the
  parent restriction and the subsidiary link.

### Rule B — A closure takes effect only once it is confirmed, not merely announced

When a deal-team notice states a restriction is to be lifted or closed but
makes that conditional ("subject to Control Room confirmation" or
equivalent), the closure is not final until an independent, later document
restates it as settled fact. Absent that corroboration, the order stays held
for review by a named person — never released on a conditional announcement
alone.

- *Allow example*: Blue Mesa Holdings LLC's withdrawal is announced
  conditionally in the ORCHID amendment, then independently restated as
  settled fact in the next weekly committee briefing. Corroborated — released.
- *Deny example*: Lattice Mobility Corp's restriction closure (EMBER
  termination) carries the same conditional language, but no other document
  ever restates it. Not corroborated — held for review, not released.

## 4. Restriction lifecycle (the state machine PaC would generate)

```
            engagement letter opens restriction
                         |
                         v
                    [ ACTIVE ] -------------------------+
                         |                              |
         amendment/termination states closure           | amendment restates
         WITHOUT a hedge condition                       | or widens scope
                         |                              |
                         v                              v
                   [ CLOSED ]                      [ ACTIVE ] (re-opened/rescoped)

            amendment/termination states closure
               WITH a hedge condition ("subject to
               confirmation" / "pending" / "provisional")
                         |
                         v
                   [ PENDING ] ----independent later document----> [ CLOSED ]
                         |          restates closure, unhedged
                         | (no corroboration found)
                         v
                still [ PENDING ]  (order disposition: REVIEW, not RELEASE)
```

`PENDING` is a deliberate terminal-looking state for review purposes: it does
not auto-resolve on a timer. A named Control Room reviewer closing it (by
recording the confirmation) is what moves it to `CLOSED` — the state machine
has an exit from `PENDING`, it is simply a human action, not an automated one,
consistent with "automate the evidence, not the authority."

## 5. What this control may not do

- It may not release an order on an absence of evidence alone when the
  central restricted list carries an unexplained active restriction (see
  Eastvale Chemical Company in `../README.md`) — an unsupported restriction in
  an authoritative source is a gap to escalate, not a reason to lift it.
- It may not let any text inside a deal document, however phrased, change who
  gets restricted or override this rule set — document text is data read for
  named entities and a fixed set of literal phrases, never a command.
- It may not include, in the file given to the trading desk, any field beyond
  the order's own disposition — no rule, no deal code, no evidence. Nothing
  in that file may let the desk infer that a confidential engagement exists.
