import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text } from "react-native";
import { fetchUndxSelfKnowledge, UndxSelfKnowledge } from "../api/undxSelfKnowledge";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { UndxCapabilityPanel } from "../components/undx/UndxCapabilityPanel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "UndxCapabilities">;

/**
 * "What is UNDX, and what can it actually do right now?" — rendered from the
 * server-authoritative self-knowledge payload, never from client-side hard-coded
 * metadata. The screen owns only fetch/loading/error/refresh; all rendering (and
 * the honest empty state) lives in {@link UndxCapabilityPanel}.
 */
export function UndxCapabilitiesScreen(_props: Props) {
  const [knowledge, setKnowledge] = useState<UndxSelfKnowledge | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      setKnowledge(await fetchUndxSelfKnowledge());
    } catch (loadError) {
      // Honest failure: keep any prior payload, surface the reason, and let the
      // panel's own null-state cover the "never loaded" case.
      setError(loadError instanceof Error ? loadError.message : "UNDX capabilities could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load("initial");
  }, [load]);

  if (error && !knowledge) {
    return (
      <LogiNexusScreenShell>
        <LogiNexusStatePanel
          state="error"
          title="UNDX capabilities are unavailable"
          body={error}
        >
          <Pressable style={styles.retry} onPress={() => void load("initial")}>
            <Text style={styles.retryText}>Try again</Text>
          </Pressable>
        </LogiNexusStatePanel>
      </LogiNexusScreenShell>
    );
  }

  return (
    <LogiNexusScreenShell>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => void load("refresh")}
            tintColor={colors.accent}
          />
        }
      >
        <UndxCapabilityPanel knowledge={knowledge} loading={loading && !knowledge} />
      </ScrollView>
    </LogiNexusScreenShell>
  );
}

const styles = StyleSheet.create({
  scroll: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16 },
  retry: {
    marginTop: 12,
    alignSelf: "flex-start",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.signalSoft,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  retryText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" }
});
