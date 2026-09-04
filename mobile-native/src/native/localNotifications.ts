/**
 * Local notification scheduling owner (Phase 12).
 *
 * Device-local reminders only (drafts, briefings, events the user opted
 * into). Remote push stays owned by api/push. Permission goes through the
 * shared orchestrator and is only requested from user-initiated actions.
 */
import * as Notifications from "expo-notifications";
import { checkPermission, requestPermission } from "./permissions";

export type LocalReminder = {
  /** Stable id so re-scheduling replaces instead of duplicating. */
  id: string;
  title: string;
  body: string;
  /** Fire time — must be in the future. */
  date: Date;
  /** Deep-link path (e.g. "briefings" or "event/42") opened on tap. */
  path?: string;
};

export type ScheduleResult = "scheduled" | "permission_denied" | "invalid_date" | "error";

/**
 * Schedule (or replace) a local reminder. Call only from a user action —
 * this may trigger the notification permission prompt on first use.
 */
export async function scheduleLocalReminder(reminder: LocalReminder): Promise<ScheduleResult> {
  if (!(reminder.date instanceof Date) || reminder.date.getTime() <= Date.now()) {
    return "invalid_date";
  }
  const permission = await requestPermission("NOTIFICATIONS");
  if (permission.state !== "GRANTED" && permission.state !== "LIMITED") {
    return "permission_denied";
  }
  try {
    await Notifications.cancelScheduledNotificationAsync(reminder.id).catch(() => undefined);
    await Notifications.scheduleNotificationAsync({
      identifier: reminder.id,
      content: {
        title: reminder.title,
        body: reminder.body,
        data: reminder.path ? { url: `pulsesoc://${reminder.path.replace(/^\//, "")}` } : {}
      },
      trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: reminder.date }
    });
    return "scheduled";
  } catch {
    return "error";
  }
}

export async function cancelLocalReminder(id: string): Promise<void> {
  await Notifications.cancelScheduledNotificationAsync(id).catch(() => undefined);
}

export async function listLocalReminderIds(): Promise<string[]> {
  try {
    const all = await Notifications.getAllScheduledNotificationsAsync();
    return all.map((n) => n.identifier);
  } catch {
    return [];
  }
}

/** Passive check for settings UIs — never prompts. */
export async function localNotificationsAllowed(): Promise<boolean> {
  const snapshot = await checkPermission("NOTIFICATIONS");
  return snapshot.state === "GRANTED" || snapshot.state === "LIMITED";
}
