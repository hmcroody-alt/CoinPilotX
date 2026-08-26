/**
 * Portfolio — holdings, their live value, and an honest total.
 *
 * ## The one thing this screen exists to get right
 *
 * The server used to value an unpriced holding at zero and then report its
 * profit as `value - cost`, i.e. a fabricated total loss on an asset that had
 * only failed to be quoted. That is fixed, and the cheapest way to undo the fix
 * is here: one `Number(value || 0)` in a render turns a `null` back into
 * `$0.00`. The web portfolio page did exactly that, printing `$0.00` in the
 * value column of a row whose price column already read "Price unavailable".
 *
 * So every number on this screen comes through `formatPrice`/`formatPercent`,
 * which render `null` as `--`, and the ones that carry weight get a sentence
 * instead of a dash. Nothing here computes a value; it only displays what the
 * server was willing to state.
 *
 * ## Why the total has a caveat attached rather than a footnote
 *
 * `total_value` sums only the holdings that could be priced. That is the right
 * total — the alternative is a total that silently counts a missing asset as
 * zero — but it is a sum over a subset, and a number whose scope is unstated is
 * read as covering everything. `totalsCoverEverything` answers that, and when
 * it says no the caveat sits inside the same block as the total, not below the
 * fold, because a member scrolls past a warning and does not scroll past a
 * headline figure.
 *
 * ## Premium, and what it does not buy
 *
 * `premium.crypto.portfolio` removes the free ceiling of three holdings. That
 * is the whole of it: free and Premium accounts get the same valuation, the
 * same prices and the same rows. So the upgrade prompt appears only where the
 * ceiling actually bites — on the add form — and never as a blur over data the
 * member already owns. The ceiling is enforced server-side in `_limit_check`;
 * this screen reads the refusal it sends back rather than counting rows and
 * guessing, so a member who is over the ceiling from before it was enforced
 * still sees and keeps everything.
 *
 * On iOS the upgrade prompt leads to the Premium screen and names no price and
 * no purchase path of its own — that screen owns the App Store relationship.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  Portfolio,
  PortfolioHolding,
  addHolding,
  deleteHolding,
  getPortfolio,
  isPremiumRequired,
  rankableHoldings,
  totalsCoverEverything
} from "../api/portfolio";
import { UNKNOWN_VALUE, formatPercent, formatPrice, formatSignedPrice } from "../api/watchlists";
import { Panel } from "../components/Panel";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props =
  | NativeStackScreenProps<RootStackParamList, "Portfolio">
  | NativeStackScreenProps<RootStackParamList, "CryptoPortfolio">;

/** Green up, red down, and neutral when we do not know — never green by default. */
function moveColor(value: number | null): string {
  if (value === null) return colors.muted;
  if (value > 0) return colors.accent;
  if (value < 0) return colors.danger;
  return colors.muted;
}

/** A number the user typed, or `null` when they typed nothing usable.
 *
 *  Deliberately not `Number(text) || 0`: an empty basis field means "I don't
 *  know what I paid", and zero would tell the server the asset was acquired for
 *  nothing, which comes back as an infinite gain. */
