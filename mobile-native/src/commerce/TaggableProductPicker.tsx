/**
 * The composer's product picker: which of my own listings is this post about?
 *
 * ## Why this sheet exists at all
 *
 * `tagging.attach` server-side asks one question — does this creator own this
 * listing — and answers `ok: true` when they do. `eligibility.gate` asks a
 * different question, separately and at *serve* time: can this listing be shown
 * to anybody. Nothing joins those two moments. So a tag can be accepted, the row
 * written, the post published clean, and the product never appear to a single
 * viewer, with no log line and nothing the creator could have noticed.
 *
 * This sheet is the only place in the product where those two questions are
 * asked together, which is why it renders *every* listing the creator owns —
 * including the ones that will not serve — instead of filtering to the servable
 * ones. A product missing from your own picker teaches you nothing. A product
 * labelled "Needs a cover photo" tells you what to fix.
 *
 * ## Blocked listings stay selectable, on purpose
 *
 * The obvious design disables a row that cannot serve. That would be wrong here,
 * and the reason is in `services/commerce_discovery/pool.py`: the tagged source
 * contributes `AND l.id IN (...)` as a *clause* onto a query whose `WHERE` is
 * `eligibility.candidate_sql()`, and `eligibility.gate()` then runs per row on
 * every serve request. The gate is therefore evaluated fresh each time the post
 * is viewed, never frozen at tag time. A tag on a listing that is blocked today
 * is durable and starts serving the moment the listing is fixed.
 *
 * So the row is selectable and wears a warning. Disabling it would throw away a
 * true statement the creator is entitled to make about their own post, in order
 * to protect them from a condition that is usually temporary and always theirs
 * to clear.
 *
 * ## Loading, empty and error are three states, never two
 *
 * `[]` on a viewer surface means "no products here" and is a safe thing to say
 * when a query fails. `[]` here means "you have no products to tag" — a claim
 * about the creator's own store. Telling a seller with forty listings that they
 * have none is a confident lie, and it renders as a dead end. So the endpoint
 * answers 500 rather than `200 []` when it cannot read the catalogue,
 * `fetchTaggableProducts` throws rather than swallowing it, and this component
 * keeps `error` and `empty` in separate branches with separate copy and a retry
 * on only one of them. Collapsing them is the single failure this whole path was
 * built to prevent.
 *
 * ## Both limits come off the wire
 *
 * `maxPerContent` and `requestLimit` are server constants
 * (`tagging.MAX_TAGGED_PER_CONTENT`, `bot.PULSE_PRODUCT_TAG_REQUEST_LIMIT`). They
 * are read from the response and never written as literals here, because a
 * client-side copy of a server-side limit is a divergence waiting for the limit
 * to change — and the divergence presents as a creator being told they may tag
 * five when the server will store three.
 *
 * Which is also why the first fetch sends no `limit`: we would have to know the
 * limit to ask for it. The server clamps to `TAGGABLE_PAGE_MAX` on its own.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { TaggableProduct, TaggableProductsResult, fetchTaggableProducts } from "../api/taggableProducts";
import { useTranslation } from "../i18n/I18nContext";
import { storeLight } from "../theme/marketplaceLight";

/**
 * Deliberately not a boolean pair. `loading` and `error` as two flags admits a
 * fourth state that means nothing (`loading && error`) and makes "show empty"
 * an inference from the absence of two other things — which is exactly how an
 * error ends up rendering as an empty store.
 */
type LoadState = "loading" | "ready" | "error";

export type TaggableProductPickerProps = {
  visible: boolean;
  /**
   * Selection is lifted to the composer rather than held here.
   *
   * The composer owns the draft; this sheet is opened and closed against it
   * repeatedly and must not be where the choice lives, or dismissing the sheet
   * would silently discard tags the creator had already made. It also means the
   * publish payload reads from one place.
   */
  selectedIds: readonly number[];
  onChange: (ids: number[]) => void;
  onClose: () => void;
};

