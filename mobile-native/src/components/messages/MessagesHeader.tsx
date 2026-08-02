/**
 * The navy header for the commerce inbox. It carries the one dark band and the
 * three controls that must not scroll away: back, the compose button, and search.
 *
 * Compose is governed by the existing initiation policy — the screen passes
 * `canCompose`, and when sellers cannot initiate a thread the button is simply not
 * rendered (never a dead ✏️). Search is wired to real conversation search: this
 * component owns the input and reports every keystroke up; the screen filters the
 * live list, so there is no second search index here.
 */

import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { messagesLight } from "../../theme/messagesLight";

export function MessagesHeader({
  title = "Messages",
  canCompose,
  onBack,
  onCompose,
  searchValue,
  onSearchChange
}: {
  title?: string;
  canCompose: boolean;
  onBack: () => void;
  onCompose: () => void;
  searchValue: string;
  onSearchChange: (next: string) => void;
}) {
  const insets = useSafeAreaInsets();

  return (
    <LinearGradient
      colors={[messagesLight.bg.headerFrom, messagesLight.bg.headerTo]}
      style={[styles.header, { paddingTop: insets.top + 8 }]}
    >
      <View style={styles.topRow}>
        <Pressable
          onPress={onBack}
          style={styles.iconButton}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={6}
        >
          <Ionicons name="chevron-back" size={24} color={messagesLight.text.onDark} />
        </Pressable>
        <Text style={styles.title} numberOfLines={1} accessibilityRole="header">
          {title}
        </Text>
        {canCompose ? (
          <Pressable
            onPress={onCompose}
            style={styles.iconButton}
            accessibilityRole="button"
            accessibilityLabel="New message"
            hitSlop={6}
          >
            <Ionicons name="create-outline" size={22} color={messagesLight.text.onDark} />
          </Pressable>
        ) : (
          <View style={styles.iconButton} />
        )}
      </View>

      <View style={styles.searchWrap}>
        <Ionicons name="search" size={16} color={messagesLight.text.onDarkMuted} />
        <TextInput
          value={searchValue}
          onChangeText={onSearchChange}
          placeholder="Search conversations"
          placeholderTextColor={messagesLight.text.onDarkMuted}
          style={styles.searchInput}
          autoCorrect={false}
          returnKeyType="search"
          accessibilityLabel="Search conversations"
          clearButtonMode="while-editing"
        />
        {searchValue.length ? (
          <Pressable onPress={() => onSearchChange("")} hitSlop={8} accessibilityLabel="Clear search" accessibilityRole="button">
            <Ionicons name="close-circle" size={16} color={messagesLight.text.onDarkMuted} />
          </Pressable>
        ) : null}
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: messagesLight.space.card,
    paddingBottom: 12,
    gap: 12,
    overflow: "hidden"
  },
  topRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  iconButton: {
    minWidth: messagesLight.size.tapTarget,
    minHeight: messagesLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  title: {
    flex: 1,
    fontSize: 20,
    fontWeight: "700",
    color: messagesLight.text.onDark,
    textAlign: "center"
  },
  searchWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "rgba(255,255,255,0.12)",
    borderRadius: messagesLight.radius.control,
    paddingHorizontal: 12,
    height: 40
  },
  searchInput: {
    flex: 1,
    color: messagesLight.text.onDark,
    fontSize: 15,
    paddingVertical: 0
  }
});
