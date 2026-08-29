# 02 — A switched-off capability must not read as an unclear sentence

## The symptom

With `UNDX_AGENT_ENABLED_CAPABILITIES` set to a list that omits `feed.posts.like`,
"Like my most recent post." came back as:

> Which post? Tell me its number, or open it and ask again.

The capability is switched off. The sentence was understood perfectly.

## Why it happened, which is not where it looks

`undx_agent_policy.evaluate` already denies a withdrawn capability correctly, at step 3
(`undx_agent_policy.py:261`), with the right message. That check was simply never
reached, because it lives in the gateway and the gateway is entered only after
arguments resolve. Resolution answers first.

The chain, each link locally correct:

1. `resolve_arguments` needs `post_id` for `feed.posts.like`, and the sentence names no
   number, so it falls through to `resolve_recent_post`.
2. `resolve_recent_post` performs its read through `_read_permitted(user_id,
   "feed.posts.list")` — deliberately, so that an operator who switches reads off during
   an incident does not still get their data rendered into a confirmation card.
3. An allowlist narrow enough to omit `feed.posts.like` omits `feed.posts.list` as well.
   The read is refused, and the resolver returns 0, which it documents as "no opinion".
4. `post_id` is therefore absent, `missing_required` reports it, and a missing required
   field is answered by `_missing_field_question` — which asks about the *sentence*.

Every step is right and the result is wrong about which thing failed. That is the
characteristic shape of a category error rather than a bug in any one function.

## Why the category matters more than the wording

Being told the sentence was ambiguous invites the person to answer it: they supply a
post id, which reaches the same disabled capability, and is refused the same way. The
loop renews for as long as they keep trying, and nothing in it ever mentions that the
feature is off. Reporting a rollout decision as a comprehension failure sends the
person to fix the one thing they cannot fix.

## The fix

`_capability_withdrawn` in `services/undx_agent_runtime.py`, consulted at the top of
`_act` — before argument resolution runs at all, which is the only position where it
can beat the clarification.

It calls `policy.evaluate` and acts on exactly one verdict: `capability_disabled`.
Every other decision returns `None` and the turn proceeds untouched, so this is not a
second authorization point — an enabled capability is still authorized by the gateway
alone, and the ordering of every other check is where it was. The user-facing sentence
is taken from the policy decision rather than written again, so there is one definition
of this condition and it cannot drift. An exception in the check degrades to `None`
rather than failing the turn.

## Tests

`tests/undx_agent/test_capability_withdrawal_message.py`, 6 tests, all passing:

- a withdrawn capability answers `permission_denied` and the reply contains neither
  "Which post" nor "Tell me its number";
- the reply equals the sentence `policy.evaluate` publishes, asserted against the
  policy rather than a copy of it;
- naming the post by number explicitly is refused the same way — this is the loop being
  closed, not just the first turn;
- nothing is written, including after a following "Yes";
- an **enabled** capability still reaches `confirmation_required` — the guard must not
  become a refusal for everything;
- an enabled capability with a genuinely unidentifiable target ("Like that post") still
  asks "Which post?" — target ambiguity is a real category and had to stay reachable.

The last two are the ones that make the first four mean something.

## Verification

`scripts/undx_production_gate_probe.py`, allowlist row, before and after:

| | Turn 1 |
| --- | --- |
| before | `clarification_required` — "Which post? Tell me its number, or open it and ask again." |
| after | `permission_denied` — "UNDX cannot do that right now." |

`tests/undx_agent`: **894 passed, 3127 subtests, 16 failed** — the same 16 as the
pre-existing baseline (`test_content_graph_intelligence_pack` ×1,
`test_saved_post_write_pack` ×3, 12 subfailures in `test_knowledge_map_grounding`).
Previously 888 passed; the 6 added are the new file. No regressions.
