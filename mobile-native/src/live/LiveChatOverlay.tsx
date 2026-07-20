import { memo, RefObject, useMemo } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "../theme/colors";
import type { PulseLiveChatMessage } from "../api/live";

/**
 * Live chat overlay + composer.
 *
 * The stream floats over the lower-left of the stage (TikTok-style), rendering
 * the most recent messages plus a distinct pinned host message. The same message
 * row + composer are reused inside the full Comments sheet, so styling stays
 * consistent between the ambient overlay and the expanded view.
 */

function initials(name: string): string {
  const trimmed = (name || "?").trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function avatarTone(name: string): string {
  const palette = [colors.accent, colors.intelligence, colors.creator, colors.accentStrong, colors.economy];
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash + name.charCodeAt(i)) % palette.length;
  return palette[hash];
}

export function LiveChatMessageRow({
  message,
  compact = false
}: {
  message: PulseLiveChatMessage;
  compact?: boolean;
}) {
  const name = message.display_name || "Viewer";
  const isPinned = Boolean(message.pinned);
  const isSystem = message.message_type === "system" || message.message_type === "join";
  const tone = avatarTone(name);

  if (isSystem) {
    return (
      <View style={styles.systemRow}>
        <Ionicons name="people" size={13} color={colors.accent} />
        <Text style={styles.systemText} numberOfLines={1}>
          <Text style={styles.systemName}>{name} </Text>
          {message.body}
        </Text>
      </View>
    );
  }

  return (
    <View style={[styles.row, isPinned && styles.rowPinned]}>
      <View style={[styles.avatar, { backgroundColor: tone }]}>
        <Text style={styles.avatarText}>{initials(name)}</Text>
      </View>
      <View style={styles.rowBody}>
        <View style={styles.rowHeader}>
          <Text style={styles.rowName} numberOfLines={1}>
            {name}
          </Text>
          {isPinned ? (
            <View style={styles.pinTag}>
              <Ionicons name="pin" size={10} color={colors.background} />
              <Text style={styles.pinTagText}>Pinned</Text>
            </View>
          ) : null}
        </View>
        <Text style={[styles.rowText, compact && styles.rowTextCompact]} numberOfLines={compact ? 2 : 6}>
          {message.body}
        </Text>
      </View>
    </View>
  );
}

export const LiveChatStream = memo(function LiveChatStream({
  messages,
  pinned,
  maxVisible = 4
}: {
  messages: PulseLiveChatMessage[];
  pinned?: PulseLiveChatMessage | null;
  maxVisible?: number;
}) {
  const recent = useMemo(() => {
    const approved = messages.filter((message) => message.moderation_status !== "hidden" && message.body);
    return approved.slice(-maxVisible);
  }, [messages, maxVisible]);

  if (recent.length === 0 && !pinned) {
    return null;
  }

  return (
    <View style={styles.streamRoot} pointerEvents="box-none">
      {recent.map((message) => (
        <LiveChatMessageRow key={message.id} message={message} compact />
      ))}
      {pinned ? <LiveChatMessageRow key={`pin-${pinned.id}`} message={{ ...pinned, pinned: true }} compact /> : null}
    </View>
  );
});

/**
 * Controlled live-comment composer.
 *
 * Draft text, sending, and error state are owned by the live-host controller so
 * the same draft survives keyboard dismissal and is shared between the ambient
 * over-stage composer and the expanded Comments sheet. This component only
 * renders — it never dismisses the keyboard or mutates the draft on its own.
 */
