/**
 * PulseSoc shared native Unicode emoji picker (Stage 3-6).
 *
 * THE one picker for the whole app — messenger, comments, feed, status, reels,
 * marketplace all open this component. Native Unicode rendering only (the OS
 * draws the glyphs); no emoji images, no remote emoji API, fully offline.
 *
 * Features: local search (names + keywords), canonical category tabs with
 * fast virtualized scrolling, RECENT from local usage, long-press skin-tone
 * variants with a persisted preference, 4-theme support via useTheme(),
 * keyboard-safe bottom sheet, a11y labels for every emoji.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FlatList,
  Keyboard,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  Text,
  TextInput,
  useWindowDimensions,
  View,
  ViewToken
} from "react-native";
import * as Haptics from "expo-haptics";
import { useTranslation } from "../i18n";
import { useTheme } from "../theme/ThemeContext";
import { emojiA11yLabel, emojiByCategory, searchEmoji } from "./emojiData";
import { getRecentEmoji, getSkinTonePreference, recordRecentEmoji, setSkinTonePreference } from "./recents";
import type { EmojiCategory, EmojiEntry, SkinTonePreference } from "./types";
import { EMOJI_CATEGORIES } from "./types";

const COLUMNS = 8;
const CELL = 44;

/** Tab glyphs — themselves native Unicode, of course. */
const CATEGORY_ICONS: Record<EmojiCategory, string> = {
  RECENT: "🕘",
  "SMILEYS & EMOTION": "😀",
  "PEOPLE & BODY": "👋",
  "ANIMALS & NATURE": "🐻",
  "FOOD & DRINK": "🍔",
  ACTIVITIES: "⚽",
  "TRAVEL & PLACES": "✈️",
  OBJECTS: "💡",
  SYMBOLS: "🔣",
  FLAGS: "🏳️"
};

const CATEGORY_LABEL_KEYS: Record<EmojiCategory, string> = {
  RECENT: "common:emoji.categories.recent",
  "SMILEYS & EMOTION": "common:emoji.categories.smileys",
  "PEOPLE & BODY": "common:emoji.categories.people",
  "ANIMALS & NATURE": "common:emoji.categories.animals",
  "FOOD & DRINK": "common:emoji.categories.food",
  ACTIVITIES: "common:emoji.categories.activities",
  "TRAVEL & PLACES": "common:emoji.categories.travel",
  OBJECTS: "common:emoji.categories.objects",
  SYMBOLS: "common:emoji.categories.symbols",
  FLAGS: "common:emoji.categories.flags"
};

type Row =
  | { kind: "header"; key: string; category: EmojiCategory }
  | { kind: "emojis"; key: string; items: EmojiEntry[] };

export interface EmojiPickerProps {
  visible: boolean;
  onClose: () => void;
  /** Receives the final Unicode emoji string (tone already applied). */
  onSelect: (emoji: string) => void;
  /** Keep the sheet open after a selection (composer use). Default: close. */
  stayOpenOnSelect?: boolean;
}

/** Apply the persisted tone preference to a tone-capable entry. */
function applyTone(entry: EmojiEntry, tone: SkinTonePreference): string {
  if (tone === 0 || !entry.skin_tone_capable) return entry.emoji;
  return entry.variants[tone - 1]?.emoji ?? entry.emoji;
}

const EmojiCell = memo(function EmojiCell({
  entry,
  tone,
  onPick,
  onLongPress
}: {
  entry: EmojiEntry;
  tone: SkinTonePreference;
  onPick: (entry: EmojiEntry, emoji: string) => void;
  onLongPress: (entry: EmojiEntry) => void;
}) {
  const shown = applyTone(entry, tone);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={emojiA11yLabel(shown)}
      onPress={() => onPick(entry, shown)}
      onLongPress={() => entry.skin_tone_capable && onLongPress(entry)}
      delayLongPress={250}
      style={{ width: CELL, height: CELL, alignItems: "center", justifyContent: "center" }}
      hitSlop={2}
    >
      <Text style={{ fontSize: 28 }} allowFontScaling={false}>
        {shown}
      </Text>
    </Pressable>
  );
});