export function TaggableProductPicker({ visible, selectedIds, onChange, onClose }: TaggableProductPickerProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<LoadState>("loading");
  const [result, setResult] = useState<TaggableProductsResult | null>(null);

  const load = useCallback(async () => {
    setState("loading");
    try {
      // No `limit` argument: see the header. The server clamps.
      const next = await fetchTaggableProducts();
      setResult(next);
      setState("ready");
    } catch {
      // The only `catch` on this path, and it sets an error state rather than an
      // empty one. It must never call `setResult({ products: [], ... })`.
      setResult(null);
      setState("error");
    }
  }, []);

  useEffect(() => {
    if (visible) void load();
  }, [visible, load]);

  const maxPerContent = result?.maxPerContent ?? 0;
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);

  /**
   * At the cap, an unselected row stops being selectable — but a *selected* row
   * never does, or the creator would be unable to undo their fifth choice.
   */
  const atCap = maxPerContent > 0 && selected.size >= maxPerContent;

  const toggle = useCallback(
    (listingId: number) => {
      const next = new Set(selected);
      if (next.has(listingId)) next.delete(listingId);
      else if (!atCap) next.add(listingId);
      else return;
      onChange(Array.from(next));
    },
    [selected, atCap, onChange]
  );

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.scrim} onPress={onClose} testID="taggable-picker-scrim" />
      <View style={styles.sheet} testID="taggable-picker">
        <Text style={styles.title}>{t("commerce:discovery.tagging.title")}</Text>
        <Text style={styles.subtitle}>{t("commerce:discovery.tagging.subtitle")}</Text>

        {state === "ready" && maxPerContent > 0 ? (
          <Text style={styles.cap} testID="taggable-picker-count">
            {selected.size > 0
              ? t("commerce:discovery.tagging.selected", { selected: selected.size, max: maxPerContent })
              : t("commerce:discovery.tagging.cap", { max: maxPerContent })}
          </Text>
        ) : null}

        {state === "loading" ? (
          <Text style={styles.body} testID="taggable-picker-loading">
            {t("commerce:discovery.tagging.loading")}
          </Text>
        ) : null}

        {state === "error" ? (
          <View testID="taggable-picker-error">
            <Text style={styles.errorTitle}>{t("commerce:discovery.tagging.error")}</Text>
            <Text style={styles.body}>{t("commerce:discovery.tagging.errorHint")}</Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => void load()}
              style={styles.retry}
              testID="taggable-picker-retry"
            >
              <Text style={styles.retryText}>{t("commerce:discovery.tagging.retry")}</Text>
            </Pressable>
          </View>
        ) : null}

        {state === "ready" && !result?.products.length ? (
          <View testID="taggable-picker-empty">
            <Text style={styles.emptyTitle}>{t("commerce:discovery.tagging.empty")}</Text>
            <Text style={styles.body}>{t("commerce:discovery.tagging.emptyHint")}</Text>
          </View>
        ) : null}

        {state === "ready" && result?.products.length ? (
          <ScrollView style={styles.list} testID="taggable-picker-list">
            {result.products.map((row) => (
              <TaggableRow
                key={row.listingId}
                row={row}
                checked={selected.has(row.listingId)}
                // A blocked row is selectable; a row past the cap is not.
                selectable={selected.has(row.listingId) || !atCap}
                onToggle={toggle}
              />
            ))}
          </ScrollView>
        ) : null}

        <Pressable accessibilityRole="button" onPress={onClose} style={styles.done} testID="taggable-picker-done">
          <Text style={styles.doneText}>{t("commerce:discovery.tagging.done")}</Text>
        </Pressable>
      </View>
    </Modal>
  );
}

