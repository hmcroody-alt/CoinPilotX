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

  // The field is a *generated cover*, so its strength is tuned to be the image.
  // Laid over an uploaded photo at that strength it read as a shield: the
  // gradient's middle stop alone was #050910 at 95%, which is very nearly
  // painting the hero black. These pin the step-down, in both directions —
  // an account with no cover must keep the full-strength artwork.
  describe("an uploaded cover outranks the decoration over it", () => {
    /** A cover that has actually arrived — the field only yields to a picture. */
    const withCover = () => {
      const tree = render(<ProfileHeader profile={baseProfile({ cover_url: "https://cdn/c.jpg" })} owner />);
      fireEvent(tree.getByTestId("profile-cover-image"), "load");
      return tree;
    };
    const withoutCover = () => render(<ProfileHeader profile={baseProfile({ cover_url: "" })} owner />);

    // Walks the rendered hero rather than reaching for testIDs, so a *new*
    // overlay added later is caught too instead of being invisible to the test.
    /** The hero subtree only — the body's glass cards are not over the photo. */
    function hero(tree: ReturnType<typeof withCover>) {
      let hit: any = null;
      const find = (node: any) => {
        if (!node || typeof node !== "object" || hit) return;
        if (node.props?.testID === "profile-hero") { hit = node; return; }
        (node.children ?? []).forEach(find);
      };
      find(tree.toJSON());
      if (!hit) throw new Error("hero not rendered");
      return hit;
    }

    function heroFills(tree: ReturnType<typeof withCover>) {
      const found: string[] = [];
      const visit = (node: any) => {
        if (!node || typeof node !== "object") return;
        const flat = StyleSheet.flatten(node.props?.style) as { backgroundColor?: string } | undefined;
        if (flat?.backgroundColor) found.push(String(flat.backgroundColor));
        if (Array.isArray(node.props?.colors)) found.push(...node.props.colors.map(String));
        (node.children ?? []).forEach(visit);
      };
      visit(hero(tree));
      return found;
    }

    /** Alpha of a colour in any of the spellings this surface uses. */
    function alphaOf(colour: string): number {
      if (colour === "transparent") return 0;
      const rgba = colour.match(/^rgba?\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\s*\)$/);
      if (rgba) return Number(rgba[1]);
      if (/^#[0-9a-f]{8}$/i.test(colour)) return parseInt(colour.slice(7), 16) / 255;
      return 1;
    }

    /** Near-black, i.e. a layer whose only effect is to remove the picture. */
    function isDark(colour: string): boolean {
      const hex = colour.match(/^#([0-9a-f]{6})/i);
      if (!hex) return /^rgba?\(\s*[0-5]?\d\s*,\s*[0-5]?\d\s*,\s*[0-9]|[1-5]\d\s*,/.test(colour);
      const n = parseInt(hex[1], 16);
      return (n >> 16) < 60 && ((n >> 8) & 255) < 60 && (n & 255) < 60;
    }

    it("stops painting the hero nearly black", () => {
      const dark = heroFills(withCover()).filter(isDark).map(alphaOf);
      // The bottom blend still reaches the page colour, but nothing may sit at
      // full-bleed near-opacity the way the old middle stop did.
      expect(dark.filter((a) => a >= 0.6 && a < 1)).toEqual([]);
      expect(Math.max(...dark.filter((a) => a < 1))).toBeLessThanOrEqual(0.45);
    });

    it("keeps the black middle stop when the field IS the cover", () => {
      expect(heroFills(withoutCover())).toContain("#050910f2");
    });

    it("drops the lit limb to framing strength and out of the subject's way", () => {
      const framed = StyleSheet.flatten(withCover().getByTestId("profile-generated-cover").props.style);
      expect(framed).toMatchObject({ borderColor: profileNeon.borderFramed, top: 252 });
      const full = StyleSheet.flatten(withoutCover().getByTestId("profile-generated-cover").props.style);
      expect(full).toMatchObject({ borderColor: profileNeon.borderStrong, top: 196 });
    });

    it("confines the page blend to the bottom quarter", () => {
      const blend = heroFills(withCover());
      expect(blend).toContain("transparent");
      // Unlocated, the ramp starts at half the hero and eats the subject.
      const ramps: number[][] = [];
      const visit = (node: any) => {
        if (!node || typeof node !== "object") return;
        if (Array.isArray(node.props?.locations)) ramps.push(node.props.locations);
        (node.children ?? []).forEach(visit);
      };
      visit(hero(withCover()));
      expect(ramps.some((r) => r[1] >= 0.7)).toBe(true);
    });

    // Found on a simulator whose CDN images never arrived: the field had
    // stepped down for a photo that was not there, so the hero came out
    // DARKER than before the fix (mean luminance 40 -> 20). Stepping down has
    // to follow the picture, not the URL.
    //
    // The guard is `onLoad` rather than "has not errored" on purpose: a request
    // that is cancelled instead of failed (a remount mid-flight reports
    // NSURLError -999) never reaches `onError`, and that is exactly the case
    // the simulator hit. Holding full strength until the picture arrives covers
    // failure, cancellation and still-in-flight with one invariant.
    it("keeps the field at full strength until the picture actually arrives", () => {
      const tree = render(<ProfileHeader profile={baseProfile({ cover_url: "https://cdn/c.jpg" })} owner />);
      expect(heroFills(tree)).toContain("#050910f2");
      expect(StyleSheet.flatten(tree.getByTestId("profile-generated-cover").props.style))
        .toMatchObject({ borderColor: profileNeon.borderStrong, top: 196 });
      fireEvent(tree.getByTestId("profile-cover-image"), "load");
      expect(heroFills(tree)).not.toContain("#050910f2");
    });

    it("takes the field back when the cover is swapped for one that has not loaded", () => {
      const tree = withCover();
      expect(heroFills(tree)).not.toContain("#050910f2");
      tree.rerender(<ProfileHeader profile={baseProfile({ cover_url: "https://cdn/next.jpg" })} owner />);
      expect(heroFills(tree)).toContain("#050910f2");
    });

    it("does not dim the photo itself instead of fixing the overlay", () => {
      const image = withCover().getByTestId("profile-cover-image");
      const style = StyleSheet.flatten(image.props.style) as { opacity?: number; tintColor?: string };
      expect(style.opacity).toBeUndefined();
      expect(style.tintColor).toBeUndefined();
    });
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
