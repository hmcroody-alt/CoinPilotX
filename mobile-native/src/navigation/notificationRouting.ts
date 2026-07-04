import { Linking } from "react-native";
import * as Notifications from "expo-notifications";
import { createNavigationContainerRef } from "@react-navigation/native";
import { PULSE_API_BASE_URL } from "../api/config";
import { RootStackParamList } from "./types";

export const navigationRef = createNavigationContainerRef<RootStackParamList>();

export type NotificationRouteResult = {
  handled: boolean;
  target: string;
  reason?: string;
};

export function setupNotificationResponseRouting() {
  return Notifications.addNotificationResponseReceivedListener((response) => {
    routeNotificationData(response.notification.request.content.data).catch(() => undefined);
  });
}

export async function routeNotificationData(data: Notifications.NotificationContent["data"] = {}): Promise<NotificationRouteResult> {
  const target = normalizeNotificationTarget(
    String(data?.target_url || data?.deep_link || data?.url || data?.web_url || data?.native_url || data?.app_url || "")
  );
  return routeNotificationTarget(target);
}

export async function routeNotificationTarget(target: string): Promise<NotificationRouteResult> {
  const normalized = normalizeNotificationTarget(target);
  if (!normalized) {
    navigateToNotifications();
    return { handled: true, target: "/pulse/notifications", reason: "missing_target" };
  }

  const messageMatch = normalized.match(/^\/pulse\/messages\/(\d+)/);
  if (messageMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("Chat", { conversationId: Number(messageMatch[1]), title: "Messenger" });
    return { handled: true, target: normalized };
  }

  if (normalized === "/pulse/messages" && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Messenger" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/profile") && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Profile" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/notifications") && navigationRef.isReady()) {
    navigationRef.navigate("NotificationCenter");
    return { handled: true, target: normalized };
  }

  const webTarget = `${PULSE_API_BASE_URL}${normalized}`;
  const supported = await Linking.canOpenURL(webTarget).catch(() => false);
  if (supported) {
    await Linking.openURL(webTarget);
    return { handled: false, target: normalized, reason: "opened_web_fallback" };
  }

  navigateToNotifications();
  return { handled: true, target: "/pulse/notifications", reason: "unsupported_target" };
}

export function normalizeNotificationTarget(raw: string) {
  let value = String(raw || "").trim();
  if (!value || /[\r\n\t]/.test(value)) return "";
  const lowered = value.toLowerCase();
  if (lowered.startsWith("javascript:") || lowered.startsWith("data:") || lowered.startsWith("file:")) return "";
  if (lowered.startsWith("pulsesoc://")) {
    value = customSchemePath(value, "pulsesoc://");
  } else if (lowered.startsWith("pulse://")) {
    value = customSchemePath(value, "pulse://");
  } else if (lowered.startsWith("http://") || lowered.startsWith("https://")) {
    const parsed = new URL(value);
    if (!["pulsesoc.com", "www.pulsesoc.com"].includes(parsed.hostname.toLowerCase())) return "";
    value = `${parsed.pathname}${parsed.search}${parsed.hash}`;
  }
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "";
  if (value.startsWith("/api/") || value.startsWith("/static/") || value.startsWith("/admin/")) return "";
  return value;
}

function navigateToNotifications() {
  if (navigationRef.isReady()) {
    navigationRef.navigate("NotificationCenter");
  }
}

function customSchemePath(value: string, prefix: string) {
  const rest = value.slice(prefix.length);
  const [pathPart, queryPart = ""] = rest.split("?");
  const normalizedPath = `/${pathPart.replace(/^\/+/, "")}`;
  return queryPart ? `${normalizedPath}?${queryPart}` : normalizedPath;
}
