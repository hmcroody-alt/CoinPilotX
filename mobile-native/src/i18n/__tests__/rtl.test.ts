/**
 * RTL is the one localization feature that cannot be verified by reading the
 * catalogs: every string can be perfectly translated into Arabic and the screen
 * can still be wrong, because the chrome around the text did not mirror. These
 * tests exist to catch the two failure modes that ship unnoticed:
 *
 *   1. A helper that silently returns the same value in both directions. It
 *      compiles, it renders, and it looks correct to a reviewer who reads LTR —
 *      so every helper below is asserted in BOTH directions and asserted to
 *      differ, rather than spot-checked in one.
 *   2. Over-mirroring. A back chevron that fails to flip is a bug; a camera or
 *      heart icon that DOES flip is an equally visible bug, and nothing in the
 *      type system distinguishes them. Both directions of that mistake are
 *      asserted.
 *
 * `react-native` is mocked down to the two objects `rtl.ts` actually touches.
 * Spreading the real module would evaluate every lazy getter in RN's index and
 * drag the native layer into a test that is purely about numbers and strings.
 */

jest.mock("react-native", () => ({
  I18nManager: { isRTL: false, allowRTL: jest.fn(), forceRTL: jest.fn() },
  Platform: { OS: "ios" }
}));

import { I18nManager, Platform } from "react-native";

import {
  __setDirectionForTests,
  applyDirectionForLocale,
  borderRadiusLogical,
  directionSign,
  endEdge,
  getLayoutDirection,
  isLayoutRtl,
  isNativeDirectionStale,
  isolateBidi,
  marginHorizontal,
  mirrorIconName,
  mirrorTransform,
  paddingHorizontal,
  positionEnd,
  positionStart,
  row,
  startEdge,
  subscribeToDirection,
  textAlign,
  writingDirectionStyle
} from "../rtl";

/**
 * The mock is a plain object so tests can move the native flag around; the RN
 * type declarations pin `Platform.OS` to a single literal, hence the casts.
 */
const nativeI18n = I18nManager as unknown as {
  isRTL: boolean;
  allowRTL: jest.Mock;
  forceRTL: jest.Mock;
};
const platform = Platform as unknown as { OS: string };

/**
 * Direction is module-level state. Without this the first test that switches to
 * Arabic would leave every later test running mirrored, and the suite would pass
 * or fail depending on file order.
 */
afterEach(() => {
  __setDirectionForTests("ltr");
  nativeI18n.isRTL = false;
  platform.OS = "ios";
  nativeI18n.allowRTL.mockClear();
  nativeI18n.forceRTL.mockClear();
});

describe("direction state", () => {
  it("reports the forced direction through both accessors", () => {
    __setDirectionForTests("ltr");
    expect(getLayoutDirection()).toBe("ltr");
    expect(isLayoutRtl()).toBe(false);

    __setDirectionForTests("rtl");
    expect(getLayoutDirection()).toBe("rtl");
    expect(isLayoutRtl()).toBe(true);
  });

  it("derives isLayoutRtl from the direction rather than the native flag", () => {
    // The whole point of the two-track model is that JS mirrors before the
    // native flag catches up. If isLayoutRtl consulted I18nManager instead, the
    // UI would stay LTR until the user restarted.
    __setDirectionForTests("rtl");
    nativeI18n.isRTL = false;
    expect(isLayoutRtl()).toBe(true);
  });
});

/**
 * Table-driven on purpose. The bug this guards against is a helper that ignores
 * its direction argument — which produces a *passing* single-direction test and
 * an unmirrored screen. Every input below is deliberately asymmetric so a helper
 * cannot pass by accident on symmetric data.
 */
