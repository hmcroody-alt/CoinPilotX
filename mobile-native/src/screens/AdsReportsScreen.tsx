/**
 * Ads Reports — `BusinessOsAdvertising { mode: "reports" }`.
 *
 * Reads `GET /api/pulse/ads/reports` through `api/adsReports`. The two honesty
 * rules enforced by the data layer are honoured on screen: a row whose
 * `results_available` is false renders "not measured" (never 0), and estimated
 * reach is labelled as estimated. The CSV export is client-built by
 * `buildAdReportCsv` and leaves the app via the system share sheet, with a
 * clipboard fallback — there is no expo-sharing dependency.
 *
 * A row navigates to the campaign detail screen exactly when the server put a
 * `campaign_id` on it; date/placement/objective rows are not tappable because
 * they name no single campaign.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Animated,
  Pressable,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { copyToClipboard } from "../native/clipboard";

import {
  AD_REPORT_BREAKDOWNS,
  AD_REPORT_RANGE_PRESETS,
  AdReport,
  AdReportBreakdown,
  AdReportRangePreset,
  AdReportRow,
  adReportRange,
  buildAdReportCsv,
  getAdReport,
  isValidReportDate
} from "../api/adsReports";
import { formatCents, listAdAccounts, loadCachedAdAccounts } from "../api/businessOs";
import { primaryAdAccount } from "../api/adsDashboard";
import {
  AdsEmpty,
  AdsScreenShell,
  AdsSectionError,
  AdsSkeletonBlock,
  SpendBarChart,
  adsSubStyles as s
} from "../components/ads";
import { useFormatters, useTranslation } from "../i18n";
import { adsLight } from "../theme/adsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance } from "../theme/storeMotion";

type Props = {
  route?: { params?: { title?: string; accountId?: number } };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

const NS = "commerce:adsReports";

const BREAKDOWN_KEYS: Record<AdReportBreakdown, string> = {
  campaign: "breakdownCampaign",
  adset: "breakdownAdset",
  ad: "breakdownAd",
  placement: "breakdownPlacement",
  date: "breakdownDate",
  objective: "breakdownObjective"
};

const PRESET_KEYS: Record<AdReportRangePreset, string> = {
  "7d": "range7d",
  "14d": "range14d",
  "30d": "range30d",
  custom: "rangeCustom"
};

export function AdsReportsScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(4, reducedMotion);

  const [accountId, setAccountId] = useState<number>(Number(route?.params?.accountId || 0));
  const [preset, setPreset] = useState<AdReportRangePreset>("7d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [breakdown, setBreakdown] = useState<AdReportBreakdown>("campaign");
  const [report, setReport] = useState<AdReport | null>(null);
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [errorText, setErrorText] = useState("");
  const [exportNote, setExportNote] = useState("");

  const load = useCallback(async () => {
    setStatus("loading");
    setErrorText("");
    try {
      let id = accountId;
      if (!id) {
        const res = await listAdAccounts().catch(async () => ({
          accounts: await loadCachedAdAccounts().catch(() => [])
        }));
        id = primaryAdAccount(res.accounts || [])?.id || 0;
        if (id) setAccountId(id);
      }
      if (!id) {
        setStatus("error");
        setErrorText(t(`${NS}.noAccount`));
        return;
      }
      const range = adReportRange(preset);
      const start = preset === "custom" ? customStart : range.start;
      const end = preset === "custom" ? customEnd : range.end;
      if (preset === "custom" && (!isValidReportDate(customStart) || !isValidReportDate(customEnd))) {
        // Not an error state — the form simply hasn't got two dates yet.
        setStatus("ok");
        setReport(null);
        return;
      }
      const data = await getAdReport(id, {
        start: start || undefined,
        end: end || undefined,
        breakdown
      });
      setReport(data);
      setStatus("ok");
    } catch (error) {
      setStatus("error");
      setErrorText(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
    }
  }, [accountId, breakdown, customEnd, customStart, preset, t]);

  useEffect(() => {
    load().catch(() => setStatus("error"));
  }, [load]);

  const money = useCallback((cents: number) => formatCents(cents), []);

  const openCampaign = useCallback(
    (campaignId: number) => {
      navigation?.navigate("BusinessOsAdvertising", { mode: "detail", campaignId });
    },
    [navigation]
  );

  const exportCsv = useCallback(
    async (viaShare: boolean) => {
      if (!report) return;
      const csv = buildAdReportCsv(report);
      try {
        if (viaShare) {
          await Share.share({ message: csv });
        } else {
          await copyToClipboard(csv, "text");
          setExportNote(t(`${NS}.csvCopied`));
        }
      } catch {
        setExportNote(t(`${NS}.csvFailed`));
      }
    },
    [report, t]
  );

  const isDate = breakdown === "date";
  const chart = useMemo(() => {
    if (!isDate || !report || report.rows.length === 0) return null;
    return {
      values: report.rows.map((row) => row.spend_cents),
      labels: report.rows.map((row) => row.label.slice(5)),
      total: report.rows.reduce((sum, row) => sum + row.spend_cents, 0)
    };
  }, [isDate, report]);

  const customIncomplete =
    preset === "custom" && (!isValidReportDate(customStart) || !isValidReportDate(customEnd));

  return (
    <AdsScreenShell
      title={route?.params?.title || t(`${NS}.title`)}
      backLabel={t(`${NS}.back`)}
      onBack={() => navigation?.goBack?.()}
    >
      {/* Range + breakdown pickers */}
      <Animated.View style={[s.stack, entrance.styleFor(0)]}>
        <Text style={s.inputLabel}>{t(`${NS}.rangeLabel`)}</Text>
        <View style={s.chipRow}>
          {AD_REPORT_RANGE_PRESETS.map((option) => (
            <Pressable
              key={option}
              onPress={() => setPreset(option)}
              style={[s.chip, preset === option ? s.chipActive : null]}
              accessibilityRole="button"
              accessibilityState={{ selected: preset === option }}
              accessibilityLabel={t(`${NS}.${PRESET_KEYS[option]}`)}
            >
              <Text style={[s.chipText, preset === option ? s.chipTextActive : null]}>
                {t(`${NS}.${PRESET_KEYS[option]}`)}
              </Text>
            </Pressable>
          ))}
        </View>
        {preset === "custom" ? (
          <View style={styles.dateRow}>
            <View style={styles.dateField}>
              <Text style={s.inputLabel}>{t(`${NS}.startLabel`)}</Text>
              <TextInput
                style={s.input}
                value={customStart}
                onChangeText={setCustomStart}
                placeholder="2026-01-01"
                autoCapitalize="none"
                autoCorrect={false}
                accessibilityLabel={t(`${NS}.startLabel`)}
              />
            </View>
            <View style={styles.dateField}>
              <Text style={s.inputLabel}>{t(`${NS}.endLabel`)}</Text>
              <TextInput
                style={s.input}
                value={customEnd}
                onChangeText={setCustomEnd}
                placeholder="2026-01-31"
                autoCapitalize="none"
                autoCorrect={false}
                accessibilityLabel={t(`${NS}.endLabel`)}
              />
            </View>
          </View>
        ) : null}
        {customIncomplete ? <Text style={s.meta}>{t(`${NS}.invalidDates`)}</Text> : null}
        <Text style={s.inputLabel}>{t(`${NS}.breakdownLabel`)}</Text>
        <View style={s.chipRow}>
          {AD_REPORT_BREAKDOWNS.map((option) => (
            <Pressable
              key={option}
              onPress={() => setBreakdown(option)}
              style={[s.chip, breakdown === option ? s.chipActive : null]}
              accessibilityRole="button"
              accessibilityState={{ selected: breakdown === option }}
              accessibilityLabel={t(`${NS}.${BREAKDOWN_KEYS[option]}`)}
            >
              <Text style={[s.chipText, breakdown === option ? s.chipTextActive : null]}>
                {t(`${NS}.${BREAKDOWN_KEYS[option]}`)}
              </Text>
            </Pressable>
          ))}
        </View>
      </Animated.View>

      {/* Body */}
      {status === "loading" ? (
        <Animated.View style={[s.stack, entrance.styleFor(1)]}>
          <View style={s.card}>
            <AdsSkeletonBlock width="55%" height={16} reducedMotion={reducedMotion} />
            <AdsSkeletonBlock width="85%" height={12} reducedMotion={reducedMotion} />
            <AdsSkeletonBlock width="70%" height={12} reducedMotion={reducedMotion} />
          </View>
        </Animated.View>
      ) : status === "error" ? (
        <Animated.View style={[s.stack, entrance.styleFor(1)]}>
          <AdsSectionError
            message={errorText || t(`${NS}.loadError`)}
            onRetry={() => {
              load().catch(() => setStatus("error"));
            }}
            reducedMotion={reducedMotion}
            retryLabel={t(`${NS}.retry`)}
          />
        </Animated.View>
      ) : !report || customIncomplete ? null : report.rows.length === 0 ? (
        <Animated.View style={[s.stack, entrance.styleFor(1)]}>
          <AdsEmpty
            title={t(`${NS}.emptyTitle`)}
            body={t(`${NS}.emptyBody`)}
            reducedMotion={reducedMotion}
          />
        </Animated.View>
      ) : (
        <>
          {chart ? (
            <Animated.View style={[s.stack, entrance.styleFor(1)]}>
              <SpendBarChart
                title={t(`${NS}.chartTitle`)}
                values={chart.values}
                dayLabels={chart.labels}
                summary={t(`${NS}.chartSummary`, {
                  total: money(chart.total),
                  days: chart.values.length
                })}
                mock={false}
                empty={chart.values.every((value) => value === 0)}
                totalLabel={money(chart.total)}
                reducedMotion={reducedMotion}
                seriesKey={`${report.start}:${report.end}`}
              />
            </Animated.View>
          ) : null}

          <Animated.View style={[s.stack, entrance.styleFor(2)]}>
            {report.rows.map((row) => (
              <ReportRowCard
                key={row.key}
                row={row}
                money={money}
                percent={(value: number) => formatters.percent(value)}
                count={(value: number) => formatters.count(value)}
                onOpenCampaign={row.campaign_id ? () => openCampaign(row.campaign_id as number) : undefined}
                openHint={t(`${NS}.openCampaignHint`)}
                t={t}
              />
            ))}
            {report.totals.key ? (
              <View style={[s.card, styles.totalsCard]}>
                <Text style={s.cardTitle}>{t(`${NS}.totalsLabel`)}</Text>
                <RowMetrics
                  row={report.totals}
                  money={money}
                  percent={(value: number) => formatters.percent(value)}
                  count={(value: number) => formatters.count(value)}
                  t={t}
                />
              </View>
            ) : null}
          </Animated.View>

          <Animated.View style={[s.stack, entrance.styleFor(3)]}>
            {report.metadata.attribution.model ? (
              <Text style={s.meta}>
                {t(`${NS}.attribution`, {
                  model: report.metadata.attribution.model,
                  days: report.metadata.attribution.window_days
                })}
              </Text>
            ) : null}
            {report.metadata.notes.map((note) => (
              <Text key={note} style={s.meta}>
                {note}
              </Text>
            ))}
            <Pressable
              onPress={() => exportCsv(true)}
              style={s.primaryBtn}
              accessibilityRole="button"
              accessibilityLabel={t(`${NS}.exportShare`)}
            >
              <Text style={s.primaryBtnText}>{t(`${NS}.exportShare`)}</Text>
            </Pressable>
            <Pressable
              onPress={() => exportCsv(false)}
              style={s.secondaryBtn}
              accessibilityRole="button"
              accessibilityLabel={t(`${NS}.exportCopy`)}
            >
              <Text style={s.secondaryBtnText}>{t(`${NS}.exportCopy`)}</Text>
            </Pressable>
            {exportNote ? (
              <Text style={s.notice} accessibilityLiveRegion="polite">
                {exportNote}
              </Text>
            ) : null}
          </Animated.View>
        </>
      )}
    </AdsScreenShell>
  );
}

