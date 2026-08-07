import { ReactElement } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { colors } from "../../theme/colors";
import {
  UndxCapabilityView,
  UndxSelfKnowledge,
  capabilitiesByDomain
} from "../../api/undxSelfKnowledge";

/**
 * Presentational, server-authoritative view of "who UNDX is and what it can
 * actually do right now". It renders ONLY the {@link UndxSelfKnowledge} the
 * backend supplied — it never invents identity, counts, or capability status.
 *
 * Data is injected rather than fetched here so the component stays pure and
 * trivially testable; a screen wires {@link fetchUndxSelfKnowledge} to it. When
 * `knowledge` is null the panel shows an honest "capabilities unavailable" state
 * instead of a fabricated list, matching the honesty rule that anything not
 * listed by the registry is not executable.
 */
export type UndxCapabilityPanelProps = {
  knowledge: UndxSelfKnowledge | null;
  loading?: boolean;
};

export function UndxCapabilityPanel({
  knowledge,
  loading = false
}: UndxCapabilityPanelProps): ReactElement {
  if (loading) {
    return (
      <View style={styles.stateBox} testID="undx-capability-loading">
        <Text style={styles.stateText}>Loading UNDX capabilities…</Text>
      </View>
    );
  }

  if (!knowledge) {
    return (
      <View style={styles.stateBox} testID="undx-capability-unavailable">
        <Text style={styles.stateText}>
          UNDX capability status is unavailable right now. UNDX will not claim an
          action it cannot verify.
        </Text>
      </View>
    );
  }

  const { assistant, company, capabilities } = knowledge;
  const counts = capabilities.counts;
  const grouped = capabilitiesByDomain(knowledge);
  const domains = Object.keys(grouped).sort();

  return (
    <ScrollView style={styles.container} testID="undx-capability-panel">
      <Text style={styles.assistantName}>{assistant.name}</Text>
      <Text style={styles.assistantDescription}>{assistant.description}</Text>

      <View style={styles.identityCard} testID="undx-company-identity">
        <Text style={styles.identityLine}>{company.legal_name}</Text>
        <Text style={styles.identityMuted}>
          {company.founder.name} · {company.founder.title}
        </Text>
      </View>

      <View style={styles.countsRow} testID="undx-capability-counts">
        <CountPill label="Available" value={counts.total} />
        <CountPill label="Read" value={counts.read_only} />
        <CountPill label="Actions" value={counts.write} />
        <CountPill label="Needs confirm" value={counts.requires_confirmation} />
      </View>

      {domains.map((domain) => (
        <View key={domain} style={styles.domainBlock} testID={`undx-domain-${domain}`}>
          <Text style={styles.domainTitle}>{domain}</Text>
          {grouped[domain].map((capability) => (
            <CapabilityRow key={capability.capability_id} capability={capability} />
          ))}
        </View>
      ))}

      <Text style={styles.honestyNote} testID="undx-honesty-note">
        {knowledge.honesty.capability_rule}
      </Text>
    </ScrollView>
  );
}

function CountPill({ label, value }: { label: string; value: number }): ReactElement {
  return (
    <View style={styles.pill}>
      <Text style={styles.pillValue}>{value}</Text>
      <Text style={styles.pillLabel}>{label}</Text>
    </View>
  );
}

function CapabilityRow({ capability }: { capability: UndxCapabilityView }): ReactElement {
  const isWrite = capability.executionMode === "EXECUTE";
  return (
    <View style={styles.capabilityRow} testID={`undx-capability-${capability.capability_id}`}>
      <View style={styles.capabilityMain}>
        <Text style={styles.capabilityId}>{capability.capability_id}</Text>
        <Text style={styles.capabilityDescription}>{capability.description}</Text>
      </View>
      <View style={styles.capabilityTags}>
        <Text style={[styles.tag, isWrite ? styles.tagWrite : styles.tagRead]}>
          {isWrite ? "Action" : "Read"}
        </Text>
        {capability.requiresConfirmation ? (
          <Text style={[styles.tag, styles.tagConfirm]}>Confirm</Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  stateBox: {
    padding: 20,
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.border
  },
  stateText: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  assistantName: { color: colors.text, fontSize: 22, fontWeight: "700", marginBottom: 4 },
  assistantDescription: { color: colors.muted, fontSize: 14, lineHeight: 20, marginBottom: 16 },
  identityCard: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 14,
    marginBottom: 16
  },
  identityLine: { color: colors.text, fontSize: 16, fontWeight: "600" },
  identityMuted: { color: colors.muted, fontSize: 13, marginTop: 2 },
  countsRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 20 },
  pill: {
    backgroundColor: colors.signalSoft,
    borderRadius: 12,
    paddingVertical: 8,
    paddingHorizontal: 12,
    minWidth: 76
  },
  pillValue: { color: colors.accentStrong, fontSize: 18, fontWeight: "700" },
  pillLabel: { color: colors.muted, fontSize: 11, marginTop: 2 },
  domainBlock: { marginBottom: 18 },
  domainTitle: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 8
  },
  capabilityRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 12,
    marginBottom: 8
  },
  capabilityMain: { flex: 1, paddingRight: 10 },
  capabilityId: { color: colors.text, fontSize: 14, fontWeight: "600" },
  capabilityDescription: { color: colors.muted, fontSize: 12, marginTop: 3, lineHeight: 16 },
  capabilityTags: { alignItems: "flex-end", gap: 4 },
  tag: {
    fontSize: 10,
    fontWeight: "700",
    overflow: "hidden",
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2
  },
  tagRead: { color: colors.accent, backgroundColor: colors.signalDim },
  tagWrite: { color: colors.accentStrong, backgroundColor: colors.signalSoft },
  tagConfirm: { color: colors.warning, backgroundColor: colors.warningSoft },
  honestyNote: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    fontStyle: "italic",
    marginTop: 4,
    marginBottom: 32
  }
});
