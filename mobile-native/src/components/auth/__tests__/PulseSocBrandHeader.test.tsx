import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("../../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: () => true
}));

import { PulseSocBrandHeader } from "../PulseSocBrandHeader";
import { activateLocale } from "../../../i18n/engine";

/**
 * The eyebrow, tagline and accessibility label are catalog-driven now, and the
 * engine only serves a namespace once it is loaded. `I18nProvider` does this
 * before the first frame in the app; without it here the header renders
 * humanized keys instead of the copy these tests assert on.
 */
beforeAll(async () => {
  await activateLocale("en");
});

describe("PulseSocBrandHeader", () => {
  it("renders the real PulseSoc logo image asset (not a code-drawn substitute)", () => {
    const { toJSON } = render(<PulseSocBrandHeader />);
    const tree = JSON.stringify(toJSON());
    expect(tree).toMatch(/"type":"Image"/);
  });

  it("does not hand-type the wordmark — the lockup lives inside the image", () => {
    const { queryByText } = render(<PulseSocBrandHeader />);
    expect(queryByText("Pulse")).toBeNull();
    expect(queryByText("Soc")).toBeNull();
  });

  it("exposes an accessible PulseSoc brand label for screen readers", () => {
    const { getByLabelText } = render(<PulseSocBrandHeader />);
    expect(getByLabelText(/PulseSoc logo/i)).toBeTruthy();
  });

  it("shows the supporting eyebrow and tagline copy", () => {
    const { getByText } = render(<PulseSocBrandHeader />);
    expect(getByText("Native Access")).toBeTruthy();
    expect(getByText("Your network is ready.")).toBeTruthy();
  });

  it("does not display any website URL or .com slogan raster", () => {
    const { queryByText, toJSON } = render(<PulseSocBrandHeader />);
    expect(queryByText(/\.com/i)).toBeNull();
    expect(JSON.stringify(toJSON())).not.toMatch(/pulsesoc\.com/i);
  });
});
