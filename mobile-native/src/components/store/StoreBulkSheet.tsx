/**
 * Review, apply, and read back — §19, §31, §33, §34.
 *
 * One sheet with several faces, because they are successive moments of one
 * decision and splitting them across screens loses the thread:
 *
 *   rule        (price only) "Cost + 20%" — and a button that only previews
 *   previewing  the server working out what that comes to
 *   confirm     "Reprice 14. 4 will stay as they are, because…"
 *   running     the same sheet, disabled, with a spinner
 *   result      "14 products repriced" / "4 products need attention", listed
 *
 * The part worth defending is the **result face**, which exists because §19 says
 * fourteen-of-eighteen is the normal outcome of a bulk action and not an error.
 * The two ways to get it wrong are a toast saying "Published!" (the seller never
 * learns about the four, finds out from a buyer) and a toast saying "Failed"
 * (the seller taps again on a batch where fourteen already went through). Both
 * are what a sheet-less implementation produces, because neither a success nor
 * an error toast has anywhere to put a list.
 *
 * The confirm face reads a {@link StoreBulkReview}, which is the one shape both
 * kinds of preview narrow to: a locally-computed partition for publish and hide,
 * whose verdicts the list payload already carries, and the server's dry run for
 * a reprice, which is the only thing that knows what a rule comes to. The sheet
 * cannot tell them apart and must not — every reason and every price in here is
 * a string the server wrote.
 */

import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { BULK_VERB, type StoreBulkAction } from "../../marketplace/storeSelection";
import type { StoreBulkOutcome, StoreBulkReview } from "../../marketplace/storeBulkRun";
import { outcomeReason, resultDetail, reviewLabel } from "../../marketplace/storeBulkRun";
import type { StorePricingRuleDraft } from "../../marketplace/storeBulkPricing";
import type { StoreCategoryDraft } from "../../marketplace/storeBulkCategory";
import { StoreBulkPriceRule } from "./StoreBulkPriceRule";
import { StoreBulkCategoryPicker } from "./StoreBulkCategoryPicker";

const ACTION_TITLE: Record<StoreBulkAction, string> = {
  publish: "Publish listings",
  hide: "Hide listings",
  price: "Review new prices",
  category: "Review new categories"
};

/** What the `previewing` face says while the server works. Per action, because
 *  "Working out the new prices" over a category move is a wrong sentence, and a
 *  generic "Working…" throws away the one bit of reassurance the face exists for. */
const PREVIEW_WAIT: Record<StoreBulkAction, string> = {
  publish: "Checking your products…",
  hide: "Checking your products…",
  price: "Working out the new prices…",
  category: "Checking where these will be filed…"
};

export type StoreBulkSheetPhase =
  /** Price only: choosing the rule. Nothing has been sent. */
  | "rule"
  /** The dry run is in flight. */
  | "previewing"
  | "confirm"
  | "running"
  | "result"
  | "error";

export type StoreBulkSheetProps = {
  visible: boolean;
  phase: StoreBulkSheetPhase;
  action: StoreBulkAction;
  /**
   * What the batch would do. `null` only while the rule is still being typed —
   * a confirm face with no review is not a state this sheet can render, and the
   * screen must not produce one.
   */
  review: StoreBulkReview | null;
  outcome: StoreBulkOutcome | null;
  /** Set when the request itself could not be completed. */
  errorMessage: string | null;
  /** Price only: the rule being edited, and how many rows it covers. */
  priceDraft: StorePricingRuleDraft;
  onChangePriceDraft: (draft: StorePricingRuleDraft) => void;
  /** Category only: the filing being chosen. */
  categoryDraft: StoreCategoryDraft;
  onChangeCategoryDraft: (draft: StoreCategoryDraft) => void;
  /** Category only: aisles this store already uses, for the suggestion chips. */
  categorySuggestions: string[];
  /** Category only: the selected rows' current filing, for the "already there" count. */
  selectedFilings: { category: string; subcategory: string }[];
  selectedCount: number;
  onPreview: () => void;
  onConfirm: () => void;
  onRetry: () => void;
  /**
   * Price only: back to the rule field, keeping what the seller typed.
   *
   * Absent for publish and hide, and that asymmetry is the point rather than an
   * oversight — there is no rule behind those, so "change" would have nothing to
   * change. For a reprice its absence was a dead end in two places. On the
   * confirm face, a seller who reads "$49.00 → $58.80" and wants 25% instead had
   * to Cancel out of the sheet and start the selection over. On the error face it
   * was worse: a rule the server refuses answers "Try again" with the identical
   * refusal, forever, and Cancel was the only way out of a sheet that had just
   * told the seller what was wrong with the number they typed.
   */
  onChangeRule?: () => void;
  onClose: () => void;
  /** Titles for result rows the server did not name. */
  titleFor: (listingId: number) => string;
};

