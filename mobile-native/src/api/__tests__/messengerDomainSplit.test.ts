/**
 * The Tier 0.4 separation, asserted at the layer that has to hold it.
 *
 * The review's finding was that commerce and social messaging must be separated
 * in the data rather than in the paint. A visual-only split passes review and
 * still fails in practice, because the next feature that reads the conversation
 * list — a search field, a widget, an unread badge — reads the unsplit list. So
 * what is tested here is the query layer and the storage keys, not a screen:
 *
 *   1. Storage is PARTITIONED. Social and commerce conversations are written to
 *      different keys, and a social read of the cache cannot return a commerce
 *      thread even if the list it was handed contained one.
 *   2. Queries are SCOPED. `listConversations("social")` and
 *      `listConversations("commerce")` answer from the same fetch and never
 *      overlap.
 *   3. Social SEARCH cannot reach a marketplace thread. The exclusion is applied
 *      before the text match, so no query string widens the result set.
 *   4. A one-scope write never blanks the other partition — the failure mode
 *      where "separated" quietly becomes "deleted".
 *   5. With the flag off, every one of these reads the way it read before.
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

const mockPulseApi = jest.fn();
jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args),
  PulseApiError: class PulseApiError extends Error {}
}));

import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  cacheConversations,
  conversationsInScope,
  listConversations,
  loadCachedConversations,
  normalizeConversations,
  searchSocialConversations,
  upsertCachedConversation
} from "../messenger";

const SOCIAL_KEY = "pulsesoc.native.messenger.v2.conversations";
const COMMERCE_KEY = "pulsesoc.native.messenger.v2.conversations.commerce";

/** Raw conversations as the live surface sends them — no domain field at all. */
const WIRE = [
  { id: 1, conversation_id: 1, title: "Maya", conversation_type: "direct", latest_message: "dinner friday?" },
  { id: 2, conversation_id: 2, title: "Design crew", conversation_type: "group", latest_message: "chair pics" },
  {
    id: 3,
    conversation_id: 3,
    title: "Dana",
    conversation_type: "marketplace_offer",
    latest_message: "is the chair still available?"
  },
  {
    id: 4,
    conversation_id: 4,
    title: "Sam",
    conversation_type: "order_support",
    latest_message: "where is my chair?"
  },
  { id: 5, conversation_id: 5, title: "Case 88", conversation_type: "dispute", latest_message: "chair never arrived" }
];

async function seed() {
  await cacheConversations(normalizeConversations(WIRE));
}

beforeEach(async () => {
  mockPulseApi.mockReset();
  await AsyncStorage.clear();
  process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT = "1";
});

afterEach(() => {
  delete process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT;
});

describe("storage is partitioned by domain", () => {
  it("writes social and commerce threads to different keys", async () => {
    await seed();
    const social = JSON.parse((await AsyncStorage.getItem(SOCIAL_KEY)) || "[]");
    const commerce = JSON.parse((await AsyncStorage.getItem(COMMERCE_KEY)) || "[]");
    expect(social.map((c: { id: number }) => c.id).sort()).toEqual([1, 2]);
    expect(commerce.map((c: { id: number }) => c.id).sort()).toEqual([3, 4, 5]);
  });

  it("cannot return a marketplace thread from a social cache read", async () => {
    await seed();
    const social = await loadCachedConversations("social");
    expect(social.map((c) => c.id).sort()).toEqual([1, 2]);
    expect(social.every((c) => c.conversation_domain === "SOCIAL")).toBe(true);
  });

  it("cannot return a friend's thread from a commerce cache read", async () => {
    await seed();
    const commerce = await loadCachedConversations("commerce");
    expect(commerce.map((c) => c.id).sort()).toEqual([3, 4, 5]);
    expect(commerce.some((c) => c.conversation_domain === "SOCIAL")).toBe(false);
  });

  it("does not blank the other partition when only one scope is written", async () => {
    await seed();
    await upsertCachedConversation({
      id: 3,
      conversation_id: 3,
      title: "Dana",
      conversation_type: "marketplace_offer",
      latest_message: "still interested"
    });
    const social = await loadCachedConversations("social");
    // The commerce write must not have wiped the friends.
    expect(social.map((c) => c.id).sort()).toEqual([1, 2]);
    const commerce = await loadCachedConversations("commerce");
    expect(commerce.map((c) => c.id).sort()).toEqual([3, 4, 5]);
  });

  it("routes an upsert by the thread's own domain, not by who called it", async () => {
    await seed();
    await upsertCachedConversation({
      id: 9,
      conversation_id: 9,
      title: "Case 91",
      conversation_type: "dispute",
      latest_message: "opened"
    });
    const social = await loadCachedConversations("social");
    expect(social.map((c) => c.id)).not.toContain(9);
    const commerce = await loadCachedConversations("commerce");
    expect(commerce.map((c) => c.id)).toContain(9);
  });
});

