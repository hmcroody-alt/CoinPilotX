/**
 * Production Dashboard Map navigation.
 *
 * The Mission Control ("User Dashboard") screen renders a rail of tiles — one
 * per command-center group — followed by the real sections for those same
 * groups on the same ScrollView. Every tile must land the user exactly on its
 * own canonical section: no approximate offsets, no dead cards.
 *
 * This module holds the pure, testable pieces: the canonical section registry,
 * the tile→section mapping, deep-link section-id normalization, and the scroll
 * offset math. The screen owns only measurement (onLayout) and the actual
 * scrollTo call.
 */

export type DashboardMapSectionId =
  | "account"
  | "network"
  | "creator"
  | "intelligence"
  | "economy"
  | "media"
  | "crypto"
  | "safety"
  | "ads"
  | "ai"
  | "system";

export type DashboardMapSection = {
  /** Stable canonical identifier, usable as a `section` navigation param. */
  sectionId: DashboardMapSectionId;
  /** Key of the module group in `dashboardModuleGroups` (data + rail + section). */
  groupKey: string;
  /** i18n key for the localized command-center name used in accessibility labels. */
  labelKey: string;
};

/**
 * Canonical registry. Order mirrors the rail/section order on screen.
 * `groupKey` values must match `dashboardModuleGroups[].key` exactly — the
 * regression suite asserts the bijection against the real data module.
 */
export const DASHBOARD_MAP_SECTIONS: readonly DashboardMapSection[] = [
  { sectionId: "account", groupKey: "account-command-center", labelKey: "discovery:dashboardMap.sections.account" },
  { sectionId: "network", groupKey: "pulse-network", labelKey: "discovery:dashboardMap.sections.network" },
  { sectionId: "creator", groupKey: "creator-studio", labelKey: "discovery:dashboardMap.sections.creator" },
  { sectionId: "intelligence", groupKey: "intelligence", labelKey: "discovery:dashboardMap.sections.intelligence" },
  { sectionId: "economy", groupKey: "economy-earnings", labelKey: "discovery:dashboardMap.sections.economy" },
  { sectionId: "media", groupKey: "pulse-radio-media", labelKey: "discovery:dashboardMap.sections.media" },
  { sectionId: "crypto", groupKey: "crypto-command-center", labelKey: "discovery:dashboardMap.sections.crypto" },
  { sectionId: "safety", groupKey: "moderation-safety", labelKey: "discovery:dashboardMap.sections.safety" },
  { sectionId: "ads", groupKey: "ads-sponsorships", labelKey: "discovery:dashboardMap.sections.ads" },
  { sectionId: "ai", groupKey: "pulsesoc-ai", labelKey: "discovery:dashboardMap.sections.ai" },
  { sectionId: "system", groupKey: "system-status", labelKey: "discovery:dashboardMap.sections.system" }
];

/**
 * Breathing room above the section heading after a jump so the title never sits
 * flush against (or under) the viewport edge. The native stack header lives
 * outside the ScrollView viewport, so no dynamic header inset is required —
 * this is purely visual clearance inside the content.
 */
export const DASHBOARD_SECTION_TOP_CLEARANCE = 12;

const BY_GROUP_KEY = new Map(DASHBOARD_MAP_SECTIONS.map((section) => [section.groupKey, section]));
const BY_SECTION_ID = new Map(DASHBOARD_MAP_SECTIONS.map((section) => [section.sectionId, section]));

/** Section descriptor for a module-group key, or null for unknown groups. */
export function dashboardSectionForGroupKey(groupKey: string): DashboardMapSection | null {
  return BY_GROUP_KEY.get(groupKey) ?? null;
}

/**
 * Resolve a `section` navigation param (case/whitespace tolerant) to the
 * module-group key that owns the canonical section. Unknown ids resolve to
 * null and must be ignored by the screen — never an approximate jump.
 */
export function groupKeyForSectionParam(section: string | undefined | null): string | null {
  if (typeof section !== "string") return null;
  const normalized = section.trim().toLowerCase();
  if (!normalized) return null;
  return BY_SECTION_ID.get(normalized as DashboardMapSectionId)?.groupKey ?? null;
}

/**
 * Content offset for a section measured at `layoutY` (relative to the scroll
 * content). Subtracts the clearance and clamps at the top so the first section
 * never over-scrolls into negative space.
 */
export function dashboardSectionScrollOffset(layoutY: number): number {
  if (!Number.isFinite(layoutY)) return 0;
  return Math.max(0, layoutY - DASHBOARD_SECTION_TOP_CLEARANCE);
}
