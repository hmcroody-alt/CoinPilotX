/**
 * The premium hard lock, exercised at its three renders.
 *
 * The assertions are rendered ones, for the same reason the alert-form suite's
 * are: a gate that computed the right boolean and rendered the wrong branch
 * would pass any spy-based test. What matters is what a member can see — and
 * above all what they can NOT see: a locked gate must not mount the body at
 * all, because a mounted body fires feature requests the member is not
 * entitled to make.
 */
import React from "react";
import { Text } from "react-native";
import { act, render, screen } from "@testing-library/react-native";

import type { TierAnswer } from "../canonicalTier";

const mockLoad = jest.fn();
let mockAnswer: TierAnswer;

jest.mock("../useCanonicalTier", () => ({
  useCanonicalTier: () => mockAnswer,
  loadCanonicalTier: (...args: unknown[]) => mockLoad(...args),
  resetCanonicalTier: jest.fn()
}));

import { PremiumFeatureGate, trialDaysLeft } from "../PremiumFeatureGate";
import { UNKNOWN_TIER } from "../canonicalTier";
import { activateLocale } from "../../i18n/engine";

beforeAll(async () => {
  await activateLocale("en");
});

function resolved(overrides: Partial<TierAnswer> = {}): TierAnswer {
  return {
    state: "resolved",
    effectiveTier: "PREMIUM",
    status: "active",
    source: "stripe",
    expiresAt: null,
    features: {},
    verifiedAt: "2026-09-01T00:00:00Z",
    ...overrides
  };
}

const body = () => <Text>FEATURE BODY</Text>;

function renderGate() {
  return render(
    <PremiumFeatureGate onUpgrade={jest.fn()}>{body()}</PremiumFeatureGate>
  );
}

describe("PremiumFeatureGate", () => {
  beforeEach(() => {
    mockLoad.mockReset();
    mockLoad.mockResolvedValue(undefined);
  });

  it("renders the body for a resolved PREMIUM member", async () => {
    mockAnswer = resolved();
    renderGate();
    await act(async () => {});
    expect(screen.getByText("FEATURE BODY")).toBeTruthy();
  });

  it("renders the body for higher tiers (inheritance, not equality)", async () => {
    mockAnswer = resolved({ effectiveTier: "PRIVATE_OFFICE" });
    renderGate();
    await act(async () => {});
    expect(screen.getByText("FEATURE BODY")).toBeTruthy();
  });

  it("locks a resolved FREE member out without mounting the body", async () => {
    mockAnswer = resolved({ effectiveTier: "FREE", status: "none" });
    renderGate();
    await act(async () => {});
    expect(screen.queryByText("FEATURE BODY")).toBeNull();
    expect(screen.getByText("See Premium plans")).toBeTruthy();
  });

  it("says 'unavailable', never 'Free', when the resolve failed", async () => {
    mockAnswer = UNKNOWN_TIER;
    renderGate();
    await act(async () => {});
    expect(screen.queryByText("FEATURE BODY")).toBeNull();
    // The distinguishing copy: an outage is not a downgrade.
    expect(screen.getByText("Membership check unavailable")).toBeTruthy();
    expect(screen.queryByText("See Premium plans")).toBeNull();
  });

  it("shows a truthful trial countdown: 6d23h reads as 6 days, not 7", async () => {
    const end = new Date(Date.now() + (6 * 24 + 23) * 3600 * 1000).toISOString();
    mockAnswer = resolved({ source: "trial", expiresAt: end });
    renderGate();
    await act(async () => {});
    expect(screen.getByText("6 days left in your Premium trial")).toBeTruthy();
    expect(screen.getByText("FEATURE BODY")).toBeTruthy();
  });

  it("shows no countdown for a non-trial grant", async () => {
    mockAnswer = resolved({ expiresAt: new Date(Date.now() + 86400000).toISOString() });
    renderGate();
    await act(async () => {});
    expect(screen.queryByText(/Premium trial/)).toBeNull();
  });
});

describe("trialDaysLeft", () => {
  const now = new Date("2026-09-04T12:00:00Z");
  const at = (hours: number) =>
    resolved({ source: "trial", expiresAt: new Date(now.getTime() + hours * 3600 * 1000).toISOString() });

  it("floors partial days", () => {
    expect(trialDaysLeft(at(6 * 24 + 23), now)).toBe(6);
    expect(trialDaysLeft(at(7 * 24), now)).toBe(7);
    expect(trialDaysLeft(at(5), now)).toBe(0);
  });

  it("is null once expired, for unavailable answers, and for non-trial grants", () => {
    expect(trialDaysLeft(at(-1), now)).toBeNull();
    expect(trialDaysLeft(UNKNOWN_TIER, now)).toBeNull();
    expect(trialDaysLeft(resolved({ expiresAt: at(24).expiresAt }), now)).toBeNull();
    expect(trialDaysLeft(resolved({ source: "trial", expiresAt: null }), now)).toBeNull();
    expect(trialDaysLeft(null, now)).toBeNull();
  });
});
