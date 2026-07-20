import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  creatorRecommendations,
  CreatorState,
  getCreatorState,
  loadCachedCreatorState,
  saveContentPlannerItem
} from "../api/creator";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type ContentPlannerRouteName =
  | "ContentPlanner"
  | "ContentPlannerWeb"
  | "ContentPlannerPulseAlias"
  | "PostScheduler"
  | "PostSchedulerPulseAlias"
  | "DraftStudio"
  | "DraftStudioPulseAlias";

type Props = NativeStackScreenProps<RootStackParamList, ContentPlannerRouteName>;
type PlannerMode = "planner" | "scheduler" | "drafts";

const CONTENT_TYPES = ["text", "photo", "video", "reel", "story", "live_stream", "event", "marketplace_listing"];

export function ContentPlannerScreen({ route, navigation }: Props) {
  const routeMode = route.params && "mode" in route.params ? route.params.mode : undefined;
  const mode = normalizePlannerRouteMode(String(route.name || ""), routeMode);
  const [state, setState] = useState<CreatorState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [contentType, setContentType] = useState(mode === "scheduler" ? "text" : "text");
  const [audience, setAudience] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [altText, setAltText] = useState("");
  const [linksValidated, setLinksValidated] = useState(false);
  const [previewReviewed, setPreviewReviewed] = useState(false);
  const [mediaAttached, setMediaAttached] = useState(false);

  const copy = useMemo(() => modeCopy(mode, offline), [mode, offline]);
  const recommendations = useMemo(() => creatorRecommendations(state).slice(0, 4), [state]);

  async function load() {
    setError("");
    setOffline(false);
    setLoading(true);
    try {
      setState(await getCreatorState());
    } catch (loadError) {
      const cached = await loadCachedCreatorState();
      if (cached) {
        setState(cached);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Content Planner could not load.");
    } finally {
      setLoading(false);
    }
  }

  async function save(status: "draft" | "scheduled") {
    const cleanTitle = title.trim();
    const cleanCaption = caption.trim();
    if (!cleanTitle && !cleanCaption) {
      Alert.alert("Add content first", "Add a title or caption before saving.");
      return;
    }
    if (status === "scheduled" && !scheduledAt.trim()) {
      Alert.alert("Schedule time required", "Scheduled content needs a scheduled time.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const result = await saveContentPlannerItem({
        title: cleanTitle,
        caption: cleanCaption,
        content_type: contentType,
        hashtags,
        audience,
        scheduled_at: scheduledAt,
        alt_text: altText,
        media_attached: mediaAttached,
        links_validated: linksValidated,
        final_preview_reviewed: previewReviewed,
        status,
        stage: status === "scheduled" ? "scheduled" : mode === "drafts" ? "drafting" : "ideas"
      });
      Alert.alert(status === "scheduled" ? "Scheduled draft saved" : "Draft saved", result.message || "Content Planner saved this item.");
      clearForm(status);
      load().catch(() => undefined);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Content Planner could not save this item.");
    } finally {
      setSaving(false);
    }
  }

  function clearForm(status: "draft" | "scheduled") {
    setTitle("");
    setCaption("");
    setHashtags("");
    setAudience("");
    if (status !== "scheduled") setScheduledAt("");
    setAltText("");
    setLinksValidated(false);
    setPreviewReviewed(false);
    setMediaAttached(false);
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, [mode]);

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Content Planner</Text>
      </View>
    );
  }

  const metrics = state?.metrics || {};
  const queue = Number(metrics.content_queue || 0);

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>{copy.title}</Text>
        <Text style={styles.subtitle}>{copy.subtitle}</Text>
      </View>
      <Pressable style={styles.refreshButton} onPress={() => load().catch(() => undefined)}>
        <Text style={styles.refreshText}>Refresh Planner</Text>
      </Pressable>
      {offline ? <Text style={styles.offline}>Showing saved creator state</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Panel>
        <Text style={styles.sectionTitle}>Planner summary</Text>
        <View style={styles.metricGrid}>
          <Metric label="Queue" value={queue} />
          <Metric label="Posts" value={metrics.posts_total} />
          <Metric label="Reels" value={metrics.reels_total} />
          <Metric label="Statuses" value={metrics.statuses_total} />
        </View>
        <Text style={styles.muted}>Detailed owned planner rows remain backend-owned until a native list contract is exposed. Draft and schedule writes use the existing planner API.</Text>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{mode === "scheduler" ? "Schedule draft" : "Create planner item"}</Text>
        <TextInput style={styles.input} value={title} onChangeText={setTitle} placeholder="Title" placeholderTextColor={colors.muted} />
        <TextInput style={[styles.input, styles.textArea]} value={caption} onChangeText={setCaption} placeholder="Caption or body" placeholderTextColor={colors.muted} multiline />
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.choiceRow}>
          {CONTENT_TYPES.map((type) => (
            <Pressable key={type} style={[styles.choice, contentType === type ? styles.choiceActive : undefined]} onPress={() => setContentType(type)}>
              <Text style={[styles.choiceText, contentType === type ? styles.choiceTextActive : undefined]}>{type.replace("_", " ")}</Text>
            </Pressable>
          ))}
        </ScrollView>
        <TextInput style={styles.input} value={audience} onChangeText={setAudience} placeholder="Audience" placeholderTextColor={colors.muted} />
        <TextInput style={styles.input} value={hashtags} onChangeText={setHashtags} placeholder="#hashtags" placeholderTextColor={colors.muted} />
        <TextInput style={styles.input} value={scheduledAt} onChangeText={setScheduledAt} placeholder="Scheduled time, for example 2026-07-09T18:00" placeholderTextColor={colors.muted} />
        <TextInput style={styles.input} value={altText} onChangeText={setAltText} placeholder="Alt text" placeholderTextColor={colors.muted} />
        <Toggle label="Media attached" value={mediaAttached} onPress={() => setMediaAttached((value) => !value)} />
        <Toggle label="Links validated" value={linksValidated} onPress={() => setLinksValidated((value) => !value)} />
        <Toggle label="Final preview reviewed" value={previewReviewed} onPress={() => setPreviewReviewed((value) => !value)} />
        <View style={styles.actionRow}>
          <Pressable style={styles.primaryButton} disabled={saving} onPress={() => save(mode === "scheduler" ? "scheduled" : "draft").catch(() => undefined)}>
            <Text style={styles.primaryText}>{saving ? "Saving..." : mode === "scheduler" ? "Save Scheduled Draft" : "Save Draft"}</Text>
          </Pressable>
          <Pressable style={styles.secondaryButton} disabled={saving} onPress={() => save("scheduled").catch(() => undefined)}>
            <Text style={styles.secondaryText}>Schedule</Text>
          </Pressable>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Publish safety</Text>
        <Text style={styles.muted}>Publish now, recurring schedules, bulk scheduling, smart rescheduling, and version history stay on safe fallback until backend contracts expose native authority.</Text>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Recommended next actions</Text>
        {recommendations.length ? recommendations.map((item) => <Text key={item} style={styles.recommendation}>{item}</Text>) : <Text style={styles.muted}>Planner recommendations appear after the backend has enough creator activity.</Text>}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Connected surfaces</Text>
        <View style={styles.actionRow}>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("CreatorStudio")}>
            <Text style={styles.secondaryText}>Creator Studio</Text>
          </Pressable>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("Events", { title: "Events" })}>
            <Text style={styles.secondaryText}>Events</Text>
          </Pressable>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("Tabs", { screen: "Home", params: { openComposer: true } })}>
            <Text style={styles.secondaryText}>Feed Composer</Text>
          </Pressable>
        </View>
      </Panel>
    </ScrollView>
  );
}

