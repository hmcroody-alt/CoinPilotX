import { pulseApi } from "./pulseApi";

export type AdCreativeType = "image" | "video" | "text" | "hologram" | "audio";

export type SponsoredAd = {
  adId: number;
  creativeId: number;
  campaignId: number;
  placementKey: string;
  label: string;
  creativeType: AdCreativeType | string;
  title: string;
  body: string;
  mediaUrl: string;
  thumbnailUrl: string;
  playbackUrl: string;
  mediaType: string;
  width: number;
  height: number;
  durationSeconds: number;
  destinationUrl: string;
  callToAction: string;
  cardStyle: string;
  placementType: string;
  deliveryToken: string;
  trackingNonce: string;
  expiresAt: string;
  reportable: boolean;
};

type RawAd = {
  ad_id?: number;
  creative_id?: number;
  campaign_id?: number;
  placement_key?: string;
  label?: string;
  creative_type?: string;
  title?: string;
  body?: string;
  media_url?: string;
  thumbnail_url?: string;
  playback_url?: string;
  media_type?: string;
  width?: number;
  height?: number;
  duration_seconds?: number;
  destination_url?: string;
  call_to_action?: string;
  card_style?: string;
  placement_type?: string;
  delivery_token?: string;
  tracking_nonce?: string;
  expires_at?: string;
  reportable?: boolean;
};

function mapAd(raw: RawAd): SponsoredAd {
  return {
    adId: Number(raw.ad_id ?? raw.creative_id) || 0,
    creativeId: Number(raw.creative_id ?? raw.ad_id) || 0,
    campaignId: Number(raw.campaign_id) || 0,
    placementKey: raw.placement_key || "",
    label: raw.label || "Sponsored",
    creativeType: (raw.creative_type as AdCreativeType) || "text",
    title: raw.title || "",
    body: raw.body || "",
    mediaUrl: raw.media_url || "",
    thumbnailUrl: raw.thumbnail_url || "",
    playbackUrl: raw.playback_url || "",
    mediaType: raw.media_type || "",
    width: Number(raw.width) || 0,
    height: Number(raw.height) || 0,
    durationSeconds: Number(raw.duration_seconds) || 0,
    destinationUrl: raw.destination_url || "",
    callToAction: raw.call_to_action || "Learn more",
    cardStyle: raw.card_style || "signal-card",
    placementType: raw.placement_type || "",
    deliveryToken: raw.delivery_token || "",
    trackingNonce: raw.tracking_nonce || "",
    expiresAt: raw.expires_at || "",
    reportable: raw.reportable !== false
  };
}

// A served ad is only valid until expiresAt; renderers must drop expired ads
// rather than tracking against a stale delivery token (the backend rejects it).
export function isAdExpired(ad: Pick<SponsoredAd, "expiresAt">): boolean {
  if (!ad.expiresAt) return false;
  const expiry = Date.parse(ad.expiresAt);
  if (Number.isNaN(expiry)) return false;
  return expiry <= Date.now();
}

export async function fetchSponsoredAds(options: {
  context?: string;
  limit?: number;
  feedContext?: string;
} = {}): Promise<SponsoredAd[]> {
  const params = new URLSearchParams({
    context: options.context || "home",
    device_type: "mobile",
    limit: String(Math.min(Math.max(options.limit ?? 3, 1), 6))
  });
  if (options.feedContext) params.set("feed_context", options.feedContext);
  try {
    const response = await pulseApi<{ ok?: boolean; ads?: RawAd[] }>(`/api/pulse/ads/placements?${params.toString()}`, {
      method: "GET"
    });
    if (!response?.ok || !Array.isArray(response.ads)) return [];
    return response.ads.map(mapAd).filter((ad) => ad.creativeId > 0 && ad.campaignId > 0 && !isAdExpired(ad));
  } catch {
    return [];
  }
}

type TrackingIdentity = Pick<
  SponsoredAd,
  "creativeId" | "campaignId" | "placementKey" | "deliveryToken" | "trackingNonce"
>;

function trackingBody(ad: TrackingIdentity, extra: Record<string, unknown> = {}) {
  return JSON.stringify({
    creative_id: ad.creativeId,
    campaign_id: ad.campaignId,
    placement_key: ad.placementKey,
    delivery_token: ad.deliveryToken,
    tracking_nonce: ad.trackingNonce,
    ...extra
  });
}

export async function recordAdImpression(
  ad: TrackingIdentity,
  extra: { viewport?: string } = {}
): Promise<number> {
  try {
    const result = await pulseApi<{ ok?: boolean; impression_id?: number }>("/api/pulse/ads/impression", {
      method: "POST",
      body: trackingBody(ad, { viewport: extra.viewport || "" })
    });
    return result?.ok ? Number(result.impression_id) || 0 : 0;
  } catch {
    return 0;
  }
}

export async function recordAdViewability(impressionId: number, visibleMs: number): Promise<boolean> {
  if (!impressionId) return false;
  try {
    const result = await pulseApi<{ ok?: boolean; viewable?: boolean }>("/api/pulse/ads/viewability", {
      method: "POST",
      body: JSON.stringify({ impression_id: impressionId, visible_ms: Math.round(visibleMs) })
    });
    return Boolean(result?.viewable);
  } catch {
    return false;
  }
}

// Routes the click server-side and returns the canonical, server-validated
// destination. Callers must open the returned URL, not the served payload's,
// so the destination always reflects backend validation at click time.
export async function recordAdClick(ad: TrackingIdentity): Promise<string> {
  try {
    const result = await pulseApi<{ ok?: boolean; destination_url?: string }>("/api/pulse/ads/click", {
      method: "POST",
      body: trackingBody(ad)
    });
    return result?.ok ? result.destination_url || "" : "";
  } catch {
    return "";
  }
}

export type AdEventType =
  | "hide"
  | "report"
  | "save"
  | "dismiss"
  | "conversion"
  | "mute"
  | "unmute"
  | "video_start"
  | "video_25"
  | "video_50"
  | "video_75"
  | "video_complete"
  | "audio_start"
  | "audio_complete"
  | "error";

export async function recordAdEvent(ad: TrackingIdentity, eventType: AdEventType, reason = ""): Promise<boolean> {
  try {
    const result = await pulseApi<{ ok?: boolean }>("/api/pulse/ads/event", {
      method: "POST",
      body: trackingBody(ad, { event_type: eventType, reason })
    });
    return Boolean(result?.ok);
  } catch {
    return false;
  }
}