function Line({
  title,
  detail,
  warning,
  tone
}: {
  title: string;
  detail?: string | null;
  warning?: string | null;
  tone: "ok" | "warn";
}) {
  return (
    <View style={styles.line}>
      <View style={[styles.dot, tone === "ok" ? styles.dotOk : styles.dotWarn]} />
      <View style={styles.lineBody}>
        <Text style={styles.lineTitle} numberOfLines={1}>
          {title}
        </Text>
        {/* The same slot carries two different kinds of sentence, told apart by
            `tone`: on a row that is changing it is the read-back — "$24.00 →
            $28.80", "Home & Kitchen" — and on a row that is not, it is the
            reason it was left alone. Gray for the first, the warning colour for
            the second. One colour for both (this used to be
            `select.disabledReason` either way) painted every successful
            reprice's new figure in the same rust as the refusals, so a batch
            where nothing went wrong still read as a screen of problems. */}
        {detail ? (
          <Text style={tone === "warn" ? styles.lineDetailWarn : styles.lineDetail}>{detail}</Text>
        ) : null}
        {/* Its own line, in the warning colour, on a row that is otherwise fine.
            "Goes back to review" is a consequence of the change, not a reason
            the change will not happen, and putting it in `detail` would dress a
            successful reprice as a problem. */}
        {warning ? <Text style={styles.lineWarning}>{warning}</Text> : null}
      </View>
    </View>
  );
}

/**
 * The way out, and — for a reprice — the way back.
 *
 * One component for both faces that have a Cancel, so the back affordance cannot
 * end up on one of them and not the other. Cancel keeps its own label and its own
 * full-width shape when there is nothing beside it, which is what the publish and
 * hide journeys already expect.
 */
/** "Change rule" / "Change category" — the back affordance names what it goes
 *  back to, because "Change" alone on a sheet with two editable things is a
 *  button whose destination the seller has to guess. */
const BACK_LABEL: Partial<Record<StoreBulkAction, string>> = {
  price: "Change rule",
  category: "Change category"
};

function Footer({
  action,
  onChangeRule,
  onClose,
  busy
}: {
  action: StoreBulkAction;
  onChangeRule?: () => void;
  onClose: () => void;
  busy: boolean;
}) {
  if (!onChangeRule) {
    return (
      <Pressable
        style={styles.secondary}
        onPress={onClose}
        disabled={busy}
        accessibilityRole="button"
        accessibilityLabel="Cancel"
        accessibilityState={{ disabled: busy }}
      >
        <Text style={styles.secondaryText}>Cancel</Text>
      </Pressable>
    );
  }
  const back = BACK_LABEL[action] ?? "Change";
  return (
    <View style={styles.footerRow}>
      <Pressable
        style={styles.secondaryHalf}
        onPress={onChangeRule}
        disabled={busy}
        accessibilityRole="button"
        accessibilityLabel={back}
        accessibilityHint={
          action === "category"
            ? "Goes back to the category, keeping what you chose"
            : "Goes back to the pricing rule, keeping the number you typed"
        }
        accessibilityState={{ disabled: busy }}
      >
        <Text style={styles.secondaryText}>{back}</Text>
      </Pressable>
      <Pressable
        style={styles.secondaryHalf}
        onPress={onClose}
        disabled={busy}
        accessibilityRole="button"
        accessibilityLabel="Cancel"
        accessibilityState={{ disabled: busy }}
      >
        <Text style={styles.secondaryTextQuiet}>Cancel</Text>
      </Pressable>
    </View>
  );
}

