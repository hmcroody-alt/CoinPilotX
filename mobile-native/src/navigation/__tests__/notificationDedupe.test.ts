import {
  DedupeStore,
  markNotificationSeen,
  notificationStableId,
  resetNotificationDedupe
} from "../notificationDedupe";

describe("notificationStableId", () => {
  it("prefers notification_id", () => {
    expect(notificationStableId({ notification_id: 42, messageId: 7 })).toBe("note:42");
    expect(notificationStableId({ notificationId: "abc" })).toBe("note:abc");
  });

  it("falls back to conversation+message id", () => {
    expect(notificationStableId({ conversationId: 9, messageId: 100 })).toBe("msg:9:100");
    expect(notificationStableId({ message_id: 100 })).toBe("msg:100");
  });

  it("uses call id, then request identifier", () => {
    expect(notificationStableId({ call_id: "room-1" })).toBe("call:room-1");
    expect(notificationStableId({}, "os-req-5")).toBe("req:os-req-5");
  });

  it("reads ids nested under data", () => {
    expect(notificationStableId({ data: { notification_id: 77 } })).toBe("note:77");
  });

  it("ignores zero / empty ids", () => {
    expect(notificationStableId({ notification_id: 0, message_id: "0" }, "fb")).toBe("req:fb");
  });

  it("never derives identity from text", () => {
    // Same body, different message ids -> different identities.
    const a = notificationStableId({ messageId: 1, title: "Hi", body: "same copy" });
    const b = notificationStableId({ messageId: 2, title: "Hi", body: "same copy" });
    expect(a).not.toBe(b);
    // No ids at all + no fallback -> unidentifiable (never suppressed downstream).
    expect(notificationStableId({ title: "Hi", body: "same copy" })).toBe("");
  });
});

describe("markNotificationSeen", () => {
  let store: DedupeStore;
  beforeEach(() => {
    store = new Map();
  });

  it("returns true first time, false on duplicate within window", () => {
    expect(markNotificationSeen("note:1", { store, now: 1000 })).toBe(true);
    expect(markNotificationSeen("note:1", { store, now: 1500 })).toBe(false);
  });

  it("re-admits the same id after the window elapses", () => {
    expect(markNotificationSeen("note:1", { store, now: 0, windowMs: 1000 })).toBe(true);
    expect(markNotificationSeen("note:1", { store, now: 1001, windowMs: 1000 })).toBe(true);
  });

  it("keeps a steady stream of duplicates suppressed by refreshing last-seen", () => {
    expect(markNotificationSeen("note:1", { store, now: 0, windowMs: 1000 })).toBe(true);
    expect(markNotificationSeen("note:1", { store, now: 900, windowMs: 1000 })).toBe(false);
    expect(markNotificationSeen("note:1", { store, now: 1800, windowMs: 1000 })).toBe(false);
  });

  it("treats distinct ids independently", () => {
    expect(markNotificationSeen("note:1", { store, now: 0 })).toBe(true);
    expect(markNotificationSeen("note:2", { store, now: 0 })).toBe(true);
  });

  it("never suppresses an empty (unidentifiable) id", () => {
    expect(markNotificationSeen("", { store, now: 0 })).toBe(true);
    expect(markNotificationSeen("", { store, now: 0 })).toBe(true);
  });

  it("caps memory: evicts oldest beyond maxEntries", () => {
    for (let i = 0; i < 10; i++) {
      markNotificationSeen(`note:${i}`, { store, now: i, windowMs: 1_000_000, maxEntries: 5 });
    }
    expect(store.size).toBeLessThanOrEqual(5);
    // Oldest evicted -> treated as new again; newest still remembered.
    expect(markNotificationSeen("note:0", { store, now: 11, windowMs: 1_000_000, maxEntries: 5 })).toBe(true);
    expect(markNotificationSeen("note:9", { store, now: 11, windowMs: 1_000_000, maxEntries: 5 })).toBe(false);
  });

  it("100 rapid duplicates of one message admit exactly one banner", () => {
    let admitted = 0;
    for (let i = 0; i < 100; i++) {
      if (markNotificationSeen("msg:5:99", { store, now: 1000 + i })) admitted++;
    }
    expect(admitted).toBe(1);
  });

  it("100 distinct messages admit exactly 100 banners", () => {
    let admitted = 0;
    for (let i = 0; i < 100; i++) {
      if (markNotificationSeen(`msg:5:${i}`, { store, now: 2000 + i })) admitted++;
    }
    expect(admitted).toBe(100);
  });
});

describe("resetNotificationDedupe", () => {
  it("clears a provided store", () => {
    const store: DedupeStore = new Map();
    markNotificationSeen("note:1", { store, now: 0 });
    resetNotificationDedupe(store);
    expect(store.size).toBe(0);
    expect(markNotificationSeen("note:1", { store, now: 0 })).toBe(true);
  });
});
