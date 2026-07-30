/**
 * One reading of a UNDX action card, shared by every renderer.
 *
 * The server speaks two dialects for the same idea. The V4/V5 conversational path
 * emits `confirmation_card`; the agent runtime emits `action_confirmation` with a
 * richer receipt attached. Left alone that difference is not cosmetic — the chat
 * screen gated its Confirm and Cancel controls on the literal string
 * `confirmation_card`, so an agent confirmation rendered as an inert block and the
 * user had no way to approve an action the backend was waiting on.
 *
 * The fix is deliberately *not* a second confirmation system. Both dialects are
 * normalised here into one shape, and the UI is written against that shape only, so
 * there is exactly one answer to "is this card asking for approval" and exactly one
 * place to change when the server settles on a single name.
 *
 * Nothing in this module renders. It is pure so the rules below — which are the
 * security-relevant ones — can be tested without a device.
 */

import { UndxResponseComponent } from "../api/messenger";

/**
 * Card types that ask the user to approve something before it happens.
 *
 * `message_draft_confirmation` belongs here even though nothing emits it yet. It is a
 * confirmation type in the server's own enum, and the classifier below treats
 * anything it does not recognise as a failure — so the day that card ships, omitting
 * it would render an *unsent draft* as a card the user reads as final. Listing it now
 * costs one line; discovering the omission costs a message sent or not sent without
 * the user knowing which.
 */
export const CONFIRMATION_COMPONENTS = [
  "action_confirmation",
  "confirmation_card",
  "message_draft_confirmation",
] as const;

/** Card types that report an action that has already been attempted. */
export const RECEIPT_COMPONENTS = [
  "action_success_receipt",
  "setting_change_receipt",
  "crypto_alert_card",
  "relationship_change_receipt",
  "verified_success_card",
] as const;

/** Card types that report an action that did not happen. */
export const FAILURE_COMPONENTS = [
  "action_failure",
  "honest_failure_card",
  "retry_action",
  "permission_denied",
  "unsupported_capability",
] as const;

/** Card types that report work still running. */
export const PROGRESS_COMPONENTS = ["action_progress"] as const;

/**
 * Card types that are an open question rather than a report.
 *
 * The distinction the client did not have. A question is not a failure — nothing was
 * attempted — and it is emphatically not a receipt, but until these names existed the
 * server had nothing else to send, so both readings happened. A missing-field question
 * arrived as `action_failure` and was drawn under "NOT DONE"; a chooser arrived as the
 * capability's own result card, `crypto_alert_card`, and was classified here as a
 * *receipt* — so "which of these two alerts?" rendered under the kicker this client
 * reserves for something that already happened.
 *
 * `choice_required` carries `candidates` and is meant to be drawn as tappable rows;
 * `clarification_required` has nothing to pick from and is a prompt to type. They are
 * two names rather than one so a renderer does not have to decide from
 * `candidates.length`, which would turn an empty chooser into a prompt for nothing.
 */
export const QUESTION_COMPONENTS = [
  "clarification_required",
  "choice_required",
] as const;

/**
 * Card types reporting that a staged action was called off in words.
 *
 * Its own bucket for the same reason `QUESTION_COMPONENTS` is: every existing home
 * would misdescribe it. Under `FAILURE_COMPONENTS` it draws as "NOT DONE", which tells
 * someone who successfully changed their mind that something went wrong; under
 * `RECEIPT_COMPONENTS` it draws as "VERIFIED RESULT", which claims a write that never
 * happened. Nothing broke and nothing was written — the only true thing to say is that
 * what was about to happen now will not.
 *
 * An older client that has never heard of `action_cancelled` falls through `kindOf` to
 * `failure`, which is wrong but visible and harmless: the grant is already dead on the
 * server by the time this card exists, so the worst outcome is a pessimistic kicker.
 */
export const CANCELLED_COMPONENTS = ["action_cancelled"] as const;

/**
 * Card types that show something found rather than something done.
 *
 * `search_results` is the agent's name and `search_result_card` the V4/V5 one; both
 * are live, because both paths are.
 */
export const RESULT_COMPONENTS = [
  "search_results",
  "search_result_card",
  "profile_result",
  "content_result",
  "conversation_result",
] as const;

export type UndxCardKind =
  | "confirmation"
  | "question"
  | "receipt"
  | "failure"
  | "result"
  | "progress"
  | "cancelled";

