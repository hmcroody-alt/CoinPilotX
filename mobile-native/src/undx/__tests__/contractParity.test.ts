/**
 * The client's card vocabulary must equal the server's, exactly.
 *
 * Two string enums describe the same thing in two languages: `CardType` and
 * `VerificationState` in `services/undx_agent_contracts.py`, and the unions in
 * `src/api/messenger.ts`. Duplicated constants across a language boundary do not stay
 * in sync by intention, and the failure mode is silent in the direction that matters
 * most — the server adds a card, the app does not know it, and the app decides what to
 * draw anyway.
 *
 * Both real divergences this test was written for had already shipped. The client's
 * `verification_state` union listed "unverified", "pending" and "mismatch", none of
 * which the server has ever emitted, and omitted "verification_pending" and
 * "verification_failed", which are precisely the two values meaning "this change could
 * not be confirmed". And `search_results` was missing from the component union
 * entirely. Neither showed up as a type error, because the wrong strings type-check
 * perfectly well against each other.
 *
 * So the Python file is the fixture. Parsing it is slightly unusual for a Jest test
 * and is the point: the assertion is against the source of truth rather than against a
 * copy of it that could drift in the same way.
 */

import { readFileSync } from "fs";
import { join } from "path";

import {
  CANCELLED_COMPONENTS,
  CONFIRMATION_COMPONENTS,
  FAILURE_COMPONENTS,
  PROGRESS_COMPONENTS,
  QUESTION_COMPONENTS,
  RECEIPT_COMPONENTS,
  RESULT_COMPONENTS,
} from "../actionCards";

/**
 * Every bucket `kindOf` consults, in one place.
 *
 * Previously spelled out twice inside the tests below. Adding `QUESTION_COMPONENTS`
 * to one copy and not the other would have left the second test asserting that the
 * question cards are unclassified, which is the opposite of what it is for.
 */
const BUCKETS = [
  CONFIRMATION_COMPONENTS,
  QUESTION_COMPONENTS,
  CANCELLED_COMPONENTS,
  RECEIPT_COMPONENTS,
  FAILURE_COMPONENTS,
  PROGRESS_COMPONENTS,
  RESULT_COMPONENTS,
];

const CONTRACTS = join(__dirname, "..", "..", "..", "..", "services", "undx_agent_contracts.py");
const MESSENGER = join(__dirname, "..", "..", "api", "messenger.ts");

/** Every string literal assigned inside one `class Name:` block in the Python file. */
function pythonEnumValues(source: string, className: string): string[] {
  const start = source.indexOf(`class ${className}:`);
  if (start === -1) {
    throw new Error(`${className} not found in undx_agent_contracts.py`);
  }
  const rest = source.slice(start + `class ${className}:`.length);
  const end = rest.search(/\nclass\s/);
  const body = end === -1 ? rest : rest.slice(0, end);
  const values = new Set<string>();
  const assignment = /^\s{4}[A-Z][A-Z0-9_]*\s*=\s*"([a-z0-9_]+)"/gm;
  let match: RegExpExecArray | null;
  while ((match = assignment.exec(body)) !== null) {
    values.add(match[1]);
  }
  return [...values].sort();
}

/** The string literals of one `field?:` union in messenger.ts. */
function tsUnionValues(source: string, field: string): string[] {
  const start = source.indexOf(field);
  if (start === -1) {
    throw new Error(`${field} not found in messenger.ts`);
  }
  const body = source.slice(start, source.indexOf(";", start));
  return [...new Set([...body.matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]))].sort();
}

describe("UNDX card contract parity", () => {
  const python = readFileSync(CONTRACTS, "utf8");
  const messenger = readFileSync(MESSENGER, "utf8");

  it("accepts every card type the server can emit", () => {
    const serverCards = pythonEnumValues(python, "CardType");
    const clientCards = tsUnionValues(messenger, "component:");
    // Subset, not equality: the client union also carries the V4/V5 names
    // (`confirmation_card`, `search_result_card`, ...) which have no entry in CardType
    // and are still live on the conversational path.
    expect(serverCards.filter((name) => !clientCards.includes(name))).toEqual([]);
  });

  it("uses exactly the server's four verification states", () => {
    expect(tsUnionValues(messenger, "verification_state?:")).toEqual(
      pythonEnumValues(python, "VerificationState"),
    );
  });

  it("classifies every server card type into exactly one kind", () => {
    const all = BUCKETS.flatMap((bucket) => [...bucket] as string[]);

    // No card may be in two buckets: `kindOf` checks them in order, so an overlap
    // would make the rendered kind depend on that order rather than on the card.
    expect(all.length).toBe(new Set(all).size);

    // Nothing the server can send may fall through to the unrecognised default, which
    // renders as a failure. That default is correct as a safety net and wrong as a
    // routine outcome.
    const unclassified = pythonEnumValues(python, "CardType").filter(
      (name) => !all.includes(name),
    );
    expect(unclassified).toEqual([]);
  });

  it("classifies every card the client type allows, including the V4/V5 names", () => {
    const buckets = BUCKETS.flatMap((bucket) => [...bucket] as string[]);
    const declared = tsUnionValues(messenger, "component:");
    // Four legacy cards are declared in the transport type but have no agent
    // equivalent and no renderer of their own yet. They are listed explicitly rather
    // than skipped, so adding a fifth is a decision someone has to write down.
    const knownUnrendered = [
      "conflict_resolution_card",
      "draft_preview",
      "progress_card",
      "settings_summary",
    ];
    expect(
      declared.filter((name) => !buckets.includes(name) && !knownUnrendered.includes(name)),
    ).toEqual([]);
  });
});