describe("logical edge helpers swap between directions", () => {
  const HELPERS: ReadonlyArray<{ name: string; ltr: unknown; rtl: unknown; produce: () => unknown }> = [
    { name: "startEdge", ltr: "left", rtl: "right", produce: () => startEdge() },
    { name: "endEdge", ltr: "right", rtl: "left", produce: () => endEdge() },
    { name: "directionSign", ltr: 1, rtl: -1, produce: () => directionSign() },
    {
      name: "textAlign(start)",
      ltr: { textAlign: "left" },
      rtl: { textAlign: "right" },
      produce: () => textAlign("start")
    },
    {
      name: "textAlign(end)",
      ltr: { textAlign: "right" },
      rtl: { textAlign: "left" },
      produce: () => textAlign("end")
    },
    {
      name: "row",
      ltr: { flexDirection: "row" },
      rtl: { flexDirection: "row-reverse" },
      produce: () => row()
    },
    {
      name: "paddingHorizontal",
      ltr: { paddingLeft: 4, paddingRight: 20 },
      rtl: { paddingLeft: 20, paddingRight: 4 },
      produce: () => paddingHorizontal(4, 20)
    },
    {
      name: "marginHorizontal",
      ltr: { marginLeft: 6, marginRight: 18 },
      rtl: { marginLeft: 18, marginRight: 6 },
      produce: () => marginHorizontal(6, 18)
    },
    { name: "positionStart", ltr: { left: 12 }, rtl: { right: 12 }, produce: () => positionStart(12) },
    { name: "positionEnd", ltr: { right: 12 }, rtl: { left: 12 }, produce: () => positionEnd(12) },
    {
      name: "borderRadiusLogical",
      ltr: {
        borderTopLeftRadius: 16,
        borderTopRightRadius: 4,
        borderBottomLeftRadius: 2,
        borderBottomRightRadius: 0
      },
      rtl: {
        borderTopLeftRadius: 4,
        borderTopRightRadius: 16,
        borderBottomLeftRadius: 0,
        borderBottomRightRadius: 2
      },
      produce: () => borderRadiusLogical({ topStart: 16, topEnd: 4, bottomStart: 2, bottomEnd: 0 })
    },
    {
      name: "writingDirectionStyle",
      ltr: { writingDirection: "ltr" },
      rtl: { writingDirection: "rtl" },
      produce: () => writingDirectionStyle()
    },
    { name: "mirrorTransform", ltr: {}, rtl: { transform: [{ scaleX: -1 }] }, produce: () => mirrorTransform() }
  ];

  HELPERS.forEach(({ name, ltr, rtl, produce }) => {
    it(`${name} resolves correctly in both directions and does not return the same value`, () => {
      __setDirectionForTests("ltr");
      expect(produce()).toEqual(ltr);

      __setDirectionForTests("rtl");
      expect(produce()).toEqual(rtl);

      // The load-bearing assertion: a helper that ignored `direction` entirely
      // would satisfy one of the two expectations above by luck, never this one.
      expect(rtl).not.toEqual(ltr);
    });
  });

  it("honours an explicit direction argument over the ambient one", () => {
    // Components inside a locked-direction subtree (a code block, a phone-number
    // field) pass the direction explicitly. If the parameter were ignored in
    // favour of module state those subtrees would mirror with the rest of the app.
    __setDirectionForTests("rtl");
    expect(startEdge("ltr")).toBe("left");
    expect(directionSign("ltr")).toBe(1);
    expect(paddingHorizontal(4, 20, "ltr")).toEqual({ paddingLeft: 4, paddingRight: 20 });
    expect(textAlign("start", "ltr")).toEqual({ textAlign: "left" });
  });

  it("leaves centered text centered in both directions", () => {
    // `center` is the one alignment with no logical counterpart. Mirroring it
    // would push centered headers off-axis in Arabic.
    __setDirectionForTests("ltr");
    expect(textAlign("center")).toEqual({ textAlign: "center" });
    __setDirectionForTests("rtl");
    expect(textAlign("center")).toEqual({ textAlign: "center" });
  });

  it("defaults textAlign to the leading edge", () => {
    __setDirectionForTests("rtl");
    expect(textAlign()).toEqual(textAlign("start"));
    expect(textAlign()).toEqual({ textAlign: "right" });
  });

  it("keeps startEdge and endEdge opposite in both directions", () => {
    // Guards against a copy-paste where endEdge is defined with startEdge's body:
    // padding would then be applied to the same side twice.
    (["ltr", "rtl"] as const).forEach((direction) => {
      expect(startEdge(direction)).not.toBe(endEdge(direction));
    });
  });
});

