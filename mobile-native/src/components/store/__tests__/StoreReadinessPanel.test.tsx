/**
 * The honesty gate for the Ready-to-sell card.
 *
 * This file exists because of one defect, and the defect was invisible from
 * either side on its own. A rejected listing is `publishable: false` — the
 * rejection *is* the blocker — and its `bulk_eligibility.publish` reason reads
 * "1 thing left". Both of the card's gates therefore said no, and the one task
 * the card listed was "Resolve policy review": the rejection the seller had
 * just answered. There was no way out. The seller who read the reviewer's
 * reason, fixed the product and came back found a dead button under a label
 * counting their own rejection.
 *
 * So the assertions here are mostly about a *combination* rather than a field.
 * Checking that `resubmittable: true` enables the button proves very little on
 * its own — the interesting case is `resubmittable: true` arriving *alongside*
 * the two hostile signals, because a panel that reads them in the wrong order
 * still passes the easy version of the test. Every resubmit case below carries
 * a `publishBlock` and a `publishable: false`, which is what the server
 * actually sends for this row.
 *
 * The other half is the four branches that must not have moved. A fix that
 * enables a button in a new state is one edit away from enabling it in all of
 * them, and the states it must stay disabled in — nothing said yet, something
 * still missing, already live — are the ones where an enabled Publish puts an
 * unfinished product in front of buyers.
 *
 * Disabled-ness is read from `accessibilityState` rather than inferred from a
 * press that did nothing. `fireEvent.press` goes through Pressability, whose
 * responder gate can lag the prop by an effect flush, so "the handler was not
 * called" is a weaker claim than it looks — it is also what a press against a
 * perfectly live button looks like when the gate has not settled. Presses are
 * used for the positive direction, where a silent no-op would fail the test
 * rather than pass it.
 */
import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

import { StoreReadinessPanel } from "../index";
import type { ListingBulkBlock, ListingFix, ListingReadiness } from "../../../api/marketplace";

/** What the server sends for a listing awaiting nothing. */
function verdict(over: Partial<ListingReadiness> = {}): ListingReadiness {
  return {
    publishable: true,
    resubmittable: false,
    checkout_ready: true,
    blockers: [],
    warnings: [],
    summary: "Ready to publish",
    fixes: [],
    notes: [],
    ...over
  };
}

function fix(code: string, label: string, section = "basics"): ListingFix {
  return { code, label, section };
}

/**
 * The exact payload the pipeline produces for a rejected listing whose seller
 * has fixed it: not publishable, one blocker naming the rejection, a bulk
 * reason counting that blocker, and `resubmittable` as the only field that
 * says otherwise. Hand-simplifying any of these three is how this test would
 * stop covering the bug it was written for.
 */
const REJECTED_AND_FIXED: ListingReadiness = verdict({
  publishable: false,
  resubmittable: true,
  blockers: ["RESTRICTED_PRODUCT"],
  summary: "1 thing left",
  fixes: [fix("RESTRICTED_PRODUCT", "Resolve policy review", "policies")]
});

const REJECTED_BLOCK: ListingBulkBlock = {
  code: "NOT_READY",
  reason: "1 thing left",
  blockers: ["RESTRICTED_PRODUCT"]
};

function renderPanel(
  props: Partial<React.ComponentProps<typeof StoreReadinessPanel>> = {}
) {
  const onFix = jest.fn();
  const onPreview = jest.fn();
  const onPublish = jest.fn();
  const tree = render(
    <StoreReadinessPanel
      readiness={verdict()}
      publishBlock={null}
      onFix={onFix}
      onPreview={onPreview}
      onPublish={onPublish}
      publishing={false}
      {...props}
    />
  );
  return { tree, onFix, onPreview, onPublish };
}

/** The primary action, found the way a screen reader finds it. */
function cta(tree: ReturnType<typeof render>) {
  const buttons = tree.getAllByRole("button");
  const preview = buttons.find(
    (node) => node.props.accessibilityLabel === "Preview as buyer"
  );
  expect(preview).toBeTruthy();
  const primary = buttons.filter((node) => node !== preview).pop();
  expect(primary).toBeTruthy();
  return primary!;
}

