import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { DIGITAL_COMMERCE_ENABLED } from "../api/config";
import {
  askLearningTutor,
  getLearningLesson,
  LearningCategory,
  LearningLessonDetail,
  LearningLessonSummary,
  learningWebRoute,
  listLearningCategories,
  listLearningLessons,
  loadCachedLearningCategories,
  loadCachedLearningLessons,
  loadRecentLearningLessons,
  openLearningWebFallback,
  submitLearningProgress
} from "../api/learning";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type CoursesRouteName =
  | "Courses"
  | "CourseDetail"
  | "LearningLessonDetail"
  | "TeacherProfileGateway"
  | "TeacherDashboardGateway";

type Props = NativeStackScreenProps<RootStackParamList, CoursesRouteName>;

export function CoursesLearningScreen({ route, navigation }: Props) {
  const routeName = String(route.name || "Courses");
  const params = (route.params || {}) as {
    lessonSlug?: string;
    courseId?: number;
    teacherId?: string;
    category?: string;
  };
  const lessonSlug = String(params.lessonSlug || "");
  const courseId = Number(params.courseId || 0);
  const teacherId = String(params.teacherId || "");
  const routeCategory = String(params.category || "");

  const [categories, setCategories] = useState<LearningCategory[]>([]);
  const [lessons, setLessons] = useState<LearningLessonSummary[]>([]);
  const [recent, setRecent] = useState<LearningLessonSummary[]>([]);
  const [selectedCategory, setSelectedCategory] = useState(routeCategory);
  const [selectedLesson, setSelectedLesson] = useState<LearningLessonDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [tutorAnswer, setTutorAnswer] = useState("");
  const [progressMessage, setProgressMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");

  const gatewayMode = useMemo(() => {
    if (routeName === "TeacherDashboardGateway") return "teacher-dashboard";
    if (routeName === "TeacherProfileGateway") return "teacher";
    if (courseId) return "course-detail";
    return "courses";
  }, [courseId, routeName]);

  async function load(mode: "initial" | "refresh" = "initial") {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const [nextCategories, nextLessons, nextRecent] = await Promise.all([
        listLearningCategories(),
        listLearningLessons(selectedCategory || undefined),
        loadRecentLearningLessons()
      ]);
      setCategories(nextCategories);
      setLessons(nextLessons);
      setRecent(nextRecent || []);
      if (lessonSlug) {
        setSelectedLesson(await getLearningLesson(lessonSlug));
      }
    } catch (loadError) {
      const [cachedCategories, cachedLessons, cachedRecent] = await Promise.all([
        loadCachedLearningCategories(),
        loadCachedLearningLessons(selectedCategory || undefined),
        loadRecentLearningLessons()
      ]);
      if (cachedCategories?.length || cachedLessons?.length) {
        setCategories(cachedCategories || []);
        setLessons(cachedLessons || []);
        setRecent(cachedRecent || []);
        setOffline(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Courses and Learning could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function openLesson(lesson: LearningLessonSummary) {
    navigation.navigate("LearningLessonDetail", { lessonSlug: lesson.slug, title: lesson.title });
    setLoading(true);
    setError("");
    setTutorAnswer("");
    setProgressMessage("");
    try {
      setSelectedLesson(await getLearningLesson(lesson.slug));
      setRecent((await loadRecentLearningLessons()) || []);
    } catch (lessonError) {
      setError(lessonError instanceof Error ? lessonError.message : "Lesson could not load.");
    } finally {
      setLoading(false);
    }
  }

  async function completeLesson() {
    if (!selectedLesson?.slug) return;
    setProgressMessage("Saving progress...");
    try {
      const result = await submitLearningProgress(selectedLesson.slug, 100);
      const message = result.message || "PulseSoc saved your lesson progress.";
      setProgressMessage(message);
      Alert.alert("Progress saved", message);
    } catch (progressError) {
      const message = progressError instanceof Error ? progressError.message : "Login is required to save progress.";
      setProgressMessage(message);
      Alert.alert("Progress unavailable", message);
    }
  }

  async function askTutor() {
    const clean = question.trim();
    if (!selectedLesson?.slug || !clean) return;
    setTutorAnswer("Thinking...");
    try {
      const result = await askLearningTutor(selectedLesson.slug, clean);
      setTutorAnswer(result.response || result.message || "Tutor response unavailable.");
    } catch (tutorError) {
      setTutorAnswer(tutorError instanceof Error ? tutorError.message : "Tutor response unavailable.");
    }
  }

  function selectCategory(category: string) {
    setSelectedCategory(category);
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [lessonSlug, selectedCategory]);

  if (loading && !lessons.length && !selectedLesson) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Courses</Text>
      </View>
    );
  }

  if (selectedLesson) {
    return (
      <ScrollView
        style={styles.root}
        contentContainerStyle={styles.detailContent}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
      >
        <Pressable style={styles.backButton} onPress={() => {
          setSelectedLesson(null);
          navigation.navigate("Courses", { title: "Courses" });
        }}>
          <Text style={styles.backText}>Back to Learning</Text>
        </Pressable>
        <View style={styles.hero}>
          <Text style={styles.kicker}>Learning Gateway</Text>
          <Text style={styles.title}>{selectedLesson.title}</Text>
          <Text style={styles.subtitle}>{selectedLesson.summary || "Structured PulseSoc lesson from the existing education backend."}</Text>
          <View style={styles.metaRow}>
            <Text style={styles.pill}>{selectedLesson.difficulty || "Guided"}</Text>
            <Text style={styles.pill}>{selectedLesson.estimated_time || "Self paced"}</Text>
            <Text style={styles.pill}>{selectedLesson.access_level || "free"}</Text>
          </View>
        </View>
        {offline ? <Text style={styles.offline}>Showing saved lesson data</Text> : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Panel>
          <Text style={styles.sectionTitle}>Lesson overview</Text>
          <Text style={styles.bodyText}>{selectedLesson.content || selectedLesson.summary || "Lesson content is available through the PulseSoc education backend."}</Text>
          <Pressable style={styles.primaryButton} onPress={completeLesson}>
            <Text style={styles.primaryText}>Mark Complete</Text>
          </Pressable>
          {progressMessage ? <Text style={styles.answer}>{progressMessage}</Text> : null}
        </Panel>
        <Panel>
          <Text style={styles.sectionTitle}>Knowledge map</Text>
          {selectedLesson.sections?.length ? selectedLesson.sections.map((section, index) => (
            <View key={`${section.heading || "section"}-${index}`} style={styles.lessonBlock}>
              <Text style={styles.lessonHeading}>{section.heading || `Step ${index + 1}`}</Text>
              <Text style={styles.bodyText}>{section.body || ""}</Text>
            </View>
          )) : <Text style={styles.muted}>Knowledge map details stay on the existing lesson page until available in the native payload.</Text>}
        </Panel>
        <Panel>
          <Text style={styles.sectionTitle}>Quiz preview</Text>
          {selectedLesson.quiz?.slice(0, 4).map((item, index) => (
            <View key={`${item.question || "quiz"}-${index}`} style={styles.lessonBlock}>
              <Text style={styles.lessonHeading}>{item.question || `Question ${index + 1}`}</Text>
              <Text style={styles.bodyText}>{Array.isArray(item.options) ? item.options.join(", ") : String(item.options || "")}</Text>
            </View>
          )) || null}
          {!selectedLesson.quiz?.length ? <Text style={styles.muted}>Quiz detail remains available through the existing lesson page.</Text> : null}
        </Panel>
        <Panel>
          <Text style={styles.sectionTitle}>PulseSoc tutor</Text>
          <TextInput style={styles.input} value={question} onChangeText={setQuestion} placeholder="Ask about this lesson" placeholderTextColor={colors.muted} />
          <Pressable style={styles.secondaryButton} onPress={askTutor}>
            <Text style={styles.secondaryText}>Ask Tutor</Text>
          </Pressable>
          {tutorAnswer ? <Text style={styles.answer}>{tutorAnswer}</Text> : <Text style={styles.muted}>Tutor answers use the existing education AI endpoint and safety rules.</Text>}
        </Panel>
        {DIGITAL_COMMERCE_ENABLED ? (
          <Panel>
            <Text style={styles.sectionTitle}>Fallbacks</Text>
            <Gateway label="Open Lesson Web" body="Full lesson, quiz, and any unsupported media/player behavior." onPress={() => openLearningWebFallback(learningWebRoute("education", selectedLesson.slug)).catch(() => undefined)} />
            <Gateway label="Course Catalog Web" body="Paid-course-ready catalog, enrollment, checkout, and teacher compliance surfaces." onPress={() => openLearningWebFallback(learningWebRoute("courses")).catch(() => undefined)} />
          </Panel>
        ) : null}
      </ScrollView>
    );
  }

  if (gatewayMode !== "courses") {
    return (
      <ScrollView style={styles.root} contentContainerStyle={styles.content}>
        <View style={styles.hero}>
          <Text style={styles.kicker}>Learning Gateway</Text>
          <Text style={styles.title}>{gatewayTitle(gatewayMode)}</Text>
          <Text style={styles.subtitle}>This native gateway preserves the existing PulseSoc teacher, course, payment, and review rules. Advanced operations stay on safe fallback.</Text>
        </View>
        <Panel>
          <Text style={styles.sectionTitle}>Backend authority preserved</Text>
          <Text style={styles.muted}>Course creation, paid enrollment, lesson editing, teacher approval, checkout, and advanced player behavior are managed by PulseSoc.</Text>
        </Panel>
        {DIGITAL_COMMERCE_ENABLED ? (
          <Panel>
            <Gateway label="Open Course Catalog" body="Browse the production course catalog." onPress={() => openLearningWebFallback(learningWebRoute("courses", courseId || undefined)).catch(() => undefined)} />
            <Gateway label="Teacher Profile" body="Open the existing trusted educator profile surface." onPress={() => openLearningWebFallback(learningWebRoute("teachers", teacherId || undefined)).catch(() => undefined)} />
            <Gateway label="Teacher Dashboard" body="Teacher applications, lessons, payouts, and review status." onPress={() => openLearningWebFallback(learningWebRoute("teacher-dashboard")).catch(() => undefined)} />
          </Panel>
        ) : null}
      </ScrollView>
    );
  }

  return (
    <FlatList
      style={styles.root}
      data={lessons}
      keyExtractor={(item) => item.slug}
      refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
      contentContainerStyle={styles.content}
      ListHeaderComponent={
        <View>
          <View style={styles.hero}>
            <Text style={styles.kicker}>Courses + Learning</Text>
            <Text style={styles.title}>Learning Gateway</Text>
            <Text style={styles.subtitle}>{offline ? "Showing saved lessons" : "Native lesson discovery powered by the existing PulseSoc education backend, with course payments and teacher tools kept on safe fallback."}</Text>
          </View>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {offline ? <Text style={styles.offline}>Showing saved learning data</Text> : null}
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.categoryRow}>
            <Pressable style={[styles.categoryChip, !selectedCategory ? styles.categoryChipActive : undefined]} onPress={() => selectCategory("")}>
              <Text style={[styles.categoryText, !selectedCategory ? styles.categoryTextActive : undefined]}>All</Text>
            </Pressable>
            {categories.map((category) => (
              <Pressable key={category.slug} style={[styles.categoryChip, selectedCategory === category.slug ? styles.categoryChipActive : undefined]} onPress={() => selectCategory(category.slug)}>
                <Text style={[styles.categoryText, selectedCategory === category.slug ? styles.categoryTextActive : undefined]}>{category.title}</Text>
              </Pressable>
            ))}
          </ScrollView>
          {DIGITAL_COMMERCE_ENABLED ? (
            <Panel>
              <Text style={styles.sectionTitle}>Course gateway</Text>
              <Gateway label="PulseSoc Courses" body="Production catalog, paid-course readiness, enrollment, and checkout fallback." onPress={() => openLearningWebFallback(learningWebRoute("courses")).catch(() => undefined)} />
              <Gateway label="Teacher Dashboard" body="Teacher applications, lesson editing, payouts, and review status." onPress={() => openLearningWebFallback(learningWebRoute("teacher-dashboard")).catch(() => undefined)} />
              <Gateway label="Create Course" body="Course creation stays on existing server validation and review workflows." onPress={() => openLearningWebFallback(learningWebRoute("create")).catch(() => undefined)} />
            </Panel>
          ) : null}
          {recent.length ? (
            <Panel>
              <Text style={styles.sectionTitle}>Continue learning</Text>
              {recent.map((item) => <MiniLesson key={item.slug} lesson={item} onPress={() => openLesson(item).catch(() => undefined)} />)}
            </Panel>
          ) : null}
          <Text style={styles.sectionLabel}>Lessons</Text>
        </View>
      }
      renderItem={({ item }) => <LessonCard lesson={item} onPress={() => openLesson(item).catch(() => undefined)} />}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No lessons found</Text>
          <Text style={styles.emptyText}>Native learning will show lessons when `/api/education/lessons` returns available rows for this category.</Text>
        </View>
      }
    />
  );
}

function LessonCard({ lesson, onPress }: { lesson: LearningLessonSummary; onPress: () => void }) {
  return (
    <Pressable style={styles.card} onPress={onPress}>
      <View style={styles.cardGlow} />
      <View style={styles.cardBody}>
        <Text style={styles.kicker}>{lesson.category_slug || "Lesson"}</Text>
        <Text style={styles.cardTitle}>{lesson.title}</Text>
        <Text style={styles.cardText}>{lesson.summary || "Open this native lesson overview."}</Text>
        <View style={styles.metaRow}>
          <Text style={styles.pill}>{lesson.difficulty || "Guided"}</Text>
          <Text style={styles.pill}>{lesson.estimated_time || "Self paced"}</Text>
          <Text style={styles.pill}>{lesson.access_level || "free"}</Text>
        </View>
      </View>
    </Pressable>
  );
}

function MiniLesson({ lesson, onPress }: { lesson: LearningLessonSummary; onPress: () => void }) {
  return (
    <Pressable style={styles.miniLesson} onPress={onPress}>
      <Text style={styles.miniLessonTitle}>{lesson.title}</Text>
      <Text style={styles.miniLessonMeta}>{lesson.difficulty || "Guided"} · {lesson.estimated_time || "Self paced"}</Text>
    </Pressable>
  );
}

function Gateway({ label, body, onPress }: { label: string; body: string; onPress: () => void }) {
  return (
    <Pressable style={styles.gateway} onPress={onPress}>
      <View style={styles.gatewayDot} />
      <View style={styles.gatewayBody}>
        <Text style={styles.gatewayTitle}>{label}</Text>
        <Text style={styles.gatewayText}>{body}</Text>
      </View>
      <Text style={styles.gatewayAction}>Open</Text>
    </Pressable>
  );
}

function gatewayTitle(mode: string) {
  if (mode === "teacher-dashboard") return "Teacher Dashboard";
  if (mode === "teacher") return "Teacher Profile";
  return "Course Detail";
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  content: {
    gap: 14,
    padding: 16,
    paddingBottom: 40
  },
  detailContent: {
    gap: 14,
    padding: 16,
    paddingBottom: 40
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    gap: 12,
    justifyContent: "center"
  },
  centerText: {
    color: colors.text,
    fontWeight: "800"
  },
  hero: {
    borderColor: "rgba(72, 228, 255, 0.26)",
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    overflow: "hidden",
    padding: 18,
    backgroundColor: "rgba(7, 14, 26, 0.94)"
  },
  kicker: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 0,
    textTransform: "uppercase"
  },
  title: {
    color: colors.text,
    fontSize: 30,
    fontWeight: "900",
    letterSpacing: 0
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21
  },
  categoryRow: {
    gap: 8,
    paddingVertical: 12
  },
  categoryChip: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  categoryChipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  categoryText: {
    color: colors.text,
    fontWeight: "800"
  },
  categoryTextActive: {
    color: "#07121d"
  },
  sectionLabel: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginTop: 4
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    marginTop: 10,
    overflow: "hidden"
  },
  cardGlow: {
    backgroundColor: "rgba(51, 214, 255, 0.34)",
    height: 2
  },
  cardBody: {
    gap: 9,
    padding: 14
  },
  cardTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  cardText: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  metaRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  pill: {
    backgroundColor: "rgba(72, 228, 255, 0.1)",
    borderColor: "rgba(72, 228, 255, 0.28)",
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    fontSize: 12,
    fontWeight: "800",
    overflow: "hidden",
    paddingHorizontal: 9,
    paddingVertical: 6
  },
  miniLesson: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 4,
    padding: 12
  },
  miniLessonTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  miniLessonMeta: {
    color: colors.muted,
    fontSize: 12
  },
  gateway: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 12,
    minHeight: 58,
    padding: 12
  },
  gatewayDot: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    height: 12,
    shadowColor: colors.accent,
    shadowOpacity: 0.45,
    shadowRadius: 12,
    width: 12
  },
  gatewayBody: {
    flex: 1,
    gap: 3
  },
  gatewayTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  gatewayText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  gatewayAction: {
    color: colors.accent,
    fontWeight: "900"
  },
  backButton: {
    alignSelf: "flex-start",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  backText: {
    color: colors.text,
    fontWeight: "900"
  },
  bodyText: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  lessonBlock: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 6,
    padding: 12
  },
  lessonHeading: {
    color: colors.text,
    fontWeight: "900"
  },
  input: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    minHeight: 46,
    paddingHorizontal: 12
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 14
  },
  primaryText: {
    color: "#07121d",
    fontWeight: "900"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 14
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900"
  },
  answer: {
    color: colors.text,
    lineHeight: 20
  },
  empty: {
    alignItems: "center",
    gap: 8,
    padding: 24
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  emptyText: {
    color: colors.muted,
    lineHeight: 20,
    textAlign: "center"
  },
  error: {
    color: colors.danger,
    fontWeight: "800",
    marginTop: 8
  },
  offline: {
    color: colors.warning,
    fontWeight: "800",
    marginTop: 8
  }
});
