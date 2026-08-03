/**
 * The domain discriminator itself.
 *
 * Tier 0.4's premise is that commerce and social conversations must be separated
 * in the data, so the first thing worth pinning is the derivation: what a raw
 * conversation becomes, and — more importantly — what it becomes when the server
 * says nothing at all. The fallback is SOCIAL and that choice is asserted here
 * rather than left as a comment, because an unlabelled thread staying where people
 * already found it is the difference between a visible bug and a lost message.
 */

import {
  COMMERCE_DOMAINS,
  CONVERSATION_DOMAINS,
  SOCIAL_DOMAINS,
  conversationSplitEnabled,
  deriveConversationDomain,
  domainScope,
  isCommerceDomain,
  scopeIncludes
} from "../conversationDomain";

afterEach(() => {
  delete process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT;
});

describe("the five domains", () => {
  it("has exactly the five values the review named, and no sixth", () => {
    expect(CONVERSATION_DOMAINS).toEqual([
      "SOCIAL",
      "MARKETPLACE",
      "STORE_SUPPORT",
      "DISPUTE",
      "EVENT"
    ]);
  });

  it("puts every non-social domain in an inbox, so no domain lands in neither", () => {
    const covered = [...SOCIAL_DOMAINS, ...COMMERCE_DOMAINS].sort();
    expect(covered).toEqual([...CONVERSATION_DOMAINS].sort());
  });

  it("maps every domain to exactly one scope", () => {
    expect(domainScope("SOCIAL")).toBe("social");
    for (const domain of COMMERCE_DOMAINS) {
      expect(domainScope(domain)).toBe("commerce");
      expect(isCommerceDomain(domain)).toBe(true);
      expect(scopeIncludes("social", domain)).toBe(false);
    }
  });
});

describe("derivation at the read boundary", () => {
  it("takes an explicit domain when the server sends one, however it is spelled", () => {
    expect(deriveConversationDomain({ conversation_domain: "MARKETPLACE" })).toBe("MARKETPLACE");
    expect(deriveConversationDomain({ conversation_domain: "store support" })).toBe("STORE_SUPPORT");
    expect(deriveConversationDomain({ domain: "dispute" })).toBe("DISPUTE");
  });

  it("ignores a value that is not one of the five rather than inventing a domain", () => {
    expect(deriveConversationDomain({ conversation_domain: "SALES" })).toBe("SOCIAL");
  });

  it("reads the conversation type when there is no explicit domain", () => {
    expect(deriveConversationDomain({ conversation_type: "marketplace_offer" })).toBe("MARKETPLACE");
    expect(deriveConversationDomain({ conversation_type: "listing_question" })).toBe("MARKETPLACE");
    expect(deriveConversationDomain({ conversation_type: "order_support" })).toBe("STORE_SUPPORT");
    expect(deriveConversationDomain({ conversation_type: "chargeback" })).toBe("DISPUTE");
    expect(deriveConversationDomain({ conversation_type: "event_attendees" })).toBe("EVENT");
  });

  it("treats an existing commerce association as proof the thread came from a money object", () => {
    expect(
      deriveConversationDomain({ commerce_link: { kind: "order", orderId: 4, statusLine: "in transit" } })
    ).toBe("MARKETPLACE");
  });

  it("falls back to SOCIAL for a plain conversation, which is the conservative guess", () => {
    expect(deriveConversationDomain({ conversation_type: "direct" })).toBe("SOCIAL");
    expect(deriveConversationDomain({ conversation_type: "group" })).toBe("SOCIAL");
    expect(deriveConversationDomain({ conversation_type: "room" })).toBe("SOCIAL");
    expect(deriveConversationDomain({ conversation_type: "undx_intelligence" })).toBe("SOCIAL");
    expect(deriveConversationDomain({})).toBe("SOCIAL");
    expect(deriveConversationDomain(undefined)).toBe("SOCIAL");
  });
});

describe("the split flag", () => {
  it("is off unless it is explicitly turned on", () => {
    expect(conversationSplitEnabled()).toBe(false);
    process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT = "0";
    expect(conversationSplitEnabled()).toBe(false);
    process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT = "1";
    expect(conversationSplitEnabled()).toBe(true);
  });
});
