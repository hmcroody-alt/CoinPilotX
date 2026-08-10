/**
 * Policy Center data layer (wave 2 — the server-backed board with appeals).
 *
 * Binds `services/pulse_ads_os.py`:
 *
 *   • `GET  /api/pulse/ads/policy-center?account_id=` → `policy_center`
 *     (`{account_status, verification_status, counts, rejected, appeals,
 *     restrictions}`).
 *   • `POST /api/pulse/ads/appeals` (`{creative_id, message}`) → `create_appeal`.
 *     Server errors are sentences (empty message; open appeal → 409) and are
 *     surfaced verbatim. Rate limit: 10 appeals per hour.
 *   • `GET  /api/pulse/ads/appeals?account_id=` → `list_appeals`
 *     (`account_id: 0` spans every owned account).
 *
 * `appealable: false` on a rejected creative means an appeal is already open —
 * the compose button must not render, because the server would 409 the submit.
 *
 * The portal-derived read-only model in `adsPolicy.ts` stays untouched; the
 * manager tile still uses it. This module serves the full-screen Policy Center.
 */
import { pulseApi } from "./pulseApi";

const nonNegInt = (value: unknown): number => Math.max(0, Math.round(Number(value) || 0));

/* ------------------------------------------------------------------ *
 * Appeals
 * ------------------------------------------------------------------ */

export type AdAppeal = {
  id: number;
  account_id: number;
  creative_id: number;
  campaign_id: number;
  message: string;
  /** "open" until decided. */
  status: string;
  decision: string;
  decision_reason: string;
  decided_at: string;
  resolution_notes: string;
  created_at: string;
  updated_at: string;
};

export function normalizeAdAppeal(value?: Partial<AdAppeal> | null): AdAppeal {
  return {
    id: nonNegInt(value?.id),
    account_id: nonNegInt(value?.account_id),
    creative_id: nonNegInt(value?.creative_id),
    campaign_id: nonNegInt(value?.campaign_id),
    message: String(value?.message || ""),
    status: String(value?.status || "open"),
    decision: String(value?.decision || ""),
    decision_reason: String(value?.decision_reason || ""),
    decided_at: String(value?.decided_at || ""),
    resolution_notes: String(value?.resolution_notes || ""),
    created_at: String(value?.created_at || ""),
    updated_at: String(value?.updated_at || "")
  };
}

export function adAppealIsOpen(appeal: Pick<AdAppeal, "status">): boolean {
  return String(appeal.status || "").toLowerCase() === "open";
}

export async function createAdAppeal(creativeId: number, message: string): Promise<AdAppeal> {
  const data = await pulseApi<{ appeal?: Partial<AdAppeal> }>("/api/pulse/ads/appeals", {
    method: "POST",
    body: JSON.stringify({ creative_id: creativeId, message })
  });
  return normalizeAdAppeal(data.appeal);
}

export async function listAdAppeals(accountId = 0): Promise<AdAppeal[]> {
  const data = await pulseApi<{ appeals?: Partial<AdAppeal>[] }>(
    `/api/pulse/ads/appeals?account_id=${encodeURIComponent(String(accountId))}`
  );
  return (Array.isArray(data.appeals) ? data.appeals : [])
    .map(normalizeAdAppeal)
    .filter((appeal) => appeal.id > 0);
}

/* ------------------------------------------------------------------ *
 * The board
 * ------------------------------------------------------------------ */

/** "destination" | "media" | "targeting" | "creative_text" — the component the
 *  rejection names, so the fix guidance can point at the right part of the ad. */
export type AdPolicyComponent = "destination" | "media" | "targeting" | "creative_text";

export type AdPolicyRejection = {
  id: number;
  campaign_id: number;
  title: string;
  creative_type: string;
  status: string;
  moderation_status: string;
  updated_at: string;
  rejection_reason: string;
  affected_component: AdPolicyComponent;
  /** False when an appeal is already open — never render Compose on false. */
  appealable: boolean;
};

export function normalizeAdPolicyRejection(value?: Record<string, unknown> | null): AdPolicyRejection {
  const component = String(value?.affected_component || "").toLowerCase();
  return {
    id: nonNegInt(value?.id),
    campaign_id: nonNegInt(value?.campaign_id),
    title: String(value?.title || ""),
    creative_type: String(value?.creative_type || ""),
    status: String(value?.status || ""),
    moderation_status: String(value?.moderation_status || ""),
    updated_at: String(value?.updated_at || ""),
    rejection_reason: String(value?.rejection_reason || ""),
    affected_component: (["destination", "media", "targeting", "creative_text"] as const).includes(
      component as AdPolicyComponent
    )
      ? (component as AdPolicyComponent)
      : "creative_text",
    // Missing flag reads as NOT appealable: offering a compose box the server
    // would 409 is the switch-that-silently-no-ops failure.
    appealable: value?.appealable === true
  };
}

export type AdPolicyRestriction = {
  creative_id: number;
  flag_type: string;
  severity: string;
  details: string;
  created_at: string;
};

export type AdPolicyCenter = {
  account_status: string;
  verification_status: string;
  counts: { in_review: number; approved: number; rejected: number; restricted: number };
  rejected: AdPolicyRejection[];
  appeals: AdAppeal[];
  restrictions: AdPolicyRestriction[];
};

export function normalizeAdPolicyCenter(value?: Record<string, unknown> | null): AdPolicyCenter {
  const counts = (value?.counts || {}) as Record<string, unknown>;
  return {
    account_status: String(value?.account_status || ""),
    verification_status: String(value?.verification_status || ""),
    counts: {
      in_review: nonNegInt(counts.in_review),
      approved: nonNegInt(counts.approved),
      rejected: nonNegInt(counts.rejected),
      restricted: nonNegInt(counts.restricted)
    },
    rejected: (Array.isArray(value?.rejected)
      ? (value!.rejected as Array<Record<string, unknown>>)
      : []
    )
      .map(normalizeAdPolicyRejection)
      .filter((rejection) => rejection.id > 0),
    appeals: (Array.isArray(value?.appeals) ? (value!.appeals as Partial<AdAppeal>[]) : [])
      .map(normalizeAdAppeal)
      .filter((appeal) => appeal.id > 0),
    restrictions: (Array.isArray(value?.restrictions)
      ? (value!.restrictions as Array<Record<string, unknown>>)
      : []
    ).map((restriction) => ({
      creative_id: nonNegInt(restriction?.creative_id),
      flag_type: String(restriction?.flag_type || ""),
      severity: String(restriction?.severity || ""),
      details: String(restriction?.details || ""),
      created_at: String(restriction?.created_at || "")
    }))
  };
}

export async function getAdPolicyCenter(accountId: number): Promise<AdPolicyCenter> {
  const data = await pulseApi<Record<string, unknown>>(
    `/api/pulse/ads/policy-center?account_id=${encodeURIComponent(String(accountId))}`
  );
  return normalizeAdPolicyCenter(data);
}

/** The open appeal for one creative, if any — drives the "appeal pending" row. */
export function openAppealForCreative(appeals: AdAppeal[], creativeId: number): AdAppeal | null {
  return (
    appeals.find((appeal) => appeal.creative_id === creativeId && adAppealIsOpen(appeal)) || null
  );
}