function ctaLabel(tree: ReturnType<typeof render>) {
  return String(cta(tree).props.accessibilityLabel);
}

/**
 * What the button announces about itself. Pressable does not forward `disabled`
 * to the host node — it consumes it and publishes the result here — so this is
 * both the assertive-technology truth and the closest readable proxy for the
 * prop the panel computed.
 */
function isEnabled(tree: ReturnType<typeof render>) {
  return !cta(tree).props.accessibilityState?.disabled;
}

/** Every string the card renders, flattened. */
function allText(tree: ReturnType<typeof render>): string {
  return tree.root
    .findAll((node: { props: Record<string, unknown> }) => typeof node.props.children === "string")
    .map((node: { props: Record<string, unknown> }) => String(node.props.children))
    .join(" | ");
}

describe("a rejection the seller has answered", () => {
  it("offers a live Resubmit button even though publish is blocked twice over", () => {
    const { tree } = renderPanel({
      readiness: REJECTED_AND_FIXED,
      publishBlock: REJECTED_BLOCK
    });
    expect(isEnabled(tree)).toBe(true);
    expect(ctaLabel(tree)).toBe("Resubmit for review");
  });

  it("does the same when the server sends no bulk verdict at all", () => {
    const { tree } = renderPanel({ readiness: REJECTED_AND_FIXED, publishBlock: null });
    expect(isEnabled(tree)).toBe(true);
    expect(ctaLabel(tree)).toBe("Resubmit for review");
  });

  it("sends the tap on, so the button is not decoration", () => {
    const { tree, onPublish } = renderPanel({
      readiness: REJECTED_AND_FIXED,
      publishBlock: REJECTED_BLOCK
    });
    fireEvent.press(cta(tree));
    expect(onPublish).toHaveBeenCalledTimes(1);
  });

  /**
   * The summary sits directly above the button and has to agree with it. The
   * server's own words here are "1 thing left", which beside an enabled
   * Resubmit reads as a warning not to press it.
   */
  it("replaces the server's blocker count with the action the seller can take", () => {
    const { tree } = renderPanel({
      readiness: REJECTED_AND_FIXED,
      publishBlock: REJECTED_BLOCK
    });
    const text = allText(tree);
    expect(text).toContain("Fixed? Send it back for review");
    expect(text).not.toContain("1 thing left");
  });

  /**
   * "Resolve policy review" points at a section that is empty for this seller,
   * and the task it names is the rejection itself. A row that opens nothing and
   * asks for something already done is exactly what §31 forbids.
   */
  it("lists no fix rows, because the only blocker left is the rejection", () => {
    const { tree } = renderPanel({
      readiness: REJECTED_AND_FIXED,
      publishBlock: REJECTED_BLOCK
    });
    expect(allText(tree)).not.toContain("Resolve policy review");
    // Preview plus the CTA, and nothing else pressable.
    expect(tree.getAllByRole("button")).toHaveLength(2);
  });

  /**
   * Suppression is scoped to the resubmit branch and to nothing else. Were it
   * keyed on the blocker code instead, a listing that is genuinely stuck behind
   * a policy refusal would show a green summary over an empty list — no reason
   * given, no way to act.
   */
  it("still lists that same blocker when the listing is not resubmittable", () => {
    const { tree } = renderPanel({
      readiness: verdict({
        publishable: false,
        resubmittable: false,
        blockers: ["RESTRICTED_PRODUCT"],
        summary: "1 thing left",
        fixes: [fix("RESTRICTED_PRODUCT", "Resolve policy review", "policies")]
      }),
      publishBlock: REJECTED_BLOCK
    });
    expect(allText(tree)).toContain("Resolve policy review");
    expect(isEnabled(tree)).toBe(false);
  });

  /**
   * Notes are not blockers and resubmitting does not answer them, so they keep
   * their own heading here as everywhere else. A seller resubmitting a listing
   * nobody can buy still needs to be told that.
   */
  it("keeps the notes it was given, which the resubmission does not resolve", () => {
    const { tree } = renderPanel({
      readiness: {
        ...REJECTED_AND_FIXED,
        checkout_ready: false,
        warnings: ["NO_STOCK"],
        notes: [fix("NO_STOCK", "Add stock so buyers can check out", "inventory")]
      },
      publishBlock: REJECTED_BLOCK
    });
    const text = allText(tree);
    expect(text).toContain("Add stock so buyers can check out");
    expect(text).toContain("nobody can buy it yet");
  });

  it("says it is sending, not publishing, while the request is in flight", () => {
    const { tree } = renderPanel({
      readiness: REJECTED_AND_FIXED,
      publishBlock: REJECTED_BLOCK,
      publishing: true
    });
    expect(ctaLabel(tree)).toBe("Sending…");
    expect(isEnabled(tree)).toBe(false);
  });
});

