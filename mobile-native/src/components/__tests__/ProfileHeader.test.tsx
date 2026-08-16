import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium", Heavy: "heavy" }
}));

jest.mock("@expo/vector-icons", () => ({
  Ionicons: ({ name }: { name: string }) => name
}));

jest.mock("expo-linear-gradient", () => ({
  LinearGradient: ({ children }: { children?: React.ReactNode }) => children ?? null
}));

jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: jest.fn().mockReturnValue(true),
  createLogiNexusAmbientPulse: () => ({ start: jest.fn(), stop: jest.fn() })
}));

import { PulseProfile } from "../../api/profile";
import { ProfileHeader } from "../ProfileHeader";

function baseProfile(overrides: Partial<PulseProfile> = {}): PulseProfile {
  return {
    user_id: 7,
    display_name: "Ada Pulse",
    username: "ada",
    public_player_id: "PULSE-ADA",
    verified_badge: true,
    premium_status: "premium",
    follower_count: 12_400,
    following_count: 318,
    post_count: 92,
    media_count: 40,
    viewer_follows: false,
    theme: { accent_color: "#32e6b3", motion_level: "reduced" },
    ...overrides
  };
}

describe("ProfileHeader (Profile V6)", () => {
  it("renders identity, stats, and the module OS grid", () => {
    const { getByTestId, getByText, queryByText } = render(<ProfileHeader profile={baseProfile()} owner />);
    expect(getByTestId("profile-v6-header")).toBeTruthy();
    expect(getByText("Ada Pulse")).toBeTruthy();
    expect(getByText("@ada")).toBeTruthy();
    expect(queryByText("Pulse ID • PLS-000007")).toBeNull();
    expect(getByText("12K")).toBeTruthy();
    expect(getByText("Pulse DNA")).toBeTruthy();
    expect(getByText("Marketplace")).toBeTruthy();
  });

  it("fires stat and module callbacks when tapped", () => {
    const onStatPress = jest.fn();
    const onModulePress = jest.fn();
    const { getByLabelText } = render(
      <ProfileHeader profile={baseProfile()} owner onStatPress={onStatPress} onModulePress={onModulePress} />
    );
    fireEvent.press(getByLabelText("92 Posts"));
    fireEvent.press(getByLabelText("Trust"));
    expect(onStatPress).toHaveBeenCalledWith("posts");
    expect(onModulePress).toHaveBeenCalledWith("trust");
  });

  it("shows Call and Video actions only for other members", () => {
    const onCall = jest.fn();
    const { getByText, queryByText } = render(
      <ProfileHeader profile={baseProfile({ is_self: false })} owner={false} onCall={onCall} />
    );
    expect(getByText("Message")).toBeTruthy();
    expect(getByText("Call")).toBeTruthy();
    expect(getByText("Video")).toBeTruthy();
    fireEvent.press(getByText("Call"));
    expect(onCall).toHaveBeenCalled();
    expect(queryByText("Edit Profile")).toBeNull();
  });

  // `publicKey` is the route lookup key, and resolveProfileTarget sets
  // `profileKey = userId ? String(userId) : …`. So on any profile opened from a
  // feed author or share link the key IS the internal user id, and the handle
  // fallback chain used to print it as "@1234567" on profiles with no username.
  describe("public handle never exposes the private user id", () => {
    it("drops a numeric route key instead of rendering it as a handle", () => {
      const { queryByText, getByText } = render(
        <ProfileHeader
          profile={baseProfile({ username: undefined, public_player_id: undefined, user_id: 1234567 })}
          owner={false}
          publicKey="1234567"
        />
      );
      expect(queryByText("@1234567")).toBeNull();
      expect(queryByText("1234567")).toBeNull();
      // Falls through to the neutral label rather than leaving a bare "@".
      expect(getByText("PulseSoc identity")).toBeTruthy();
    });

    it("still prefers the public username over a numeric route key", () => {
      const { getByText, queryByText } = render(
        <ProfileHeader profile={baseProfile({ user_id: 1234567 })} owner={false} publicKey="1234567" />
      );
      expect(getByText("@ada")).toBeTruthy();
      expect(queryByText("@1234567")).toBeNull();
    });

    it("keeps using a non-numeric route key, which is a public handle", () => {
      const { getByText } = render(
        <ProfileHeader
          profile={baseProfile({ username: undefined, public_player_id: undefined })}
          owner={false}
          publicKey="ada-pulse"
        />
      );
      expect(getByText("@ada-pulse")).toBeTruthy();
    });
  });

  it("discloses an automated system profile and omits human contact actions", () => {
    const { getByText, getByLabelText, queryByText } = render(
      <ProfileHeader profile={baseProfile({
        display_name: "PulseSoc Insight",
        username: "pulsesoc_insight",
        automated: true,
        account_type: "PULSESOC_AUTOMATED",
        system_account_label: "Official PulseSoc System Account",
        automation_disclosure: "This account is operated automatically by PulseSoc. It is not a human user."
      })} owner={false} />
    );
    expect(getByLabelText("Automated PulseSoc account disclosure")).toBeTruthy();
    expect(getByText("Official PulseSoc System Account")).toBeTruthy();
    expect(queryByText("Message")).toBeNull();
    expect(queryByText("Call")).toBeNull();
    expect(queryByText("Followers")).toBeNull();
    expect(queryByText("Following")).toBeNull();
  });
});
