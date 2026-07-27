import { useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { clearPerfSamples, getPerfSamples, isPerfTracingEnabled, type PerfSample } from "../core/perfTrace";
import { colors } from "../theme/colors";

interface MetricRow {
  name: string;
  count: number;
  p50: number;
  p95: number;
  max: number;
}

/** Nearest-rank percentile over an already-sorted ascending array. */
function percentile(sortedAscending: number[], fraction: number): number {
  if (!sortedAscending.length) return 0;
  const rank = Math.ceil(fraction * sortedAscending.length);
  const index = Math.min(sortedAscending.length - 1, Math.max(0, rank - 1));
  return sortedAscending[index];
}

function aggregate(samples: PerfSample[]): MetricRow[] {
  const byName = new Map<string, number[]>();
  for (const sample of samples) {
    const bucket = byName.get(sample.name) || [];
    bucket.push(sample.durationMs);
    byName.set(sample.name, bucket);
  }
  const rows: MetricRow[] = [];
  for (const [name, durations] of byName) {
    const sorted = durations.slice().sort((a, b) => a - b);
    rows.push({
      name,
      count: sorted.length,
      p50: percentile(sorted, 0.5),
      p95: percentile(sorted, 0.95),
      max: sorted[sorted.length - 1]
    });
  }
  return rows.sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * On-device performance HUD. Renders only when perf tracing is enabled (dev build
 * or the EXPO_PUBLIC_PULSESOC_PERF_OVERLAY QA flag), so it is inert in a normal
 * Release build. Reads the local ring buffer on a 1s cadence — it never triggers
 * network work and holds no private content (samples are already sanitized).
 */
export function PerfOverlay() {
  const insets = useSafeAreaInsets();
  const [expanded, setExpanded] = useState(false);
  const [samples, setSamples] = useState<PerfSample[]>([]);

  useEffect(() => {
    if (!isPerfTracingEnabled()) return;
    const tick = () => setSamples(getPerfSamples());
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, []);

  const rows = useMemo(() => aggregate(samples), [samples]);

  if (!isPerfTracingEnabled()) return null;

  if (!expanded) {
    return (
      <Pressable
        style={[styles.pill, { top: insets.top + 6 }]}
        onPress={() => setExpanded(true)}
        accessibilityLabel="Open performance overlay"
      >
        <Text style={styles.pillText}>⏱ {samples.length}</Text>
      </Pressable>
    );
  }

  return (
    <View style={[styles.panel, { top: insets.top + 6 }]} pointerEvents="box-none">
      <View style={styles.panelInner}>
        <View style={styles.header}>
          <Text style={styles.title}>Perf · client-observed ms</Text>
          <View style={styles.headerButtons}>
            <Pressable style={styles.headerButton} onPress={() => clearPerfSamples()}>
              <Text style={styles.headerButtonText}>Clear</Text>
            </Pressable>
            <Pressable style={styles.headerButton} onPress={() => setExpanded(false)}>
              <Text style={styles.headerButtonText}>Hide</Text>
            </Pressable>
          </View>
        </View>
        <View style={styles.rowHead}>
          <Text style={[styles.cell, styles.cellName, styles.muted]}>metric</Text>
          <Text style={[styles.cell, styles.muted]}>n</Text>
          <Text style={[styles.cell, styles.muted]}>p50</Text>
          <Text style={[styles.cell, styles.muted]}>p95</Text>
          <Text style={[styles.cell, styles.muted]}>max</Text>
        </View>
        <ScrollView style={styles.scroll}>
          {rows.length ? (
            rows.map((row) => (
              <View key={row.name} style={styles.row}>
                <Text style={[styles.cell, styles.cellName]} numberOfLines={1}>{row.name}</Text>
                <Text style={styles.cell}>{row.count}</Text>
                <Text style={styles.cell}>{row.p50}</Text>
                <Text style={styles.cell}>{row.p95}</Text>
                <Text style={styles.cell}>{row.max}</Text>
              </View>
            ))
          ) : (
            <Text style={styles.empty}>No samples yet — interact with the app.</Text>
          )}
        </ScrollView>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    backgroundColor: "rgba(0,0,0,0.72)",
    borderColor: colors.accent,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingVertical: 4,
    position: "absolute",
    right: 8,
    zIndex: 9999
  },
  pillText: { color: colors.accent, fontSize: 11, fontWeight: "900" },
  panel: { left: 8, position: "absolute", right: 8, zIndex: 9999 },
  panelInner: {
    backgroundColor: "rgba(0,0,0,0.86)",
    borderColor: colors.accent,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    maxHeight: 280,
    padding: 10
  },
  header: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", marginBottom: 8 },
  headerButtons: { flexDirection: "row", gap: 8 },
  headerButton: { borderColor: colors.border, borderRadius: 6, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 8, paddingVertical: 3 },
  headerButtonText: { color: colors.text, fontSize: 11, fontWeight: "800" },
  title: { color: colors.accent, fontSize: 12, fontWeight: "900" },
  rowHead: { borderBottomColor: colors.border, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: "row", paddingBottom: 4 },
  row: { flexDirection: "row", paddingVertical: 3 },
  cell: { color: colors.text, flex: 1, fontSize: 11, fontVariant: ["tabular-nums"], textAlign: "right" },
  cellName: { flex: 3, textAlign: "left" },
  muted: { color: colors.muted, fontWeight: "800" },
  scroll: { flexGrow: 0 },
  empty: { color: colors.muted, fontSize: 12, paddingVertical: 8, textAlign: "center" }
});
