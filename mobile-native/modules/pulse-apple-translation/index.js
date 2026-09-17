import { requireNativeViewManager, requireOptionalNativeModule } from "expo-modules-core";

// Thin, defensive surface over the `PulseAppleTranslation` native module.
//
// Everything here must behave sensibly when the native module is absent —
// Android, Expo Go, a web build, or an iOS build made before this module
// existed. In every one of those cases the caller gets the typed
// `native_bridge_unavailable` failure and the JS router decides what to do,
// rather than an exception escaping into a render.
//
// All policy (Apple-first ordering, caching, whether cloud fallback is
// permitted) lives in `src/services/translation/`. This file only marshals.

const NativeModule = requireOptionalNativeModule("PulseAppleTranslation");

/** True when the native module is linked into this binary. */
export const isAppleTranslationModuleLinked = Boolean(NativeModule);

/**
 * True when the native module is linked AND this device's iOS version actually
 * ships Translation.framework. These are different questions: an iOS 16 device
 * running this build answers `true` to the first and `false` to this one.
 */
export const isAppleTranslationAvailable = Boolean(NativeModule?.isAvailable);

export const appleTranslationLimits = {
  maxTextLength: NativeModule?.maxTextLength ?? 5000,
  minimumOSVersion: NativeModule?.minimumOSVersion ?? "18.0",
  osVersion: NativeModule?.osVersion ?? null,
};

function unavailable(requestId) {
  return {
    ok: false,
    requestId,
    code: "native_bridge_unavailable",
    recoverable: false,
    permitsCloudFallback: true,
  };
}

export async function getSupportedLanguages() {
  if (!NativeModule) return [];
  try {
    return await NativeModule.getSupportedLanguages();
  } catch {
    return [];
  }
}

export async function getLanguageStatus(sourceLanguage, targetLanguage) {
  if (!NativeModule) {
    return { status: "unsupported", reason: "native_bridge_unavailable", target: targetLanguage };
  }
  try {
    return await NativeModule.getLanguageStatus(sourceLanguage ?? null, targetLanguage);
  } catch {
    // A thrown availability probe is not evidence that Apple lacks the
    // language, so it must not be reported as `unsupported` — that would make
    // the UI lie about Apple's coverage (Stage 5).
    return { status: "temporarily_unavailable", target: targetLanguage };
  }
}

export async function translate(request) {
  if (!NativeModule) return unavailable(request?.requestId ?? "");
  try {
    return await NativeModule.translate(request);
  } catch (error) {
    // The native side returns typed failures as resolved payloads, so reaching
    // here means the bridge itself broke (module unloaded, argument coercion).
    return {
      ok: false,
      requestId: request?.requestId ?? "",
      code: "native_bridge_unavailable",
      recoverable: true,
      permitsCloudFallback: true,
      detail: String(error?.message ?? error ?? "bridge_threw").slice(0, 120),
    };
  }
}

export async function cancelRequests(requestIds, reason) {
  if (!NativeModule || !requestIds?.length) return;
  try {
    await NativeModule.cancelRequests(requestIds, reason ?? null);
  } catch {
    // Cancellation is best effort; a failure here only costs wasted on-device
    // work, never a billable request.
  }
}

export async function cancelContent(contentIds, reason) {
  if (!NativeModule || !contentIds?.length) return;
  try {
    await NativeModule.cancelContent(contentIds, reason ?? null);
  } catch {
    /* best effort — see cancelRequests */
  }
}

export async function resetAppleTranslation(reason) {
  if (!NativeModule) return;
  try {
    await NativeModule.reset(reason ?? null);
  } catch {
    /* best effort */
  }
}

export async function getDiagnostics() {
  if (!NativeModule) {
    return { isAvailable: false, isHostMounted: false, activeHosts: 0 };
  }
  try {
    return await NativeModule.getDiagnostics();
  } catch {
    return { isAvailable: false, isHostMounted: false, activeHosts: 0 };
  }
}

/**
 * The native host view. Mounting it is what gives Apple a `TranslationSession`;
 * nothing can be translated while it is unmounted.
 *
 * Returns `null` when the native module is absent so callers can render nothing
 * instead of crashing.
 */
export const AppleTranslationHostView = (() => {
  if (!NativeModule) return null;
  try {
    return requireNativeViewManager("PulseAppleTranslation");
  } catch {
    return null;
  }
})();
