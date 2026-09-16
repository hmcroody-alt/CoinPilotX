import * as Notifications from "expo-notifications";
import { pulseApi } from "../../api/pulseApi";
import { getNotificationBadgeCounts } from "../../api/notifications";
import { pendingMutations, outboxScope } from "../mutations/outbox";
import { cancelMessageReconciliation, parseMessageNotification, reconcileMessageNotifications } from "../messageNotificationReconciliation";
jest.mock("expo-notifications", () => ({ getPresentedNotificationsAsync: jest.fn(), dismissNotificationAsync: jest.fn(), setBadgeCountAsync: jest.fn() }));
jest.mock("../../api/pulseApi", () => ({ pulseApi: jest.fn() }));
jest.mock("../../api/notifications", () => ({ getNotificationBadgeCounts: jest.fn() }));
jest.mock("../unreadCounts", () => ({ setUnreadCounts: (c: { total_unread_count: number }) => ({ totalCount: c.total_unread_count }) }));
jest.mock("../mutations/outbox", () => ({ pendingMutations: jest.fn(), outboxScope: jest.fn() }));
const data = (messageId = 1, conversationId = 10) => ({ schemaVersion: 1, notificationType: "message", messageNamespace: "comm_v2", recipientUserId: 7, messageId, conversationId });
const note = (key: string, payload: Record<string, unknown>) => ({ request: { identifier: key, content: { data: payload } } });
beforeEach(() => {
  jest.clearAllMocks();
  cancelMessageReconciliation();
  (outboxScope as jest.Mock).mockReturnValue("u7");
  (pendingMutations as jest.Mock).mockResolvedValue([]);
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([]);
  (Notifications.dismissNotificationAsync as jest.Mock).mockResolvedValue(undefined);
  (getNotificationBadgeCounts as jest.Mock).mockResolvedValue({ total_unread_count: 5 });
  (pulseApi as jest.Mock).mockImplementation(async (_url, options) => ({ ok: true, recipientUserId: 7, dismiss: JSON.parse(options.body).entries.filter((e: { messageId: number }) => e.messageId === 1).map((e: { key: string }) => e.key) }));
});
it.each(["security", "login", "private_office", "marketplace", "payment", "crypto", "follow", "reaction", "status", "reel", "admin", "missed_call", "live", "system", "unknown"])("preserves %s even with message IDs", async type => {
  expect(parseMessageNotification("os", { ...data(), notificationType: type }, 7)).toBeNull();
  expect(parseMessageNotification("os", { type, message_id: 1, conversation_id: 10, notification_id: 9 }, 7)).toBeNull();
});
it.each([{ schemaVersion: 2 }, { recipientUserId: 8 }, { messageId: true }, { messageId: -1 }, { messageId: "broken" }, { messageNamespace: "other" }])("rejects malformed/unknown contract %j", fields => {
  expect(parseMessageNotification("os", { ...data(), ...fields }, 7)).toBeNull();
});
it("removes duplicate read messages by OS identifier while preserving newer and other conversations", async () => {
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("os-a", data()), note("os-duplicate", data()), note("os-b", data(2)), note("os-c", data(3, 20)), note("security", { type: "security" })]);
  const result = await reconcileMessageNotifications();
  expect(Notifications.dismissNotificationAsync).toHaveBeenCalledTimes(2);
  expect(Notifications.dismissNotificationAsync).toHaveBeenCalledWith("os-a");
  expect(Notifications.dismissNotificationAsync).toHaveBeenCalledWith("os-duplicate");
  expect(result).toMatchObject({ dismissed: 2, preserved: 3 });
});
it("dismisses exact durable offline IDs before waiting for the server", async () => {
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("read", data()), note("newer", data(2))]);
  (pendingMutations as jest.Mock).mockResolvedValue([{ type: "messenger.read", payload: { conversationId: 10, messageIds: [1], accountScope: "u7" } }]);
  (pulseApi as jest.Mock).mockImplementation(async () => { expect(Notifications.dismissNotificationAsync).toHaveBeenCalledWith("read"); throw Error("offline"); });
  expect(await reconcileMessageNotifications()).toMatchObject({ dismissed: 1, failures: 1 });
});
it("preserves notifications and badge on server failure", async () => {
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("os", data())]);
  (pulseApi as jest.Mock).mockRejectedValue(Error("unavailable"));
  (getNotificationBadgeCounts as jest.Mock).mockRejectedValue(Error("offline"));
  await reconcileMessageNotifications();
  expect(Notifications.dismissNotificationAsync).not.toHaveBeenCalled();
  expect(Notifications.setBadgeCountAsync).not.toHaveBeenCalled();
});
it("cancels outstanding work on logout or account switch", async () => {
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("os", data())]);
  (pulseApi as jest.Mock).mockImplementation(async () => { cancelMessageReconciliation(); return { ok: true, recipientUserId: 7, dismiss: ["os"] }; });
  expect(await reconcileMessageNotifications()).toMatchObject({ cancelled: true });
  expect(Notifications.dismissNotificationAsync).not.toHaveBeenCalled();
  expect(Notifications.setBadgeCountAsync).not.toHaveBeenCalled();
});
it("continues after one dismissal fails and retries on next lifecycle pass", async () => {
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("a", data()), note("b", data())]);
  (Notifications.dismissNotificationAsync as jest.Mock).mockRejectedValueOnce(Error("native"));
  expect(await reconcileMessageNotifications()).toMatchObject({ dismissed: 1, failures: 1 });
  expect(await reconcileMessageNotifications()).toMatchObject({ dismissed: 2, failures: 0 });
});
it("coalesces duplicate triggers with a trailing pass", async () => {
  const first = reconcileMessageNotifications();
  expect(reconcileMessageNotifications()).toBe(first);
  await first;
  expect(Notifications.getPresentedNotificationsAsync).toHaveBeenCalledTimes(2);
});
it("preserves unknown legacy payloads and verifies identified legacy on server", async () => {
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue([note("legacy", { type: "message", message_id: 1, conversation_id: 10, notification_id: 9 }), note("unknown", { type: "message", message_id: 1, conversation_id: 10 })]);
  expect(await reconcileMessageNotifications()).toMatchObject({ dismissed: 1, preserved: 1 });
});
it.each([0, 1, 99, 100, 250])("sets authoritative combined badge %i exactly", async count => {
  (getNotificationBadgeCounts as jest.Mock).mockResolvedValue({ total_unread_count: count });
  await reconcileMessageNotifications();
  expect(Notifications.setBadgeCountAsync).toHaveBeenCalledWith(count);
});
it("bounds batches without truncating a large Notification Center", async () => {
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockResolvedValue(Array.from({ length: 205 }, (_, i) => note(`os-${i}`, data())));
  expect(await reconcileMessageNotifications()).toMatchObject({ dismissed: 205 });
  expect(pulseApi).toHaveBeenCalledTimes(3);
});
it("handles native enumeration failure", async () => {
  (Notifications.getPresentedNotificationsAsync as jest.Mock).mockRejectedValue(Error("native"));
  expect(await reconcileMessageNotifications()).toMatchObject({ failures: 1 });
});