function ReportRowCard({
  row,
  money,
  percent,
  count,
  onOpenCampaign,
  openHint,
  t
}: {
  row: AdReportRow;
  money: (cents: number) => string;
  percent: (value: number) => string;
  count: (value: number) => string;
  onOpenCampaign?: () => void;
  openHint: string;
  t: (key: string, vars?: Record<string, unknown>) => string;
}) {
  const body = (
    <View style={s.card}>
      <View style={s.headRow}>
        <Text style={s.cardTitle} numberOfLines={2}>
          {row.label}
        </Text>
      </View>
      <RowMetrics row={row} money={money} percent={percent} count={count} t={t} />
      {onOpenCampaign ? <Text style={s.inlineLink}>{openHint}</Text> : null}
    </View>
  );
  if (!onOpenCampaign) return body;
  return (
    <Pressable onPress={onOpenCampaign} accessibilityRole="button" accessibilityLabel={row.label}>
      {body}
    </Pressable>
  );
}

/**
 * The metric grid for one row. "Not measured" and null-CPC handling are the
 * point of this component: an unavailable result never prints as a zero.
 */
function RowMetrics({
  row,
  money,
  percent,
  count,
  t
}: {
  row: AdReportRow;
  money: (cents: number) => string;
  percent: (value: number) => string;
  count: (value: number) => string;
  t: (key: string, vars?: Record<string, unknown>) => string;
}) {
  const reachLabel = row.reach_estimated
    ? t(`${NS}.reachEstimated`, { reach: count(row.reach) })
    : count(row.reach);
  const cells: Array<{ key: string; label: string; value: string }> = [
    { key: "spend", label: t(`${NS}.colSpend`), value: money(row.spend_cents) },
    { key: "impressions", label: t(`${NS}.colImpressions`), value: count(row.impressions) },
    { key: "reach", label: t(`${NS}.colReach`), value: reachLabel },
    { key: "clicks", label: t(`${NS}.colClicks`), value: count(row.clicks) },
    { key: "ctr", label: t(`${NS}.colCtr`), value: percent(row.ctr) },
    {
      key: "cpc",
      label: t(`${NS}.colCpc`),
      value: row.cpc_cents == null ? "—" : money(row.cpc_cents)
    },
    {
      key: "results",
      label: row.results_metric || t(`${NS}.colResults`),
      value: row.results_available ? count(row.results) : t(`${NS}.notMeasured`)
    },
    {
      key: "cpr",
      label: t(`${NS}.colCostPerResult`),
      value: row.cost_per_result_cents == null ? "—" : money(row.cost_per_result_cents)
    },
    { key: "purchases", label: t(`${NS}.colPurchases`), value: count(row.purchases) },
    { key: "revenue", label: t(`${NS}.colRevenue`), value: money(row.revenue_cents) },
    { key: "roas", label: t(`${NS}.colRoas`), value: row.roas == null ? "—" : `${row.roas}×` }
  ];
  return (
    <View style={styles.metricGrid}>
      {cells.map((cell) => (
        <View key={cell.key} style={styles.metricCell}>
          <Text style={s.reasonLabel} numberOfLines={1}>
            {cell.label}
          </Text>
          <Text style={styles.metricValue} numberOfLines={1}>
            {cell.value}
          </Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  dateRow: { flexDirection: "row", gap: 10 },
  dateField: { flex: 1, gap: 4 },
  metricGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  metricCell: { minWidth: "30%", flexGrow: 1, gap: 1 },
  metricValue: { fontSize: 14, fontWeight: "800", color: adsLight.text.primary },
  totalsCard: { backgroundColor: adsLight.bg.strip }
});

export default AdsReportsScreen;
