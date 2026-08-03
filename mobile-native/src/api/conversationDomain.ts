/**
 * The conversation domain discriminator — Tier 0.4's data-layer split.
 *
 * ## Why this exists
 *
 * The product review of the shipped screens found commerce conversations
 * (marketplace offers, order questions, disputes) sitting in the same list as
 * conversations with friends. Its top finding was that the separation has to be
 * made in the data, not in the paint: a visual-only split still lets a social
 * list query return a buyer haggling over a chair, and still lets a "Contact
 * Seller" tap dump somebody into their friends list.
 *
 * So every conversation carries a domain, and the domain is required rather than
 * optional. An optional discriminator is not a discriminator: the first `?? ` in
 * a view file turns it back into a suggestion. The field is filled in exactly one
 * place — `deriveConversationDomain`, called from the read boundary in
 * `./messenger` — and read everywhere else.
 *
 * ## The five values
 *
 *   SOCIAL         friends, groups, rooms, the assistant. The Messenger tab.
 *   MARKETPLACE    a listing, an offer, a question about an item for sale.
 *   STORE_SUPPORT  a buyer or seller asking about a store or an order.
 *   DISPUTE        a contested order — a claim, a chargeback, a case.
 *   EVENT          an event's attendee thread.
 *
 * SOCIAL is the only domain the Messenger tab shows. The other four belong to
 * the Commerce Inbox, which is why `COMMERCE_DOMAINS` lists all of them and not
 * only the three the filter rail names — a domain nobody drew a chip for must
 * still land in an inbox rather than vanish.
 *
 * ## Where the value comes from, and the documented fallback
 *
 * The live conversation surface does not send this field yet (see the MOCK-DATA
 * ledger in `./commerceInbox`). Until it does, the value is derived here, at the
 * read boundary, in this order:
 *
 *   1. An explicit `conversation_domain` (or `domain`) if the server ever sends
 *      one and it names one of the five values.
 *   2. The conversation's type — "marketplace", "listing_question", "dispute",
 *      "event" and their neighbours map onto a domain.
 *   3. A commerce association already on the conversation (`commerce_link`),
 *      which can only have come from a money object → MARKETPLACE.
 *   4. Otherwise SOCIAL.
 *
 * Step 4 is the fallback, and it is deliberately the conservative one. Guessing
 * SOCIAL for an unlabelled thread keeps it where people already found it; guessing
 * a commerce domain would silently move a friend's messages into a seller tool.
 * The cost of the conservative guess is that an unlabelled marketplace thread stays
 * social until the server labels it, which is a visible, reportable bug rather than
 * a quiet disappearance.
 *
 * ## Scope, and why the split is flag-gated
 *
 * Partitioned storage and the social/commerce read split change which threads a
 * person can see. If the derivation above is wrong for somebody's account, that
 * is a lost conversation. So the behaviour is off by default behind
 * `EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT`; with the flag off the field is still
 * derived and still correct, but every list reads the way it read before.
 */

export const CONVERSATION_DOMAINS = [
  "SOCIAL",
  "MARKETPLACE",
  "STORE_SUPPORT",
  "DISPUTE",
  "EVENT"
] as const;

export type ConversationDomain = (typeof CONVERSATION_DOMAINS)[number];

/** The two halves the app keeps apart. Never merge these into one list. */
export type ConversationScope = "social" | "commerce";

/** The one domain the Messenger tab is allowed to show. */
export const SOCIAL_DOMAINS: readonly ConversationDomain[] = ["SOCIAL"];

/**
 * Everything the Commerce Inbox owns. EVENT is here even though the filter rail
 * has no chip for it: a domain with no chip still belongs to an inbox, and the
 * alternative is a thread that appears in neither.
 */
export const COMMERCE_DOMAINS: readonly ConversationDomain[] = [
  "MARKETPLACE",
  "STORE_SUPPORT",
  "DISPUTE",
  "EVENT"
];

/**
 * Tier 0.4's split. Off by default — see the header for why a wrong derivation
 * is a lost conversation and therefore has to ship dark first.
 */
export function conversationSplitEnabled(): boolean {
  const raw = String(process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT || "").toLowerCase();
  return raw === "1" || raw === "true" || raw === "on" || raw === "yes";
}

export function isCommerceDomain(domain: ConversationDomain): boolean {
  return COMMERCE_DOMAINS.includes(domain);
}

export function isSocialDomain(domain: ConversationDomain): boolean {
  return SOCIAL_DOMAINS.includes(domain);
}

export function domainScope(domain: ConversationDomain): ConversationScope {
  return isSocialDomain(domain) ? "social" : "commerce";
}

export function scopeIncludes(scope: ConversationScope, domain: ConversationDomain): boolean {
  return domainScope(domain) === scope;
}

/** The five values, spelled however a server might spell them. */
function explicitDomain(value: unknown): ConversationDomain | null {
  if (typeof value !== "string") return null;
  const upper = value.trim().toUpperCase().replace(/[\s-]+/g, "_");
  return (CONVERSATION_DOMAINS as readonly string[]).includes(upper)
    ? (upper as ConversationDomain)
    : null;
}

/**
 * Conversation types that name a commerce object. Matched as substrings because
 * the live surface spells the same idea several ways ("marketplace", "listing",
 * "marketplace_offer") and a fixed list of exact strings would go stale silently.
 */
const TYPE_RULES: Array<[RegExp, ConversationDomain]> = [
  [/dispute|chargeback|claim|appeal|case/, "DISPUTE"],
  [/store[_-]?support|order[_-]?support|seller[_-]?support|shop[_-]?support|support/, "STORE_SUPPORT"],
  [/marketplace|listing|offer|order|pickup|checkout|shop|store/, "MARKETPLACE"],
  [/event/, "EVENT"]
];

/**
 * Derive the domain for one raw conversation. Call this at the read boundary and
 * nowhere else; view code reads `conversation_domain` and never re-derives.
 */
export function deriveConversationDomain(raw: unknown): ConversationDomain {
  if (!raw || typeof raw !== "object") return "SOCIAL";
  const record = raw as Record<string, unknown>;

  const declared = explicitDomain(record.conversation_domain) || explicitDomain(record.domain);
  if (declared) return declared;

  const type = String(record.conversation_type || record.type || "").toLowerCase();
  if (type) {
    for (const [pattern, domain] of TYPE_RULES) {
      if (pattern.test(type)) return domain;
    }
  }

  // A conversation already carrying a money object can only have come from one.
  const link = record.commerce_link as { kind?: unknown } | undefined;
  if (link && typeof link === "object" && typeof link.kind === "string") return "MARKETPLACE";

  return "SOCIAL";
}
