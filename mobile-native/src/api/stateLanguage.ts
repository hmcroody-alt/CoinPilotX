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
 * THE FLAG
 * --------
 * `EXPO_PUBLIC_STATE_LANGUAGE` gates the change and defaults off. With it off,
 * {@link absentValueText} returns the same em dash every one of these sites
 * renders today, so a build that has not opted in behaves exactly as it did.
 * The flag is read per call rather than at import so a test can turn it on
 * without re-importing the module graph, matching `api/eventsManager.ts`.
 */

import { PulseApiError } from "./pulseApi";

/** The character the app currently uses for all four meanings. Retired below. */
export const LEGACY_ABSENT_TEXT = "—";

/** What a true zero looks like when the caller has no locale formatter. */
export const ZERO_TEXT = "0";

export const STATE_LANGUAGE_FLAG = "EXPO_PUBLIC_STATE_LANGUAGE";

/** True when a build has opted into the ADR-0003 wording. Off by default. */
export function stateLanguageEnabled(): boolean {
  return String(process.env[STATE_LANGUAGE_FLAG] || "").trim() === "1";
}

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
 * With the flag off this returns the legacy dash for every one of them, so the
 * call sites can be converted ahead of the rollout.
 */
export function absentValueText(state: SurfaceState, options: AbsentValueOptions = {}): string {
  const zeroText = options.zeroText ?? ZERO_TEXT;
  // A real zero is a real figure and was never the dash's job. It renders as a
  // number whether or not the new wording is switched on, because rendering a
  // measured zero as a dash is the one case that is wrong under both standards.
  if (state === "zero") return zeroText;
  if (!stateLanguageEnabled()) return LEGACY_ABSENT_TEXT;
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

/**
 * The same thing, but returning exactly what the call site renders today until
 * the flag is thrown.
 *
 * {@link absentValueText} answers "what is the right wording for this state",
 * and its answer for a measured zero is `0` under either standard — rendering a
 * counted zero as a dash is wrong on both. That is correct for the function and
 * wrong for a rollout, because it would change a screen in a build that has not
 * opted in.
 *
 * So the conversion of a call site is this function, not that one: the state is
 * chosen and recorded now, in the same commit as the rest of the reasoning,
 * while a build with the flag off keeps the string it has always shown down to
 * the character. `legacy` is the expression being replaced.
 */
export function absentValueTextOr(
  legacy: string,
  state: SurfaceState,
  options: AbsentValueOptions = {}
): string {
  return stateLanguageEnabled() ? absentValueText(state, options) : legacy;
}

/**
 * The spoken form of the same thing.
 *
 * "—" is announced as "em dash" or skipped entirely depending on the screen
 * reader, so every one of these states was previously either mispronounced or
 * silent. This is always the full wording regardless of the flag: assistive
 * technology has no visual context to fall back on, so the degraded case is
 * worse there than anywhere else.
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