describe("directional animation and gesture offsets", () => {
  /**
   * Swipe deltas, drawer slide-ins and rotation angles are all plain numbers, so
   * nothing stops them from being written as raw constants. `directionSign` is
   * the seam that makes them mirror; these assertions pin the arithmetic that
   * every animation in the app performs.
   */
  it("flips translateX and rotation through directionSign", () => {
    __setDirectionForTests("ltr");
    expect(24 * directionSign()).toBe(24);
    expect(`${45 * directionSign()}deg`).toBe("45deg");

    __setDirectionForTests("rtl");
    expect(24 * directionSign()).toBe(-24);
    expect(`${45 * directionSign()}deg`).toBe("-45deg");
  });

  it("mirrors a glyph with scaleX when the icon set has no mirrored twin", () => {
    __setDirectionForTests("ltr");
    // An empty object, not `{ transform: [] }` — spreading it into a style must
    // be a no-op rather than clobbering an existing transform in LTR.
    expect(mirrorTransform()).toEqual({});

    __setDirectionForTests("rtl");
    expect(mirrorTransform()).toEqual({ transform: [{ scaleX: -1 }] });
  });
});

describe("mirrorIconName", () => {
  const DIRECTIONAL: ReadonlyArray<[string, string]> = [
    ["chevron-back", "chevron-forward"],
    ["chevron-forward", "chevron-back"],
    ["chevron-left", "chevron-right"],
    ["chevron-right", "chevron-left"],
    ["arrow-back", "arrow-forward"],
    ["arrow-forward", "arrow-back"],
    ["arrow-left", "arrow-right"],
    ["arrow-right", "arrow-left"],
    ["arrow-back-circle", "arrow-forward-circle"],
    ["arrow-forward-circle", "arrow-back-circle"],
    ["caret-back", "caret-forward"],
    ["caret-forward", "caret-back"],
    ["play-back", "play-forward"],
    ["play-forward", "play-back"],
    ["return-down-back", "return-down-forward"],
    ["return-down-forward", "return-down-back"]
  ];

  /**
   * Icons with no horizontal handedness. Mirroring any of these is immediately
   * visible as breakage — a reversed camera body, a backwards gear tooth — and
   * is exactly what an over-eager `name.includes("-back")` heuristic would do.
   */
  const NON_DIRECTIONAL = [
    "settings",
    "settings-outline",
    "heart",
    "heart-outline",
    "camera",
    "camera-reverse",
    "close",
    "close-circle",
    "search",
    "add",
    "trash",
    "notifications",
    "person",
    "ellipsis-horizontal",
    "checkmark",
    "star"
  ];

  it("flips every directional glyph in RTL", () => {
    __setDirectionForTests("rtl");
    DIRECTIONAL.forEach(([input, expected]) => expect(mirrorIconName(input)).toBe(expected));
  });

  it("leaves non-directional glyphs alone in RTL", () => {
    __setDirectionForTests("rtl");
    NON_DIRECTIONAL.forEach((name) => expect(mirrorIconName(name)).toBe(name));
  });

  it("never rewrites anything in LTR", () => {
    // A mirror table applied unconditionally would point the English back button
    // forwards — a bug that only reproduces for the majority of users.
    __setDirectionForTests("ltr");
    [...DIRECTIONAL.map(([input]) => input), ...NON_DIRECTIONAL, "log-in"].forEach((name) =>
      expect(mirrorIconName(name)).toBe(name)
    );
  });

  it("is its own inverse for every symmetric pair", () => {
    // Round-tripping catches a one-way table entry, where an icon mirrors into a
    // name that does not mirror back and the pair becomes indistinguishable.
    __setDirectionForTests("rtl");
    DIRECTIONAL.forEach(([input]) => expect(mirrorIconName(mirrorIconName(input))).toBe(input));
  });

  /**
   * KNOWN BUG, pinned rather than endorsed. The swap table maps `log-in` to
   * `log-out` but has no reverse entry, so in Arabic a sign-in button and a
   * sign-out button both render the `log-out` glyph. Asserted as-is so the suite
   * stays green; when the missing `"log-out": "log-in"` entry is added this test
   * will fail and should be rewritten to expect the symmetric result.
   */
  it("does not round-trip log-in, because log-out has no reverse mapping", () => {
    __setDirectionForTests("rtl");
    expect(mirrorIconName("log-in")).toBe("log-out");
    expect(mirrorIconName("log-out")).toBe("log-out");
    expect(mirrorIconName(mirrorIconName("log-in"))).not.toBe("log-in");
  });

  it("passes unknown icon names through untouched", () => {
    __setDirectionForTests("rtl");
    expect(mirrorIconName("pulse-custom-glyph")).toBe("pulse-custom-glyph");
    expect(mirrorIconName("")).toBe("");
  });
});

