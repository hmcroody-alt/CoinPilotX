import { PulseApiError } from "./pulseApi";

// Shared, user-facing error messaging for destructive post/reel delete
// flows. Centralized so Home, Profile, Post Detail, and Reels all surface
// consistent, specific copy for each failure mode instead of a generic
// "Something went wrong."
export function describeDeleteError(err: unknown, kind: "Post" | "Reel" = "Post"): string {
  if (err instanceof PulseApiError) {
    switch (err.status) {
      case 401:
        return "Your session expired. Sign in again to finish deleting.";
      case 403:
        return `You can only delete your own ${kind.toLowerCase()}s.`;
      case 404:
        return `This ${kind.toLowerCase()} was already removed.`;
      case 409:
        return `This ${kind.toLowerCase()} changed elsewhere and could not be deleted. Refresh and try again.`;
      case 422:
        return err.message || `This ${kind.toLowerCase()} could not be deleted.`;
      case 429:
        return "Too many attempts. Wait a moment and try again.";
      default:
        if (err.status >= 500) {
          return `${kind} could not be deleted right now. Please try again shortly.`;
        }
        return err.message || `${kind} could not be deleted.`;
    }
  }
  if (err instanceof TypeError || (err instanceof Error && /network/i.test(err.message))) {
    return "You're offline. Reconnect and try deleting again.";
  }
  return err instanceof Error ? err.message : `${kind} could not be deleted.`;
}