function normalizePlannerMode(value?: string): PlannerMode {
  if (value === "scheduler" || value === "drafts") return value;
  return "planner";
}

function normalizePlannerRouteMode(routeName: string, value?: string): PlannerMode {
  if (routeName === "PostScheduler" || routeName === "PostSchedulerPulseAlias") return "scheduler";
  if (routeName === "DraftStudio" || routeName === "DraftStudioPulseAlias") return "drafts";
  return normalizePlannerMode(value);
}

function modeCopy(mode: PlannerMode, offline: boolean) {
  if (mode === "scheduler") {
    return {
      title: "Scheduled Publishing",
      subtitle: offline ? "Showing saved creator state" : "Schedule owned drafts through the existing Content Planner backend."
    };
  }
  if (mode === "drafts") {
    return {
      title: "Draft Studio",
      subtitle: offline ? "Showing saved creator state" : "Create and refine private drafts using the existing planner API."
    };
  }
  return {
    title: "Content Planner",
    subtitle: offline ? "Showing saved creator state" : "Plan drafts, scheduled content, and creator workflow without bypassing backend publishing rules."
  };
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricValue}>{String(value ?? 0)}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function Toggle({ label, value, onPress }: { label: string; value: boolean; onPress: () => void }) {
  return (
    <Pressable style={styles.toggle} onPress={onPress}>
      <View style={[styles.toggleDot, value ? styles.toggleDotOn : undefined]} />
      <Text style={styles.toggleText}>{label}</Text>
      <Text style={styles.toggleState}>{value ? "Yes" : "No"}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
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
  choice: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  choiceActive: {
    backgroundColor: "rgba(37,208,167,0.14)",
    borderColor: colors.accent
  },
  choiceRow: {
    gap: 8
  },
  choiceText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "capitalize"
  },
  choiceTextActive: {
    color: colors.accent
  },
  content: {
    gap: 14,
    padding: 18,
    paddingBottom: 34
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    lineHeight: 19
  },
  gateway: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    padding: 12
  },
  gatewayBody: {
    flex: 1,
    gap: 3
  },
  gatewayOpen: {
    color: colors.accent,
    fontWeight: "900"
  },
  gatewayPulse: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    height: 10,
    shadowColor: colors.accent,
    shadowOpacity: 0.45,
    shadowRadius: 10,
    width: 10
  },
  gatewayTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  header: {
    gap: 5
  },
  input: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 44,
    padding: 12
  },
  metric: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexBasis: "45%",
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
  offline: {
    color: colors.warning,
    fontSize: 13
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flexGrow: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900",
    textAlign: "center"
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
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexGrow: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  secondaryText: {
    color: colors.text,
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
  textArea: {
    minHeight: 96,
    textAlignVertical: "top"
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  toggle: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 44,
    paddingHorizontal: 12
  },
  toggleDot: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    height: 14,
    width: 14
  },
  toggleDotOn: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  toggleState: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900"
  },
  toggleText: {
    color: colors.text,
    flex: 1,
    fontWeight: "800"
  }
});