describe("the branches that must not have moved", () => {
  it("publishes a finished listing", () => {
    const { tree, onPublish } = renderPanel();
    expect(isEnabled(tree)).toBe(true);
    expect(ctaLabel(tree)).toBe("Publish");
    fireEvent.press(cta(tree));
    expect(onPublish).toHaveBeenCalledTimes(1);
  });

  /**
   * The §21 case: finished, and still must not be republished, because doing so
   * takes it off the storefront and back into the queue. Only the server knows
   * that, and only through this field.
   */
  it("refuses a listing the server says is already live, however finished it looks", () => {
    const { tree } = renderPanel({
      readiness: verdict({ publishable: true }),
      publishBlock: { code: "ALREADY_PUBLISHED", reason: "Already published" }
    });
    expect(isEnabled(tree)).toBe(false);
    expect(ctaLabel(tree)).toBe("Already published");
  });

  it("refuses an unfinished listing and names what is left, in the server's words", () => {
    const { tree, onFix } = renderPanel({
      readiness: verdict({
        publishable: false,
        blockers: ["NO_PRICE", "NO_IMAGE"],
        summary: "2 things left",
        fixes: [fix("NO_PRICE", "Set a price", "pricing"), fix("NO_IMAGE", "Add a photo", "media")]
      })
    });
    expect(isEnabled(tree)).toBe(false);
    expect(ctaLabel(tree)).toBe("2 things left");
    const text = allText(tree);
    expect(text).toContain("Set a price");
    expect(text).toContain("Add a photo");

    fireEvent.press(tree.getByLabelText("Set a price. Required before publishing."));
    expect(onFix).toHaveBeenCalledWith(
      expect.objectContaining({ code: "NO_PRICE", section: "pricing" })
    );
  });

  /**
   * Not told is not fine. An absent verdict used to be indistinguishable from a
   * clean one: an empty to-do list over a live button.
   */
  it("refuses to publish when it was never told anything", () => {
    const { tree } = renderPanel({ readiness: undefined });
    expect(isEnabled(tree)).toBe(false);
    expect(ctaLabel(tree)).toBe("Not checked yet");
    expect(allText(tree)).toContain("we can't tell you what's");
  });

  it("leaves preview working in every state, since looking is always safe", () => {
    const states: Partial<React.ComponentProps<typeof StoreReadinessPanel>>[] = [
      {},
      { readiness: undefined },
      { readiness: REJECTED_AND_FIXED, publishBlock: REJECTED_BLOCK },
      { readiness: verdict({ publishable: false, summary: "2 things left" }) },
      { publishing: true }
    ];
    for (const props of states) {
      const { tree, onPreview } = renderPanel(props);
      const preview = tree.getByLabelText("Preview as buyer");
      expect(preview.props.accessibilityState?.disabled).toBeFalsy();
      fireEvent.press(preview);
      expect(onPreview).toHaveBeenCalledTimes(1);
      tree.unmount();
    }
  });
});