export type UndxActionCard = {
  kind: UndxCardKind;
  /** Short all-caps label above the card. */
  kicker: string;
  /** What the action is. */
  title: string;
  /** The resource the action names, when the server identified one. */
  target: string;
  /**
   * The same resource in words, when the server could read the row back.
   *
   * `target` is an identifier — for a crypto alert it is the row id, a number this
   * app never displays anywhere. Two confirmations staging pauses of two different
   * coins differ only in that number, so a person reading the card cannot tell which
   * alert they are approving. This is what the sentence is built from when present;
   * it is blank whenever the server could not read the row, and blank falls back to
   * the identifier rather than to an invented description.
   */
  resourceLabel: string;
  /** State before the change, blank when the server could not read it. */
  before: string;
  /** State the action would leave behind, blank when not applicable. */
  after: string;
  /** Why this needs thought. Only populated for consequential work. */
  risk: string;
  /** True only when the server independently read the change back. */
  verified: boolean;
  /** Approval token; empty string when this card is not actionable. */
  confirmationToken: string;
  /** ISO expiry of the approval, when the server sent one. */
  expiresAt: string;
  /** In-app destination for the affected resource. */
  deepLink: string;
  /** Present when the server declined to repeat a mutation it had already done. */
  idempotentReplay: boolean;
  /**
   * The capability that reverses this action, or "" when there is no undo.
   *
   * Read together with `undoArguments`, never separately. The server clears both
   * fields as a pair, so an Undo control gated on this string alone is still
   * correct — but the arguments must come from the server rather than from
   * re-sending what produced this card, which for a preference change would
   * re-apply it.
   */
  undoCapabilityId: string;
  /** Arguments for `undoCapabilityId`; empty whenever no undo is offered. */
  undoArguments: Record<string, unknown>;
};

const READABLE: Record<string, string> = {
  true: "on",
  false: "off",
  paused: "paused",
  active: "active",
  deleted: "deleted",
};

/**
 * Render a value the way a person would say it.
 *
 * `false` is the case that matters. A raw falsy check would print an empty string
 * for "notifications are currently off", and a card that silently omits the before
 * state is a card the user cannot audit — which defeats the point of asking.
 */
export function readable(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (typeof value === "boolean") {
    return value ? "on" : "off";
  }
  const text = String(value);
  return READABLE[text] ?? text;
}

function includes(list: readonly string[], name: string): boolean {
  return list.indexOf(name) !== -1;
}

/** Whether this card is asking the user to approve something not yet done. */
export function isConfirmation(component: UndxResponseComponent): boolean {
  return includes(CONFIRMATION_COMPONENTS, component.component);
}

/**
 * Whether the Confirm control should be offered.
 *
 * A confirmation card without a token cannot be approved — the approval it refers
 * to was never minted or has already been spent. Offering a button that is
 * guaranteed to fail teaches the user that Confirm sometimes does nothing, so the
 * token is part of the predicate rather than something checked after the press.
 */
export function isActionable(component: UndxResponseComponent): boolean {
  return isConfirmation(component) && Boolean(component.confirmation_token);
}

/**
 * Classify a card, and treat an unrecognised one as a failure.
 *
 * The default is the whole point. This previously fell through to `"receipt"`, which
 * meant any component name the client did not know — a new server card, a typo, a
 * rename shipped ahead of the app — rendered under the kicker "VERIFIED RESULT". The
 * one thing this client must never do is tell a user an action succeeded on the
 * strength of not recognising the message. Every classification is now explicit, and
 * whatever is left over is reported as not done.
 *
 * The lists are checked before the confirmation test purely for readability; they are
 * disjoint, and `contractParity` in the tests asserts they cover the server's enum
 * exactly, so a card added on the server without a home here fails CI rather than
 * failing quietly on a phone.
 */
function kindOf(component: UndxResponseComponent): UndxCardKind {
  if (isConfirmation(component)) {
    return "confirmation";
  }
  if (includes(QUESTION_COMPONENTS, component.component)) {
    return "question";
  }
  if (includes(CANCELLED_COMPONENTS, component.component)) {
    return "cancelled";
  }
  if (includes(RECEIPT_COMPONENTS, component.component)) {
    return "receipt";
  }
  if (includes(FAILURE_COMPONENTS, component.component)) {
    return "failure";
  }
  if (includes(PROGRESS_COMPONENTS, component.component)) {
    return "progress";
  }
  if (includes(RESULT_COMPONENTS, component.component)) {
    return "result";
  }
  return "failure";
}

const KICKERS: Record<UndxCardKind, string> = {
  confirmation: "CONFIRM ACTION",
  // Deliberately not "ACTION REQUIRED" or anything that reads like an error. The
  // person is not being told something went wrong; they are being asked one thing.
  question: "ONE MORE THING",
  receipt: "VERIFIED RESULT",
  failure: "NOT DONE",
  progress: "IN PROGRESS",
  result: "MATCH",
  // Not "NOT DONE". The action not happening is the outcome the person asked for, and
  // a kicker that reads like an error would turn a successful change of mind into a
  // report that something went wrong.
  cancelled: "CANCELLED",
};

/**
 * The rows a `choice_required` card offers, empty for every other kind.
 *
 * Read off the card rather than off `candidates.length` so a chooser that arrived
 * without rows renders as a chooser with nothing in it — visibly wrong — instead of
 * quietly turning into some other kind of card. A server bug should look like a server
 * bug.
 */
export function choicesOf(component: UndxResponseComponent): Array<Record<string, unknown>> {
  if (component.component !== "choice_required") {
    return [];
  }
  return Array.isArray(component.candidates) ? component.candidates : [];
}

/** One row of a chooser, ready to draw. */
export type UndxChoiceRow = {
  /** The number shown beside the row, assigned by the server. */
  position: number;
  label: string;
  /** Secondary line, empty when the row carries nothing worth adding. */
  detail: string;
  /** The message that answers the question by picking this row. */
  reply: string;
};

