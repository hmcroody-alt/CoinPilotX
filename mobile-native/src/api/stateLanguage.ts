/**
 * The state and failure vocabulary, per ADR-0003.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * The em dash is doing at least four jobs across this app. In the same
 * character, and in the same position on the card, it means "the true answer is
 * zero", "this has not loaded yet", "this failed to load", and "this seller has
 * not set the feature up". A reader cannot tell which, and the four call for
 * four different responses — one of them is good news, one of them is a retry,
 * one of them is a setup flow, and one of them is nothing at all.
 *
 * ADR-0003 closes that set. A section is in exactly one state drawn from
 * {@link SurfaceState}, and each state has one presentation and one sentence.
 * This module is where that sentence lives, once, so that two screens cannot
 * describe the same situation differently.
 *
 * WHAT IS DELIBERATELY NOT HERE
 * -----------------------------
 * No component. The ADR's decision is about vocabulary, not about a widget: the
 * Store's KPI card, the Advertising wallet chip and the hub's Today strip each
 * have their own shape and each will keep it. What they share is the words.
 *
 * No formatting. A real zero renders as `0`, not as a dash and not as "None" —
 * but *which* zero, in which locale, is the caller's business through
 * `useFormatters`. {@link ZERO_TEXT} is the fallback for callers with no
 * formatter to hand.
 *
 * THE FLAG THAT USED TO BE HERE
 * -----------------------------
 * `EXPO_PUBLIC_STATE_LANGUAGE` staged this change and defaulted off, which meant
 * every screen converted to this vocabulary still rendered the em dash in every
 * build anyone actually installed. The conversion was real and the rollout never
 * happened, so the defect the module was written to fix was still shipping —
 * with the code to fix it sitting one unset variable away.
 *
 * The wording is unconditional now and the flag is gone. That is a decision
 * about *when*, not about *what*: nothing below changed, three screens started
 * showing what they were already computing, and there is no longer a build in
 * which a "couldn't load" and a "not set up yet" look identical.
 */

import { PulseApiError } from "./pulseApi";

/**
 * The character this module exists to retire.
 *
 * Kept as an export, but no function returns it any more. It survives so tests
 * can assert its absence by name rather than by pasting a dash that is easy to
 * confuse with a hyphen, and so a future call site reaching for "the dash" finds
 * this comment first.
 */
export const LEGACY_ABSENT_TEXT = "—";

/** What a true zero looks like when the caller has no locale formatter. */
export const ZERO_TEXT = "0";

/**
 * The closed set of states a screen or a section can be in.
 *
 * `zero` and `no_activity` are separate on purpose. "You have sold nothing,
 * ever" and "you have sold nothing *this week*" call for different reactions,
 * and a seller who reads the second as the first concludes their business has
 * stopped.
 *
 * `not_configured` and `zero` are separate for the same reason in the other
 * direction: an empty store is not an open store with no sales, it is a store
 * that has not been set up, and only one of those has a next step.
 */
export type SurfaceState =
  | "loading"
  | "ready"
  | "ready_from_cache"
  | "zero"
  | "no_activity"
  | "not_configured"
  | "restricted"
  | "unavailable";

/** The closed set of reasons a request did not answer. */
export type FailureCause =
  | "offline"
  | "authentication"
  | "entitlement"
  | "service_unavailable"
  | "unexpected";

/**
 * Read the cause out of whatever was thrown.
 *
 * The offline case is the one worth spelling out. A device with no connection
 * never reaches the other side, so `fetch` rejects and `pulseApi` converts that
 * into a 503 carrying `request_unreachable`. A 503 *with* that code is the
 * phone's problem and retrying in the same second will fail again; a 503
 * without it is the other side's problem and retrying is exactly right. Reading
 * only the status collapses those two into one message with one wrong action.
 */
export function failureCause(error: unknown): FailureCause {
  if (!(error instanceof PulseApiError)) return "unexpected";
  if (error.code === "request_unreachable") return "offline";
  if (error.status === 401) return "authentication";
  if (error.status === 403) return "entitlement";
  if (error.status === 503 || error.status === 502 || error.status === 504) {
    return "service_unavailable";
  }
  return "unexpected";
}

export type FailureCopy = {
  cause: FailureCause;
  /** One sentence naming what happened, in the reader's terms. */
  message: string;
  /**
   * The control that would resolve it, or `null` when nothing the reader can
   * press would help. Entitlement is the only cause that does not retry — a
   * second attempt at something you are not allowed to see fails identically.
   */
  actionLabel: string | null;
};

/**
 * One sentence per cause, written once.
 *
 * `subject` is what failed, in the reader's words — "Your sales", "Nearby
 * items". It is interpolated rather than baked in so that the five sentences
 * stay five sentences instead of multiplying by the number of screens.
 */
