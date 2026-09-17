import * as Notifications from "expo-notifications";
import { pulseApi } from "../../api/pulseApi";
import { getNotificationBadgeCounts } from "../../api/notifications";
import { pendingMutations, outboxScope } from "../mutations/outbox";
import { cancelMessageReconciliation, parseMessageNotification, reconcileMessageNotifications } from "../messageNotificationReconciliation";
import { __resetUnreadCounts } from "../unreadCounts";

jest.mock("expo-notifications", () => ({ getPresentedNotificationsAsync: jest.fn(), dismissNotificationAsync: jest.fn(), setBadgeCountAsync: jest.fn() }));
jest.mock("../../api/pulseApi", () => ({ pulseApi: jest.fn() }));
jest.mock("../../api/notifications", () => ({ ...jest.requireActual("../../api/notifications"), getNotificationBadgeCounts: jest.fn() }));
jest.mock("../mutations/outbox", () => ({ pendingMutations: jest.fn(), outboxScope: jest.fn() }));

const ME = 7;
const data = (messageId = 1, conversationId = 10, recipientUserId = ME) => ({ schemaVersion: 1, notificationType: "message", messageNamespace: "comm_v2", recipientUserId, messageId, conversationId });
const note = (key: string, payload: Record<string, unknown>) => ({ request: { identifier: key, content: { data: payload } } });
const flush = () => new Promise(resolve => setImmediate(resolve));

beforeEach(() => {
  jest.clearAllMocks();
  __resetUnreadCounts();
  cancelMessageReconciliation();
  (outboxScope as jest.Mock).mockReturnValue(`u${ME}`);
  (pendingMutations as jest.Mock).mockResolvedValue([]);
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([]);
  (Notifications.dismissNotificationAsync as jest.Mock).mockResolvedValue(undefined);
  (getNotificationBadgeCounts as jest.Mock).mockResolvedValue({ total_unread_count: 0 });
  (pulseApi as jest.Mock).mockResolvedValue({ ok: true, recipientUserId: ME, dismiss: [] });
});

describe("the fields the classifier must never read", () => {
  // Stage 13 asks for proof that identity never comes from presentation text. A
  // decoy carrying every human-readable field a real message push carries, but
  // none of the contract fields, must classify as "not a message" -- because the
  // alternative is dismissing someone else's unread alert on a title match.
  it.each([
    ["title", { title: "New message from Dana" }],
    ["body", { body: "hey, are you around?" }],
    ["subtitle", { subtitle: "Dana Whitfield" }],
    ["sender name", { senderName: "Dana Whitfield", sender_name: "Dana Whitfield" }],
    ["thread id", { threadId: "conversation-10", threadIdentifier: "conversation-10" }],
    ["category", { categoryIdentifier: "message", categoryId: "message" }],
  ])("refuses a decoy whose only message-shaped field is its %s", (_label, decoy) => {
    expect(parseMessageNotification("os", decoy as Record<string, unknown>, ME)).toBeNull();
  });

  it("classifies identically with and without every presentation field present", () => {
    const bare = parseMessageNotification("os", data(), ME);
    const dressed = parseMessageNotification("os", {
      ...data(),
      title: "New message from Dana", body: "hey, are you around?", subtitle: "Dana Whitfield",
      senderName: "Dana Whitfield", threadId: "conversation-10", categoryIdentifier: "message",
    }, ME);
    expect(bare).not.toBeNull();
    expect(dressed).toEqual(bare);
  });

  it("carries no human-readable field out of the parser", () => {
    const parsed = parseMessageNotification("os", {
      ...data(), title: "New message from Dana", body: "hey, are you around?", senderName: "Dana Whitfield",
    }, ME);
    // Whatever the parser keeps is what a log line or a crash report can leak.
    const serialized = JSON.stringify(parsed);
    expect(serialized).not.toMatch(/Dana|hey, are you around/);
    expect(Object.keys(parsed ?? {}).sort()).toEqual(
      ["conversationId", "key", "messageId", "messageNamespace", "notificationType", "recipientUserId", "schemaVersion"]
    );
  });

  it("refuses a well-formed contract addressed to another account", () => {
    expect(parseMessageNotification("os", data(1, 10, 8), ME)).toBeNull();
    expect(parseMessageNotification("os", data(1, 10, ME), 8)).toBeNull();
  });

  it.each([
    ["float", 1.5], ["negative", -1], ["zero", 0], ["boolean", true],
    ["numeric string with space", " 1"], ["hex", "0x1"], ["overflowing", Number.MAX_SAFE_INTEGER + 2], ["null", null],
  ])("refuses a %s message id", (_label, messageId) => {
    expect(parseMessageNotification("os", { ...data(), messageId }, ME)).toBeNull();
  });

  it.each(["", "a string", 12, [], true, [{ notificationType: "message" }]])(
    "survives the unreadable payload %p",
    (payload) => {
      // `null` and `undefined` are deliberately absent. The only call site reads
      // `n.request.content.data || {}`, so they cannot reach the parser, and a
      // test asserting otherwise would be demanding a guard against a state that
      // does not occur. Everything else here genuinely can arrive: `content.data`
      // is whatever the push carried, and a sender picks that.
      expect(() => parseMessageNotification("os", payload as never, ME)).not.toThrow();
      expect(parseMessageNotification("os", payload as never, ME)).toBeNull();
    }
  );
});

describe("an account switching underneath a pass", () => {
  it("abandons the outgoing account's pass instead of dismissing into it", async () => {
    (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("os-1", data())]);
    (pulseApi as jest.Mock).mockImplementation(async () => {
      (outboxScope as jest.Mock).mockReturnValue("u8");
      cancelMessageReconciliation();
      return { ok: true, recipientUserId: ME, dismiss: ["os-1"] };
    });
    expect(await reconcileMessageNotifications()).toMatchObject({ cancelled: true, dismissed: 0 });
    expect(Notifications.dismissNotificationAsync).not.toHaveBeenCalled();
  });

  it("gives the incoming account a pass of its own rather than the abandoned one", async () => {
    (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("os-1", data())]);
    const parked: { release: () => void } = { release: () => undefined };
    (pulseApi as jest.Mock).mockImplementationOnce(
      () => new Promise((resolve) => { parked.release = () => resolve({ ok: true, recipientUserId: ME, dismiss: [] }); })
    );
    const outgoing = reconcileMessageNotifications();
    await flush();
    (outboxScope as jest.Mock).mockReturnValue("u8");
    cancelMessageReconciliation();
    const incoming = reconcileMessageNotifications();
    parked.release();
    await Promise.all([outgoing, incoming]);
    await flush();
    expect(incoming).not.toBe(outgoing);
    // Two enumerations means the incoming account's Notification Center was
    // actually read. Sharing the outgoing promise reported success over a
    // Notification Center nobody looked at.
    expect(Notifications.getPresentedNotificationsAsync).toHaveBeenCalledTimes(2);
    expect((await incoming).cancelled).toBe(false);
  });

  it("still coalesces two triggers inside one account", async () => {
    const first = reconcileMessageNotifications();
    expect(reconcileMessageNotifications()).toBe(first);
    await first;
  });

  it("does nothing at all when signed out", async () => {
    (outboxScope as jest.Mock).mockReturnValue("");
    (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("os-1", data())]);
    expect(await reconcileMessageNotifications()).toMatchObject({ examined: 0, dismissed: 0 });
    expect(Notifications.getPresentedNotificationsAsync).not.toHaveBeenCalled();
    expect(Notifications.setBadgeCountAsync).not.toHaveBeenCalled();
  });
});
