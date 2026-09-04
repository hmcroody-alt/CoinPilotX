/**
 * The one-glance verdict on a market row.
 *
 * ## Why this is small
 *
 * Market Pulse is a list of prices. Somebody scanning fifty rows is answering
 * "what is happening", not "what should I do about MATIC", and a card that
 * argues its case in four lines makes the list unreadable for the ninety percent
 * of visits that are a glance. So the row gets one word and two numbers, and
 * every sentence of reasoning lives one tap away on the detail screen.
 *
 * ## Why two scores and not one
 *
 * They answer different questions and routinely disagree. Opportunity is "is
 * this a good asset right now"; entry is "is this a good moment to start". A
 * strong uptrend that has already run produces a high opportunity and a low
 * entry, and the honest verdict is WAIT. Averaging them into a single number
 * would produce a mediocre score for a situation that is not mediocre at all —
 * it is clear, it is just not a buy. The pair is the point.
 *
 * ## Why the colour comes from the server
 *
 * `tone` is chosen alongside the state in `services/market_intelligence`. If the
 * client mapped state to colour itself, the day a state was added would be the
 * day a red verdict rendered green by falling through to a default.
 */

import { Text, View } from "react-native";
import { AssetIntelligence, ActionTone, RiskLevel, formatScore } from "../../api/marketIntelligence";
import { colors } from "../../theme/colors";
import { createThemedStyles } from "../../theme/themedStyles";

/**
 * Tone to colour.
 *
 * `muted` is deliberately the same grey the rest of the app uses for "unknown".
 * DATA_UNAVAILABLE arrives with that tone, and it must read as an absence rather
 * than as a mild opinion.
 */
export function toneColor(tone: ActionTone): string {
  switch (tone) {
    case "positive":
      return colors.accent;
    case "negative":
      return colors.danger;
    case "caution":
      return colors.warning;
    case "watch":
      return colors.crypto;
    case "muted":
      return colors.muted;
    default:
      return colors.text;
  }
}

export function riskColor(level: RiskLevel | null): string {
  switch (level) {
    case "LOW":
      return colors.accent;
    case "MODERATE":
      return colors.warning;
    case "HIGH":
    case "EXTREME":
      return colors.danger;
    default:
      return colors.muted;
  }
}

/**
 * The row indicator.
 *
 * Returns null when there is no analysis, rather than a "no signal" chip. A
 * placeholder in every row of a fifty-row list is fifty pieces of furniture
 * saying nothing, and it would also make an outage look like a market condition.
 */
export function IntelligenceBadge({ intelligence }: { intelligence: AssetIntelligence | null }) {
  if (!intelligence || !intelligence.action.state) return null;
  const tint = toneColor(intelligence.action.tone);
  const hasScores = intelligence.opportunity.score !== null || intelligence.entry.score !== null;
  return (
    <View style={styles.wrap}>
      <Text style={[styles.state, { color: tint }]} numberOfLines={1}>
        {intelligence.action.label}
      </Text>
      {hasScores ? (
        // "O" and "E" rather than the full words: at this size the words would
        // wrap and push the price column, and the detail screen spells them out
        // in full one tap away.
        <Text style={styles.scores}>
          {`O ${formatScore(intelligence.opportunity.score)} · E ${formatScore(intelligence.entry.score)}`}
        </Text>
      ) : null}
    </View>
  );
}

const styles = createThemedStyles(() => ({
  scores: { color: colors.muted, fontSize: 10, fontWeight: "700", letterSpacing: 0.2 },
  state: { fontSize: 10, fontWeight: "900", letterSpacing: 0.3, textTransform: "uppercase" },
  wrap: { alignItems: "flex-end", gap: 1 }
}));
