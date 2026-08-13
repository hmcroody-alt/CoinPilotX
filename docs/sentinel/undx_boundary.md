# Sentinel ↔ UNDX Boundary & AI Security (Stages 18–19)

## The rule above all rules

**UNDX IS INTELLIGENCE, NOT ROOT** (SC2). The model may observe (redacted)
and advise (recorded); it may not decide, act, escalate, or grade its own
input's importance.

## Structured interface (Stage 18)

Module: `services/sentinel/undx_interface.py`.

**Reads** — `read(surface)` against a closed allowlist
(`recent_events`, `open_incidents`, `provider_health`). Unknown surface →
fail closed with an SC15-citing error. Every row is re-redacted to the
INTERNAL ceiling before the model sees it — stricter than what the
database stores. There is no free-form query surface, no SQL passthrough.

**Writes** — exactly one: `submit_analysis(subject_type, subject_id,
analysis, confidence)`. It records a `UNDX` event with:

- severity forced to `info` — the model cannot self-assign importance
- actor `undx.model` (ADVISORY tier)
- result tagged `authority: "ADVISORY"`

Empty analyses are rejected. The regression suite scans the module's
exports for mutation-shaped names (`execute`, `run_sql`, `write`,
`delete`, …) and fails if any appear.

Model analyses can *feed* correlation rule CR5-style patterns as one
signal among several — they can never open incidents directly.

## AI security (Stage 19)

Module: `services/sentinel/ai_security.py`.

**Content is data (SC10).** Text scanned for prompt injection is never
executed or obeyed, and a positive scan never punishes the author —
hostile-looking text is *evidence*, recorded as a capped `medium`
`UNDX/injection_detected` event with no automatic incident and no
enforcement.

Mechanics, honestly labeled (**NO FAKE AI**):

- `scan_for_injection` — weighted regex heuristics
  (`method: "heuristic_regex_v1"`), including the UNDX write-approval
  phrase as a high-weight pattern; threshold 3 keeps single weak matches
  benign.
- `wrap_untrusted` — wraps content in untrusted-boundary markers and
  neutralizes nested/forged markers inside the content so hostile text
  cannot fake its way out of the data channel.
- `record_injection_event` — event only; unflagged scans record nothing.

Known limitation: heuristics miss novel phrasings and flag some benign
text. That is acceptable because the output is advisory evidence for
correlation and human review — never a verdict (SC9 applied to our own
detector).