export function failureCopy(cause: FailureCause, subject: string): FailureCopy {
  switch (cause) {
    case "offline":
      return {
        cause,
        message: `${subject} couldn't load — you're offline.`,
        actionLabel: "Try again"
      };
    case "authentication":
      return {
        cause,
        message: `${subject} couldn't load. Sign in again to see it.`,
        actionLabel: "Sign in"
      };
    case "entitlement":
      return {
        cause,
        message: `${subject} isn't available on your account.`,
        actionLabel: null
      };
    case "service_unavailable":
      return {
        cause,
        message: `${subject} couldn't load. PulseSoc couldn't be reached.`,
        actionLabel: "Try again"
      };
    default:
      return {
        cause,
        message: `${subject} couldn't load.`,
        actionLabel: "Try again"
      };
  }
}

/** Shorthand: cause and copy in one step, from a caught error. */
export function failureFrom(error: unknown, subject: string): FailureCopy {
  return failureCopy(failureCause(error), subject);
}

export type AbsentValueOptions = {
  /**
   * What a real zero should read as. Pass the locale-formatted zero when one is
   * available; the default is the digit, never a dash and never a word.
   */
  zeroText?: string;
  /**
   * Overrides "Not set up yet" for surfaces where the noun makes it read badly.
   * Kept as an override rather than a template because most callers should use
   * the shared sentence.
   */
  notConfiguredText?: string;
};

/**
 * What to render in a value slot when there is no number to render.
 *
 * This is the function that retires the dash. Each branch is a different claim,
 * and the whole point is that they no longer look alike:
 *
 * * `zero` is a **success**. The request answered and the answer was none, so
 *   it renders as a figure — `0` — because that is what it is.
 * * `no_activity` is also a success, but a windowed one. "None yet" rather than
 *   `0` because the reader is looking at a period, and a bare zero in a period
 *   view reads as a total.
 * * `not_configured` is a next step, not a measurement.
 * * `unavailable` is a failure, and says so instead of looking like a zero.
 * * `restricted` names the wall without implying the data is missing.
 *
 * Every branch returns wording. There is no path back to the dash.
 */
export function absentValueText(state: SurfaceState, options: AbsentValueOptions = {}): string {
  const zeroText = options.zeroText ?? ZERO_TEXT;
  // A real zero is a real figure and was never the dash's job: it renders as a
  // number, because that is what was measured.
  if (state === "zero") return zeroText;
  switch (state) {
    case "not_configured":
      return options.notConfiguredText ?? "Not set up yet";
    case "no_activity":
      return "None yet";
    case "unavailable":
      return "Couldn't load";
    case "restricted":
      return "Not available to you";
    case "loading":
      return "Checking…";
    default:
      return zeroText;
  }
}

/*
 * `absentValueTextOr(legacy, state, options)` used to live here.
 *
 * It existed for the rollout: a call site could record its state in the same
 * commit as the reasoning while a build with the flag off kept the exact string
 * it had always rendered. With the flag gone it reduces to
 * `absentValueText(state, options)` with a dead first argument — and a dead
 * argument that used to hold a dash is an invitation to put a dash back.
 *
 * Its three call sites now call `absentValueText` directly.
 */

/**
 * The spoken form of the same thing.
 *
 * "—" is announced as "em dash" or skipped entirely depending on the screen
 * reader, so every one of these states was previously either mispronounced or
 * silent. This stayed at full wording throughout the rollout, when the visual
 * form could still degrade to the dash: assistive technology has no visual
 * context to fall back on, so the degraded case was worse there than anywhere
 * else. Now both forms say the same thing, which is what should have been true
 * all along.
 */
export function absentValueSpokenText(state: SurfaceState, options: AbsentValueOptions = {}): string {
  switch (state) {
    case "zero":
      return options.zeroText ?? ZERO_TEXT;
    case "not_configured":
      return options.notConfiguredText ?? "Not set up yet";
    case "no_activity":
      return "None yet";
    case "unavailable":
      return "Couldn't load";
    case "restricted":
      return "Not available to you";
    case "loading":
      return "Checking";
    default:
      return options.zeroText ?? ZERO_TEXT;
  }
}

/**
 * Choose a state for one figure.
 *
 * The order matters and encodes the ADR: a failure outranks everything, then
 * "you have not set this up", then the windowed distinction, and only then is a
 * number a number. Callers that skip this and write `value ?? "—"` are how the
 * four meanings collapsed into one character in the first place.
 */
export function valueState(input: {
  failed?: boolean;
  restricted?: boolean;
  loading?: boolean;
  configured?: boolean;
  /** True when the figure covers a period rather than all time. */
  windowed?: boolean;
  value: number | null | undefined;
}): SurfaceState {
  if (input.restricted) return "restricted";
  if (input.failed) return "unavailable";
  if (input.loading) return "loading";
  if (input.configured === false) return "not_configured";
  if (input.value === null || input.value === undefined) return "unavailable";
  if (input.value === 0) return input.windowed ? "no_activity" : "zero";
  return "ready";
}
