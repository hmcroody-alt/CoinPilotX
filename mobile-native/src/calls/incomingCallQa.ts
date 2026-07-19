import { PulseCall } from "../api/calls";
import { PULSE_API_BASE_URL } from "../api/config";

export function qaIncomingCallFromUrl(url: string | null, currentUserId?: number): PulseCall | null {
  if (!url || !isIncomingCallQaEnabled(url)) return null;
  const parsed = parseUrl(url);
  if (!parsed || !isDedicatedIncomingCallQaRoute(parsed)) return null;
  const incoming = truthy(parsed.searchParams.get("qa_incoming_call"));
  const active = truthy(parsed.searchParams.get("qa_active_call"));
  if (!incoming && !active) return null;
  const callId = parsed.searchParams.get("call_id") || (incoming ? "qa-incoming-call" : "qa-active-call");
  const callType = parsed.searchParams.get("call_type") === "video" ? "video" : "audio";
  const status = active ? "connected" : "ringing";
  const callerName = parsed.searchParams.get("caller") || "PulseSoc QA";
  return {
    ok: true,
    call_id: callId,
    public_id: callId,
    conversation_id: Number(parsed.searchParams.get("conversation_id") || 1001),
    call_type: callType,
    status,
    room_name: `qa-${callId}`,
    participants: [
      {
        user_id: 9001,
        display_name: callerName,
        username: "pulseqa",
        role: "caller",
        status: active ? "joined" : "calling"
      },
      {
        user_id: Number(currentUserId || 9002),
        display_name: "You",
        role: "callee",
        status
      }
    ],
    participant: {
      user_id: Number(currentUserId || 9002),
      role: "callee",
      status
    }
  };
}

function isDedicatedIncomingCallQaRoute(parsed: URL) {
  if (parsed.protocol === "pulsesoc:") return parsed.hostname === "qa" && parsed.pathname === "/incoming-call";
  return isLocalUrl(parsed.toString()) && parsed.pathname === "/qa/incoming-call";
}

export function isIncomingCallQaEnabled(url: string) {
  return __DEV__ && (isLocalApiBaseUrl(PULSE_API_BASE_URL) || isLocalUrl(url));
}

function truthy(value: string | null) {
  return ["1", "true", "yes"].includes(String(value || "").toLowerCase());
}

function parseUrl(url: string) {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

function isLocalApiBaseUrl(value: string) {
  const parsed = parseUrl(value);
  return Boolean(parsed && ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname));
}

function isLocalUrl(value: string) {
  const parsed = parseUrl(value);
  return Boolean(parsed && ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname));
}
