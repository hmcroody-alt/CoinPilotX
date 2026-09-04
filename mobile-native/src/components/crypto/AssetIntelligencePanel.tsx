/**
 * The layered drill-down for one asset.
 *
 * ## The shape of the thing
 *
 * Verdict → Why → Setup → Entry quality → Risk → Timeframes → Deep data →
 * Evidence. Each layer is a closed section the member opens deliberately, and
 * only the verdict is open on arrival. The depth belongs to the reader: somebody
 * who wants the word gets the word, somebody who wants to know why the model
 * said it can walk down to the raw hourly series count and the list of things
 * this analysis cannot see.
 *
 * ## Fetched on expand, not on mount
 *
 * The deep payload is a second request. Mounting the asset screen does not make
 * it — most visits to a coin are to look at the price. The panel shows its
 * collapsed header, and the request happens the first time somebody actually
 * asks. After that it is held in state for the life of the screen; reopening a
 * section does not refetch.
 *
 * ## Nothing here is a recommendation
 *
 * Every setup field is conditional and level-based — a trigger that has not
 * happened, a price that would prove the idea wrong. The sizing block is
 * arithmetic on a risk budget and says so inside its own payload. The
 * disclaimer is rendered from the server's string rather than a local constant,
 * so it cannot drift from what the analysis engine believes it is claiming.
 *
 * ## Absence is drawn as absence
 *
 * A null score renders `--`, never 0. A factor the server could not measure
 * renders its own "unmeasured" sentence rather than being dropped — a risk
 * factor that silently disappears reads as a risk that is not present, and
 * "event risk: unmeasured" and "event risk: none" are opposite claims.
 */

