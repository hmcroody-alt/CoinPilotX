/**
 * The Profile header's graphite decisions, its control states and the parts of
 * the brief that are accessibility promises rather than colours.
 *
 * Separate from `ProfileHeader.test.tsx` on purpose. That file is about what the
 * header *says* — whose handle, which actions, which cover. This one is about how
 * it is *built*: which step each surface sits on, whether a tile's identity is
 * carried by something other than a translucent wash, whether a state is
 * announced as well as tinted, and whether anything animates forever.
 *
 * Every colour is read from `profileSurface`, never written out as a hex. The
 * literals are pinned once, in `theme/__tests__/profileGraphite.test.ts`; pinning
 * them again here would mean a ramp retune turns two files red for one decision.
 */

import React from "react";
import { StyleSheet } from "react-native";
import { act, fireEvent, render } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(), setItem: jest.fn(), removeItem: jest.fn()
}));

jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium", Heavy: "heavy" }
}));

jest.mock("@expo/vector-icons", () => ({ Ionicons: ({ name }: { name: string }) => name }));

jest.mock("expo-linear-gradient", () => {
  const { View } = require("react-native");
  return { LinearGradient: View };
});

jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: jest.fn().mockReturnValue(true),
  createLogiNexusAmbientPulse: () => ({ start: jest.fn(), stop: jest.fn() })
}));

import { readFileSync } from "fs";
import { join } from "path";
import { PulseProfile } from "../../api/profile";
import { colors } from "../../theme/colors";
import { profileSurface, resolveProfileSurface } from "../../theme/profileGraphite";
import { applyPaletteToLegacyColors, __testing } from "../../theme/ThemeContext";
import { ProfileHeader } from "../ProfileHeader";

const surface = profileSurface(colors);
const HEADER_SOURCE = readFileSync(join(__dirname, "..", "ProfileHeader.tsx"), "utf8");

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

type Flat = {
  backgroundColor?: string;
  borderColor?: string;
  borderWidth?: number;
  shadowRadius?: number;
  shadowColor?: string;
  shadowOpacity?: number;
  opacity?: number;
  width?: number | string;
  minHeight?: number;
};

/** Every node's flattened style, with pressable style functions resolved unpressed. */
function styles(tree: { toJSON: () => unknown }, pressed = false): Flat[] {
  const found: Flat[] = [];
  const visit = (node: any) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) return node.forEach(visit);
    const raw = node.props?.style;
    const resolved = typeof raw === "function" ? raw({ pressed }) : raw;
    const flat = StyleSheet.flatten(resolved) as Flat | undefined;
    if (flat) found.push(flat);
    (node.children ?? []).forEach(visit);
  };
  visit(tree.toJSON());
  return found;
}

/**
 * A touch event shaped the way `Pressability` reads it.
 *
 * `fireEvent(el, "pressIn")` looks for an `onPressIn` prop and silently does
 * nothing when it finds none — and a `Pressable` never exposes one. What it puts
 * on its host view is the responder set (`onResponderGrant`, `onResponderRelease`
 * and friends), so the responder events are the only ones that reach the
 * component. `Pressability` calls `event.persist()` on the way in and reads
 * `currentTarget.measure`, neither of which RNTL synthesises, hence both stubs.
 */
function touch() {
  return {
    persist: () => {},
    currentTarget: { measure: () => {} },
    dispatchConfig: {},
    nativeEvent: {
      changedTouches: [],
      force: 0,
      identifier: 1,
      locationX: 0,
      locationY: 0,
      pageX: 0,
      pageY: 0,
      target: 1,
      timestamp: Date.now(),
      touches: []
    }
  };
}