/**
 * The chooser's rows as the screen should draw them.
 *
 * A named function rather than a `.map` inside the JSX, for the reason the header of
 * `ChatScreen` already records about `isConfirmation`: a decision spelled out inline in
 * a two-thousand-line render is a decision nothing tests, and the last one of those
 * left agent confirmations unapprovable for a release.
 *
 * Three things here are load-bearing. `position` is the server's `choice_index` and is
 * never recomputed from the array index unless the server omitted it — the reply is
 * resolved against the list the *server* remembers, so a locally renumbered row would
 * resolve a different alert than the one under the person's finger. `reply` is that
 * same number as text, because tapping a row and typing its number must mean the same
 * thing; the server reads a lone number as the position it published. And a row with no
 * usable label still gets one, because a chooser is unanswerable if a row is blank.
 */
export function choiceRowsOf(component: UndxResponseComponent): UndxChoiceRow[] {
  return choicesOf(component).map((choice, index) => {
    const position = Number(choice.choice_index) > 0 ? Number(choice.choice_index) : index + 1;
    const named = [choice.display_name, choice.label, choice.title, choice.name, choice.symbol]
      .map((value) => (value === undefined || value === null ? "" : String(value)))
      .find((value) => value.length > 0);
    const detail = [choice.condition, choice.threshold ?? choice.threshold_value, choice.status]
      .map((value) => (value === undefined || value === null ? "" : String(value)))
      .filter((value) => value.length > 0)
      .join(" · ");
    return {
      position,
      label: named || `Option ${position}`,
      detail,
      reply: String(position),
    };
  });
}

/**
 * Collapse either server dialect into the single shape the UI renders.
 *
 * The agent sends `title`/`message`; the legacy path sends `action_name`/`value`.
 * Both are read here, agent field first, so a card gains detail when the richer
 * payload is present and still renders when it is not.
 */
export function toActionCard(component: UndxResponseComponent): UndxActionCard {
  const kind = kindOf(component);
  // An undo is offered only on a receipt the server verified, and only when it sent
  // arguments to perform it with. The `kind` check is the client's own guard: the
  // server already withholds undo on anything else, and a card that arrived claiming
  // otherwise is a card this client should not act on.
  const undoable =
    kind === "receipt" &&
    Boolean(component.undo_capability_id) &&
    Object.keys(component.undo_arguments || {}).length > 0;
  // Only a receipt may claim a change was verified, and the `kind` guard belongs on
  // both signals. It used to sit on `verification_state` alone, so the *weaker* of the
  // two was checked against the card type and the stronger one — a bare `verified:
  // true` — was taken at face value on any card at all. A question card carrying it,
  // which is what a server borrowing a receipt's payload produces and what this batch
  // found the server doing, drew a verification checkmark against a change that had
  // not been attempted.
  const verified =
    kind === "receipt" &&
    (component.verified === true || component.verification_state === "verified");
  const kicker =
    kind === "result"
      ? `${(component.content_type || "content").toUpperCase()} MATCH`
      : kind === "receipt" && !verified
        ? "RESULT"
        : KICKERS[kind];
  return {
    kind,
    kicker,
    title:
      kind === "result"
        ? component.title || component.preview_text || "PulseSOC result"
        : component.action_name || component.title || "UNDX operation",
    target: component.target || "",
    resourceLabel: component.resource_label || "",
    before: readable(component.current_value),
    after: readable(
      component.proposed_value !== undefined ? component.proposed_value : component.value,
    ),
    risk: component.risk_summary || component.message || "",
    verified,
    confirmationToken: isActionable(component) ? component.confirmation_token || "" : "",
    expiresAt: component.expires_at || "",
    deepLink: component.deep_link || "",
    idempotentReplay: component.idempotent_replay === true,
    // Both or neither. A capability id with no arguments is a button that would send
    // an incomplete call, so the pair is dropped together rather than half-kept.
    undoCapabilityId: undoable ? component.undo_capability_id || "" : "",
    undoArguments: undoable ? { ...component.undo_arguments } : {},
  };
}

/**
 * The sentence under the title.
 *
 * A confirmation must name the transition, not just the destination: "off" alone
 * does not tell the user whether pressing Confirm changes anything. When the server
 * could not read the current value the arrow is dropped rather than filled with a
 * guess.
 *
 * The subject is the label before the identifier, for the same reason. "2: active →
 * paused" names a transition and no resource; the row id is not shown anywhere else
 * in this app, so it identifies the alert to the server and to nobody else. The
 * identifier stays as the fallback because a card with a bare id still says more
 * than a card that says "PulseSOC".
 */
export function describeTransition(card: UndxActionCard): string {
  const subject = card.resourceLabel || card.target || "PulseSOC";
  if (card.kind === "result") {
    return card.risk || card.title;
  }
  if (card.before && card.after) {
    return `${subject}: ${card.before} → ${card.after}`;
  }
  if (card.after) {
    return `${subject}: ${card.after}`;
  }
  return subject;
}

export default toActionCard;
