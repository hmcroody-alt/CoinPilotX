import React from "react";
import { StyleSheet } from "react-native";
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

// Forwards props onto a plain View rather than collapsing to children: the
// gradient ramps ARE the palette, so a mock that throws `colors` away makes
// every colour assertion on this surface unfalsifiable.
jest.mock("expo-linear-gradient", () => {
  const { View } = require("react-native");
  return { LinearGradient: View };
});

jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: jest.fn().mockReturnValue(true),
  createLogiNexusAmbientPulse: () => ({ start: jest.fn(), stop: jest.fn() })
}));

import { PulseProfile } from "../../api/profile";
import { profileNeon } from "../../theme/profileNeon";
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
    // The accent the backend substitutes when the user has no theme row. It is
    // NOT a choice, and the header must not read it as one — see the palette
    // test below and theme/profileNeon.ts.
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


describe("approved automated account cover", () => {
  const cover = "https://pulsesoc.com/static/brand/pulsesoc-insight-cover-20260825.png";
  it("shows the entire banner at its original aspect ratio", () => {
    const { getByTestId } = render(<ProfileHeader profile={baseProfile({
      user_id: 0, public_player_id: "pulsesoc_insight", automated: true, cover_url: cover
    })} />);
    const image = getByTestId("automated-account-brand-cover");
    expect(image.props.source).toEqual({ uri: cover });
    expect(image.props.resizeMode).toBe("contain");
    expect(StyleSheet.flatten(image.props.style)).toMatchObject({ width: "100%", aspectRatio: 2.5, top: 0 });
  });
  it("does not change another account's cover rendering", () => {
    const { queryByTestId } = render(<ProfileHeader profile={baseProfile({ cover_url: cover })} />);
    expect(queryByTestId("automated-account-brand-cover")).toBeNull();
  });
});

describe("profile media editing", () => {
  it("offers both entry points to the owner and wires them to their handlers", () => {
    const onEditAvatar = jest.fn();
    const onEditCover = jest.fn();
    const { getByTestId, getByText } = render(
      <ProfileHeader profile={baseProfile()} owner canEditMedia onEditAvatar={onEditAvatar} onEditCover={onEditCover} />
    );
    fireEvent.press(getByTestId("profile-edit-avatar"));
    fireEvent.press(getByTestId("profile-edit-cover"));
    expect(onEditAvatar).toHaveBeenCalledTimes(1);
    expect(onEditCover).toHaveBeenCalledTimes(1);
    expect(getByText("Edit cover")).toBeTruthy();
  });

  it("shows neither control to a visitor", () => {
    const { queryByTestId } = render(<ProfileHeader profile={baseProfile()} owner={false} />);
    expect(queryByTestId("profile-edit-avatar")).toBeNull();
    expect(queryByTestId("profile-edit-cover")).toBeNull();
  });

  // `owner` is false whenever the profile was reached by tapping a name in the
  // feed, including your own — so the edit controls must not hang off it.
  it("still offers the controls on your own profile reached as a route target", () => {
    const { getByTestId } = render(
      <ProfileHeader profile={baseProfile()} owner={false} canEditMedia onEditAvatar={jest.fn()} onEditCover={jest.fn()} />
    );
    expect(getByTestId("profile-edit-avatar")).toBeTruthy();
    expect(getByTestId("profile-edit-cover")).toBeTruthy();
  });

  it("states the in-flight upload and refuses a second tap", () => {
    const onEditCover = jest.fn();
    const { getByTestId, getByText } = render(
      <ProfileHeader profile={baseProfile()} owner canEditMedia onEditCover={onEditCover} coverBusy />
    );
    const control = getByTestId("profile-edit-cover");
    expect(control.props.accessibilityState).toMatchObject({ busy: true, disabled: true });
    expect(getByText("Uploading…")).toBeTruthy();
    fireEvent.press(control);
    expect(onEditCover).not.toHaveBeenCalled();
  });

  it("gives both controls a reachable label and a 44pt target", () => {
    const { getByLabelText, getByTestId } = render(
      <ProfileHeader profile={baseProfile()} owner canEditMedia onEditAvatar={jest.fn()} onEditCover={jest.fn()} />
    );
    expect(getByLabelText("Change profile photo")).toBeTruthy();
    expect(getByLabelText("Edit cover photo")).toBeTruthy();
    expect(StyleSheet.flatten(getByTestId("profile-edit-cover").props.style)).toMatchObject({ minHeight: 44 });
  });
});

describe("cover fallback", () => {
  // §45: an account that has never set a cover gets a generated one, not a
  // 320pt hole where a picture would be.
  it("draws the generated field when the account has no cover", () => {
    const { getByTestId, queryByTestId } = render(<ProfileHeader profile={baseProfile({ cover_url: "" })} owner />);
    expect(queryByTestId("profile-cover-image")).toBeNull();
    expect(getByTestId("profile-generated-cover")).toBeTruthy();
    expect(StyleSheet.flatten(getByTestId("profile-v6-header").props.style)).not.toMatchObject({ height: 0 });
  });

  it("lays an uploaded cover over that field rather than replacing the hero", () => {
    const { getByTestId } = render(<ProfileHeader profile={baseProfile({ cover_url: "https://cdn/c.jpg" })} owner />);
    expect(getByTestId("profile-cover-image").props.resizeMode).toBe("cover");
    expect(getByTestId("profile-generated-cover")).toBeTruthy();
  });

  it("shows initials rather than a blank disc when there is no avatar", () => {
    const { getByText, queryByTestId } = render(
      <ProfileHeader profile={baseProfile({ avatar_url: "", display_name: "Ada Pulse" })} owner />
    );
    expect(getByText("A")).toBeTruthy();
    expect(queryByTestId("profile-cover-image")).toBeNull();
  });
});

describe("identity palette", () => {
  it("paints the neon ramp on a profile whose accent is only the server default", () => {
    const { getByTestId } = render(<ProfileHeader profile={baseProfile()} owner />);
    expect(getByTestId("profile-identity-ring").props.colors).toEqual([...profileNeon.identityRing]);
  });

  it("yields the ramp to an accent the owner actually chose", () => {
    const { queryByTestId } = render(
      <ProfileHeader profile={baseProfile({ theme: { accent_color: "#ff8a00", motion_level: "reduced" } })} owner />
    );
    expect(queryByTestId("profile-identity-ring")).toBeNull();
  });
});
