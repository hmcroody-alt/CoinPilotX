import React from "react";
import { render } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

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

  it("does not render a filled color disc behind the logo", () => {
    const screen = render(<PulseSocBrandHeader />);
    const filledDiscs = screen.UNSAFE_root.findAll((node: { props: { style?: unknown } }) => {
      const style = StyleSheet.flatten(node.props.style as never) as { backgroundColor?: string; width?: number; height?: number } | undefined;
      return Boolean(style?.backgroundColor && Number(style.width) > 24 && Number(style.height) > 24);
    });
    expect(filledDiscs).toHaveLength(0);
  });

  it("exposes an accessible PulseSoc brand label for screen readers", () => {
    const { getByLabelText } = render(<PulseSocBrandHeader />);
    expect(getByLabelText(/PulseSoc.*Connected/i)).toBeTruthy();
  });

  it("shows the live connection state without legacy arrival copy", () => {
    const { getByText } = render(<PulseSocBrandHeader />);
    expect(getByText("Connected")).toBeTruthy();
  });

  it("does not display any website URL or .com slogan raster", () => {
    const { queryByText, toJSON } = render(<PulseSocBrandHeader />);
    expect(queryByText(/\.com/i)).toBeNull();
    expect(JSON.stringify(toJSON())).not.toMatch(/pulsesoc\.com/i);
  });
});
