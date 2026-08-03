/**
 * The dash is the defect these tests exist for.
 *
 * Across the Business OS screens, "—" renders in the same slot for four
 * unrelated situations: a measured zero, a section still loading, a section
 * that failed, and a feature the seller has not set up. Three of those four
 * call for an action and one of them is good news, so a reader who cannot tell
 * them apart cannot act on any of them.
 *
 * The tests below are mostly about *separation*: not "does this state produce
 * a nice sentence" but "does this state produce a different sentence from that
 * one". The last test in each block is the one that would catch a regression,
 * because a well-meaning refactor that routes two states to one string reads
 * fine in review and reintroduces exactly the bug.
 */
import {
  LEGACY_ABSENT_TEXT,
  STATE_LANGUAGE_FLAG,
  SurfaceState,
  ZERO_TEXT,
  absentValueSpokenText,
  absentValueText,
  absentValueTextOr,
  failureCause,
  failureCopy,
  failureFrom,
  stateLanguageEnabled,
  valueState
} from "../stateLanguage";
import { PulseApiError } from "../pulseApi";

const ALL_STATES: SurfaceState[] = [
  "loading",
  "ready",
  "ready_from_cache",
  "zero",
  "no_activity",
  "not_configured",
  "restricted",
  "unavailable"
];

/** The states that mean "there is no number to show here". */
const ABSENT_STATES: SurfaceState[] = [
  "loading",
  "no_activity",
  "not_configured",
  "restricted",
  "unavailable"
];

const original = process.env[STATE_LANGUAGE_FLAG];

afterEach(() => {
  if (original === undefined) delete process.env[STATE_LANGUAGE_FLAG];
  else process.env[STATE_LANGUAGE_FLAG] = original;
});

function withFlag<T>(value: string, run: () => T): T {
  process.env[STATE_LANGUAGE_FLAG] = value;
  return run();
}

describe("the flag", () => {
  it('is off unless the build opts in, and accepts every spelling of "on"', () => {
    // The accepted spellings are the shared set in core/envFlag.ts. This flag
    // shipped taking the literal "1" alone while flags on adjacent screens also
    // took "true", so setting it to "true" was a silent no-op.
    for (const value of ["", "0", "false", "off", "no", "2", " "]) {
      expect(withFlag(value, stateLanguageEnabled)).toBe(false);
    }
    for (const value of ["1", "true", "on", "yes", "YES", "On"]) {
      expect(withFlag(value, stateLanguageEnabled)).toBe(true);
    }
    // Trailing whitespace from a .env file must not silently disable it.
    expect(withFlag(" 1 ", stateLanguageEnabled)).toBe(true);
    expect(withFlag("\ttrue\n", stateLanguageEnabled)).toBe(true);
  });

  it("leaves every absent state rendering exactly as it does today when off", () => {
    withFlag("0", () => {
      for (const state of ABSENT_STATES) {
        expect(absentValueText(state)).toBe(LEGACY_ABSENT_TEXT);
      }
    });
  });
});

describe("absentValueText", () => {
  /**
   * The correction the brief names outright: "a real zero renders as 0, not a
   * dash". It is the one case that is wrong under both the old standard and
   * the new one, so it does not wait for the flag.
   */
  it("renders a measured zero as a figure whether or not the flag is on", () => {
    for (const value of ["0", "1"]) {
      expect(withFlag(value, () => absentValueText("zero"))).toBe(ZERO_TEXT);
      expect(withFlag(value, () => absentValueText("zero"))).not.toBe(LEGACY_ABSENT_TEXT);
    }
  });

  it("uses the locale-formatted zero when the caller has one", () => {
    withFlag("1", () => {
      expect(absentValueText("zero", { zeroText: "٠" })).toBe("٠");
    });
  });

  /**
   * The heart of it. Four situations that shared one character must now share
   * none — and the assertion is on the *set*, so a future edit that makes two
   * of them agree fails here rather than shipping.
   */
  it("gives every absent state a distinct sentence once switched on", () => {
    withFlag("1", () => {
      const rendered = ABSENT_STATES.map((state) => absentValueText(state));
      expect(new Set(rendered).size).toBe(ABSENT_STATES.length);
      expect(rendered).not.toContain(LEGACY_ABSENT_TEXT);
    });
  });

  it("distinguishes a section that failed from one that is empty", () => {
    withFlag("1", () => {
      expect(absentValueText("unavailable")).not.toBe(absentValueText("no_activity"));
      expect(absentValueText("unavailable")).not.toBe(absentValueText("zero"));
      expect(absentValueText("not_configured")).not.toBe(absentValueText("no_activity"));
    });
  });

  it("lets a surface override the setup wording without touching the rest", () => {
    withFlag("1", () => {
      expect(absentValueText("not_configured", { notConfiguredText: "No store yet" })).toBe(
        "No store yet"
      );
      expect(absentValueText("no_activity", { notConfiguredText: "No store yet" })).not.toBe(
        "No store yet"
      );
    });
  });

  it("never returns an empty string for any state in the type", () => {
    for (const flag of ["0", "1"]) {
      withFlag(flag, () => {
        for (const state of ALL_STATES) {
          expect(absentValueText(state).length).toBeGreaterThan(0);
        }
      });
    }
  });
});