function typedNumber(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function PortfolioScreen({ navigation }: Props) {
  const { t } = useTranslation();
  /**
   * The translator, reachable from `refresh` without being a dependency of it.
   *
   * `refresh` is the initial-load effect's only dependency, so anything
   * `refresh` closes over decides how often the portfolio is fetched. `t` is
   * memoized by the current provider and so happens to be stable — but that is
   * a property of a file this screen does not own, and if it ever stopped
   * holding, the effect would re-run on every render and refetch forever while
   * looking perfectly correct in review. The ref keeps the latest translator
   * available and keeps the loader's identity out of it.
   */
  const translate = useRef(t);
  translate.current = t;
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  /** Set only by a server refusal, never by counting rows on the client. */
  const [ceilingReached, setCeilingReached] = useState(false);

  const [symbol, setSymbol] = useState("");
  const [amount, setAmount] = useState("");
  const [basis, setBasis] = useState("");

  const refresh = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    if (mode === "refresh") setRefreshing(true);
    try {
      setPortfolio(await getPortfolio());
      setError("");
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : translate.current("premium:crypto.portfolio.loadError")
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    refresh("initial").catch(() => undefined);
  }, [refresh]);

  async function onAdd() {
    const trimmedSymbol = symbol.trim().toUpperCase();
    const typedAmount = typedNumber(amount);
    if (!trimmedSymbol) {
      setError(t("premium:crypto.portfolio.form.symbolRequired"));
      return;
    }
    if (typedAmount === null || typedAmount <= 0) {
      setError(t("premium:crypto.portfolio.form.amountRequired"));
      return;
    }
    setBusy("add");
    setError("");
    setNotice("");
    try {
      const result = await addHolding({
        symbol: trimmedSymbol,
        amount: typedAmount,
        averageBuyPrice: typedNumber(basis)
      });
      setNotice(result.message || t("premium:crypto.portfolio.form.added"));
      setCeilingReached(false);
      setSymbol("");
      setAmount("");
      setBasis("");
      await refresh("refresh");
    } catch (addError) {
      // The ceiling and a bad symbol are different failures and get different
      // offers. Only the first one may show an upgrade.
      if (isPremiumRequired(addError)) setCeilingReached(true);
      setError(addError instanceof Error ? addError.message : t("premium:crypto.portfolio.form.addFailed"));
    } finally {
      setBusy("");
    }
  }

  function confirmDelete(holding: PortfolioHolding) {
    Alert.alert(
      t("premium:crypto.portfolio.remove.title"),
      t("premium:crypto.portfolio.remove.body", { symbol: holding.symbol }),
      [
        { text: t("common:actions.cancel"), style: "cancel" },
        {
          text: t("common:actions.remove"),
          style: "destructive",
          onPress: () => {
            setBusy(`delete-${holding.id}`);
            deleteHolding(holding.id)
              .then((result) => {
                setNotice(result.message || t("premium:crypto.portfolio.remove.done"));
                return refresh("refresh");
              })
              .catch((deleteError) => {
                setError(
                  deleteError instanceof Error
                    ? deleteError.message
                    : t("premium:crypto.portfolio.remove.failed")
                );
              })
              .finally(() => setBusy(""));
          }
        }
      ]
    );
  }

  function renderHolding(holding: PortfolioHolding) {
    const tint = moveColor(holding.pnlPercent);
    return (
      <View key={holding.id || holding.symbol} style={styles.holdingRow}>
        <View style={styles.holdingMain}>
          <View style={styles.holdingIdentity}>
            <Text style={styles.holdingSymbol}>{holding.symbol}</Text>
            <Text style={styles.holdingName} numberOfLines={1}>
              {holding.coinName}
            </Text>
            <Text style={styles.holdingMeta}>
              {t("premium:crypto.portfolio.row.units", { amount: holding.amount.toLocaleString() })}
              {/* An absent basis is stated, not filled in with zero. A holding
                  carried over from the original CoinPilotX portfolio has an
                  amount and no buy price, and "bought at $0.00" is a claim. */}
              {holding.averageBuyPrice === null
                ? ` · ${t("premium:crypto.portfolio.row.noBasis")}`
                : ` · ${t("premium:crypto.portfolio.row.avg", {
                    price: formatPrice(holding.averageBuyPrice)
                  })}`}
            </Text>
          </View>
          <View style={styles.holdingNumbers}>
            {/* The value column. `formatPrice(null)` is "--" and never "$0.00",
                which is the single line this whole feature turns on. */}
            <Text style={styles.holdingValue}>{formatPrice(holding.value)}</Text>
            <Text style={[styles.holdingChange, { color: tint }]}>{formatPercent(holding.pnlPercent)}</Text>
          </View>
        </View>

        <View style={styles.holdingFooter}>
          <Text style={styles.holdingMeta}>
            {holding.priced
              ? t("premium:crypto.portfolio.row.price", { price: formatPrice(holding.price) })
              : t("premium:crypto.portfolio.row.priceUnavailable")}
            {holding.pnlValue === null ? "" : ` · ${formatSignedPrice(holding.pnlValue)}`}
          </Text>
          {holding.id ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t("premium:crypto.portfolio.remove.a11y", { symbol: holding.symbol })}
              disabled={busy === `delete-${holding.id}`}
              onPress={() => confirmDelete(holding)}
            >
              <Text style={styles.removeAction}>{t("common:actions.remove")}</Text>
            </Pressable>
          ) : null}
        </View>
      </View>
    );
  }

  if (loading && !portfolio) {
    return (
      <View style={styles.loadingRoot}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  const holdings = portfolio?.holdings || [];
  const complete = portfolio ? totalsCoverEverything(portfolio) : true;
  const ranked = portfolio ? rankableHoldings(portfolio) : [];
  const valuation = portfolio?.valuation;
  // No holding has a buy price, so there is no basis to measure a gain against.
  const basisKnown = (valuation?.basisKnown ?? 0) > 0;
  const aggregatePnl = basisKnown ? portfolio?.pnlValue ?? null : null;
  const aggregateMove = basisKnown ? portfolio?.pnlPercent ?? null : null;

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => refresh("refresh")} tintColor={colors.accent} />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("premium:crypto.portfolio.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:crypto.portfolio.subtitle")}</Text>
      </View>

      <Panel>
        <Text style={styles.muted}>
          {complete
            ? t("premium:crypto.portfolio.total.label")
            : t("premium:crypto.portfolio.total.partialLabel", {
                priced: valuation?.priced ?? 0,
                holdings: valuation?.holdings ?? 0
              })}
        </Text>
        <Text style={styles.total}>{formatPrice(portfolio?.totalValue ?? null)}</Text>
        {/* The aggregate P/L sums the holdings whose basis is known. When that
            set is empty the sum is 0, but 0 here would mean "you are exactly
            break-even" -- a claim nothing in the data supports. Undecidable is
            not break-even, so the number becomes "--" like every other unknown
            on this screen, and the note below names the holdings responsible. */}
        <Text style={[styles.totalMove, { color: moveColor(aggregateMove) }]}>
          {formatSignedPrice(aggregatePnl)} · {formatPercent(aggregateMove)}
        </Text>
        {/* The caveat lives with the number it qualifies. Below the fold it would
            be a footnote on a figure the member has already read and believed. */}
        {complete ? null : (
          <Text style={styles.warning}>
            {portfolio?.warning || t("premium:crypto.portfolio.total.incomplete")}
          </Text>
        )}
        {valuation && valuation.basisKnown < valuation.holdings ? (
          <Text style={styles.muted}>
            {t("premium:crypto.portfolio.total.basisNote", {
              count: valuation.holdings - valuation.basisKnown
            })}
          </Text>
        ) : null}
      </Panel>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      <Panel>
        <Text style={styles.sectionTitle}>{t("premium:crypto.portfolio.form.title")}</Text>
        <TextInput
          style={styles.input}
          value={symbol}
          onChangeText={setSymbol}
          autoCapitalize="characters"
          autoCorrect={false}
          placeholder={t("premium:crypto.portfolio.form.symbolPlaceholder")}
          placeholderTextColor={colors.muted}
          accessibilityLabel={t("premium:crypto.portfolio.form.symbolLabel")}
        />
        <TextInput
          style={styles.input}
          value={amount}
          onChangeText={setAmount}
          keyboardType="decimal-pad"
          placeholder={t("premium:crypto.portfolio.form.amountPlaceholder")}
          placeholderTextColor={colors.muted}
          accessibilityLabel={t("premium:crypto.portfolio.form.amountLabel")}
        />
        <TextInput
          style={styles.input}
          value={basis}
          onChangeText={setBasis}
          keyboardType="decimal-pad"
          placeholder={t("premium:crypto.portfolio.form.basisPlaceholder")}
          placeholderTextColor={colors.muted}
          accessibilityLabel={t("premium:crypto.portfolio.form.basisLabel")}
        />
        {/* Says what leaving it blank means, so nobody types 0 to fill the box. */}
        <Text style={styles.muted}>{t("premium:crypto.portfolio.form.basisHint")}</Text>
        <Pressable
          accessibilityRole="button"
          style={styles.primaryButton}
          disabled={busy === "add"}
          onPress={onAdd}
        >
          <Text style={styles.primaryButtonLabel}>
            {busy === "add" ? t("premium:crypto.portfolio.form.adding") : t("premium:crypto.portfolio.form.submit")}
          </Text>
        </Pressable>

        {/* Shown only after the server has actually refused, and only about the
            ceiling. It offers more room, not a different portfolio. */}
        {ceilingReached ? (
          <Pressable
            accessibilityRole="button"
            style={styles.upgradeBlock}
            onPress={() => navigation.navigate("Premium")}
          >
            <Text style={styles.upgradeTitle}>{t("premium:crypto.portfolio.upgrade.title")}</Text>
            <Text style={styles.muted}>{t("premium:crypto.portfolio.upgrade.body")}</Text>
            <Text style={styles.upgradeAction}>{t("premium:crypto.portfolio.upgrade.action")}</Text>
          </Pressable>
        ) : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("premium:crypto.portfolio.holdings.title")}</Text>
        {holdings.length ? (
          holdings.map(renderHolding)
        ) : (
          <Text style={styles.muted}>{t("premium:crypto.portfolio.holdings.empty")}</Text>
        )}
      </Panel>

      {/* Only holdings with a real P/L can be ranked. An unpriced one has no
          profit at all, and letting it in would rank an absence — which is how
          an asset that merely failed to quote used to win "biggest loser". */}
      {ranked.length >= 2 ? (
        <Panel>
          <Text style={styles.sectionTitle}>{t("premium:crypto.portfolio.movers.title")}</Text>
          {[...ranked]
            .sort((a, b) => (b.pnlPercent ?? 0) - (a.pnlPercent ?? 0))
            .slice(0, 3)
            .map((holding) => (
              <View key={`mover-${holding.id}`} style={styles.moverRow}>
                <Text style={styles.moverSymbol}>{holding.symbol}</Text>
                <Text style={[styles.moverChange, { color: moveColor(holding.pnlPercent) }]}>
                  {formatPercent(holding.pnlPercent)}
                </Text>
              </View>
            ))}
          {valuation && ranked.length < valuation.holdings ? (
            <Text style={styles.muted}>
              {t("premium:crypto.portfolio.movers.excluded", {
                count: valuation.holdings - ranked.length
              })}
            </Text>
          ) : null}
        </Panel>
      ) : null}

      <Text style={styles.footnote}>{t("premium:crypto.portfolio.footnote")}</Text>
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  content: { gap: 12, padding: 16, paddingBottom: 48 },
  error: { color: colors.danger, fontSize: 13, lineHeight: 19 },
  footnote: { color: colors.muted, fontSize: 11, lineHeight: 17 },
  header: { gap: 4 },
  holdingChange: { fontSize: 13, fontWeight: "800" },
  holdingFooter: { alignItems: "center", flexDirection: "row", gap: 10, justifyContent: "space-between" },
  holdingIdentity: { flex: 1, gap: 2 },
  holdingMain: { alignItems: "flex-start", flexDirection: "row", gap: 10 },
  holdingMeta: { color: colors.muted, flex: 1, fontSize: 11, lineHeight: 16 },
  holdingName: { color: colors.muted, fontSize: 12 },
  holdingNumbers: { alignItems: "flex-end", minWidth: 96 },
  holdingRow: {
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: 8,
    paddingTop: 10
  },
  holdingSymbol: { color: colors.text, fontSize: 15, fontWeight: "900" },
  holdingValue: { color: colors.text, fontSize: 15, fontWeight: "800" },
  input: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 44,
    paddingHorizontal: 12
  },
  loadingRoot: { alignItems: "center", flex: 1, justifyContent: "center" },
  moverChange: { fontSize: 14, fontWeight: "800" },
  moverRow: {
    alignItems: "center",
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingTop: 8
  },
  moverSymbol: { color: colors.text, fontSize: 14, fontWeight: "900" },
  muted: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  notice: { color: colors.accent, fontSize: 13, lineHeight: 19 },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingVertical: 12
  },
  primaryButtonLabel: { color: colors.background, fontSize: 14, fontWeight: "900" },
  removeAction: { color: colors.danger, fontSize: 12, fontWeight: "700" },
  root: { backgroundColor: "transparent", flex: 1 },
  sectionTitle: { color: colors.text, fontSize: 15, fontWeight: "900" },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  title: { color: colors.text, fontSize: 24, fontWeight: "900" },
  total: { color: colors.text, fontSize: 34, fontWeight: "900" },
  totalMove: { fontSize: 14, fontWeight: "800" },
  upgradeAction: { color: colors.accentStrong, fontSize: 13, fontWeight: "800" },
  upgradeBlock: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: 1,
    gap: 4,
    padding: 12
  },
  upgradeTitle: { color: colors.text, fontSize: 14, fontWeight: "900" },
  warning: { color: colors.warning, fontSize: 13, lineHeight: 19 }
}));

/** Re-exported so a test can assert the dash without importing the market API. */
export { UNKNOWN_VALUE };
