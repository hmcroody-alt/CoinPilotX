/**
 * Production Dashboard Map — canonical tile→section mapping.
 *
 * The rail tiles and the command-center sections both render from
 * `dashboardModuleGroups`, so the registry here must be a perfect bijection
 * against that data: every tile has exactly one canonical destination, no tile
 * is a no-op, and no tile can land on the wrong section.
 */

import {
  DASHBOARD_MAP_SECTIONS,
  DASHBOARD_SECTION_TOP_CLEARANCE,
  dashboardSectionForGroupKey,
  dashboardSectionScrollOffset,
  groupKeyForSectionParam
} from "../dashboardMapNavigation";
import { dashboardModuleGroups } from "../../data/dashboardModules";

const EXPECTED: Array<[string, string]> = [
  ["account", "account-command-center"],
  ["network", "pulse-network"],
  ["creator", "creator-studio"],
  ["intelligence", "intelligence"],
  ["economy", "economy-earnings"],
  ["media", "pulse-radio-media"],
  ["crypto", "crypto-command-center"],
  ["safety", "moderation-safety"],
  ["ads", "ads-sponsorships"],
  ["ai", "pulsesoc-ai"],
  ["system", "system-status"]
];

describe("dashboard map section registry", () => {
  test.each(EXPECTED)("tile %s → exactly the %s section", (sectionId, groupKey) => {
    expect(groupKeyForSectionParam(sectionId)).toBe(groupKey);
    expect(dashboardSectionForGroupKey(groupKey)?.sectionId).toBe(sectionId);
  });

  it("covers all 11 tiles with no no-op and no wrong section", () => {
    expect(DASHBOARD_MAP_SECTIONS).toHaveLength(11);
    // Every rendered module group (tile AND section source) has a canonical entry.
    const dataKeys = dashboardModuleGroups.map((group) => group.key);
    expect(dataKeys).toHaveLength(11);
    for (const key of dataKeys) {
      expect(dashboardSectionForGroupKey(key)).not.toBeNull();
    }
    // Bijection: no duplicate destinations, no duplicate ids.
    expect(new Set(DASHBOARD_MAP_SECTIONS.map((s) => s.groupKey)).size).toBe(11);
    expect(new Set(DASHBOARD_MAP_SECTIONS.map((s) => s.sectionId)).size).toBe(11);
    // Registry order mirrors on-screen order, so tile n cannot target section m.
    expect(DASHBOARD_MAP_SECTIONS.map((s) => s.groupKey)).toEqual(dataKeys);
  });

  it("every tile has a distinct accessibility label key", () => {
    const labelKeys = DASHBOARD_MAP_SECTIONS.map((s) => s.labelKey);
    expect(new Set(labelKeys).size).toBe(11);
    for (const key of labelKeys) {
      expect(key).toMatch(/^discovery:dashboardMap\.sections\.[a-z]+$/);
    }
  });
});

describe("deep section parameter", () => {
  it("normalizes case and whitespace", () => {
    expect(groupKeyForSectionParam("CRYPTO")).toBe("crypto-command-center");
    expect(groupKeyForSectionParam("  System ")).toBe("system-status");
  });

  it("rejects unknown, empty, and missing ids (never an approximate jump)", () => {
    expect(groupKeyForSectionParam("payments")).toBeNull();
    expect(groupKeyForSectionParam("")).toBeNull();
    expect(groupKeyForSectionParam(undefined)).toBeNull();
    expect(groupKeyForSectionParam(null)).toBeNull();
  });
});

describe("scroll offset", () => {
  it("aligns the section heading near the top with fixed clearance", () => {
    expect(dashboardSectionScrollOffset(4000)).toBe(4000 - DASHBOARD_SECTION_TOP_CLEARANCE);
    expect(dashboardSectionScrollOffset(500.5)).toBe(500.5 - DASHBOARD_SECTION_TOP_CLEARANCE);
  });

  it("clamps at the top so the first section never over-scrolls", () => {
    expect(dashboardSectionScrollOffset(0)).toBe(0);
    expect(dashboardSectionScrollOffset(DASHBOARD_SECTION_TOP_CLEARANCE - 1)).toBe(0);
  });

  it("is deterministic for repeated jumps to the same section", () => {
    expect(dashboardSectionScrollOffset(1234)).toBe(dashboardSectionScrollOffset(1234));
  });

  it("degrades safely on non-finite measurements", () => {
    expect(dashboardSectionScrollOffset(Number.NaN)).toBe(0);
    expect(dashboardSectionScrollOffset(Number.POSITIVE_INFINITY)).toBe(0);
  });
});
