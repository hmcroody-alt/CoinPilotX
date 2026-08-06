/**
 * Stage-by-stage trace of the engineer access flow.
 *
 * The reason this exists: when the gate failed, every stage looked identical
 * from the outside. Tapping the button, typing eight digits and being shown
 * "Access Denied" is the same user-visible outcome whether the passcode was
 * wrong, the account was not authorized, a stale lockout was in force, or — as
 * was actually the case — the server route did not exist and every attempt was
 * a 404 that the client correctly, and silently, treated as a denial. A single
 * denial string cannot distinguish those, so the flow is traced instead.
 *
 * Nothing here may carry the entered passcode. `inputLength` is a count and the
 * payload type has no field a passcode could be put in without editing this
 * file, which is the point: the constraint is enforced by the type, not by
 * everyone remembering it.
 */

import { isFlagValueOn } from "../core/envFlag";

export type EngineerAccessStage =
  | "button_tapped"
  | "modal_opened"
  | "input_length"
  | "verification_started"
  | "verification_source"
  | "authorization_result"
  | "destination_requested"
  | "navigation_completed";

export type EngineerAccessVerificationSource = "local" | "server";

export type EngineerAccessAuthorizationResult = "approved" | "denied" | "error" | "locked";

export type EngineerAccessDiagnostic = {
  stage: EngineerAccessStage;
  /** Digit count only. Never the digits. */
  inputLength?: number;
  source?: EngineerAccessVerificationSource;
  result?: EngineerAccessAuthorizationResult;
  /** Route name the engineer was originally headed for. */
  destination?: string;
  /** HTTP status when a stage failed against the server. */
  status?: number;
};

type Sink = (event: EngineerAccessDiagnostic) => void;

/**
 * Off unless the build opted in, so a public build emits nothing at all — not
 * even the fact that somebody tapped the button.
 *
 * Read from a *static* `process.env` member expression on purpose. Expo's babel
 * plugin only inlines `process.env.EXPO_PUBLIC_X` when the key is a literal; a
 * computed `process.env[name]` lookup is left as a runtime read against an
 * object that is empty in a release bundle. Spelling the name out here is what
 * makes the value survive into the build.
 */
const DIAGNOSTICS_ENABLED =
  __DEV__ || isFlagValueOn(process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK);

const defaultSink: Sink = (event) => console.log("PulseSocEngineerAccess", event);
let sink: Sink = defaultSink;

/** Test seam. Returns a restore function. */
export function setEngineerAccessDiagnosticsSink(next: Sink | null): () => void {
  const previous = sink;
  sink = next || defaultSink;
  return () => {
    sink = previous;
  };
}

export function engineerAccessDiagnosticsEnabled(): boolean {
  return DIAGNOSTICS_ENABLED;
}

export function emitEngineerAccessDiagnostic(event: EngineerAccessDiagnostic): void {
  if (!DIAGNOSTICS_ENABLED) return;
  try {
    sink(event);
  } catch {
    // A broken sink must never take down the gate it is observing.
  }
}
