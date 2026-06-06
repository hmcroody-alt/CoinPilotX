import { pulseApi } from "./apiClient";

export function trackMobileEvent(eventName: string, metadata: Record<string, unknown> = {}) {
  pulseApi("/api/track", {
    method: "POST",
    body: JSON.stringify({
      event: eventName,
      source: "pulse_mobile",
      metadata
    })
  }).catch(() => undefined);
}
