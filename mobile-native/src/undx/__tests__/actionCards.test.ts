/**
 * The client's half of the confirmation contract.
 *
 * These assertions are about a bug that shipped: the chat screen decided whether to
 * draw Confirm and Cancel by comparing `component.component` to the literal
 * `"confirmation_card"`, while the agent runtime emits `"action_confirmation"`. The
 * backend was correct, the transport was correct, and the user still could not
 * approve anything. So the predicate is now a named function with tests, and the
 * payloads below are copied from real server responses rather than invented.
 */

import {
  describeTransition,
  isActionable,
  isConfirmation,
  readable,
  toActionCard,
} from "../actionCards";
import { UndxResponseComponent } from "../../api/messenger";

/** Verbatim shape of what services/undx_agent_runtime.build_card emits. */
const agentConfirmation: UndxResponseComponent = {
  component: "action_confirmation",
  capability_id: "crypto.alerts.delete",
  status: "confirmation_required",
  verification_state: "impossible_to_verify",
  verified: false,
  title: "Delete one crypto alert owned by the authenticated user",
  message: "I need you to confirm this before I make the change.",
  risk: "consequential_write",
  deep_link: "/pulse/crypto/alerts",
  canonical_resource_ids: [],
  task_id: "undx_req_1234",
  undo_capability_id: "",
  can_undo: false,
  timestamp: "2026-07-27T07:00:00+00:00",
  confirmation_id: "undx_confirm_1234",
  confirmation_token: "tok-abc",
  expires_at: "2026-07-27T07:05:00+00:00",
  action_name: "Delete one crypto alert owned by the authenticated user",
  target: "1",
  current_value: "active",
  proposed_value: "deleted",
  risk_summary: "This cannot be undone.",
};

/** The V4/V5 dialect for the same idea. */
const legacyConfirmation: UndxResponseComponent = {
  component: "confirmation_card",
  action_name: "Turn off notifications",
  target: "global",
  current_value: "on",
  proposed_value: "off",
  risk_summary: "You can turn this back on at any time.",
  confirmation_id: "undx_confirm_9",
  confirmation_token: "tok-legacy",
  expires_at: "2026-07-27T07:05:00+00:00",
};

describe("which cards ask for approval", () => {
  it("treats both server dialects as confirmations", () => {
    expect(isConfirmation(agentConfirmation)).toBe(true);
    expect(isConfirmation(legacyConfirmation)).toBe(true);
  });

  it("offers Confirm for the agent card the screen used to ignore", () => {
    // The regression, stated directly. Before the normaliser this was false and the
    // user had no way to complete a confirmed action from the native client.
    expect(isActionable(agentConfirmation)).toBe(true);
    expect(isActionable(legacyConfirmation)).toBe(true);
  });

  it("does not offer Confirm without a token", () => {
    // A card whose approval was never minted, or has already been spent. The button
    // is withheld rather than shown and then rejected by the server.
    expect(isActionable({ ...agentConfirmation, confirmation_token: undefined })).toBe(false);
    expect(toActionCard({ ...agentConfirmation, confirmation_token: "" }).confirmationToken).toBe("");
  });

  it("never offers Confirm on a receipt", () => {
    const receipt: UndxResponseComponent = {
      component: "crypto_alert_card",
      status: "verified_success",
      verification_state: "verified",
      verified: true,
      confirmation_token: "tok-should-be-ignored",
    };
    expect(isConfirmation(receipt)).toBe(false);
    expect(isActionable(receipt)).toBe(false);
    expect(toActionCard(receipt).confirmationToken).toBe("");
  });
});

describe("what the confirmation says", () => {
  it("names the resource and both ends of the transition", () => {
    const card = toActionCard(agentConfirmation);
    expect(card.kind).toBe("confirmation");
    expect(card.kicker).toBe("CONFIRM ACTION");
    expect(card.target).toBe("1");
    expect(describeTransition(card)).toBe("1: active → deleted");
    expect(card.risk).toBe("This cannot be undone.");
    expect(card.expiresAt).toBe("2026-07-27T07:05:00+00:00");
  });

  it("renders a false current value as 'off' rather than dropping it", () => {
    // The reason `readable` exists. Notification preferences arrive as booleans, and
    // a truthiness check would print "notifications: on" for a card whose whole
    // purpose is to say they are currently off.
    const card = toActionCard({
      ...legacyConfirmation,
      target: "messages",
      current_value: false,
      proposed_value: true,
    });
    expect(readable(false)).toBe("off");
    expect(describeTransition(card)).toBe("messages: off → on");
  });

  it("omits the arrow when the server could not read the current value", () => {
    const card = toActionCard({ ...agentConfirmation, current_value: null });
    expect(describeTransition(card)).toBe("1: deleted");
  });
});

describe("what the receipt says", () => {
  it("claims a verified change only when the server verified it", () => {
    const verified = toActionCard({
      component: "setting_change_receipt",
      status: "verified_success",
      verification_state: "verified",
      verified: true,
      title: "Update a notification preference",
      target: "messages",
      current_value: true,
      proposed_value: false,
    });
    expect(verified.kind).toBe("receipt");
    expect(verified.verified).toBe(true);
    expect(verified.kicker).toBe("VERIFIED RESULT");
  });

  it("downgrades its own kicker when the write could not be read back", () => {
    const unverified = toActionCard({
      component: "setting_change_receipt",
      status: "accepted_unverified",
      verification_state: "unverified",
      verified: false,
      title: "Update a notification preference",
    });
    expect(unverified.verified).toBe(false);
    expect(unverified.kicker).toBe("RESULT");
  });

  it("reads a typed refusal as a failure, not a result", () => {
    for (const component of ["action_failure", "permission_denied", "unsupported_capability", "retry_action"] as const) {
      expect(toActionCard({ component, title: "x" }).kind).toBe("failure");
      expect(toActionCard({ component, title: "x" }).kicker).toBe("NOT DONE");
    }
  });

  it("surfaces a refused duplicate as such", () => {
    const replay = toActionCard({
      component: "crypto_alert_card",
      status: "verified_success",
      verified: true,
      idempotent_replay: true,
    });
    expect(replay.idempotentReplay).toBe(true);
  });
});

describe("search results still render as before", () => {
  it("keeps its own kicker and preview text", () => {
    const card = toActionCard({
      component: "search_result_card",
      content_type: "reel",
      preview_text: "A reel about staking",
      relevance_reason: "Matches your search",
      deep_link: "/pulse/reels/9",
    });
    expect(card.kind).toBe("result");
    expect(card.kicker).toBe("REEL MATCH");
    expect(card.title).toBe("A reel about staking");
    expect(isActionable({ component: "search_result_card", confirmation_token: "x" })).toBe(false);
  });
});