export function LiveChatComposer({
  value,
  onChangeText,
  onSend,
  onEmoji,
  onGuests,
  onFocus,
  guestCount = 0,
  placeholder = "Say something…",
  disabled = false,
  sending = false,
  errorText,
  inputRef,
  inputAccessoryViewID
}: {
  value: string;
  onChangeText: (text: string) => void;
  onSend: () => void;
  onEmoji?: () => void;
  onGuests?: () => void;
  onFocus?: () => void;
  guestCount?: number;
  placeholder?: string;
  disabled?: boolean;
  sending?: boolean;
  errorText?: string;
  inputRef?: RefObject<TextInput | null>;
  inputAccessoryViewID?: string;
}) {
  const canSend = value.trim().length > 0 && !sending && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSend();
  };

  return (
    <View style={styles.composerWrap}>
      {errorText ? (
        <View style={styles.composerError}>
          <Ionicons name="alert-circle" size={14} color={colors.danger} />
          <Text style={styles.composerErrorText} numberOfLines={2}>
            {errorText}
          </Text>
        </View>
      ) : null}
      <View style={styles.composerRow}>
        <View style={styles.composerField}>
          <TextInput
            ref={inputRef}
            value={value}
            onChangeText={onChangeText}
            onSubmitEditing={submit}
            onFocus={onFocus}
            placeholder={placeholder}
            placeholderTextColor={colors.muted}
            style={styles.composerInput}
            returnKeyType="send"
            editable={!disabled && !sending}
            blurOnSubmit={false}
            multiline
            inputAccessoryViewID={inputAccessoryViewID}
            accessibilityLabel="Live chat message"
          />
          <Pressable
            onPress={onEmoji}
            accessibilityRole="button"
            accessibilityLabel="Emoji"
            style={styles.composerEmoji}
            hitSlop={8}
          >
            <Ionicons name="happy-outline" size={22} color={colors.muted} />
          </Pressable>
          <Pressable
            onPress={submit}
            disabled={!canSend}
            accessibilityRole="button"
            accessibilityLabel="Send comment"
            accessibilityState={{ disabled: !canSend }}
            style={styles.composerSend}
            hitSlop={6}
          >
            {sending ? (
              <ActivityIndicator size="small" color={colors.accent} />
            ) : (
              <Ionicons name="arrow-up-circle" size={26} color={canSend ? colors.accent : colors.muted} />
            )}
          </Pressable>
        </View>
        {onGuests ? (
          <Pressable
            onPress={onGuests}
            accessibilityRole="button"
            accessibilityLabel="Guests"
            style={styles.composerGuests}
          >
            <Ionicons name="people" size={20} color={colors.text} />
            {guestCount > 0 ? (
              <View style={styles.composerGuestBadge}>
                <Text style={styles.composerGuestBadgeText}>{guestCount}</Text>
              </View>
            ) : null}
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  streamRoot: {
    gap: 6,
    maxWidth: "82%"
  },
  row: {
    alignItems: "flex-start",
    backgroundColor: "rgba(4,10,18,0.42)",
    borderRadius: 16,
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 8,
    paddingVertical: 6
  },
  rowPinned: {
    backgroundColor: "rgba(50,230,179,0.14)",
    borderColor: "rgba(50,230,179,0.4)",
    borderWidth: 1
  },
  avatar: {
    alignItems: "center",
    borderRadius: 999,
    height: 30,
    justifyContent: "center",
    width: 30
  },
  avatarText: {
    color: colors.background,
    fontSize: 12,
    fontWeight: "900"
  },
  rowBody: {
    flex: 1,
    gap: 1
  },
  rowHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6
  },
  rowName: {
    color: colors.accentStrong,
    fontSize: 13,
    fontWeight: "800"
  },
  pinTag: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 6,
    flexDirection: "row",
    gap: 3,
    paddingHorizontal: 5,
    paddingVertical: 1
  },
  pinTagText: {
    color: colors.background,
    fontSize: 9,
    fontWeight: "900"
  },
  rowText: {
    color: "#f4f7fb",
    fontSize: 14,
    fontWeight: "600",
    lineHeight: 19
  },
  rowTextCompact: {
    fontSize: 13,
    lineHeight: 17
  },
  systemRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 8,
    paddingVertical: 3
  },
  systemName: {
    color: colors.accent,
    fontWeight: "800"
  },
  systemText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600"
  },
  composerWrap: {
    gap: 8
  },
  composerError: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: "rgba(255,86,86,0.14)",
    borderColor: "rgba(255,86,86,0.4)",
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    maxWidth: "100%",
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  composerErrorText: {
    color: colors.danger,
    flexShrink: 1,
    fontSize: 12,
    fontWeight: "800"
  },
  composerRow: {
    alignItems: "flex-end",
    flexDirection: "row",
    gap: 10
  },
  composerField: {
    alignItems: "center",
    backgroundColor: "rgba(6,14,24,0.6)",
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 24,
    borderWidth: 1,
    flex: 1,
    flexDirection: "row",
    paddingHorizontal: 16,
    paddingVertical: 4
  },
  composerInput: {
    color: colors.text,
    flex: 1,
    fontSize: 15,
    fontWeight: "600",
    maxHeight: 96,
    paddingVertical: 8
  },
  composerEmoji: {
    paddingLeft: 8
  },
  composerSend: {
    alignItems: "center",
    justifyContent: "center",
    minWidth: 26,
    paddingLeft: 8
  },
  composerGuests: {
    alignItems: "center",
    backgroundColor: "rgba(6,14,24,0.6)",
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 999,
    borderWidth: 1,
    height: 46,
    justifyContent: "center",
    width: 46
  },
  composerGuestBadge: {
    alignItems: "center",
    backgroundColor: colors.danger,
    borderRadius: 999,
    height: 18,
    justifyContent: "center",
    minWidth: 18,
    paddingHorizontal: 4,
    position: "absolute",
    right: -2,
    top: -2
  },
  composerGuestBadgeText: {
    color: "#fff",
    fontSize: 10,
    fontWeight: "900"
  }
});
