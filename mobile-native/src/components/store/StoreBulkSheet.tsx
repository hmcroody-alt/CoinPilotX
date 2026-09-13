/**
 * Review, apply, and read back — §19, §31, §33.
 *
 * One sheet with three faces, because they are three moments of one decision
 * and splitting them across screens loses the thread:
 *
 *   confirm  "Publish 14 products. 4 will stay as they are, because…"
 *   running  the same sheet, disabled, with a spinner
 *   result   "14 products published" / "4 products need attention", listed
 *
 * The part worth defending is the **result face**, which exists because §19 says
 * fourteen-of-eighteen is the normal outcome of a bulk publish and not an error.
 * The two ways to get it wrong are a toast saying "Published!" (the seller never
 * learns about the four, finds out from a buyer) and a toast saying "Failed"
 * (the seller taps again on a batch where fourteen already went through). Both
 * are what a sheet-less implementation produces, because neither a success nor
 * an error toast has anywhere to put a list.
 *
 * The blocked list is the same prose the rows and the button already carry, from
 * the same server field. Nothing here composes a reason.
 */

import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import type { StoreListingRow } from "../../api/storeDashboard";
import type { StoreBulkAction, StoreBulkPartition } from "../../marketplace/storeSelection";
import type { StoreBulkOutcome } from "../../marketplace/storeBulkRun";
import { outcomeReason } from "../../marketplace/storeBulkRun";

const ACTION_TITLE: Record<StoreBulkAction, string> = {
  publish: "Publish listings",
  hide: "Hide listings"
};

export type StoreBulkSheetPhase = "confirm" | "running" | "result" | "error";

export type StoreBulkSheetProps = {
  visible: boolean;
  phase: StoreBulkSheetPhase;
  action: StoreBulkAction;
  /** The same partition the rows and the CTA read. */
  partitioned: StoreBulkPartition;
  outcome: StoreBulkOutcome | null;
  /** Set when the request itself could not be completed. */
  errorMessage: string | null;
  onConfirm: () => void;
  onRetry: () => void;
  onClose: () => void;
  /** Titles for result rows the server did not name. */
  titleFor: (listingId: number) => string;
};

function Line({ title, detail, tone }: { title: string; detail?: string | null; tone: "ok" | "warn" }) {
  return (
    <View style={styles.line}>
      <View style={[styles.dot, tone === "ok" ? styles.dotOk : styles.dotWarn]} />
      <View style={styles.lineBody}>
        <Text style={styles.lineTitle} numberOfLines={1}>
          {title}
        </Text>
        {detail ? <Text style={styles.lineDetail}>{detail}</Text> : null}
      </View>
    </View>
  );
}

export function StoreBulkSheet({
  visible,
  phase,
  action,
  partitioned,
  outcome,
  errorMessage,
  onConfirm,
  onRetry,
  onClose,
  titleFor
}: StoreBulkSheetProps) {
  const { eligible, blocked } = partitioned;
  const verb = action === "publish" ? "publish" : "hide";
  const showingResult = phase === "result";
  const busy = phase === "running";

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      // A batch in flight must not be dismissable by the OS back gesture: the
      // request is already sent, and a seller who backs out mid-write would be
      // left looking at a list that has not been read back yet.
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
                    detail={action === "publish" ? "Submitted for review" : "Hidden from buyers"}
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
              <Pressable
                style={styles.secondary}
                onPress={onClose}
                accessibilityRole="button"
                accessibilityLabel="Cancel"
              >
                <Text style={styles.secondaryText}>Cancel</Text>
              </Pressable>
            </>
          ) : (
            <>
              <Text style={styles.title} accessibilityRole="header">
                {ACTION_TITLE[action]}
              </Text>
              <Text style={styles.subtitle}>
                {eligible.length} of {eligible.length + blocked.length} selected will {verb}.
              </Text>

              <ScrollView style={styles.list} contentContainerStyle={styles.listBody}>
                {blocked.length > 0 ? (
                  <Text style={styles.groupHead}>Staying as they are</Text>
                ) : null}
                {blocked.map(({ row, reason }) => (
                  <Line key={`b-${row.id}`} title={row.title} detail={reason} tone="warn" />
                ))}
              </ScrollView>

              <Pressable
                style={[styles.primary, eligible.length === 0 || busy ? styles.primaryOff : null]}
                onPress={onConfirm}
                disabled={eligible.length === 0 || busy}
                accessibilityRole="button"
                accessibilityLabel={
                  eligible.length === 0
                    ? `Nothing to ${verb}`
                    : `${ACTION_TITLE[action]}, ${eligible.length}`
                }
                accessibilityState={{ disabled: eligible.length === 0 || busy }}
              >
                {busy ? (
                  <ActivityIndicator size="small" color={storeLight.cta.text} />
                ) : (
                  <Text style={styles.primaryText}>
                    {eligible.length === 0
                      ? `Nothing to ${verb}`
                      : `${action === "publish" ? "Publish" : "Hide"} ${eligible.length}`}
                  </Text>
                )}
              </Pressable>
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
  subtitleQuiet: { fontSize: 12, color: storeLight.select.disabledReason, marginTop: 8 },
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
  lineDetail: { fontSize: 12, color: storeLight.select.disabledReason, marginTop: 1 },
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
  secondaryText: { fontSize: 14, fontWeight: "600", color: storeLight.text.link }
});
