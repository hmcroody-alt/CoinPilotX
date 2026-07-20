/**
 * UNDX screen-context provider (privacy-sanitizing, pure).
 *
 * The native chat previously sent a hardcoded `{ current_route: "Chat" }` to UNDX
 * regardless of where the user actually was. This module builds an honest, minimal,
 * privacy-safe `ui_context` from real signals and strips anything sensitive.
 *
 * Design rules (see mission section 10 Screen Context, section 19 Privacy):
 * - Allowlist only: the output is assembled from known-safe fields, never spread from raw input.
 * - Defense in depth: `sanitizeUiContext` additionally drops any key whose name looks sensitive
 *   and any non-primitive/oversized value, so accidental leakage cannot reach the wire.
 * - No tokens, message bodies, content, contact info, or private fields are ever included.
 */

export type UndxColorScheme = "light" | "dark";

export type UndxContextSignals = {
  surface?: string | null;
  originRoute?: string | null;
  platform?: string | null;
  appVersion?: string | null;
  screenReaderEnabled?: boolean | null;
  reduceMotionEnabled?: boolean | null;
  colorScheme?: UndxColorScheme | string | null;
  timezone?: string | null;
  selectedConversationId?: number | null;
};

export type UndxUiContextValue = string | number | boolean;
export type UndxUiContext = Record<string, UndxUiContextValue>;

const MAX_VALUE_LENGTH = 64;

// Keys that must never be forwarded to the assistant context, even if a caller adds them.
const FORBIDDEN_KEY_PATTERN =
  /(token|secret|password|passcode|auth|bearer|credential|cookie|session|body|message|content|caption|email|phone|address|payment|card|ssn|private|apikey|api_key|api-key)/i;

// A route name is an identifier only. Reject anything carrying a path, query, url, or params.
const SAFE_ROUTE_NAME = /^[A-Za-z][A-Za-z0-9_]{0,47}$/;

function stripControlChars(value: string): string {
  let out = "";
  for (const ch of value) {
    const code = ch.codePointAt(0) ?? 0;
    if (code < 0x20 || code === 0x7f) continue;
    out += ch;
  }
  return out;
}

function sanitizeToken(value: string | null | undefined): string | undefined {
  if (typeof value !== "string") return undefined;
  const cleaned = stripControlChars(value).trim();
  if (!cleaned) return undefined;
  return cleaned.slice(0, MAX_VALUE_LENGTH);
}

export function sanitizeRouteName(value: string | null | undefined): string | undefined {
  const token = sanitizeToken(value);
  if (!token) return undefined;
  if (!SAFE_ROUTE_NAME.test(token)) return undefined;
  if (FORBIDDEN_KEY_PATTERN.test(token)) return undefined;
  return token;
}

/**
 * Final redaction pass. Drops sensitive-looking keys, non-primitive values, and caps strings.
 * Safe to run on any object before it leaves the device.
 */
export function sanitizeUiContext(raw: Record<string, unknown>): UndxUiContext {
  const output: UndxUiContext = {};
  for (const [key, value] of Object.entries(raw || {})) {
    if (FORBIDDEN_KEY_PATTERN.test(key)) continue;
    if (typeof value === "string") {
      const token = sanitizeToken(value);
      if (token !== undefined) output[key] = token;
    } else if (typeof value === "boolean") {
      output[key] = value;
    } else if (typeof value === "number") {
      if (Number.isFinite(value)) output[key] = value;
    }
    // objects, arrays, null, undefined, and functions are intentionally dropped
  }
  return output;
}

export function buildUndxUiContext(signals: UndxContextSignals): UndxUiContext {
  const draft: Record<string, unknown> = {
    surface: sanitizeToken(signals.surface) || "undx_chat"
  };

  const origin = sanitizeRouteName(signals.originRoute);
  if (origin) draft.origin_route = origin;

  const platform = sanitizeToken(signals.platform);
  if (platform) draft.platform = platform;

  const appVersion = sanitizeToken(signals.appVersion);
  if (appVersion) draft.app_version = appVersion;

  if (typeof signals.screenReaderEnabled === "boolean") {
    draft.screen_reader_enabled = signals.screenReaderEnabled;
  }
  if (typeof signals.reduceMotionEnabled === "boolean") {
    draft.reduce_motion_enabled = signals.reduceMotionEnabled;
  }
  if (signals.colorScheme === "light" || signals.colorScheme === "dark") {
    draft.color_scheme = signals.colorScheme;
  }

  const timezone = sanitizeToken(signals.timezone);
  if (timezone) draft.timezone = timezone;

  if (
    typeof signals.selectedConversationId === "number" &&
    Number.isFinite(signals.selectedConversationId)
  ) {
    draft.selected_conversation_id = signals.selectedConversationId;
  }

  return sanitizeUiContext(draft);
}
