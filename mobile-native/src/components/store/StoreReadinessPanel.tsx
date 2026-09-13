/**
 * "Ready to Sell" — the single-product half of §32, and the answer to §31.
 *
 * The seller opens a draft and needs three things from one card: what is left to
 * do, a way to go and do each one, and a Publish button that is honest about
 * whether it will work. All three come from the server's verdict; none of them
 * are derived here.
 *
 * Four decisions worth the words:
 *
 * 1. **Publish is gated by `bulk_eligibility.publish`, not by `publishable`.**
 *    That looks like the wrong field on a screen with no bulk anything, and it is
 *    the point of §21. `publishable` says the *listing* is finished. It says
 *    nothing about the state it would be published *from* — a live, perfect
 *    product is `publishable: true`, and publishing it again knocks it off the
 *    storefront and back into the review queue. `block_reason` is the one place
 *    that owns that gate, and the bulk bar already asks it. Asking it here too is
 *    one authority answering twice; re-deriving "well, is it already live?" from
 *    `status` would be a second implementation of a rule that already exists.
 *
 * 2. **A missing verdict disables Publish rather than enabling it.** An absent
 *    `readiness` means this build was not told, and "not told" is not "fine".
 *    The card says so in words instead of showing an empty to-do list over a
 *    green button, which is the reading that lets a merchant publish a product
 *    no buyer can check out.
 *
 * 3. **`notes` are listed, separately, under their own heading.** They do not
 *    stop the publish and the card must not imply they do — but a listing that
 *    publishes and cannot be bought has to say so somewhere, and an empty list
 *    above a green button is exactly the silence `notes` was added to break.
 *
 * 4. **No code→English table lives here.** Every string on a fix row is
 *    `fix.label`, written by `listing_readiness.FIXES`. This card is the fourth
 *    surface to render these codes, and the fourth private translation table is
 *    how the store row and the bulk sheet start disagreeing about the same
 *    listing. `section` is routed on, never displayed.
 */

import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import type { ListingBulkBlock, ListingFix, ListingReadiness } from "../../api/marketplace";

export type StoreReadinessPanelProps = {
  /** The server's verdict. Absent means "not told" — see decision 2. */
  readiness?: ListingReadiness;
  /**
   * `bulk_eligibility.publish` from the same payload: `null` when a publish
   * would go through, otherwise the server's reason it would not.
   */
  publishBlock?: ListingBulkBlock | null;
  /** Called with the tapped fix so the editor can open the right section. */
  onFix: (fix: ListingFix) => void;
  onPreview: () => void;
  onPublish: () => void;
  publishing: boolean;
};

export function StoreReadinessPanel({
  readiness,
  publishBlock,
  onFix,
  onPreview,
  onPublish,
  publishing
}: StoreReadinessPanelProps) {
  const blockedReason = publishBlock?.reason || null;
  const canPublish = Boolean(readiness?.publishable) && !blockedReason && !publishing;
  // Server prose in every branch. The one string invented here covers the case
  // where the server said nothing at all, which it has no prose for.
  const ctaLabel = publishing
    ? "Publishing…"
    : blockedReason
      ? blockedReason
      : !readiness
        ? "Not checked yet"
        : readiness.publishable
          ? "Publish"
          : readiness.summary;

  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={styles.title}>Ready to sell</Text>
        <Text
          style={[styles.summary, readiness?.publishable ? styles.summaryOk : styles.summaryWork]}
        >
          {readiness ? readiness.summary : "Not checked yet"}
        </Text>
      </View>

      {!readiness ? (
        <Text style={styles.unknown}>
          We haven&apos;t heard back about this listing yet, so we can&apos;t tell you what&apos;s
          left. Save an edit to check again.
        </Text>
      ) : null}

      {readiness?.fixes.length ? (
        <View style={styles.list}>
          {readiness.fixes.map((fix) => (
            <FixRow key={`fix-${fix.code}`} fix={fix} kind="blocker" onPress={() => onFix(fix)} />
          ))}
        </View>
      ) : null}

      {readiness?.notes.length ? (
        <View style={styles.list}>
          <Text style={styles.notesHead}>
            {readiness.checkout_ready
              ? "Worth a look — won't stop you publishing"
              : "Won't stop you publishing, but nobody can buy it yet"}
          </Text>
          {readiness.notes.map((note) => (
            <FixRow key={`note-${note.code}`} fix={note} kind="note" onPress={() => onFix(note)} />
          ))}
        </View>
      ) : null}

      <View style={styles.actions}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Preview as buyer"
          style={styles.secondary}
          onPress={onPreview}
        >
          <Text style={styles.secondaryText}>Preview as buyer</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={ctaLabel}
          accessibilityState={{ disabled: !canPublish }}
          style={[styles.primary, canPublish ? null : styles.primaryOff]}
          disabled={!canPublish}
          onPress={onPublish}
        >
          {publishing ? (
            <ActivityIndicator size="small" color={storeLight.cta.text} />
          ) : (
            <Text
              style={[styles.primaryText, canPublish ? null : styles.primaryTextOff]}
              numberOfLines={1}
            >
              {ctaLabel}
            </Text>
          )}
        </Pressable>
      </View>
    </View>
  );
}

function FixRow({
  fix,
  kind,
  onPress
}: {
  fix: ListingFix;
  kind: "blocker" | "note";
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      // The label carries the consequence as well as the task, because a screen
      // reader user gets no colour to tell a blocker from a note.
      accessibilityLabel={
        kind === "blocker" ? `${fix.label}. Required before publishing.` : `${fix.label}. Optional.`
      }
      accessibilityHint="Opens the part of the listing that fixes this"
      style={styles.row}
      onPress={onPress}
    >
      <View style={[styles.dot, kind === "note" ? styles.dotNote : null]} />
      <Text style={styles.rowLabel}>{fix.label}</Text>
      <Text style={styles.chevron}>›</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    padding: storeLight.space.card,
    gap: storeLight.space.gutter
  },
  head: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  title: { fontSize: 15, fontWeight: "800", color: storeLight.text.primary },
  summary: { fontSize: 13, fontWeight: "700" },
  summaryOk: { color: storeLight.status.success },
  summaryWork: { color: storeLight.status.warning },
  unknown: { fontSize: 13, lineHeight: 18, color: storeLight.text.muted },
  list: { gap: 6 },
  notesHead: {
    fontSize: 12,
    fontWeight: "700",
    color: storeLight.text.muted,
    textTransform: "none"
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 10,
    borderRadius: storeLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    backgroundColor: storeLight.bg.card
  },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: storeLight.status.error },
  dotNote: { backgroundColor: storeLight.status.warning },
  rowLabel: { flex: 1, fontSize: 14, fontWeight: "600", color: storeLight.text.primary },
  chevron: { fontSize: 18, color: storeLight.text.muted },
  actions: { flexDirection: "row", alignItems: "center", gap: storeLight.space.gutter },
  secondary: {
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 14,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center"
  },
  secondaryText: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  primary: {
    flex: 1,
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 16,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.to,
    alignItems: "center",
    justifyContent: "center"
  },
  primaryOff: { backgroundColor: storeLight.bg.skeleton },
  primaryText: { fontSize: 14, fontWeight: "800", color: storeLight.cta.text },
  primaryTextOff: { color: storeLight.text.muted }
});
