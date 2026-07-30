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
  choiceRowsOf,
  choicesOf,
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

  it("names the alert in words when the server sent them", () => {
    // "1: active → deleted" is a transition attached to a row id this app never
    // displays. Two alerts on two different coins produce cards that differ only in
    // that number, so the sentence has to be built from the label when there is one.
    const card = toActionCard({
      ...agentConfirmation,
      resource_label: "DOGE alert · above · 0.5",
    });
    expect(card.resourceLabel).toBe("DOGE alert · above · 0.5");
    expect(describeTransition(card)).toBe("DOGE alert · above · 0.5: active → deleted");
    // The identifier is not replaced by the label. It is what the approval is bound
    // to, and a client that dropped it would have nothing to send back.
    expect(card.target).toBe("1");
  });

  it("falls back to the identifier rather than inventing a description", () => {
    // A blank label means the server could not read the row. A bare id says little,
    // but it says something true; anything composed here would be the client
    // describing a row it has never seen.
    const card = toActionCard({ ...agentConfirmation, resource_label: "" });
    expect(card.resourceLabel).toBe("");
    expect(describeTransition(card)).toBe("1: active → deleted");
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
      verification_state: "verification_pending",
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

  it("carries the arguments that reverse the change, not the ones that made it", () => {
    // The trap this guards. `notifications.preference.update` undoes itself, so a
    // client that built the undo by replaying what it just sent would turn the
    // notification back off — confirming the change instead of reversing it. The
    // server sends the inverted arguments and the client must use those.
    const card = toActionCard({
      component: "setting_change_receipt",
      status: "verified_success",
      verification_state: "verified",
      verified: true,
      target: "reels",
      current_value: true,
      proposed_value: false,
      undo_capability_id: "notifications.preference.update",
      undo_arguments: { category: "reels", push: true },
    });
    expect(card.undoCapabilityId).toBe("notifications.preference.update");
    expect(card.undoArguments).toEqual({ category: "reels", push: true });
  });

  it("offers no undo when the server sent a capability but no arguments", () => {
    // Half a contract is not a button. A capability id alone would produce a call
    // with no target, which the server would reject after the user had pressed it.
    const card = toActionCard({
      component: "crypto_alert_card",
      status: "verified_success",
      verification_state: "verified",
      verified: true,
      undo_capability_id: "crypto.alerts.delete",
    });
    expect(card.undoCapabilityId).toBe("");
    expect(card.undoArguments).toEqual({});
  });

  it("offers no undo on a card that is not a receipt", () => {
    const card = toActionCard({
      ...agentConfirmation,
      undo_capability_id: "crypto.alerts.resume",
      undo_arguments: { alert_id: 1 },
    });
    expect(card.kind).toBe("confirmation");
    expect(card.undoCapabilityId).toBe("");
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

/**
 * Both question cards, copied verbatim from this runtime rather than invented — the
 * `candidates` array is the only thing trimmed, and it is rebuilt below where it
 * matters. Two alerts and "pause my bitcoin alert" produces the first; one alert and
 * "change my bitcoin alert" produces the second.
 */
const chooser: UndxResponseComponent = {
  component: "choice_required",
  capability_id: "crypto.alerts.pause",
  status: "clarification_required",
  verification_state: "impossible_to_verify",
  verified: false,
  title: "Pause one crypto alert so it stops triggering",
  message: "More than one of your alerts matches that description.",
  risk: "reversible_write",
  deep_link: "/pulse/alerts",
  record_count: 2,
  needs_answer: true,
  needs_disambiguation: true,
  awaiting_fields: ["alert_id"],
  candidates: [
    { alert_id: 2, symbol: "BTC", display_name: "BTC alert", choice_index: 1 },
    { alert_id: 1, symbol: "BTC", display_name: "BTC alert", choice_index: 2 },
  ],
  task_id: "undx_req_06f1efb14faeed5ccdb0",
  timestamp: "2026-07-30T13:53:09+00:00",
};

const clarification: UndxResponseComponent = {
  component: "clarification_required",
  capability_id: "crypto.alerts.update",
  status: "clarification_required",
  verification_state: "impossible_to_verify",
  verified: false,
  title: "Change the threshold or condition of an existing crypto alert",
  message: "What price should it trigger at?",
  risk: "consequential_write",
  deep_link: "/pulse/alerts",
  record_count: 0,
  needs_answer: true,
  needs_disambiguation: false,
  awaiting_fields: ["threshold"],
  task_id: "undx_req_da4ec2baeae9db382816",
  timestamp: "2026-07-30T13:53:09+00:00",
};

describe("a question is drawn as a question", () => {
  /**
   * The defect this batch fixed, asserted on the side of the wire where it did damage.
   *
   * Before it, the chooser arrived as `crypto_alert_card` — the capability's *success*
   * card — which lands in `RECEIPT_COMPONENTS`, so "which of these two alerts?" was
   * drawn under the kicker reserved for something that already happened. The kind is
   * asserted here rather than on the server because the kind is a client decision;
   * the server's half is in `tests/undx_agent/test_question_shape.py`.
   */
  it("classifies both question cards as questions, not receipts or failures", () => {
    expect(toActionCard(chooser).kind).toBe("question");
    expect(toActionCard(clarification).kind).toBe("question");
  });

  it("uses a kicker that does not read like something went wrong", () => {
    // The person is not being told the request failed. They are being asked one thing.
    expect(toActionCard(chooser).kicker).toBe("ONE MORE THING");
    expect(toActionCard(clarification).kicker).toBe("ONE MORE THING");
  });

  it("never claims a question verified anything", () => {
    // `verified` drives the checkmark, and the second half of each pair is the part
    // worth having: a question that arrived claiming verification — because a server
    // regression borrowed a receipt's payload, which is exactly what this batch found
    // it doing — still must not draw one, because only a receipt can be verified.
    for (const component of [chooser, clarification]) {
      expect(toActionCard(component).verified).toBe(false);
      expect(
        toActionCard({ ...component, verified: true, verification_state: "verified" }).verified,
      ).toBe(false);
    }
  });

  it("offers neither Confirm nor Undo on a question", () => {
    // A question carries no confirmation token and nothing to reverse. The token is
    // forced on below to prove the guard is the card kind rather than its absence.
    for (const component of [chooser, clarification]) {
      expect(isConfirmation(component)).toBe(false);
      expect(isActionable({ ...component, confirmation_token: "tok-should-be-ignored" })).toBe(
        false,
      );
      expect(
        toActionCard({
          ...component,
          undo_capability_id: "crypto.alerts.resume",
          undo_arguments: { alert_id: 1 },
        }).undoCapabilityId,
      ).toBe("");
    }
  });

  it("hands back the chooser's rows in the server's order", () => {
    // Order is load-bearing, not cosmetic. The reply is resolved against the list the
    // server remembers, so a screen that sorted these locally would put a different
    // row under the finger than the one the server would resolve.
    const rows = choicesOf(chooser);
    expect(rows.map((row) => row.alert_id)).toEqual([2, 1]);
    expect(rows.map((row) => row.choice_index)).toEqual([1, 2]);
  });

  it("offers no rows on anything that is not a chooser", () => {
    // `candidates` has historically ridden along on other cards. Only the chooser is a
    // list of things to pick between, and a prompt-to-type that rendered rows would be
    // asking two different questions at once.
    expect(choicesOf(clarification)).toEqual([]);
    expect(choicesOf({ ...clarification, candidates: [{ alert_id: 1 }] })).toEqual([]);
    expect(
      choicesOf({
        component: "crypto_alert_card",
        status: "verified_success",
        candidates: [{ alert_id: 1 }],
      }),
    ).toEqual([]);
  });

  it("survives a chooser whose rows did not arrive", () => {
    // Defensive rather than expected: the server pairs the component with the rows and
    // `test_a_chooser_carries_the_rows_it_is_asking_about` holds it to that. A screen
    // that read `.length` off undefined would blank on a malformed payload instead.
    expect(choicesOf({ ...chooser, candidates: undefined })).toEqual([]);
  });
});

describe("what a chooser's rows say", () => {
  it("numbers each row with the server's choice_index, not its array index", () => {
    // The distinction is the whole point and this fixture is built so the two disagree
    // where it matters: ids run [2, 1] while the published positions run [1, 2]. A
    // screen that numbered rows by array index would happen to agree here — so the
    // second half of this assertion uses a chooser the server numbered from 2, where
    // agreeing by accident is impossible.
    expect(choiceRowsOf(chooser).map((row) => row.position)).toEqual([1, 2]);
    const renumbered = choiceRowsOf({
      ...chooser,
      candidates: [
        { alert_id: 9, display_name: "BTC alert", choice_index: 2 },
        { alert_id: 8, display_name: "ETH alert", choice_index: 3 },
      ],
    });
    expect(renumbered.map((row) => row.position)).toEqual([2, 3]);
    expect(renumbered.map((row) => row.reply)).toEqual(["2", "3"]);
  });

  it("falls back to the array position only when the server sent none", () => {
    // Not a licence to renumber. It is the one case where there is nothing to preserve,
    // and drawing an unnumbered row would leave a chooser whose rows cannot be named.
    const rows = choiceRowsOf({
      ...chooser,
      candidates: [{ alert_id: 2, display_name: "BTC alert" }, { alert_id: 1, display_name: "ETH alert" }],
    });
    expect(rows.map((row) => row.position)).toEqual([1, 2]);
  });

  it("replies with exactly the number it draws", () => {
    // Tapping a row and typing its number must mean the same thing. The server reads a
    // lone number as the position it published, so `reply` may not be an id, a label, or
    // a sentence containing the number — a sentence goes through the contradiction rule
    // and is refused.
    for (const row of choiceRowsOf(chooser)) {
      expect(row.reply).toBe(String(row.position));
    }
  });

  it("never draws a blank row", () => {
    // A chooser is unanswerable if a row has nothing on it. The person is being asked to
    // pick between things, and an empty line is not a thing.
    const rows = choiceRowsOf({
      ...chooser,
      candidates: [{ alert_id: 4, choice_index: 1 }, { symbol: "ETH", choice_index: 2 }],
    });
    expect(rows.map((row) => row.label)).toEqual(["Option 1", "ETH"]);
    expect(rows.every((row) => row.label.length > 0)).toBe(true);
  });

  it("carries the secondary line only when there is something to put on it", () => {
    expect(choiceRowsOf(chooser).map((row) => row.detail)).toEqual(["", ""]);
    const detailed = choiceRowsOf({
      ...chooser,
      candidates: [
        { alert_id: 2, display_name: "BTC alert", condition: "above", threshold: 90000, status: "active", choice_index: 1 },
      ],
    });
    expect(detailed[0].detail).toBe("above · 90000 · active");
  });

  it("offers no rows on anything that is not a chooser", () => {
    expect(choiceRowsOf(clarification)).toEqual([]);
    expect(choiceRowsOf({ ...chooser, candidates: undefined })).toEqual([]);
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
