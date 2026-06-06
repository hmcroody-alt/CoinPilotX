import { trackMobileEvent } from "../analytics";

export function emitFeedHook(eventName: "like" | "comment" | "repost", payload: Record<string, unknown>) {
  trackMobileEvent(`mobile_${eventName}`, payload);
}