describe("the tile grid reads as tiles, not as an accent wash", () => {
  // The fourth and last cause of the flat look. Every tile's fill was
  // `${accent}12` — a 7% accent over the canvas, which is simultaneously a
  // translucent layer over the page (forbidden outright) and, at that alpha,
  // indistinguishable from it.
  it("gives every tile the same opaque elevated fill", () => {
    const screen = render(<ProfileHeader profile={baseProfile()} owner />);
    const tiles = styles(screen).filter((style) => style.width === 58 && style.borderWidth === 1);
    expect(tiles.length).toBeGreaterThan(8);
    for (const tile of tiles) {
      expect(tile.backgroundColor).toBe(surface.raisedStrong);
      // Opaque: six hex digits, no alpha pair, no `rgba(`.
      expect(tile.backgroundColor).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  // Identity had to survive the fill becoming uniform, or the grid would be
  // eighteen identical squares. It moved to the border and the glyph.
  it("keeps each tile's hue on its border rather than in its fill", () => {
    const screen = render(<ProfileHeader profile={baseProfile()} owner />);
    const borders = styles(screen)
      .filter((style) => style.width === 58 && style.backgroundColor === surface.raisedStrong)
      .map((style) => style.borderColor);
    expect(new Set(borders).size).toBeGreaterThan(3);
  });

  it("never gives a tile a permanent halo", () => {
    const screen = render(<ProfileHeader profile={baseProfile()} owner />);
    const all = styles(screen);
    expect(all.filter((style) => typeof style.shadowRadius === "number")).toEqual([]);
    expect(all.filter((style) => style.shadowColor !== undefined)).toEqual([]);
    expect(all.filter((style) => typeof style.shadowOpacity === "number")).toEqual([]);
  });

  /*
   * A visitor's grid is shorter, and the tiles that remain have to look the same.
   *
   * The risk in a "signed-in user vs viewed user" split is not usually the colour
   * — it is that one branch renders a *different component*. Asserting the fill on
   * both is what proves the visitor grid went through the same `Module`, and the
   * count assertion is what proves the two cases are actually different renders
   * and not the same grid twice.
   */
  it("gives a visited profile's shorter grid the same tiles, not a second styling", () => {
    const visitorKeys = ["identity", "media", "achievements"] as const;
    const visited = render(
      <ProfileHeader
        profile={baseProfile({ user_id: 91, display_name: "Maria Reyes", username: "maria" })}
        moduleKeys={[...visitorKeys]}
        moduleOwnerName="Maria"
      />
    );
    const visitedTiles = styles(visited).filter((style) => style.width === 58 && style.borderWidth === 1);
    expect(visitedTiles).toHaveLength(visitorKeys.length);
    for (const tile of visitedTiles) expect(tile.backgroundColor).toBe(surface.raisedStrong);

    const ownTiles = styles(render(<ProfileHeader profile={baseProfile()} owner />))
      .filter((style) => style.width === 58 && style.borderWidth === 1);
    expect(ownTiles.length).toBeGreaterThan(visitedTiles.length);
  });
});

/*
 * The states a tile can be in, and the one thing they must not do.
 *
 * A tile's identity lives on its border and its glyph now, which means state has
 * to arrive the same way. The trap is that `tint` looks like a licence to fill:
 * an amber *fill* for a billing problem would be a coloured wash over the canvas
 * — the exact treatment this mission removed — and it would also be the only
 * signal, which is state by colour alone.
 */
describe("tile states are carried by the edge and by words", () => {
  const stateFill = (screen: ReturnType<typeof render>) =>
    styles(screen).filter((style) => style.width === 58 && style.borderWidth === 1).map((s) => s.backgroundColor);

  // Cold start. `status: undefined` means "say nothing", which is deliberately
  // not the same as an empty string: a paying member must not watch their
  // membership appear to vanish while the request is in flight.
  it("renders a loading tile with no status word rather than a wrong one", () => {
    const screen = render(
      <ProfileHeader profile={baseProfile()} owner moduleState={{ premium: { accessibilityLabel: "Premium" } }} />
    );
    expect(screen.getByLabelText("Premium")).toBeTruthy();
    expect(screen.queryByText("Premium", { exact: true })).toBeTruthy(); // the tile's own name
    expect(screen.queryByText("Active")).toBeNull();
    expect(screen.queryByText("Inactive")).toBeNull();
    // A tile with nothing to say still sits on the elevated step.
    for (const fill of stateFill(screen)) expect(fill).toBe(surface.raisedStrong);
  });

  it("states a resolved premium account in a word, not only in gold", () => {
    const screen = render(
      <ProfileHeader
        profile={baseProfile()}
        owner
        moduleState={{ premium: { status: "Active", accessibilityLabel: "Premium, active" } }}
      />
    );
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.getByLabelText("Premium, active")).toBeTruthy();
  });

  it("states a non-premium account as its own word instead of dropping the tile", () => {
    const screen = render(
      <ProfileHeader
        profile={baseProfile({ premium_status: "" })}
        owner
        moduleState={{ premium: { status: "Inactive", accessibilityLabel: "Premium, inactive" } }}
      />
    );
    expect(screen.getByText("Inactive")).toBeTruthy();
    expect(screen.getByLabelText("Premium, inactive")).toBeTruthy();
    // Non-premium is not a disabled tile: it is the tile's reason for existing.
    expect(screen.getByLabelText("Premium, inactive").props.accessibilityState).not.toMatchObject({ disabled: true });
  });

  it("keeps an attention tint on the border and out of the fill", () => {
    const amber = "#F5A524";
    const screen = render(
      <ProfileHeader
        profile={baseProfile()}
        owner
        moduleState={{ premium: { status: "Billing", tint: amber, accessibilityLabel: "Premium, billing problem" } }}
      />
    );
    const tiles = styles(screen).filter((style) => style.width === 58 && style.borderWidth === 1);
    // The tint reaches the edge...
    expect(tiles.map((tile) => tile.borderColor)).toContain(`${amber}55`);
    // ...and no tile's fill moves off the ramp because of it.
    for (const tile of tiles) expect(tile.backgroundColor).toBe(surface.raisedStrong);
    // ...and the state is legible without seeing the colour at all.
    expect(screen.getByText("Billing")).toBeTruthy();
    expect(screen.getByLabelText("Premium, billing problem")).toBeTruthy();
  });
});

/*
 * The bio line, in both directions.
 *
 * Absent is a real state with its own text, not a collapsed gap — and its text
 * differs by who is reading, because "add a bio" is an instruction only the owner
 * can act on. The graphite requirement is that the placeholder sits on the
 * *secondary* weight: a missing bio should read as quieter than a present one,
 * not as an error and not as a hole.
 */
describe("the bio line", () => {
  const bioStyle = (screen: ReturnType<typeof render>, text: string) =>
    StyleSheet.flatten(screen.getByText(text).props.style) as { color?: string };

  it("draws a written bio at the primary text weight", () => {
    const screen = render(<ProfileHeader profile={baseProfile({ bio: "Building PulseSoc." })} owner />);
    expect(bioStyle(screen, "Building PulseSoc.").color).toBe(surface.primaryText);
  });

  it("invites the owner to add one, at the secondary weight", () => {
    const screen = render(<ProfileHeader profile={baseProfile({ bio: "" })} owner />);
    const placeholder = screen.getByText(/Add a bio/i);
    expect(StyleSheet.flatten(placeholder.props.style)).toMatchObject({ color: surface.secondaryText });
  });

  // A visitor cannot add the bio, so telling them to would be an instruction they
  // cannot follow — the same class of mistake as a disabled control with no reason.
  it("tells a visitor about the absence instead of instructing them", () => {
    const screen = render(<ProfileHeader profile={baseProfile({ bio: "" })} />);
    expect(screen.getByText(/has not added a bio/i)).toBeTruthy();
    expect(screen.queryByText(/Add a bio/i)).toBeNull();
  });
});

describe("the statistics panel", () => {
  it("sits one step above the canvas behind a single steel hairline", () => {
    const screen = render(<ProfileHeader profile={baseProfile()} owner />);
    const panel = styles(screen).find(
      (style) => style.backgroundColor === surface.raised && style.borderWidth === 1 && style.width === undefined
    );
    expect(panel).toBeTruthy();
    expect(panel?.borderColor).toBe(surface.border);
  });

  // "Clear column separation": on a 3x device a RN hairline is 0.33pt, which is
  // not a column boundary, it is a suggestion of one.
  it("separates the columns with a full point, not a hairline", () => {
    const screen = render(<ProfileHeader profile={baseProfile()} owner />);
    const dividers = styles(screen).filter((style) => style.width === 1);
    expect(dividers.length).toBeGreaterThanOrEqual(3);
    expect(StyleSheet.hairlineWidth).toBeLessThan(1);
  });
});

describe("control states are announced, not only tinted", () => {
  it("states a selected action to assistive technology and by fill", () => {
    const screen = render(<ProfileHeader profile={baseProfile({ viewer_follows: true })} />);
    const following = screen.getByLabelText("Following");
    expect(following.props.accessibilityState).toMatchObject({ selected: true, disabled: false });
    const unselected = screen.getByLabelText("Message");
    expect(unselected.props.accessibilityState).toMatchObject({ selected: false });
    expect(StyleSheet.flatten(following.props.style)).not.toEqual(StyleSheet.flatten(unselected.props.style));
  });

  // Disabled has to be legible as disabled without colour vision: the label
  // changes word as well as the surface changing opacity.
  it("states a busy action in words and in opacity", () => {
    const screen = render(<ProfileHeader profile={baseProfile()} followBusy />);
    const busy = screen.getByLabelText("Follow");
    expect(busy.props.accessibilityState).toMatchObject({ disabled: true });
    expect(screen.getByText("Working…")).toBeTruthy();
    expect(StyleSheet.flatten(busy.props.style)).toMatchObject({ opacity: 0.55 });
  });

  /*
   * Driven through a real press, not by calling the style function.
   *
   * A `Pressable` resolves `style({ pressed })` during its own render, so the
   * rendered tree only ever shows the resting result and `UNSAFE_getAllByType`
   * cannot reach the component either (RN wraps `Pressable` in `memo`, so the
   * exported symbol is not the type in the tree). Granting the responder makes the
   * component change its own state, which is also the only version of this that
   * proves the feedback is reachable by touch at all.
   *
   * Two details this test had to be rewritten around, both of which fail *silently*
   * — the assertion goes red while the source is correct:
   *
   *   - The element is re-queried on every read. A press re-renders the
   *     `Pressable`, producing a new host node; a handle captured before the press
   *     keeps the props it was created with and reports the resting style forever.
   *   - Release is followed by a timer advance. `Pressability` holds the pressed
   *     visual for `DEFAULT_MIN_PRESS_DURATION` (130ms) so a fast tap still reads
   *     as a tap, so the un-press is genuinely asynchronous. Checking straight
   *     after release finds the tile still dimmed and looks like a stuck state.
   */
  it("gives press feedback by opacity, not by scale, and only while pressed", () => {
    jest.useFakeTimers();
    try {
      const screen = render(<ProfileHeader profile={baseProfile()} owner />);
      const tile = () => screen.getByLabelText("Pulse DNA");
      const at = () => StyleSheet.flatten(tile().props.style) as Flat & { transform?: unknown };

      expect(at().opacity).toBeUndefined();
      fireEvent(tile(), "responderGrant", touch());
      expect(at().opacity).toBe(0.7);
      // Opacity rather than a transform: a scale press on a 25%-width grid tile
      // reflows nothing visible but still costs a layout read on every touch.
      expect(at().transform).toBeUndefined();

      fireEvent(tile(), "responderRelease", touch());
      act(() => {
        // Past the minimum-press hold and past the brief's 120–180ms band, so a
        // treatment that stayed dim would still be caught here.
        jest.advanceTimersByTime(400);
      });
      expect(at().opacity).toBeUndefined();
    } finally {
      jest.useRealTimers();
    }
  });

  it("announces an in-flight avatar upload as busy rather than just spinning", () => {
    const screen = render(
      <ProfileHeader profile={baseProfile()} owner canEditMedia avatarBusy onEditAvatar={jest.fn()} />
    );
    const control = screen.getByTestId("profile-edit-avatar");
    expect(control.props.accessibilityState).toMatchObject({ busy: true, disabled: true });
  });
});

describe("identity indicators survive the simplified rings", () => {
  it("keeps the seal, the presence dot and the badges on a verified active member", () => {
    const screen = render(<ProfileHeader profile={baseProfile()} owner />);
    expect(screen.getByText("Verified")).toBeTruthy();
    expect(screen.getByText("Active now")).toBeTruthy();
  });

  it("says Away instead of borrowing the active colour when the account is not active", () => {
    const screen = render(<ProfileHeader profile={baseProfile({ account_status: "suspended" })} owner />);
    expect(screen.getByText("Away")).toBeTruthy();
    expect(screen.queryByText("Active now")).toBeNull();
  });

  it("drops the verification wording entirely for an unverified member", () => {
    const screen = render(
      <ProfileHeader profile={baseProfile({ verified_badge: false, verification_status: "not_started" })} owner />
    );
    expect(screen.queryByText("Verified")).toBeNull();
  });
});

describe("themes other than graphite", () => {
  afterEach(() => {
    applyPaletteToLegacyColors(__testing.DARK);
  });

  /**
   * Repaint through the same function `ThemeProvider` uses, then re-render.
   *
   * This is the only honest way to assert "switching updates Profile
   * immediately": every Profile stylesheet is a module-scope `createThemedStyles`
   * factory, and those rebuild when `applyPaletteToLegacyColors` bumps the epoch —
   * not when a React value changes. Setting a mode on a provider would prove
   * nothing about these sheets.
   */
  function renderUnder(palette: typeof __testing.DARK) {
    applyPaletteToLegacyColors(palette);
    return { screen: render(<ProfileHeader profile={baseProfile()} owner />), expected: resolveProfileSurface(palette) };
  }

  it("paints Black genuinely black instead of graphite-dimmed", () => {
    const { screen, expected } = renderUnder(__testing.BLACK);
    const fills = styles(screen).map((style) => style.backgroundColor);
    expect(expected.canvasTop).toBe("#000000");
    expect(fills).not.toContain(surface.raised);
    expect(fills).not.toContain(surface.raisedStrong);
  });

  it("keeps White a readable light page and lets the border carry the card edge", () => {
    const { screen, expected } = renderUnder(__testing.WHITE);
    expect(expected.raised).toBe(expected.canvasTop);
    const panel = styles(screen).find((style) => style.backgroundColor === expected.raised && style.borderWidth === 1);
    expect(panel?.borderColor).toBe(expected.border);
  });

  it("repaints the same tree when the palette changes under it", () => {
    const graphiteTiles = styles(render(<ProfileHeader profile={baseProfile()} owner />))
      .filter((style) => style.width === 58)
      .map((style) => style.backgroundColor);
    const { screen } = renderUnder(__testing.LIGHT_FUTURISTIC);
    const lightTiles = styles(screen).filter((style) => style.width === 58).map((style) => style.backgroundColor);
    expect(lightTiles).not.toEqual(graphiteTiles);
    expect(lightTiles.filter(Boolean).length).toBe(graphiteTiles.filter(Boolean).length);
  });
});

describe("motion and type scaling", () => {
  // The brief forbids continuous animation on this surface. A loop is the one
  // construct that cannot be made to stop for Reduce Motion by not starting it,
  // so its absence is asserted against the source rather than a render: a loop
  // guarded by a flag still ships the loop.
  it("runs nothing forever", () => {
    expect(HEADER_SOURCE).not.toMatch(/Animated\.loop/);
    expect(HEADER_SOURCE).not.toMatch(/useNativeDriver:\s*false/);
  });

  it("never opts a label out of OS font scaling", () => {
    const screen = render(<ProfileHeader profile={baseProfile()} owner />);
    expect(HEADER_SOURCE).not.toMatch(/allowFontScaling=\{false\}/);
    // And the tiles that hold those labels are sized by content, so a larger
    // type size grows the row instead of clipping the word.
    expect(screen.getByText("Pulse DNA")).toBeTruthy();
  });

  /*
   * The cover control's own 44pt floor is covered in `ProfileHeader.test.tsx`.
   * The avatar control has no size of its own: the whole portrait is the target
   * and the camera badge is only the affordance, which is the right arrangement
   * but means "is it 44pt" has to be asked of the portrait. Asserted here because
   * shrinking the avatar for a tighter hero would silently shrink the target.
   */
  it("makes the whole portrait the avatar control's target, not just the badge", () => {
    const screen = render(
      <ProfileHeader profile={baseProfile()} owner canEditMedia onEditCover={jest.fn()} onEditAvatar={jest.fn()} />
    );
    const control = screen.getByTestId("profile-edit-avatar");
    const portrait = styles({ toJSON: () => control } as never).find(
      (style) => typeof style.width === "number" && style.width >= 44
    );
    expect(portrait?.width).toBe(112);
  });
});