function TaggableRow({
  row,
  checked,
  selectable,
  onToggle
}: {
  row: TaggableProduct;
  checked: boolean;
  selectable: boolean;
  onToggle: (listingId: number) => void;
}) {
  const { t } = useTranslation();

  /**
   * Guarded here as well as by the `disabled` prop. `Pressable` resolves
   * `disabled` through the responder system a flush later than the prop
   * changes, so the prop alone makes "pressing an unselectable row does
   * nothing" true only most of the time — and true for a reason a test cannot
   * distinguish from the event never having been delivered.
   */
  const press = useCallback(() => {
    if (!selectable) return;
    onToggle(row.listingId);
  }, [selectable, onToggle, row.listingId]);

  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityState={{ checked, disabled: !selectable }}
      accessibilityLabel={row.product.title}
      disabled={!selectable}
      onPress={press}
      style={[styles.row, checked && styles.rowChecked, !selectable && styles.rowDisabled]}
      testID={`taggable-picker-row-${row.listingId}`}
    >
      <View style={styles.thumb}>
        {row.product.coverImageUrl ? (
          <Image source={{ uri: row.product.coverImageUrl }} style={styles.thumbImage} resizeMode="cover" />
        ) : null}
      </View>

      <View style={styles.rowBody}>
        <Text style={[styles.rowTitle, !selectable && styles.rowTitleDisabled]} numberOfLines={2}>
          {row.product.title}
        </Text>
        {/* Already formatted by the server; this client never re-derives a price. */}
        {row.product.priceLabel ? <Text style={styles.rowPrice}>{row.product.priceLabel}</Text> : null}

        {row.serves ? null : (
          <View testID={`taggable-picker-blocked-${row.listingId}`}>
            <Text style={styles.blockedBadge}>{t("commerce:discovery.tagging.wontShow")}</Text>
            {/* The reason, then what it costs. Never one without the other: the
                badge alone is a verdict, the reason alone reads like a label. */}
            <Text style={styles.blockedReason}>
              {t(`commerce:discovery.tagging.blocked.${row.blockedReason || "unknown_reason"}`)}
            </Text>
            <Text style={styles.blockedHint}>{t("commerce:discovery.tagging.wontShowHint")}</Text>
          </View>
        )}
      </View>

      {/* Selection is a shape, not only a colour — see `storeLight.select`. */}
      <View style={[styles.tick, checked && styles.tickChecked]}>
        {checked ? <Text style={styles.tickMark}>✓</Text> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  scrim: { backgroundColor: "rgba(11, 11, 12, 0.55)", flex: 1 },
  sheet: {
    backgroundColor: storeLight.bg.card,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    maxHeight: "80%",
    paddingBottom: 24,
    paddingHorizontal: 16,
    paddingTop: 18
  },
  title: { color: storeLight.text.primary, fontSize: 17, fontWeight: "800" },
  subtitle: { color: storeLight.text.muted, fontSize: 13, marginTop: 2 },
  cap: { color: storeLight.text.muted, fontSize: 12, fontWeight: "700", marginTop: 10 },
  body: { color: storeLight.text.muted, fontSize: 13, marginTop: 8 },
  errorTitle: { color: storeLight.status.error, fontSize: 15, fontWeight: "700", marginTop: 14 },
  emptyTitle: { color: storeLight.text.primary, fontSize: 15, fontWeight: "700", marginTop: 14 },
  retry: {
    alignSelf: "flex-start",
    borderColor: storeLight.border.secondaryButton,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 12,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  retryText: { color: storeLight.text.link, fontSize: 14, fontWeight: "700" },
  list: { marginTop: 12 },
  row: {
    alignItems: "center",
    borderColor: storeLight.border.hairline,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: "row",
    marginBottom: 8,
    padding: 10
  },
  rowChecked: { backgroundColor: storeLight.select.selected, borderColor: storeLight.select.selectedBorder },
  rowDisabled: { backgroundColor: storeLight.select.disabled },
  thumb: {
    backgroundColor: storeLight.bg.skeleton,
    borderRadius: 8,
    height: 56,
    overflow: "hidden",
    width: 56
  },
  thumbImage: { height: "100%", width: "100%" },
  rowBody: { flex: 1, paddingHorizontal: 10 },
  rowTitle: { color: storeLight.text.primary, fontSize: 14, fontWeight: "700" },
  rowTitleDisabled: { color: storeLight.select.disabledText },
  rowPrice: { color: storeLight.text.muted, fontSize: 13, marginTop: 2 },
  blockedBadge: { color: storeLight.status.warning, fontSize: 11, fontWeight: "800", marginTop: 6 },
  blockedReason: { color: storeLight.select.disabledReason, fontSize: 12, fontWeight: "700", marginTop: 2 },
  blockedHint: { color: storeLight.text.muted, fontSize: 11, marginTop: 2 },
  tick: {
    alignItems: "center",
    borderColor: storeLight.border.secondaryButton,
    borderRadius: 11,
    borderWidth: 1,
    height: 22,
    justifyContent: "center",
    width: 22
  },
  tickChecked: { backgroundColor: storeLight.select.selectedBorder, borderColor: storeLight.select.selectedBorder },
  tickMark: { color: "#FFFFFF", fontSize: 13, fontWeight: "900" },
  done: {
    alignItems: "center",
    backgroundColor: storeLight.accent.brand,
    borderRadius: 10,
    marginTop: 14,
    paddingVertical: 12
  },
  doneText: { color: "#04231A", fontSize: 15, fontWeight: "800" }
});