describe("absentValueTextOr", () => {
  /**
   * The rollout guarantee. Every call site converted in this pass renders the
   * exact string it rendered before until the flag is thrown — including the
   * measured zeroes, which `absentValueText` alone would have promoted to `0`
   * in a build that had not opted in.
   */
  it("returns the caller's existing string, byte for byte, while the flag is off", () => {
    withFlag("0", () => {
      for (const state of ALL_STATES) {
        expect(absentValueTextOr(LEGACY_ABSENT_TEXT, state)).toBe(LEGACY_ABSENT_TEXT);
      }
      // Including a legacy expression that was never the dash.
      expect(absentValueTextOr("No clicks yet", "no_activity")).toBe("No clicks yet");
    });
  });

  it("switches to the new vocabulary, and passes the options through, when on", () => {
    withFlag("1", () => {
      expect(absentValueTextOr("—", "no_activity")).toBe("None yet");
      expect(absentValueTextOr("—", "not_configured", { notConfiguredText: "Not measured yet" })).toBe(
        "Not measured yet"
      );
      expect(absentValueTextOr("—", "zero", { zeroText: "0" })).toBe("0");
    });
  });

  it("agrees with absentValueText on every state once switched on", () => {
    withFlag("1", () => {
      for (const state of ALL_STATES) {
        expect(absentValueTextOr("—", state)).toBe(absentValueText(state));
      }
    });
  });
});

describe("absentValueSpokenText", () => {
  /**
   * A screen reader either says "em dash" or says nothing at all, so the
   * degraded rendering is worse for assistive technology than it is on screen.
   * That is why the spoken form does not consult the flag.
   */
  it("speaks the full wording even with the flag off", () => {
    withFlag("0", () => {
      for (const state of ABSENT_STATES) {
        expect(absentValueSpokenText(state)).not.toBe(LEGACY_ABSENT_TEXT);
        expect(absentValueSpokenText(state).length).toBeGreaterThan(0);
      }
    });
  });

  it("never speaks a dash for any state", () => {
    for (const flag of ["0", "1"]) {
      withFlag(flag, () => {
        for (const state of ALL_STATES) {
          expect(absentValueSpokenText(state)).not.toContain(LEGACY_ABSENT_TEXT);
        }
      });
    }
  });

  it("speaks a real zero as the figure", () => {
    expect(absentValueSpokenText("zero")).toBe(ZERO_TEXT);
    expect(absentValueSpokenText("zero", { zeroText: "0 orders" })).toBe("0 orders");
  });
});

describe("failureCause", () => {
  /**
   * The distinction the whole failure vocabulary rests on. A phone with no
   * connection never reaches the other side, so the request is converted into a
   * 503 carrying `request_unreachable`. Reading the status alone makes "you are
   * offline" and "the other side is down" one message with one wrong action.
   */
  it("separates an unreachable request from a service that answered 503", () => {
    const offline = new PulseApiError("no route", 503, "request_unreachable");
    const down = new PulseApiError("maintenance", 503, "service_unavailable");
    expect(failureCause(offline)).toBe("offline");
    expect(failureCause(down)).toBe("service_unavailable");
    expect(failureCause(offline)).not.toBe(failureCause(down));
  });

  it("maps the authorisation statuses apart from each other", () => {
    expect(failureCause(new PulseApiError("nope", 401))).toBe("authentication");
    expect(failureCause(new PulseApiError("nope", 403))).toBe("entitlement");
  });

  it("treats the whole gateway family as the service being unreachable", () => {
    for (const status of [502, 503, 504]) {
      expect(failureCause(new PulseApiError("gateway", status))).toBe("service_unavailable");
    }
  });

  it("falls back to unexpected for anything that is not one of ours", () => {
    expect(failureCause(new Error("boom"))).toBe("unexpected");
    expect(failureCause("boom")).toBe("unexpected");
    expect(failureCause(null)).toBe("unexpected");
    expect(failureCause(undefined)).toBe("unexpected");
    expect(failureCause(new PulseApiError("teapot", 418))).toBe("unexpected");
  });
});

