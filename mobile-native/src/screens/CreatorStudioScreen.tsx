import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, AppState, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  creatorRecommendations,
  creatorScore,
  CreatorAiTool,
  CreatorCard,
  CreatorState,
  creatorWebRoute,
  getCreatorState,
  loadCachedCreatorState,
  openCreatorWebFallback,
  runCreatorAiTool,
  saveContentPlannerItem
} from "../api/creator";
import { getPremiumStatus, premiumStateLabel, PremiumStatus } from "../api/premium";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "CreatorStudio" | "CreatorStudioAlias">;

const AI_TOOLS: Array<{ key: CreatorAiTool; label: string; placeholder: string }> = [
  { key: "hook", label: "Hook", placeholder: "Turn this idea into a stronger opening..." },
  { key: "caption", label: "Caption", placeholder: "Improve this caption for PulseSoc..." },
  { key: "virality", label: "Safety Check", placeholder: "Check this post for retention and risk..." },
  { key: "live-title", label: "Live Title", placeholder: "Create a live title for this topic..." }
];

export function CreatorStudioScreen({ navigation }: Props) {
  const [state, setState] = useState<CreatorState | null>(null);
  const [premium, setPremium] = useState<PremiumStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [aiText, setAiText] = useState("");
  const [aiOutput, setAiOutput] = useState("");
  const [busyTool, setBusyTool] = useState<CreatorAiTool | "draft" | "">("");

  const recommendations = useMemo(() => creatorRecommendations(state).slice(0, 4), [state]);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const [nextState, nextPremium] = await Promise.all([
        getCreatorState(),
        getPremiumStatus().catch(() => null)
      ]);
      setState(nextState);
      setPremium(nextPremium);
    } catch (loadError) {
      const cached = await loadCachedCreatorState();
      if (cached) {
        setState(cached);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Creator Studio could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      if (next === "active") load("refresh").catch(() => undefined);
    });
    return () => sub.remove();
  }, [load]);

  async function runAi(tool: CreatorAiTool) {
    const text = aiText.trim() || AI_TOOLS.find((item) => item.key === tool)?.placeholder || "PulseSoc creator studio";
    setBusyTool(tool);
    setAiOutput("Thinking...");
    try {
      const result = await runCreatorAiTool(tool, text);
      const next = result.output || [result.score ? `Score: ${result.score}` : "", result.retention_tip, result.risk_note, result.safety].filter(Boolean).join("\n\n");
      setAiOutput(next || "Creator AI returned an empty response.");
    } catch (toolError) {
      setAiOutput(toolError instanceof Error ? toolError.message : "Creator AI is unavailable.");
    } finally {
      setBusyTool("");
    }
  }

  async function saveDraft() {
    const caption = aiText.trim();
    if (!caption) {
      Alert.alert("Draft needs text", "Add a caption or idea first.");
      return;
    }
    setBusyTool("draft");
    try {
      const result = await saveContentPlannerItem({
        title: caption.slice(0, 80),
        caption,
        content_type: "text",
        status: "draft",
        stage: "planning",
        final_preview_reviewed: false
      });
      Alert.alert("Draft saved", result.message || "Content Planner accepted this draft.");
      load("refresh").catch(() => undefined);
    } catch (draftError) {
      Alert.alert("Draft unavailable", draftError instanceof Error ? draftError.message : "Content Planner could not save this draft.");
    } finally {
      setBusyTool("");
    }
  }

  function openCreatorRoute(card: CreatorCard) {
    const route = creatorWebRoute(card.route);
    if (route.includes("content-planner")) {
      navigation.navigate("ContentPlanner", { mode: "planner", title: "Content Planner" });
      return;
    }
    if (route.includes("draft-studio")) {
      navigation.navigate("ContentPlanner", { mode: "drafts", title: "Draft Studio" });
      return;
    }
    if (route.includes("post-scheduler")) {
      navigation.navigate("ContentPlanner", { mode: "scheduler", title: "Scheduled Publishing" });
      return;
    }
    if (route.includes("live-studio")) {
      navigation.navigate("LiveStudio", { title: "Live Studio" });
      return;
    }
    openCreatorWebFallback(route).catch(() => undefined);
  }

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Creator Studio</Text>
      </View>
    );
  }

  const score = creatorScore(state);
  const metrics = state?.metrics || {};

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Creator Studio</Text>
        <Text style={styles.subtitle}>{offline ? "Showing saved creator state" : "Your creator tools, in one place"}</Text>
      </View>
      <Pressable style={styles.refreshButton} onPress={() => load("refresh").catch(() => undefined)}>
        <Text style={styles.refreshText}>{refreshing ? "Refreshing..." : "Refresh Creator Studio"}</Text>
      </Pressable>
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Panel>
        <View style={styles.heroRow}>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>Creator readiness</Text>
            <Text style={styles.score}>{score || "Learning"}</Text>
            <Text style={styles.muted}>Premium: {premiumStateLabel(premium)}. Eligibility and limits remain backend-controlled.</Text>
          </View>
          <View style={styles.scoreBadge}>
            <Text style={styles.scoreBadgeText}>{state?.intelligence?.community_guideline_status || "Clear"}</Text>
          </View>
        </View>
      </Panel>

      <View style={styles.metricGrid}>
        <Metric label="Posts" value={metrics.posts_total} />
        <Metric label="Reels" value={metrics.reels_total} />
        <Metric label="Statuses" value={metrics.statuses_total} />
        <Metric label="Reach" value={metrics.today_reach} />
        <Metric label="Queue" value={metrics.content_queue} />
        <Metric label="Reviews" value={metrics.moderation_reviews} />
      </View>

      <Panel>
        <Text style={styles.sectionTitle}>Create</Text>
        <View style={styles.actionGrid}>
          <Action label="Feed Composer" onPress={() => navigation.navigate("Tabs", { screen: "Home", params: { openComposer: true } })} />
          <Action label="Status Creator" onPress={() => navigation.navigate("Tabs", { screen: "Status", params: { openCreator: true } })} />
          <Action label="Content Planner" onPress={() => navigation.navigate("ContentPlanner", { mode: "planner", title: "Content Planner" })} />
          <Action label="Scheduled Publishing" onPress={() => navigation.navigate("ContentPlanner", { mode: "scheduler", title: "Scheduled Publishing" })} />
          <Action label="Courses and Learning" onPress={() => navigation.navigate("Courses", { title: "Courses" })} />
          <Action label="Reels" onPress={() => navigation.navigate("Reels")} />
          <Action label="Profile" onPress={() => navigation.navigate("ProfileDetail", undefined)} />
          <Action label="Premium" onPress={() => navigation.navigate("Premium")} />
          <Action label="Live Studio" onPress={() => navigation.navigate("LiveStudio", { title: "Live Studio" })} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Content planner</Text>
        <Text style={styles.muted}>Drafts, scheduling, and content checklist rules use the existing Content Planner backend.</Text>
        <TextInput
          style={styles.input}
          value={aiText}
          onChangeText={setAiText}
          placeholder="Write a post idea, caption, or live topic"
          placeholderTextColor={colors.muted}
          multiline
        />
        <Pressable style={styles.button} disabled={busyTool === "draft"} onPress={saveDraft}>
          <Text style={styles.buttonText}>{busyTool === "draft" ? "Saving..." : "Save Draft to Content Planner"}</Text>
        </Pressable>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Creator AI</Text>
        <View style={styles.actionGrid}>
          {AI_TOOLS.map((tool) => (
            <Action key={tool.key} label={busyTool === tool.key ? "Running..." : tool.label} onPress={() => runAi(tool.key)} />
          ))}
        </View>
        {aiOutput ? <Text style={styles.aiOutput}>{aiOutput}</Text> : <Text style={styles.muted}>AI suggestions stay routed through existing PulseSoc creator AI APIs.</Text>}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Recommended next actions</Text>
        {recommendations.length ? recommendations.map((item) => <Text key={item} style={styles.recommendation}>{item}</Text>) : <Text style={styles.muted}>Recommendations appear after the backend has enough creator activity.</Text>}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Performance summary</Text>
        <ContentRow label="Posts" total={state?.posts?.total} review={state?.posts?.in_review} />
        <ContentRow label="Reels" total={state?.reels?.total} review={state?.reels?.in_review} processing={state?.reels?.processing} />
        <ContentRow label="Status" total={state?.statuses?.total} review={state?.statuses?.in_review} views={state?.statuses?.views} />
        <ContentRow label="Live" total={state?.live?.total} active={state?.live?.active} review={state?.live?.reports_open} />
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Studio tools</Text>
        {(state?.cards || []).slice(0, 8).map((card) => (
          <Pressable key={card.key} style={styles.toolRow} onPress={() => openCreatorRoute(card)}>
            <View style={styles.toolCopy}>
              <Text style={styles.toolTitle}>{card.label}</Text>
              <Text style={styles.muted}>{card.detail || card.action || "Open existing Creator Studio flow."}</Text>
            </View>
            <Text style={styles.toolState}>{card.state || "BETA"}</Text>
          </Pressable>
        ))}
      </Panel>
    </ScrollView>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricValue}>{String(value ?? 0)}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function Action({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.secondaryButton} onPress={onPress}>
      <Text style={styles.secondaryText}>{label}</Text>
    </Pressable>
  );
}

