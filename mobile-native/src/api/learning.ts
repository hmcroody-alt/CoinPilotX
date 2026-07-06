import { Linking } from "react-native";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

const LEARNING_CATEGORIES_CACHE_KEY = "pulsesoc.native.learning.categories";
const LEARNING_LESSONS_CACHE_KEY = "pulsesoc.native.learning.lessons";
const LEARNING_RECENT_CACHE_KEY = "pulsesoc.native.learning.recent";

export type LearningCategory = {
  slug: string;
  title: string;
  summary?: string;
  icon?: string;
};

export type LearningLessonSummary = {
  slug: string;
  category_slug?: string;
  title: string;
  difficulty?: string;
  estimated_time?: string;
  summary?: string;
  access_level?: string;
};

export type LearningLessonSection = {
  heading?: string;
  body?: string;
};

export type LearningQuizQuestion = {
  question?: string;
  options?: string[] | string;
  answer?: string;
  explanation?: string;
};

export type LearningLessonDetail = LearningLessonSummary & {
  content?: string;
  sections?: LearningLessonSection[];
  quiz?: LearningQuizQuestion[];
};

export async function listLearningCategories() {
  const data = await pulseApi<{ ok?: boolean; categories?: LearningCategory[] }>("/api/education/categories");
  const categories = normalizeCategories(data.categories || []);
  await writeJsonCache(LEARNING_CATEGORIES_CACHE_KEY, categories).catch(() => undefined);
  return categories;
}

export async function loadCachedLearningCategories() {
  return readJsonCache<LearningCategory[]>(LEARNING_CATEGORIES_CACHE_KEY, normalizeCategories);
}

export async function listLearningLessons(category?: string) {
  const path = category ? `/api/education/lessons?category=${encodeURIComponent(category)}` : "/api/education/lessons";
  const data = await pulseApi<{ ok?: boolean; lessons?: LearningLessonSummary[] }>(path);
  const lessons = normalizeLessons(data.lessons || []);
  await writeJsonCache(cacheKeyForLessons(category), lessons).catch(() => undefined);
  if (!category) await writeJsonCache(LEARNING_LESSONS_CACHE_KEY, lessons).catch(() => undefined);
  return lessons;
}

export async function loadCachedLearningLessons(category?: string) {
  return readJsonCache<LearningLessonSummary[]>(cacheKeyForLessons(category), normalizeLessons);
}

export async function getLearningLesson(slug: string) {
  const cleanSlug = encodeURIComponent(String(slug || ""));
  const data = await pulseApi<{ ok?: boolean; lesson?: LearningLessonDetail }>(`/api/education/lesson/${cleanSlug}`);
  const lesson = normalizeLessonDetail(data.lesson || {});
  await saveRecentLearningLesson(lesson).catch(() => undefined);
  return lesson;
}

export async function submitLearningProgress(lessonSlug: string, score = 100) {
  return pulseApi<{ ok?: boolean; score?: number; message?: string }>("/api/education/quiz/submit", {
    method: "POST",
    body: JSON.stringify({ lesson_slug: lessonSlug, score })
  });
}

export async function askLearningTutor(lessonSlug: string, question: string) {
  return pulseApi<{ ok?: boolean; response?: string; message?: string }>("/api/education/tutor", {
    method: "POST",
    body: JSON.stringify({ lesson_slug: lessonSlug, question })
  });
}

export async function loadRecentLearningLessons() {
  return readJsonCache<LearningLessonSummary[]>(LEARNING_RECENT_CACHE_KEY, normalizeLessons);
}

export async function saveRecentLearningLesson(lesson: LearningLessonSummary) {
  const current = (await loadRecentLearningLessons()) || [];
  const clean = normalizeLessonSummary(lesson);
  if (!clean.slug) return current;
  const next = [clean, ...current.filter((item) => item.slug !== clean.slug)].slice(0, 8);
  await writeJsonCache(LEARNING_RECENT_CACHE_KEY, next);
  return next;
}

export async function openLearningWebFallback(path = "/pulse/courses") {
  const target = /^https?:\/\//i.test(path) ? path : `${PULSE_API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  await Linking.openURL(target).catch(() => undefined);
}

export function learningWebRoute(mode: "courses" | "create" | "teachers" | "teacher-dashboard" | "education" = "courses", id?: number | string) {
  if (mode === "create") return "/pulse/courses/create";
  if (mode === "teachers") return id ? `/pulse/teachers/${encodeURIComponent(String(id))}` : "/pulse/teachers";
  if (mode === "teacher-dashboard") return "/pulse/teacher-dashboard";
  if (mode === "education") return id ? `/education/lesson/${encodeURIComponent(String(id))}` : "/education";
  return id ? `/pulse/courses/${encodeURIComponent(String(id))}` : "/pulse/courses";
}

function normalizeCategories(categories: LearningCategory[]) {
  return (Array.isArray(categories) ? categories : []).map((category, index) => ({
    slug: String(category.slug || `category-${index}`),
    title: String(category.title || category.slug || "Learning"),
    summary: String(category.summary || ""),
    icon: String(category.icon || "")
  }));
}

function normalizeLessons(lessons: LearningLessonSummary[]) {
  return (Array.isArray(lessons) ? lessons : []).map(normalizeLessonSummary).filter((lesson) => lesson.slug);
}

function normalizeLessonSummary(lesson: Partial<LearningLessonSummary>) {
  return {
    slug: String(lesson.slug || ""),
    category_slug: String(lesson.category_slug || ""),
    title: String(lesson.title || "PulseSoc lesson"),
    difficulty: String(lesson.difficulty || ""),
    estimated_time: String(lesson.estimated_time || ""),
    summary: String(lesson.summary || ""),
    access_level: String(lesson.access_level || "free")
  };
}

function normalizeLessonDetail(lesson: Partial<LearningLessonDetail>) {
  return {
    ...normalizeLessonSummary(lesson),
    content: String(lesson.content || ""),
    sections: Array.isArray(lesson.sections) ? lesson.sections : [],
    quiz: Array.isArray(lesson.quiz) ? lesson.quiz : []
  };
}

function cacheKeyForLessons(category?: string) {
  const clean = String(category || "").trim();
  return clean ? `${LEARNING_LESSONS_CACHE_KEY}.${clean}` : LEARNING_LESSONS_CACHE_KEY;
}