describe("failureCopy", () => {
  it("names the subject in every sentence, so no screen says only that it failed", () => {
    for (const cause of ["offline", "authentication", "entitlement", "service_unavailable", "unexpected"] as const) {
      expect(failureCopy(cause, "Your insights").message).toContain("Your insights");
    }
  });

  /**
   * Entitlement is the only cause with no button. A second identical attempt at
   * something the account may not see fails identically, and a retry that
   * cannot work is worse than no retry: it implies the reader did something
   * wrong.
   */
  it("offers no control only when pressing one could not help", () => {
    expect(failureCopy("entitlement", "Insights").actionLabel).toBeNull();
    expect(failureCopy("offline", "Insights").actionLabel).toBe("Try again");
    expect(failureCopy("service_unavailable", "Insights").actionLabel).toBe("Try again");
    expect(failureCopy("unexpected", "Insights").actionLabel).toBe("Try again");
  });

  it("sends an expired session to sign in rather than to a retry that will fail", () => {
    const copy = failureCopy("authentication", "Insights");
    expect(copy.actionLabel).toBe("Sign in");
    expect(copy.actionLabel).not.toBe("Try again");
  });

  it("gives the four recoverable causes four different sentences", () => {
    const messages = (["offline", "authentication", "entitlement", "service_unavailable"] as const).map(
      (cause) => failureCopy(cause, "Your sales").message
    );
    expect(new Set(messages).size).toBe(4);
  });

  it("reads the cause straight off a thrown error", () => {
    const copy = failureFrom(new PulseApiError("x", 403), "Advertising");
    expect(copy.cause).toBe("entitlement");
    expect(copy.actionLabel).toBeNull();
    expect(copy.message).toContain("Advertising");
  });
});

describe("valueState", () => {
  /**
   * The ordering is the decision. A figure that failed to load is not a zero,
   * and a store that was never set up is not a store with no sales — the
   * precedence here is what stops a caller from writing `value ?? "—"` and
   * collapsing them again.
   */
  it("ranks a wall above a failure above a load above a missing setup", () => {
    expect(valueState({ value: 5, restricted: true, failed: true, loading: true })).toBe("restricted");
    expect(valueState({ value: 5, failed: true, loading: true })).toBe("unavailable");
    expect(valueState({ value: 5, loading: true, configured: false })).toBe("loading");
    expect(valueState({ value: 5, configured: false })).toBe("not_configured");
  });

  it("calls an all-time zero a zero and a windowed zero no activity", () => {
    expect(valueState({ value: 0 })).toBe("zero");
    expect(valueState({ value: 0, windowed: true })).toBe("no_activity");
  });

  /**
   * The consequence that matters: an all-time zero reaches the reader as the
   * digit, while the same zero over a period does not — because a bare 0 in a
   * period view reads as a total and tells a seller their business has stopped.
   */
  it("renders those two zeroes differently", () => {
    withFlag("1", () => {
      expect(absentValueText(valueState({ value: 0 }))).toBe(ZERO_TEXT);
      expect(absentValueText(valueState({ value: 0, windowed: true }))).not.toBe(ZERO_TEXT);
    });
  });

  it("treats an absent figure as a failure, never as a zero", () => {
    expect(valueState({ value: null })).toBe("unavailable");
    expect(valueState({ value: undefined })).toBe("unavailable");
    expect(valueState({ value: null })).not.toBe("zero");
  });

  it("passes a real figure through as ready", () => {
    expect(valueState({ value: 1 })).toBe("ready");
    expect(valueState({ value: -3 })).toBe("ready");
    expect(valueState({ value: 12, configured: true })).toBe("ready");
  });
});
