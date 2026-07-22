import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("../../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: () => true
}));

import { PulseSocBrandHeader } from "../PulseSocBrandHeader";

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
