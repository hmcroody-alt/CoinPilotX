/**
 * BCP-47 canonicalisation, mirroring `LanguageNormalizer` in
 * `modules/pulse-apple-translation/ios/AppleTranslationModels.swift`.
 *
 * The two implementations must agree, because the router uses this one to build
 * cache keys and to decide whether a pair is same-language, while the native
 * side uses the Swift one to build the `TranslationSession.Configuration`. A
 * divergence would mean a cache entry filed under `zh-CN` that Apple answered
 * as `zh-Hans`, i.e. a permanent cache miss that costs an on-device round trip
 * per render. `__tests__/languages.test.ts` pins the shared cases.
 */

/** Tokens that mean "unspecified" and therefore request auto-detection. */
const AUTO_TOKENS = new Set(["", "auto", "und", "unknown", "zxx", "mul"]);

/**
 * Codes ICU canonicalises away. The left side still arrives from PulseSoc's own
 * stored user preferences and from legacy Google responses, so it cannot simply
 * be rejected.
 */
const LEGACY_PRIMARY: Record<string, string> = {
  iw: "he",
  in: "id",
  ji: "yi",
  mo: "ro",
  no: "nb",
  sh: "sr",
  tl: "fil"
};

/** Region-flavoured Chinese must become script-flavoured or Apple reports it unsupported. */
const CHINESE_REGION_TO_SCRIPT: Record<string, string> = {
  cn: "Hans",
  sg: "Hans",
  tw: "Hant",
  hk: "Hant",
  mo: "Hant"
};

export type LanguageOutcome =
  | { kind: "language"; tag: string }
  | { kind: "automatic" }
  | { kind: "invalid" };

const ALPHA = /^[a-z]+$/;
const DIGITS = /^[0-9]+$/;

function isValidPrimarySubtag(value: string) {
  return value.length >= 2 && value.length <= 8 && ALPHA.test(value);
}

function capitalizeAscii(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
}

export function normalizeLanguage(raw: string | null | undefined): LanguageOutcome {
  if (raw === null || raw === undefined) return { kind: "automatic" };
  const cleaned = raw.trim().replace(/_/g, "-");
  if (AUTO_TOKENS.has(cleaned.toLowerCase())) return { kind: "automatic" };

  const parts = cleaned.split("-").filter(part => part.length > 0);
  if (parts.length === 0) return { kind: "automatic" };

  let primary = parts[0].toLowerCase();
  if (!isValidPrimarySubtag(primary)) return { kind: "invalid" };
  if (primary in LEGACY_PRIMARY) primary = LEGACY_PRIMARY[primary];

  let script: string | null = null;
  let region: string | null = null;
  const variants: string[] = [];

  for (const part of parts.slice(1)) {
    const lower = part.toLowerCase();
    if (part.length === 4 && ALPHA.test(lower) && script === null) {
      script = capitalizeAscii(part);
    } else if (region === null && part.length === 2 && ALPHA.test(lower)) {
      region = part.toUpperCase();
    } else if (region === null && part.length === 3 && DIGITS.test(part)) {
      region = part;
    } else {
      variants.push(lower);
    }
  }

  if (primary === "zh" && script === null && region !== null) {
    // Keep the region as well: Apple accepts `zh-Hant-TW` and it is the more
    // specific request.
    const mapped = CHINESE_REGION_TO_SCRIPT[region.toLowerCase()];
    if (mapped) script = mapped;
  }

  let tag = primary;
  if (script) tag += `-${script}`;
  if (region) tag += `-${region}`;
  for (const variant of variants) tag += `-${variant}`;
  return { kind: "language", tag };
}

/**
 * The subtag Apple matches models on — primary plus script, region dropped.
 * `pt-BR` and `pt-PT` share one model, so keeping the region would mount two
 * hosts for one download.
 */
export function modelScope(tag: string) {
  const parts = tag.split("-");
  const primary = parts[0];
  if (!primary) return tag;
  if (parts.length > 1 && parts[1].length === 4) return `${primary}-${parts[1]}`;
  return primary;
}

/**
 * Whether translating between these two canonical tags would be a no-op.
 *
 * Compared at model scope rather than on the full tag: Apple has no `pt-BR` →
 * `pt-PT` model, and asking for one returns `same_language`, so the router must
 * answer that itself instead of mounting a host to find out.
 */
export function isSameLanguage(source: string, target: string) {
  return modelScope(source) === modelScope(target);
}