function ContentRow({ label, total, review, processing, views, active }: { label: string; total?: number; review?: number; processing?: number; views?: number; active?: number }) {
  const details = [
    `${Number(total || 0)} total`,
    review ? `${review} review` : "",
    processing ? `${processing} processing` : "",
    views ? `${views} views` : "",
    active ? `${active} active` : ""
  ].filter(Boolean);
  return (
    <View style={styles.contentRow}>
      <Text style={styles.contentLabel}>{label}</Text>
      <Text style={styles.muted}>{details.join(" · ")}</Text>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  aiOutput: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 14,
    lineHeight: 21,
    padding: 12
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 46,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  buttonText: {
    color: colors.background,
    fontWeight: "900",
    textAlign: "center"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 10
  },
  content: {
    gap: 14,
    padding: 18,
    paddingBottom: 34
  },
  contentLabel: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  contentRow: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 4,
    paddingBottom: 10
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    lineHeight: 19
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  header: {
    gap: 5
  },
  heroCopy: {
    flex: 1
  },
  heroRow: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 12
  },
  input: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 92,
    padding: 12,
    textAlignVertical: "top"
  },
  metric: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexBasis: "30%",
    flexGrow: 1,
    minHeight: 76,
    justifyContent: "center",
    padding: 12
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4
  },
  metricValue: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  recommendation: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21
  },
  refreshButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 42,
    justifyContent: "center"
  },
  refreshText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  score: {
    color: colors.text,
    fontSize: 38,
    fontWeight: "900"
  },
  scoreBadge: {
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  scoreBadgeText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  secondaryText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900",
    textAlign: "center"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 21
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  toolCopy: {
    flex: 1,
    gap: 4
  },
  toolRow: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    padding: 12
  },
  toolState: {
    color: colors.warning,
    fontSize: 12,
    fontWeight: "900"
  },
  toolTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  }
}));