describe("queries are scoped", () => {
  it("gives the Messenger tab and the Commerce Inbox disjoint lists from one fetch", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, conversations: WIRE });
    const social = await listConversations("social");
    const commerce = await listConversations("commerce");
    expect(social.map((c) => c.id).sort()).toEqual([1, 2]);
    expect(commerce.map((c) => c.id).sort()).toEqual([3, 4, 5]);
    const overlap = social.filter((s) => commerce.some((c) => c.id === s.id));
    expect(overlap).toEqual([]);
  });

  it("stamps every conversation with a domain at the read boundary", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, conversations: WIRE });
    const all = await listConversations();
    expect(all.every((c) => typeof c.conversation_domain === "string")).toBe(true);
    expect(all.find((c) => c.id === 3)?.conversation_domain).toBe("MARKETPLACE");
    expect(all.find((c) => c.id === 4)?.conversation_domain).toBe("STORE_SUPPORT");
    expect(all.find((c) => c.id === 5)?.conversation_domain).toBe("DISPUTE");
    expect(all.find((c) => c.id === 1)?.conversation_domain).toBe("SOCIAL");
  });
});

describe("marketplace threads never appear in social search", () => {
  it("excludes commerce threads even when the query matches their text exactly", async () => {
    await seed();
    // "chair" appears in three commerce threads and one social one.
    const hits = await searchSocialConversations("chair");
    expect(hits.map((c) => c.id)).toEqual([2]);
    expect(hits.every((c) => c.conversation_domain === "SOCIAL")).toBe(true);
  });

  it("cannot be widened past the social half by any query, including an empty one", async () => {
    await seed();
    for (const query of ["", "  ", "a", "Dana", "chair never arrived"]) {
      const hits = await searchSocialConversations(query);
      expect(hits.every((c) => c.conversation_domain === "SOCIAL")).toBe(true);
      expect(hits.map((c) => c.id)).not.toContain(3);
    }
  });

  it("still finds a social thread by title and by message text", async () => {
    await seed();
    expect((await searchSocialConversations("maya")).map((c) => c.id)).toEqual([1]);
    expect((await searchSocialConversations("dinner")).map((c) => c.id)).toEqual([1]);
  });
});

describe("with the split flag off, nothing changes", () => {
  beforeEach(() => {
    delete process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT;
  });

  it("keeps one cache key and returns every thread to every reader", async () => {
    await cacheConversations(normalizeConversations(WIRE));
    expect(await AsyncStorage.getItem(COMMERCE_KEY)).toBeNull();
    const social = await loadCachedConversations("social");
    expect(social.map((c) => c.id).sort()).toEqual([1, 2, 3, 4, 5]);
  });

  it("makes the scope filter the identity function", () => {
    const all = normalizeConversations(WIRE);
    expect(conversationsInScope(all, "social")).toHaveLength(5);
  });

  it("still derives the domain, so the field is correct before the flag is flipped", () => {
    const all = normalizeConversations(WIRE);
    expect(all.find((c) => c.id === 3)?.conversation_domain).toBe("MARKETPLACE");
  });
});