export function EmojiPicker({ visible, onClose, onSelect, stayOpenOnSelect }: EmojiPickerProps) {
  const { t } = useTranslation();
  const theme = useTheme();
  const { height: windowHeight } = useWindowDimensions();
  const [query, setQuery] = useState("");
  const [recents, setRecents] = useState<string[]>([]);
  const [tone, setTone] = useState<SkinTonePreference>(0);
  const [toneTarget, setToneTarget] = useState<EmojiEntry | null>(null);
  const [activeCategory, setActiveCategory] = useState<EmojiCategory>("SMILEYS & EMOTION");
  const listRef = useRef<FlatList<Row>>(null);

  useEffect(() => {
    if (!visible) return;
    setQuery("");
    setToneTarget(null);
    getRecentEmoji().then(setRecents);
    getSkinTonePreference().then(setTone);
  }, [visible]);

  /** Row model: category header rows + fixed-width emoji rows (virtualizable). */
  const { rows, categoryIndex } = useMemo(() => {
    const out: Row[] = [];
    const index = new Map<EmojiCategory, number>();
    for (const cat of EMOJI_CATEGORIES) {
      const entries =
        cat === "RECENT"
          ? recents
              .map((e) => ({ emoji: e, base: e }))
              .map(
                (r): EmojiEntry => ({
                  emoji: r.emoji,
                  name: emojiA11yLabel(r.emoji),
                  keywords: [],
                  category: "RECENT",
                  subgroup: "recent",
                  skin_tone_capable: false,
                  variants: []
                })
              )
          : emojiByCategory(cat);
      if (cat === "RECENT" && entries.length === 0) continue;
      index.set(cat, out.length);
      out.push({ kind: "header", key: `h-${cat}`, category: cat });
      for (let i = 0; i < entries.length; i += COLUMNS) {
        out.push({ kind: "emojis", key: `r-${cat}-${i}`, items: entries.slice(i, i + COLUMNS) });
      }
    }
    return { rows: out, categoryIndex: index };
  }, [recents]);

  const searchResults = useMemo(() => (query.trim() ? searchEmoji(query) : null), [query]);

  const searchRows = useMemo(() => {
    if (!searchResults) return null;
    const out: Row[] = [];
    for (let i = 0; i < searchResults.length; i += COLUMNS) {
      out.push({ kind: "emojis", key: `s-${i}`, items: searchResults.slice(i, i + COLUMNS) });
    }
    return out;
  }, [searchResults]);

  const pick = useCallback(
    (_entry: EmojiEntry, emoji: string) => {
      onSelect(emoji);
      recordRecentEmoji(emoji).then(setRecents);
      if (!stayOpenOnSelect) onClose();
    },
    [onClose, onSelect, stayOpenOnSelect]
  );

  const openTonePopover = useCallback(
    (entry: EmojiEntry) => {
      if (theme.hapticFeedback) Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
      setToneTarget(entry);
    },
    [theme.hapticFeedback]
  );

  const pickTone = useCallback(
    (entry: EmojiEntry, variantEmoji: string, variantTone: SkinTonePreference) => {
      setTone(variantTone);
      setSkinTonePreference(variantTone);
      setToneTarget(null);
      pick(entry, variantEmoji);
    },
    [pick]
  );

  const jumpTo = useCallback(
    (cat: EmojiCategory) => {
      const idx = categoryIndex.get(cat);
      if (idx === undefined) return;
      setActiveCategory(cat);
      Keyboard.dismiss();
      listRef.current?.scrollToIndex({ index: idx, animated: false });
    },
    [categoryIndex]
  );

  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    const first = viewableItems[0]?.item as Row | undefined;
    if (!first) return;
    const cat = first.kind === "header" ? first.category : (first.key.split("-")[1] as EmojiCategory);
    if (EMOJI_CATEGORIES.includes(cat)) setActiveCategory(cat);
  }).current;

  const renderRow = useCallback(
    ({ item }: { item: Row }) => {
      if (item.kind === "header") {
        return (
          <Text
            accessibilityRole="header"
            style={{
              color: theme.colors.muted,
              fontSize: theme.scaleFont(12),
              fontWeight: "700",
              letterSpacing: 1,
              paddingHorizontal: 14,
              paddingTop: 14,
              paddingBottom: 6,
              textTransform: "uppercase"
            }}
          >
            {t(CATEGORY_LABEL_KEYS[item.category])}
          </Text>
        );
      }
      return (
        <View style={{ flexDirection: "row", paddingHorizontal: 8 }}>
          {item.items.map((entry) => (
            <EmojiCell key={entry.emoji} entry={entry} tone={tone} onPick={pick} onLongPress={openTonePopover} />
          ))}
        </View>
      );
    },
    [openTonePopover, pick, t, theme, tone]
  );

  const getItemLayout = useCallback(
    (_data: ArrayLike<Row> | null | undefined, index: number) => {
      // Headers and rows have close-enough heights for jump scroll; FlatList
      // corrects small drift on render.
      let offset = 0;
      const data = searchRows ?? rows;
      for (let i = 0; i < index; i += 1) offset += data[i]?.kind === "header" ? 40 : CELL;
      return { length: data[index]?.kind === "header" ? 40 : CELL, offset, index };
    },
    [rows, searchRows]
  );

  const sheetHeight = Math.min(Math.round(windowHeight * 0.62), 560);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable
        accessibilityLabel={t("common:emoji.close")}
        style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.45)" }}
        onPress={onClose}
      />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View
          style={{
            height: sheetHeight,
            backgroundColor: theme.colors.surface,
            borderTopLeftRadius: 18,
            borderTopRightRadius: 18,
            borderColor: theme.colors.border,
            borderTopWidth: 1
          }}
        >
          <View style={{ alignItems: "center", paddingTop: 8 }}>
            <View style={{ width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border }} />
          </View>
          <View style={{ paddingHorizontal: 12, paddingVertical: 10 }}>
            <TextInput
              value={query}
              onChangeText={setQuery}
              placeholder={t("common:emoji.search")}
              placeholderTextColor={theme.colors.muted}
              keyboardAppearance={theme.keyboardAppearance}
              accessibilityLabel={t("common:emoji.search")}
              autoCorrect={false}
              style={{
                backgroundColor: theme.colors.surfaceRaised,
                borderRadius: 12,
                color: theme.colors.text,
                fontSize: theme.scaleFont(15),
                paddingHorizontal: 12,
                paddingVertical: 9
              }}
            />
          </View>
          {searchRows && searchRows.length === 0 ? (
            <View style={{ alignItems: "center", paddingTop: 40 }}>
              <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(14) }}>
                {t("common:emoji.noResults")}
              </Text>
            </View>
          ) : (
            <FlatList
              ref={listRef}
              data={searchRows ?? rows}
              renderItem={renderRow}
              keyExtractor={(row) => row.key}
              getItemLayout={getItemLayout}
              initialNumToRender={16}
              maxToRenderPerBatch={24}
              windowSize={9}
              keyboardShouldPersistTaps="handled"
              onViewableItemsChanged={searchRows ? undefined : onViewableItemsChanged}
              viewabilityConfig={{ itemVisiblePercentThreshold: 5 }}
              onScrollToIndexFailed={() => {}}
              style={{ flex: 1 }}
            />
          )}
          {!searchRows && (
            <View
              style={{
                flexDirection: "row",
                justifyContent: "space-around",
                borderTopWidth: 1,
                borderColor: theme.colors.border,
                paddingVertical: 6,
                backgroundColor: theme.colors.surface
              }}
            >
              {EMOJI_CATEGORIES.filter((c) => categoryIndex.has(c)).map((cat) => (
                <Pressable
                  key={cat}
                  accessibilityRole="tab"
                  accessibilityLabel={t(CATEGORY_LABEL_KEYS[cat])}
                  accessibilityState={{ selected: activeCategory === cat }}
                  onPress={() => jumpTo(cat)}
                  style={{
                    padding: 6,
                    borderRadius: 10,
                    backgroundColor: activeCategory === cat ? theme.colors.surfaceRaised : "transparent"
                  }}
                >
                  <Text style={{ fontSize: 20, opacity: activeCategory === cat ? 1 : 0.55 }} allowFontScaling={false}>
                    {CATEGORY_ICONS[cat]}
                  </Text>
                </Pressable>
              ))}
            </View>
          )}
          {toneTarget && (
            <View
              accessibilityViewIsModal
              style={{
                position: "absolute",
                left: 0,
                right: 0,
                bottom: 64,
                alignItems: "center"
              }}
            >
              <View
                style={{
                  flexDirection: "row",
                  backgroundColor: theme.colors.surfaceRaised,
                  borderRadius: 24,
                  borderWidth: 1,
                  borderColor: theme.colors.border,
                  paddingHorizontal: 8,
                  paddingVertical: 6,
                  shadowColor: "#000",
                  shadowOpacity: 0.3,
                  shadowRadius: 12,
                  elevation: 8
                }}
              >
                {[toneTarget.emoji, ...toneTarget.variants.map((v) => v.emoji)].map((glyph, i) => (
                  <Pressable
                    key={glyph}
                    accessibilityRole="button"
                    accessibilityLabel={emojiA11yLabel(glyph)}
                    onPress={() => pickTone(toneTarget, glyph, i as SkinTonePreference)}
                    style={{ paddingHorizontal: 6, paddingVertical: 2 }}
                  >
                    <Text style={{ fontSize: 30 }} allowFontScaling={false}>
                      {glyph}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </View>
          )}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

export default EmojiPicker;