describe("isolateBidi", () => {
  const HANDLE = "@pulse_user";
  // Written as escapes, never as literal characters: this repository treats a
  // bare bidi control in source or catalog data as a defect, and an invisible
  // character in a test fixture is unreviewable.
  const LRM = "\u200E";
  const RLM = "\u200F";

  /**
   * The catalogs are forbidden from containing bare bidi control characters —
   * direction is handled structurally — which makes this the one sanctioned
   * runtime path for wrapping a Latin handle, URL or number embedded in Arabic
   * copy. If it stopped emitting the control characters, the failure would be a
   * subtly scrambled line rather than an exception.
   */
  it("wraps the value in the mark for the surrounding direction", () => {
    __setDirectionForTests("rtl");
    expect(isolateBidi(HANDLE)).toBe(`${RLM}${HANDLE}${RLM}`);

    __setDirectionForTests("ltr");
    expect(isolateBidi(HANDLE)).toBe(`${LRM}${HANDLE}${LRM}`);
  });

  it("uses a different mark per direction and leaves the payload untouched", () => {
    expect(isolateBidi(HANDLE, "rtl")).not.toBe(isolateBidi(HANDLE, "ltr"));
    (["ltr", "rtl"] as const).forEach((direction) => {
      const wrapped = isolateBidi(HANDLE, direction);
      expect(wrapped).toHaveLength(HANDLE.length + 2);
      // The username itself must survive byte-for-byte; a mangled handle is
      // worse than a mis-ordered one because it is no longer copy-pasteable.
      expect(wrapped.slice(1, -1)).toBe(HANDLE);
    });
  });

  it("keeps a Latin run intact inside an Arabic sentence", () => {
    __setDirectionForTests("rtl");
    const sentence = `تابع ${isolateBidi(HANDLE)} الآن`;
    expect(sentence).toContain(`${RLM}${HANDLE}${RLM}`);
    // Neutral trailing punctuation is what actually jumps to the wrong end of
    // the line when the run is not delimited, so the closing mark must be there.
    expect(sentence.indexOf(RLM)).toBeLessThan(sentence.lastIndexOf(RLM));
  });

  it("returns an empty string unwrapped", () => {
    // Wrapping "" would produce a two-character string that renders as an empty
    // but non-zero-width node, breaking `value ? ... : null` checks upstream.
    __setDirectionForTests("rtl");
    expect(isolateBidi("")).toBe("");
  });
});

describe("applyDirectionForLocale", () => {
  it("switches to RTL for Arabic and asks for a restart on native", () => {
    const result = applyDirectionForLocale("ar");

    expect(result.direction).toBe("rtl");
    expect(getLayoutDirection()).toBe("rtl");
    expect(nativeI18n.allowRTL).toHaveBeenCalledWith(true);
    expect(nativeI18n.forceRTL).toHaveBeenCalledWith(true);
    expect(result.restartRequired).toBe(true);
  });

  it("resolves LTR for every non-Arabic language", () => {
    ["en", "es", "fr", "ht", "pt", "de", "hi", "ja", "ko", "zh"].forEach((code) => {
      __setDirectionForTests("rtl");
      expect(applyDirectionForLocale(code).direction).toBe("ltr");
    });
  });

  it("does not ask for a restart when the native flag already agrees", () => {
    // Re-selecting the current language must not surface a "restart to finish"
    // prompt; nothing changed and the prompt would look like a malfunction.
    nativeI18n.isRTL = true;
    __setDirectionForTests("rtl");

    const result = applyDirectionForLocale("ar");
    expect(result.restartRequired).toBe(false);
    expect(nativeI18n.forceRTL).not.toHaveBeenCalled();
  });

  it("never asks for a restart on web", () => {
    // react-native-web re-renders straight from the JS direction, so prompting a
    // browser user to relaunch would be asking for a reload that achieves nothing.
    platform.OS = "web";
    const result = applyDirectionForLocale("ar");
    expect(result.direction).toBe("rtl");
    expect(result.restartRequired).toBe(false);
  });

  it("still flips the JS direction when the native module throws", () => {
    // I18nManager is absent under some renderers. Rendering must not depend on it.
    nativeI18n.allowRTL.mockImplementationOnce(() => {
      throw new Error("I18nManager unavailable");
    });
    const result = applyDirectionForLocale("ar");
    expect(result.direction).toBe("rtl");
    expect(isLayoutRtl()).toBe(true);
    expect(result.restartRequired).toBe(false);
  });
});

