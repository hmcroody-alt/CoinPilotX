/**
 * Clipboard service (Phase 8) — the single clipboard owner.
 *
 * Write-only by design: PulseSoc never reads or scrapes clipboard content.
 * Every copy returns a confirmation flag so surfaces can show "Copied".
 */
import * as ExpoClipboard from "expo-clipboard";
import { haptic } from "./haptics";

export type CopyKind =
  | "link"
  | "username"
  | "invite_code"
  | "address"
  | "wallet_address"
  | "business_info"
  | "text";

export type CopyResult = { ok: boolean; kind: CopyKind };

export async function copyToClipboard(value: string, kind: CopyKind = "text"): Promise<CopyResult> {
  const text = String(value ?? "").trim();
  if (!text) return { ok: false, kind };
  try {
    await ExpoClipboard.setStringAsync(text);
    haptic("light");
    return { ok: true, kind };
  } catch {
    return { ok: false, kind };
  }
}
