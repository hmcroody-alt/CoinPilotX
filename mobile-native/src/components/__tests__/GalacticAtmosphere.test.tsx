import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("expo-battery", () => ({
  useLowPowerMode: jest.fn(() => false),
  isLowPowerModeEnabledAsync: jest.fn(async () => false),
  addLowPowerModeListener: jest.fn(() => ({ remove: jest.fn() }))
}));
jest.mock("expo-linear-gradient", () => ({
  LinearGradient: ({ children }: { children?: React.ReactNode }) => children ?? null
}));

import { GalacticAtmosphere } from "../GalacticAtmosphere";

describe("GalacticAtmosphere", () => {
  it("renders as a non-interactive, accessibility-hidden background", () => {
    const screen = render(<GalacticAtmosphere variant="messages" testID="calm-space" />);
    const field = screen.toJSON();
    expect(field && !Array.isArray(field) ? field.props.accessibilityElementsHidden : false).toBe(true);
    expect(field && !Array.isArray(field) ? field.props.importantForAccessibility : "").toBe("no-hide-descendants");
  });

  it("uses only tiny, low-opacity stars", () => {
    const screen = render(<GalacticAtmosphere variant="feed" />);
    const stars = screen.UNSAFE_root.findAll((node: { props: { style?: unknown } }) => {
      const flattened = Array.isArray(node.props.style) ? Object.assign({}, ...node.props.style.filter(Boolean)) : node.props.style;
      return flattened?.backgroundColor === "#CDEBFA";
    });
    expect(stars.length).toBeGreaterThan(20);
    for (const star of stars) {
      const flattened = Array.isArray(star.props.style) ? Object.assign({}, ...star.props.style.filter(Boolean)) : star.props.style;
      expect(Number(flattened.width)).toBeLessThanOrEqual(2);
      if (typeof flattened.opacity === "number") expect(flattened.opacity).toBeLessThanOrEqual(0.2);
    }
  });
});