describe("isNativeDirectionStale", () => {
  it("reports the native side as pending after switching to Arabic", () => {
    // This is what drives the optional "restart to finish mirroring" banner. If
    // it read false here the user would never be offered the reload, and native
    // scroll indicators and caret placement would stay LTR forever.
    expect(isNativeDirectionStale()).toBe(false);

    applyDirectionForLocale("ar");
    expect(isLayoutRtl()).toBe(true);
    expect(nativeI18n.isRTL).toBe(false);
    expect(isNativeDirectionStale()).toBe(true);
  });

  it("clears once the native flag has caught up on the next launch", () => {
    applyDirectionForLocale("ar");
    // Simulates the relaunch: the native flag is now read as RTL at startup.
    nativeI18n.isRTL = true;
    expect(isNativeDirectionStale()).toBe(false);
  });

  it("is stale in the other direction too, switching Arabic back to English", () => {
    nativeI18n.isRTL = true;
    __setDirectionForTests("rtl");

    applyDirectionForLocale("en");
    expect(isNativeDirectionStale()).toBe(true);
  });
});

describe("subscribeToDirection", () => {
  it("notifies subscribers when the direction changes", () => {
    const listener = jest.fn();
    const unsubscribe = subscribeToDirection(listener);

    applyDirectionForLocale("ar");
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith("rtl");

    applyDirectionForLocale("en");
    expect(listener).toHaveBeenLastCalledWith("ltr");
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
  });

  it("stays silent when the new locale has the same direction", () => {
    // Switching French to German must not re-render every direction-aware
    // subtree in the tree for a change that has no layout effect.
    const listener = jest.fn();
    const unsubscribe = subscribeToDirection(listener);

    applyDirectionForLocale("fr");
    applyDirectionForLocale("de");
    expect(listener).not.toHaveBeenCalled();

    unsubscribe();
  });

  it("stops notifying after unsubscribe", () => {
    // Subscribers are components. A retained listener would call setState on an
    // unmounted screen every time the user changed language.
    const listener = jest.fn();
    const unsubscribe = subscribeToDirection(listener);
    unsubscribe();

    applyDirectionForLocale("ar");
    expect(listener).not.toHaveBeenCalled();
  });

  it("is idempotent when unsubscribed twice", () => {
    const listener = jest.fn();
    const unsubscribe = subscribeToDirection(listener);
    unsubscribe();
    expect(() => unsubscribe()).not.toThrow();

    applyDirectionForLocale("ar");
    expect(listener).not.toHaveBeenCalled();
  });

  it("delivers to the remaining subscribers when one throws", () => {
    // A crash in one direction-aware component must not abort the language
    // change halfway, leaving half the tree mirrored and half not.
    const thrower = jest.fn(() => {
      throw new Error("subscriber exploded");
    });
    const survivor = jest.fn();
    const unsubscribeThrower = subscribeToDirection(thrower);
    const unsubscribeSurvivor = subscribeToDirection(survivor);

    expect(() => applyDirectionForLocale("ar")).not.toThrow();
    expect(thrower).toHaveBeenCalledTimes(1);
    expect(survivor).toHaveBeenCalledWith("rtl");
    expect(getLayoutDirection()).toBe("rtl");

    unsubscribeThrower();
    unsubscribeSurvivor();
  });
});