export function StoreBulkSheet({
  visible,
  phase,
  action,
  review,
  outcome,
  errorMessage,
  priceDraft,
  onChangePriceDraft,
  categoryDraft,
  onChangeCategoryDraft,
  categorySuggestions,
  selectedFilings,
  selectedCount,
  onPreview,
  onConfirm,
  onRetry,
  onChangeRule,
  onClose,
  titleFor
}: StoreBulkSheetProps) {
  const changing = review?.changing ?? [];
  const staying = review?.staying ?? [];
  const verb = BULK_VERB[action].plain;
  const showingResult = phase === "result";
  const busy = phase === "running";
  const previewing = phase === "previewing";

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      // A batch in flight must not be dismissable by the OS back gesture: the
      // request is already sent, and a seller who backs out mid-write would be
      // left looking at a list that has not been read back yet. A dry run in
      // flight is not in that category — it changes nothing, so backing out of
      // one is free.
      onRequestClose={busy ? () => undefined : onClose}
    >
      <View style={styles.scrim}>
        <View style={styles.sheet} accessibilityViewIsModal>
          <View style={styles.grabber} />

          {showingResult && outcome ? (
            <>
              <Text style={styles.title} accessibilityRole="header">
                {outcome.headline}
              </Text>
              {outcome.attention ? (
                <Text style={styles.subtitleWarn}>{outcome.attention}</Text>
              ) : (
                <Text style={styles.subtitle}>Everything you selected went through.</Text>
              )}

              <ScrollView style={styles.list} contentContainerStyle={styles.listBody}>
                {outcome.succeeded.map((entry) => (
                  <Line
                    key={`ok-${entry.listing_id}`}
                    title={entry.title || titleFor(entry.listing_id)}
                    // The server names the value it stored, so a repriced row
                    // reads back the figure and a moved row reads back the aisle
                    // rather than the word "done". §31's last step is READ BACK,
                    // and a value the seller can check against the list behind
                    // the sheet is the only version of that which proves
                    // anything.
                    detail={resultDetail(action, entry)}
                    // A change that pulled a live listing out of the store says
                    // so here too, not only in the preview: the seller who
                    // scrolled past the warning still has to learn about it.
                    warning={entry.returns_to_review ? "Back in review" : null}
                    tone="ok"
                  />
                ))}
                {[...outcome.blocked, ...outcome.failed].map((entry) => (
                  <Line
                    key={`no-${entry.listing_id}`}
                    title={entry.title || titleFor(entry.listing_id)}
                    detail={outcomeReason(entry)}
                    tone="warn"
                  />
                ))}
              </ScrollView>

              <Pressable
                style={styles.primary}
                onPress={onClose}
                accessibilityRole="button"
                accessibilityLabel="Done"
              >
                <Text style={styles.primaryText}>Done</Text>
              </Pressable>
            </>
          ) : phase === "error" ? (
            <>
              <Text style={styles.title} accessibilityRole="header">
                Couldn't finish
              </Text>
              <Text style={styles.subtitle}>
                {errorMessage || "The request could not be completed."}
              </Text>
              {/* This used to read "Nothing was changed", which is the one thing
                  this face cannot honestly claim: a request that timed out may
                  already have published fourteen products. What *is* true either
                  way is that the retry carries the same idempotency key — if the
                  batch landed the server replays its answer, and if it never
                  arrived the retry does the work. So the sheet answers the
                  seller's actual next question ("do I dare tap it again?")
                  instead of guessing at the one it cannot know. */}
              <Text style={styles.subtitleQuiet}>
                Trying again won't repeat anything that already went through.
              </Text>
              <Pressable
                style={styles.primary}
                onPress={onRetry}
                accessibilityRole="button"
                accessibilityLabel="Try again"
              >
                <Text style={styles.primaryText}>Try again</Text>
              </Pressable>
              <Footer action={action} onChangeRule={onChangeRule} onClose={onClose} busy={false} />
            </>
          ) : phase === "rule" ? (
            <>
              {/* The `rule` phase is "the payload action is being set up", and
                  which face that is depends on the action. Named `rule` rather
                  than renamed when the second payload action arrived, because the
                  phase is part of the screen's state machine and its meaning did
                  not change — only the number of things it can render. */}
              {action === "category" ? (
                <StoreBulkCategoryPicker
                  draft={categoryDraft}
                  onChange={onChangeCategoryDraft}
                  suggestions={categorySuggestions}
                  selectedFilings={selectedFilings}
                  busy={previewing}
                  onPreview={onPreview}
                />
              ) : (
                <StoreBulkPriceRule
                  draft={priceDraft}
                  onChange={onChangePriceDraft}
                  selectedCount={selectedCount}
                  busy={previewing}
                  onPreview={onPreview}
                />
              )}
              <Pressable
                style={styles.secondary}
                onPress={onClose}
                disabled={previewing}
                accessibilityRole="button"
                accessibilityLabel="Cancel"
                accessibilityState={{ disabled: previewing }}
              >
                <Text style={styles.secondaryText}>Cancel</Text>
              </Pressable>
            </>
          ) : previewing ? (
            <View style={styles.waiting}>
              <ActivityIndicator size="small" color={storeLight.status.success} />
              <Text style={styles.subtitle}>{PREVIEW_WAIT[action]}</Text>
            </View>
          ) : (
            <>
              <Text style={styles.title} accessibilityRole="header">
                {ACTION_TITLE[action]}
              </Text>
              <Text style={styles.subtitle}>
                {changing.length} of {changing.length + staying.length} selected will {verb}.
              </Text>

              <ScrollView style={styles.list} contentContainerStyle={styles.listBody}>
                {/* The changing rows are listed for a reprice and not for a
                    publish, and the difference is not inconsistency: a reprice
                    has something to show per row — the pair of numbers — and a
                    publish has only its own count, which the subtitle and the
                    button already carry. Repeating fourteen titles under
                    "Publishing" would bury the four that are not. */}
                {changing.some((line) => line.detail) ? (
                  <Text style={styles.groupHead}>What changes</Text>
                ) : null}
                {changing
                  .filter((line) => line.detail)
                  .map((line) => (
                    <Line
                      key={`c-${line.id}`}
                      title={line.title}
                      detail={line.detail}
                      warning={line.warning}
                      tone="ok"
                    />
                  ))}
                {staying.length > 0 ? (
                  <Text style={styles.groupHead}>Staying as they are</Text>
                ) : null}
                {staying.map((line) => (
                  <Line key={`b-${line.id}`} title={line.title} detail={line.detail} tone="warn" />
                ))}
              </ScrollView>

              <Pressable
                style={[styles.primary, changing.length === 0 || busy ? styles.primaryOff : null]}
                onPress={onConfirm}
                disabled={changing.length === 0 || busy}
                accessibilityRole="button"
                // Names the sheet as well as the count, which the docked bar's
                // CTA deliberately does not — the two buttons are one tap apart
                // and both say "Publish 14", so a label that did not distinguish
                // them would leave a screen-reader user unable to tell whether
                // they were arming the action or committing it. The blocked
                // count is missing here on purpose: the subtitle above states
                // both numbers in prose, which the bar has no room for.
                accessibilityLabel={
                  changing.length === 0
                    ? `Nothing to ${verb}`
                    : `${ACTION_TITLE[action]}, ${changing.length}`
                }
                accessibilityState={{ disabled: changing.length === 0 || busy }}
              >
                {busy ? (
                  <ActivityIndicator size="small" color={storeLight.cta.text} />
                ) : (
                  <Text style={styles.primaryText}>
                    {review ? reviewLabel(review) : `Nothing to ${verb}`}
                  </Text>
                )}
              </Pressable>
              <Footer action={action} onChangeRule={onChangeRule} onClose={onClose} busy={busy} />
            </>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: { flex: 1, backgroundColor: "rgba(15,17,17,0.45)", justifyContent: "flex-end" },
  sheet: {
    backgroundColor: storeLight.bg.card,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: storeLight.space.card,
    paddingTop: 8,
    paddingBottom: 20,
    maxHeight: "80%"
  },
  grabber: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: storeLight.border.hairline,
    marginBottom: 12
  },
  title: { fontSize: 18, fontWeight: "800", color: storeLight.text.primary },
  subtitle: { fontSize: 13, color: storeLight.text.muted, marginTop: 4 },
  /**
   * "Trying again won't repeat anything that already went through." The one
   * calming line on the failure face, so it is the last text that should be
   * coloured like an alarm — it was in the rust amber, directly under a red
   * error title, which made the reassurance read as a second problem.
   */
  subtitleQuiet: { fontSize: 12, color: storeLight.text.muted, marginTop: 8 },
  subtitleWarn: { fontSize: 13, fontWeight: "700", color: storeLight.status.warning, marginTop: 4 },
  list: { marginTop: 12, maxHeight: 260 },
  listBody: { gap: 10, paddingBottom: 4 },
  groupHead: {
    fontSize: 11,
    fontWeight: "800",
    color: storeLight.text.muted,
    textTransform: "uppercase",
    letterSpacing: 0.4
  },
  line: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  dot: { width: 8, height: 8, borderRadius: 4, marginTop: 5 },
  dotOk: { backgroundColor: storeLight.status.success },
  dotWarn: { backgroundColor: storeLight.status.warning },
  lineBody: { flex: 1 },
  lineTitle: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary },
  lineDetail: { fontSize: 12, color: storeLight.text.muted, marginTop: 1 },
  /**
   * Why this row is staying as it is. `status.warning` rather than
   * `select.disabledReason`, because this line sits on the white card: the rust
   * hue exists to clear AA against the *disabled wash* in the list behind the
   * sheet (see `storeLightContrast.test.ts`), and here it would be a third
   * near-amber beside `lineWarning` and `subtitleWarn` on the same face. Not
   * bold — `lineWarning` is the consequence of a change that did happen, and
   * this is a change that did not, so they must not compete.
   */
  lineDetailWarn: { fontSize: 12, color: storeLight.status.warning, marginTop: 1 },
  lineWarning: { fontSize: 12, fontWeight: "700", color: storeLight.status.warning, marginTop: 1 },
  waiting: { paddingVertical: 36, alignItems: "center", gap: 10 },
  primary: {
    marginTop: 14,
    minHeight: storeLight.size.tapTarget,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.to,
    alignItems: "center",
    justifyContent: "center"
  },
  primaryOff: { backgroundColor: storeLight.bg.skeleton },
  primaryText: { fontSize: 15, fontWeight: "800", color: storeLight.cta.text },
  secondary: {
    marginTop: 8,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  footerRow: { flexDirection: "row", marginTop: 8 },
  secondaryHalf: {
    flex: 1,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  secondaryText: { fontSize: 14, fontWeight: "600", color: storeLight.text.link },
  // Beside "Change rule", Cancel is the colder of the two: one goes back a step
  // and the other throws the selection away, and giving them the same weight
  // would make the destructive one look like the obvious next tap.
  secondaryTextQuiet: { fontSize: 14, fontWeight: "600", color: storeLight.text.muted }
});