import { ReactNode, useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import {
  AssetIntelligenceDetail,
  Confidence,
  Reason,
  UNKNOWN_READING,
  formatLevel,
  formatPct,
  formatPlainPct,
  formatRatio,
  formatScore,
  getAssetIntelligence,
  humanizeState
} from "../../api/marketIntelligence";
import { colors } from "../../theme/colors";
import { createThemedStyles } from "../../theme/themedStyles";
import { riskColor, toneColor } from "./IntelligenceBadge";

type SectionKey = "why" | "setup" | "entry" | "risk" | "timeframes" | "deep" | "evidence";

/** Direction arrows for the timeframe table. Neutral glyphs, not sentiment icons. */
const DIRECTION_GLYPH: Record<string, string> = { UP: "▲", DOWN: "▼", FLAT: "→" };

function directionColor(direction: string | null): string {
  if (direction === "UP") return colors.accent;
  if (direction === "DOWN") return colors.danger;
  return colors.muted;
}

/**
 * The KNOWN / INFERRED / UNAVAILABLE marker.
 *
 * Only drawn for the two that are not KNOWN. Labelling every measured number
 * "measured" is noise that trains people to ignore the label, and the label is
 * only load-bearing on the readings that are *not* direct measurements.
 */
function ConfidenceTag({ confidence }: { confidence: Confidence }) {
  if (confidence === "KNOWN") return null;
  return (
    <Text style={confidence === "INFERRED" ? styles.tagInferred : styles.tagUnavailable}>
      {confidence === "INFERRED" ? "Inferred" : "Unavailable"}
    </Text>
  );
}

function ReasonList({ reasons }: { reasons: Reason[] }) {
  if (!reasons.length) return <Text style={styles.muted}>No reasons were recorded for this reading.</Text>;
  return (
    <View style={styles.reasonList}>
      {reasons.map((reason) => (
        <View key={reason.code} style={styles.reasonRow}>
          <Text style={styles.bullet}>·</Text>
          <View style={styles.reasonBody}>
            <Text style={reason.confidence === "UNAVAILABLE" ? styles.reasonMuted : styles.reason}>{reason.text}</Text>
            <ConfidenceTag confidence={reason.confidence} />
          </View>
        </View>
      ))}
    </View>
  );
}

function Row({ label, value, tint }: { label: string; value: string; tint?: string }) {
  return (
    <View style={styles.dataRow}>
      <Text style={styles.muted}>{label}</Text>
      <Text style={[styles.dataValue, tint ? { color: tint } : null]}>{value}</Text>
    </View>
  );
}

function Section({
  title,
  subtitle,
  open,
  onToggle,
  children
}: {
  title: string;
  subtitle?: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <View style={styles.section}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={`${title}. ${open ? "Collapse" : "Expand"}`}
        style={styles.sectionHead}
        onPress={onToggle}
      >
        <View style={styles.sectionTitleWrap}>
          <Text style={styles.sectionTitle}>{title}</Text>
          {subtitle ? (
            <Text style={styles.sectionSubtitle} numberOfLines={1}>
              {subtitle}
            </Text>
          ) : null}
        </View>
        <Text style={styles.chevron}>{open ? "−" : "+"}</Text>
      </Pressable>
      {open ? <View style={styles.sectionBody}>{children}</View> : null}
    </View>
  );
}

export function AssetIntelligencePanel({ symbol }: { symbol: string }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<AssetIntelligenceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Record<SectionKey, boolean>>({
    why: false,
    setup: false,
    entry: false,
    risk: false,
    timeframes: false,
    deep: false,
    evidence: false
  });

  const toggle = useCallback((key: SectionKey) => {
    setOpen((current) => ({ ...current, [key]: !current[key] }));
  }, []);

  // A new symbol is a new asset: keeping the previous verdict on screen while
  // the next one loads would attribute one coin's analysis to another.
  useEffect(() => {
    setDetail(null);
    setError("");
    setExpanded(false);
  }, [symbol]);

  useEffect(() => {
    if (!expanded || detail || loading) return;
    let active = true;
    setLoading(true);
    getAssetIntelligence(symbol)
      .then((payload) => {
        if (!active) return;
        setDetail(payload);
        setError(payload ? "" : "Market intelligence is unavailable for this asset.");
      })
      .catch((fetchError) => {
        if (!active) return;
        setError(fetchError instanceof Error ? fetchError.message : "Market intelligence could not load.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [expanded, detail, loading, symbol]);

  if (!expanded) {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Show UNDX market intelligence for ${symbol}`}
        style={({ pressed }) => [styles.teaser, pressed && styles.pressed]}
        onPress={() => setExpanded(true)}
      >
        <Text style={styles.teaserTitle}>UNDX intelligence</Text>
        <Text style={styles.teaserHint}>Tap to analyse ›</Text>
      </Pressable>
    );
  }

  if (loading && !detail) {
    return (
      <View style={styles.panel}>
        <ActivityIndicator color={colors.intelligence} />
      </View>
    );
  }

  if (!detail) {
    return (
      <View style={styles.panel}>
        <Text style={styles.heading}>UNDX intelligence</Text>
        <Text style={styles.warning}>{error || "Market intelligence is unavailable for this asset."}</Text>
      </View>
    );
  }

  const tint = toneColor(detail.action.tone);
  const { structure, setup, riskDetail, timeframes, evidence, sizing, holding, anomalies, volume } = detail;

  return (
    <View style={styles.panel}>
      {/* Layer 1 — the verdict. The only thing open on arrival. */}
      <View style={styles.verdictHead}>
        <Text style={styles.heading}>UNDX intelligence</Text>
        <Pressable accessibilityRole="button" accessibilityLabel="Hide intelligence" onPress={() => setExpanded(false)}>
          <Text style={styles.collapse}>Hide</Text>
        </Pressable>
      </View>

      <Text style={[styles.verdict, { color: tint }]}>{detail.action.label}</Text>
      {/* Said in words, because the same market produces a different verdict for
          a holder and a non-holder and the reader must know which one this is. */}
      <Text style={styles.perspective}>
        {detail.action.perspective === "holder"
          ? "Framed for your existing position."
          : detail.action.perspective === "non_holder"
            ? "Framed for someone not currently holding."
            : "Perspective could not be established."}
      </Text>

      <View style={styles.scoreRow}>
        <ScoreCell
          label="Opportunity"
          score={formatScore(detail.opportunity.score)}
          band={detail.opportunity.band}
          confidence={detail.opportunity.confidence}
        />
        <ScoreCell
          label="Entry"
          score={formatScore(detail.entry.score)}
          band={detail.entry.band}
          confidence={detail.entry.confidence}
        />
        <ScoreCell
          label="Risk"
          score={detail.risk.level ? humanizeState(detail.risk.level) : UNKNOWN_READING}
          band={null}
          confidence={detail.risk.confidence}
          tint={riskColor(detail.risk.level)}
        />
      </View>
      {/* Spelled out once, here, because the two scores answering different
          questions is the single most load-bearing idea on this screen. */}
      <Text style={styles.muted}>
        Opportunity is about the asset. Entry is about this moment. They disagree often, and that disagreement is
        usually the answer.
      </Text>

      {detail.dataQuality.level !== "FULL" ? <Text style={styles.warning}>{detail.dataQuality.note}</Text> : null}
      {!detail.personalized ? <Text style={styles.warning}>{holding.note}</Text> : null}

      {/* Layer 2 — Why. */}
      <Section title="Why?" subtitle={detail.whyDetail.action[0]?.text} open={open.why} onToggle={() => toggle("why")}>
        <ReasonList reasons={detail.whyDetail.action} />
        {holding.known ? (
          <View style={styles.subBlock}>
            <Text style={styles.subTitle}>Your position</Text>
            <Row label="Unrealised P&L" value={formatPct(holding.unrealizedPnlPct)} />
            <Row label="Share of portfolio" value={formatPlainPct(holding.portfolioWeightPct)} />
          </View>
        ) : (
          <Text style={styles.muted}>{holding.note}</Text>
        )}
      </Section>

      {/* Layer 3 — Setup. Conditional throughout: a trigger, not a prediction. */}
      <Section
        title="Setup"
        subtitle={setup.label || "No clear structure"}
        open={open.setup}
        onToggle={() => toggle("setup")}
      >
        {setup.type ? (
          <>
            <View style={styles.setupHead}>
              <Text style={styles.subTitle}>{setup.label}</Text>
              <Text style={styles.status}>{humanizeState(setup.status)}</Text>
            </View>
            <Row label="Trigger" value={setup.trigger || UNKNOWN_READING} />
            <Row
              label="Entry zone"
              value={setup.entryZone ? `${formatLevel(setup.entryZone[0])} – ${formatLevel(setup.entryZone[1])}` : UNKNOWN_READING}
            />
            <Row label="Invalidation" value={formatLevel(setup.invalidation)} tint={colors.danger} />
            <Row label="Target 1" value={formatLevel(setup.target1)} />
            <Row label="Target 2" value={formatLevel(setup.target2)} />
            <Row
              label="Reward : risk"
              value={setup.riskReward === null ? UNKNOWN_READING : `${setup.riskReward.toFixed(2)} : 1`}
            />
            <Text style={styles.muted}>{setup.note}</Text>
            {sizing.available ? (
              <View style={styles.subBlock}>
                <Text style={styles.subTitle}>Sizing</Text>
                <Row label="Risk per unit" value={formatPlainPct(sizing.riskPerUnitPct)} />
                <Row label="Implied allocation" value={formatPlainPct(sizing.suggestedAllocationPct)} />
                <Row label="Risk budget used" value={formatPlainPct(sizing.riskBudgetPct)} />
                {sizing.riskAdjusted ? (
                  <Text style={styles.muted}>Reduced because the risk surface reads high.</Text>
                ) : null}
                {/* The caveat is the server's own string and is never omitted. */}
                <Text style={styles.caveat}>{sizing.caveat}</Text>
              </View>
            ) : (
              <Text style={styles.muted}>{sizing.note || sizing.caveat}</Text>
            )}
          </>
        ) : (
          <Text style={styles.muted}>{setup.note}</Text>
        )}
      </Section>

      {/* Layer 4 — Entry quality, which is where "good asset, bad moment" lives. */}
      <Section
        title="Entry quality"
        subtitle={`${formatScore(detail.entry.score)} · ${detail.entry.band || UNKNOWN_READING}`}
        open={open.entry}
        onToggle={() => toggle("entry")}
      >
        <ReasonList reasons={detail.whyDetail.entry} />
        <View style={styles.subBlock}>
          <Text style={styles.subTitle}>Opportunity quality</Text>
          <ReasonList reasons={detail.whyDetail.opportunity} />
        </View>
      </Section>

      {/* Layer 5 — Risk, factor by factor including the ones we cannot measure. */}
      <Section
        title="Risk"
        subtitle={riskDetail.level ? humanizeState(riskDetail.level) : "Not measurable"}
        open={open.risk}
        onToggle={() => toggle("risk")}
      >
        {riskDetail.factors.map((factor) => (
          <View key={factor.key} style={styles.factor}>
            <View style={styles.factorHead}>
              <Text style={styles.subTitle}>{factor.label}</Text>
              <Text style={[styles.factorLevel, { color: riskColor(factor.level) }]}>
                {factor.level ? humanizeState(factor.level) : UNKNOWN_READING}
              </Text>
            </View>
            <Text style={styles.muted}>{factor.detail}</Text>
          </View>
        ))}
        {/* Stated rather than implied: the surface is the worst honest factor,
            not their average, and a reader comparing the two would otherwise
            think the summary was wrong. */}
        <Text style={styles.muted}>
          The overall level is the worst measured factor, not an average of them. {riskDetail.measuredFactors} of{" "}
          {riskDetail.factors.length} factors could be measured.
        </Text>
      </Section>

      {/* Layer 6 — Timeframes. Four real rows; intraday is absent, not greyed. */}
      <Section
        title="Timeframes"
        subtitle={
          timeframes.alignment.alignmentPct === null
            ? "Not measurable"
            : `${formatPlainPct(timeframes.alignment.alignmentPct, 0)} aligned ${humanizeState(timeframes.alignment.direction).toLowerCase()}`
        }
        open={open.timeframes}
        onToggle={() => toggle("timeframes")}
      >
        {timeframes.rows.map((row) => (
          <View key={row.key} style={styles.dataRow}>
            <Text style={styles.tfLabel}>{row.label}</Text>
            <Text style={[styles.tfDirection, { color: directionColor(row.direction) }]}>
              {row.direction ? `${DIRECTION_GLYPH[row.direction]} ${humanizeState(row.direction)}` : UNKNOWN_READING}
            </Text>
            <Text style={styles.dataValue}>{formatPct(row.changePct)}</Text>
          </View>
        ))}
        <Text style={styles.muted}>
          {timeframes.alignment.measured} of {timeframes.alignment.total} timeframes could be measured.
        </Text>
        <Text style={styles.muted}>{timeframes.note}</Text>
      </Section>

      {/* Layer 7 — the raw structure the verdict was computed from. */}
      <Section title="Deep market data" open={open.deep} onToggle={() => toggle("deep")}>
        <Text style={styles.subTitle}>Position in range</Text>
        <Row label="Support" value={formatLevel(structure.levels.support)} />
        <Row label="Resistance" value={formatLevel(structure.levels.resistance)} />
        <Row label="Distance to support" value={formatPlainPct(structure.levels.supportDistancePct)} />
        <Row label="Distance to resistance" value={formatPlainPct(structure.levels.resistanceDistancePct)} />
        <Row label="7d range" value={`${formatLevel(structure.levels.rangeLow)} – ${formatLevel(structure.levels.rangeHigh)}`} />
        <Row label="Position in range" value={formatPlainPct(structure.levels.positionInRangePct, 0)} />
        <Text style={styles.muted}>{structure.levels.basis}</Text>

        <View style={styles.subBlock}>
          <Text style={styles.subTitle}>Extension</Text>
          <Row
            label="From 3-day mean"
            value={structure.extension.zScore === null ? UNKNOWN_READING : `${structure.extension.zScore.toFixed(2)} sd`}
          />
          <Row label="Versus mean" value={formatPct(structure.extension.vsMeanPct)} />
          <Row label="State" value={humanizeState(structure.extension.state)} />
        </View>

        <View style={styles.subBlock}>
          <Text style={styles.subTitle}>Volatility and liquidity</Text>
          <Row label="Typical daily move" value={formatPlainPct(structure.volatility.dailyPct)} />
          <Row label="24h turnover" value={formatPlainPct(structure.liquidity.turnoverPct)} />
          <Row label="Liquidity" value={humanizeState(structure.liquidity.band)} />
        </View>

        <View style={styles.subBlock}>
          <Text style={styles.subTitle}>Relative strength</Text>
          <Row label="Versus board" value={formatPct(structure.relativeStrength.vsBoardPct)} />
          <Row label="Versus BTC" value={formatPct(structure.relativeStrength.vsBenchmarkPct)} />
          <Row label="Volume versus median" value={formatRatio(volume.ratio)} />
          {volume.confidence === "UNAVAILABLE" ? <Text style={styles.muted}>{volume.note}</Text> : null}
        </View>

        {anomalies.findings.length ? (
          <View style={styles.subBlock}>
            <Text style={styles.subTitle}>Worth a look</Text>
            {anomalies.findings.map((finding) => (
              <View key={finding.key} style={styles.factor}>
                <Text style={styles.reason}>{finding.label}</Text>
                <Text style={styles.muted}>{finding.detail}</Text>
              </View>
            ))}
            {/* Never rendered without this line. */}
            <Text style={styles.caveat}>{anomalies.caveat}</Text>
          </View>
        ) : null}
      </Section>

      {/* Layer 8 — where the numbers came from, and what they cannot see. */}
      <Section title="Evidence" open={open.evidence} onToggle={() => toggle("evidence")}>
        <Row
          label="Price series"
          value={
            evidence.priceSeries.points === null
              ? UNKNOWN_READING
              : `${evidence.priceSeries.points} points · ${evidence.priceSeries.granularity}`
          }
        />
        <Text style={styles.muted}>{evidence.priceSeries.source}</Text>
        <Row
          label="Volume readings"
          value={evidence.volumeSeries.samples === null ? UNKNOWN_READING : String(evidence.volumeSeries.samples)}
        />
        <Row label="Board median 24h" value={formatPct(evidence.boardContext.medianChangePct)} />
        <Row label="BTC 24h" value={formatPct(evidence.boardContext.benchmarkChangePct)} />
        <View style={styles.subBlock}>
          <Text style={styles.subTitle}>What this cannot see</Text>
          {evidence.limits.map((limit) => (
            <View key={limit} style={styles.reasonRow}>
              <Text style={styles.bullet}>·</Text>
              <Text style={styles.reasonMuted}>{limit}</Text>
            </View>
          ))}
        </View>
      </Section>

      <Text style={styles.caveat}>{detail.disclaimer}</Text>
    </View>
  );
}

function ScoreCell({
  label,
  score,
  band,
  confidence,
  tint
}: {
  label: string;
  score: string;
  band: string | null;
  confidence: Confidence;
  tint?: string;
}) {
  return (
    <View style={styles.scoreCell}>
      <Text style={styles.scoreLabel}>{label}</Text>
      <Text style={[styles.scoreValue, tint ? { color: tint } : null]}>{score}</Text>
      {band ? <Text style={styles.scoreBand}>{humanizeState(band)}</Text> : null}
      <ConfidenceTag confidence={confidence} />
    </View>
  );
}

const styles = createThemedStyles(() => ({
  bullet: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  caveat: { color: colors.muted, fontSize: 11, fontStyle: "italic", lineHeight: 16 },
  chevron: { color: colors.muted, fontSize: 18, fontWeight: "900", paddingHorizontal: 4 },
  collapse: { color: colors.muted, fontSize: 12, fontWeight: "800" },
  dataRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between",
    paddingVertical: 3
  },
  dataValue: { color: colors.text, fontSize: 13, fontWeight: "700", textAlign: "right" },
  factor: { gap: 2, paddingVertical: 4 },
  factorHead: { alignItems: "center", flexDirection: "row", gap: 8, justifyContent: "space-between" },
  factorLevel: { fontSize: 12, fontWeight: "900" },
  heading: { color: colors.intelligence, fontSize: 13, fontWeight: "900", letterSpacing: 0.6 },
  muted: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  panel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 14
  },
  perspective: { color: colors.muted, fontSize: 12 },
  pressed: { opacity: 0.7 },
  reason: { color: colors.text, fontSize: 13, lineHeight: 19 },
  reasonBody: { flex: 1, gap: 2 },
  reasonList: { gap: 4 },
  reasonMuted: { color: colors.muted, flex: 1, fontSize: 13, lineHeight: 19 },
  reasonRow: { flexDirection: "row", gap: 6 },
  scoreBand: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  scoreCell: { flex: 1, gap: 1 },
  scoreLabel: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  scoreRow: { flexDirection: "row", gap: 10 },
  scoreValue: { color: colors.text, fontSize: 20, fontWeight: "900" },
  section: { borderTopColor: colors.border, borderTopWidth: StyleSheet.hairlineWidth },
  sectionBody: { gap: 6, paddingBottom: 8 },
  sectionHead: { alignItems: "center", flexDirection: "row", gap: 8, justifyContent: "space-between", paddingVertical: 10 },
  sectionSubtitle: { color: colors.muted, fontSize: 12 },
  sectionTitle: { color: colors.text, fontSize: 14, fontWeight: "800" },
  sectionTitleWrap: { flex: 1, gap: 1 },
  setupHead: { alignItems: "center", flexDirection: "row", gap: 8, justifyContent: "space-between" },
  status: { color: colors.crypto, fontSize: 11, fontWeight: "900", letterSpacing: 0.4 },
  subBlock: { gap: 3, paddingTop: 6 },
  subTitle: { color: colors.text, fontSize: 13, fontWeight: "800" },
  tagInferred: { color: colors.crypto, fontSize: 10, fontWeight: "800", letterSpacing: 0.3 },
  tagUnavailable: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.3 },
  teaser: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 12
  },
  teaserHint: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  teaserTitle: { color: colors.intelligence, fontSize: 13, fontWeight: "900", letterSpacing: 0.6 },
  tfDirection: { flex: 1, fontSize: 12, fontWeight: "800" },
  tfLabel: { color: colors.text, fontSize: 13, fontWeight: "800", minWidth: 36 },
  verdict: { fontSize: 22, fontWeight: "900", letterSpacing: 0.3 },
  verdictHead: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  warning: { color: colors.warning, fontSize: 12, lineHeight: 18 }
}));
