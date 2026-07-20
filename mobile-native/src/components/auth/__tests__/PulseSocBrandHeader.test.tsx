import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({
  Ionicons: ({ name }: { name: string }) => name
}));

jest.mock("../../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: () => true
}));

import { PulseSocBrandHeader } from "../PulseSocBrandHeader";

describe("PulseSocBrandHeader", () => {
  it("renders the two-tone PulseSoc wordmark", () => {
    const { getByText } = render(<PulseSocBrandHeader />);
    expect(getByText("Pulse")).toBeTruthy();
    expect(getByText("Soc")).toBeTruthy();
  });

  it("does not display any website URL or .com slogan raster", () => {
    const { queryByText, toJSON } = render(<PulseSocBrandHeader />);
    expect(queryByText(/\.com/i)).toBeNull();
    expect(JSON.stringify(toJSON())).not.toMatch(/pulsesoc\.com/i);
  });

  it("renders the code-drawn pulse glyph (no baked-in image asset)", () => {
    const { toJSON } = render(<PulseSocBrandHeader />);
    const tree = JSON.stringify(toJSON());
    // Ionicons mock renders the icon name; the pulse glyph should be present.
    expect(tree).toMatch(/pulse/i);
    // No <Image> nodes should be part of the mark (fully code-drawn).
    expect(tree).not.toMatch(/"type":"Image"/);
  });
});
