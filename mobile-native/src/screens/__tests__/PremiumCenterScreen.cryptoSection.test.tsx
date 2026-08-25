/**
 * The Premium crypto section: does it show, and does it open the real screens?
 *
 * The section was added because Premium advertised crypto tools it never linked
 * to — a member on a paid plan saw "The requested PulseSoc service was not
 * found." The fix is navigation into the screens that already exist, so the
 * assertion that matters is *which* screens it navigates to. If a future change
 * points a tile at a Premium-only copy of the portfolio or the alert engine,
 * these tests fail, which is the entire point: the mission forbade duplicating
 * those systems and a duplicate is invisible in review once it renders fine.
 *
 * Watchlist is pinned by name rather than folded into the loop. It was the one
 * entry missing from Premium altogether, and a loop over whatever the array
 * happens to contain would pass just as happily if it went missing again.
 */
import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({
  LinearGradient: ({ children }: { children?: React.ReactNode }) => children ?? null
}));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => value, number: (value: number) => String(value) })
}));

import { CryptoIntelligenceSection } from "../PremiumCenterScreen";

type NavigateCall = { screen: string; params?: any };

function makeNavigation() {
  const calls: NavigateCall[] = [];
  return { navigation: { navigate: (screen: string, params?: any) => calls.push({ screen, params }) } as any, calls };
}

/** The four tiles, in the order the mission specified them. */
const ENTRIES = [
  ["alerts", "CryptoAlertManagement"],
  ["portfolio", "Portfolio"],
  ["watchlist", "Watchlists"],
  ["intelligence", "IntelligenceCenter"]
] as const;

function renderHeld(navigation: any) {
  return render(<CryptoIntelligenceSection experience="active" held navigation={navigation} />);
}

describe("Premium crypto section visibility", () => {
  it("shows for a member who holds Premium", () => {
    const { navigation } = makeNavigation();
    const { getByText } = renderHeld(navigation);
    expect(getByText("premium:cryptoIntelligence.heading")).toBeTruthy();
    expect(getByText("premium:cryptoIntelligence.subhead")).toBeTruthy();
  });

  it("shows for the founder experience even without a purchased plan", () => {
    // Founder access is granted rather than bought; gating on `held` alone would
    // hide the section from the one account most likely to be checking it.
    const { navigation } = makeNavigation();
    const { getByText } = render(
      <CryptoIntelligenceSection experience="founder" held={false} navigation={navigation} />
    );
    expect(getByText("premium:cryptoIntelligence.heading")).toBeTruthy();
  });

  it.each(["none", "expired", "grace", "hold"] as const)(
    "stays hidden for a %s member who does not hold Premium",
    (experience) => {
      // The section is Premium-gated, but it must gate by hiding rather than by
      // showing dead rows — a tile that opens nothing is the bug being fixed.
      // `expired` matters most: a lapsed member is on the purchase surface, and
      // crypto tiles there would be selling access they no longer have.
      const { navigation } = makeNavigation();
      const { queryByText } = render(
        <CryptoIntelligenceSection experience={experience} held={false} navigation={navigation} />
      );
      expect(queryByText("premium:cryptoIntelligence.heading")).toBeNull();
    }
  );

  it("shows for a member in grace or hold who still holds Premium", () => {
    // Billing trouble is not loss of access. `held` is the live entitlement, and
    // gating on the experience label instead would cut a paying member off from
    // their own holdings over a failed card retry.
    for (const experience of ["grace", "hold"] as const) {
      const { navigation } = makeNavigation();
      const { queryByText } = render(
        <CryptoIntelligenceSection experience={experience} held navigation={navigation} />
      );
      expect(queryByText("premium:cryptoIntelligence.heading")).toBeTruthy();
    }
  });
});

describe("Premium crypto section destinations", () => {
  it("renders exactly the four entries, each with a label and a description", () => {
    const { navigation } = makeNavigation();
    const { getByText } = renderHeld(navigation);
    for (const [key] of ENTRIES) {
      expect(getByText(`premium:cryptoIntelligence.items.${key}.label`)).toBeTruthy();
      expect(getByText(`premium:cryptoIntelligence.items.${key}.hint`)).toBeTruthy();
    }
  });

  it.each(ENTRIES)("opens %s on the canonical %s screen", (key, screen) => {
    const { navigation, calls } = makeNavigation();
    const { getByLabelText } = renderHeld(navigation);
    fireEvent.press(getByLabelText(`premium:cryptoIntelligence.items.${key}.label`));
    expect(calls).toEqual([{ screen, params: undefined }]);
  });

  it("includes the watchlist, which Premium was missing entirely", () => {
    const { navigation, calls } = makeNavigation();
    const { getByLabelText } = renderHeld(navigation);
    fireEvent.press(getByLabelText("premium:cryptoIntelligence.items.watchlist.label"));
    expect(calls).toEqual([{ screen: "Watchlists", params: undefined }]);
  });

  it("passes no title param, so each screen keeps its translated header", () => {
    // A hardcoded English `title` is why other entry points show "Watchlists"
    // above an otherwise French screen. Every tile here must stay clear of it.
    const { navigation, calls } = makeNavigation();
    const { getByLabelText } = renderHeld(navigation);
    for (const [key] of ENTRIES) {
      fireEvent.press(getByLabelText(`premium:cryptoIntelligence.items.${key}.label`));
    }
    expect(calls.map((call) => call.params)).toEqual([undefined, undefined, undefined, undefined]);
  });
});
