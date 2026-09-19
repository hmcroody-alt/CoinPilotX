/**
 * The long-press experience for a single message.
 *
 * Not a sheet. A sheet slides up from the bottom and is about the screen; this
 * is about the one message under the finger, so the conversation dims, that
 * message stays at the coordinates it was pressed at, reactions appear above
 * it and its actions below it. Everything on screen is a statement about the
 * thing you are still touching.
 *
 * ## What it does not decide
 *
 * Which actions exist is `messageActionRules`' job, and which of them are
 * available is decided from the message and its context before this component
 * renders. Nothing here asks "is this mine" or "is this a voice note" -- the
 * overlay draws the rules it is handed, in the order it is handed them. That
 * separation is the whole reason the menu can stay honest: a new action is a
 * new rule, and this file does not change.
 *
 * Where the three bands sit is likewise `focusedMessageLayout`'s job, because
 * the interesting cases are the first and last messages in a thread and those
 * are exactly the ones you cannot see in a screenshot of the middle.
 *
 * ## Audio
 *
 * Nothing here touches playback, the audio session, or the waveform. A long
 * press on a voice note dims the screen and shows a menu; the note keeps
 * playing if it was playing. Pausing it would be a change to audio behaviour
 * arriving through a menu, which is how a session gets stolen.
 */
import { Ionicons } from "@expo/vector-icons";
import React, { useMemo, useRef } from "react";
import {
  Modal,
  PanResponder,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { MessengerMessage } from "../api/messenger";
import { QUICK_REACTIONS } from "../emoji";
import { useTranslation } from "../i18n";
import {
  FOCUS_GAP,
  REACTION_STRIP_HEIGHT,
  focusedMessageLayout,
  type Rect
} from "../pulseCommand/focusedMessageLayout";
import type { PulseCommandActionKey, PulseCommandActionRule } from "../pulseCommand/domain";
import { colors } from "../theme/colors";

/** One row per action, plus the container's own padding. */
const ACTION_ROW_HEIGHT = 46;
const MENU_PADDING = 12;
/** Past this many actions the menu scrolls rather than growing. */
const MENU_VISIBLE_ROWS = 8;

export type MessageFocusOverlayProps = {
  /** Null closes the overlay; there is no separate `visible` flag to disagree with it. */
  message: MessengerMessage | null;
  /** Where the pressed bubble is, in window coordinates. */
  anchor: Rect | null;
  /** Already filtered to the available ones, in display order. */
  actions: PulseCommandActionRule[];
  /** Short text shown in the focused bubble facsimile. */
  preview: string;
  canReact: boolean;
  viewerReaction?: string;
  onAction: (key: PulseCommandActionKey) => void;
  onReact: (reaction: string) => void;
  onReactMore: () => void;
  onClose: () => void;
};

export function MessageFocusOverlay({
  message,
  anchor,
  actions,
  preview,
  canReact,
  viewerReaction,
  onAction,
  onReact,
  onReactMore,
  onClose
}: MessageFocusOverlayProps) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const { width: windowWidth, height: windowHeight } = useWindowDimensions();

  /**
   * A downward or upward flick dismisses, the same as tapping outside.
   *
   * The threshold is on distance rather than velocity so a slow deliberate
   * drag works too; `onMoveShouldSetPanResponder` only claims the gesture once
   * the finger has actually travelled, which leaves taps on the action rows
   * underneath alone.
   */
  const pan = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_event, gesture) => Math.abs(gesture.dy) > 12 && Math.abs(gesture.dy) > Math.abs(gesture.dx),
      onPanResponderRelease: (_event, gesture) => {
        if (Math.abs(gesture.dy) > 48) onClose();
      }
    })
  ).current;

  const menuNaturalHeight = actions.length * ACTION_ROW_HEIGHT + MENU_PADDING * 2;

  const layout = useMemo(() => {
    const bubble = anchor || { x: 16, y: windowHeight / 2, width: windowWidth - 32, height: 64 };
    return focusedMessageLayout({
      bubble,
      viewport: {
        top: insets.top + 8,
        bottom: windowHeight - insets.bottom - 8,
        width: windowWidth
      },
      menuHeight: Math.min(menuNaturalHeight, MENU_VISIBLE_ROWS * ACTION_ROW_HEIGHT + MENU_PADDING * 2),
      reactionsHeight: canReact ? REACTION_STRIP_HEIGHT : 0
    });
  }, [anchor, canReact, insets.bottom, insets.top, menuNaturalHeight, windowHeight, windowWidth]);

  if (!message) return null;

  const mine = Boolean(message.is_mine);
  /**
   * The menu hugs the side the bubble is on, so it reads as belonging to that
   * bubble rather than to the screen. Clamped so a wide bubble near an edge
   * does not push it off.
   */
  const menuWidth = Math.min(268, windowWidth - 32);
  const anchorLeft = anchor?.x ?? 16;
  const anchorRight = anchorLeft + (anchor?.width ?? windowWidth - 32);
  const menuLeft = mine
    ? Math.max(16, Math.min(anchorRight - menuWidth, windowWidth - menuWidth - 16))
    : Math.max(16, Math.min(anchorLeft, windowWidth - menuWidth - 16));

  return (
    <Modal transparent animationType="fade" visible onRequestClose={onClose}>
      {/*
        The backdrop is the dismissal surface. It is a sibling of the focused
        group rather than its parent, so a tap on an action row never has to
        out-compete the backdrop for the gesture.
      */}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("messaging:messageActions.closeMenu", { defaultValue: "Close message menu" })}
        style={styles.backdrop}
        onPress={onClose}
      />
      <View style={styles.group} pointerEvents="box-none" {...pan.panHandlers}>
        {canReact ? (
          <View
            style={[styles.reactionStrip, { top: layout.reactionsTop, left: 16, right: 16 }]}
            accessibilityRole="menu"
          >
            {QUICK_REACTIONS.map((reaction) => (
              <Pressable
                key={reaction}
                accessibilityRole="button"
                accessibilityLabel={t("messaging:messageActions.reactWith", { emoji: reaction, defaultValue: `React with ${reaction}` })}
                style={({ pressed }) => [
                  styles.reactionButton,
                  viewerReaction === reaction && styles.reactionActive,
                  pressed && styles.pressed
                ]}
                onPress={() => onReact(reaction)}
              >
                <Text style={styles.reactionGlyph} allowFontScaling={false}>
                  {reaction}
                </Text>
              </Pressable>
            ))}
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t("messaging:chat.moreReactions", { defaultValue: "More reactions" })}
              style={({ pressed }) => [styles.reactionButton, styles.reactionMore, pressed && styles.pressed]}
              onPress={onReactMore}
            >
              <Ionicons name="add" size={20} color={colors.text} />
            </Pressable>
          </View>
        ) : null}

        {/*
          A facsimile of the bubble, not the bubble itself. The real row is
          behind a modal and cannot be lifted out of the list; redrawing it
          here keeps the message visible while it is being acted on, which is
          what makes the menu feel attached to it.
        */}
        <View
          style={[
            styles.focusBubble,
            mine ? styles.focusBubbleMine : styles.focusBubbleTheirs,
            {
              top: layout.bubbleTop,
              maxHeight: layout.bubbleHeight,
              left: mine ? undefined : 16,
              right: mine ? 16 : undefined,
              maxWidth: windowWidth - 32
            }
          ]}
          pointerEvents="none"
        >
          <Text style={styles.focusBubbleText} numberOfLines={6}>
            {preview}
          </Text>
        </View>

        <View style={[styles.menu, { top: layout.menuTop, left: menuLeft, width: menuWidth, maxHeight: layout.menuHeight }]}>
          <ScrollView
            scrollEnabled={layout.menuScrolls}
            showsVerticalScrollIndicator={layout.menuScrolls}
            contentContainerStyle={styles.menuContent}
          >
            {actions.map((action, index) => (
              <Pressable
                key={action.key}
                accessibilityRole="button"
                accessibilityLabel={action.accessibilityLabel}
                testID={`message-action-${action.key}`}
                style={({ pressed }) => [
                  styles.actionRow,
                  index > 0 && styles.actionRowDivided,
                  pressed && styles.pressed
                ]}
                onPress={() => onAction(action.key)}
              >
                <Text style={[styles.actionLabel, { color: toneColor(action) }]} numberOfLines={1}>
                  {action.i18nKey ? t(action.i18nKey, { defaultValue: action.label }) : action.label}
                </Text>
                {action.icon ? (
                  <Ionicons
                    name={action.icon as React.ComponentProps<typeof Ionicons>["name"]}
                    size={18}
                    color={toneColor(action)}
                  />
                ) : null}
              </Pressable>
            ))}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

/**
 * Destructive actions are red, safety is the safety token, warnings amber,
 * everything else plain. Read from `tone`/`destructive` on the rule rather
 * than from the key, so an action added later is coloured by what it does
 * instead of by whether someone remembered to add it to a list.
 */
function toneColor(action: PulseCommandActionRule) {
  if (action.destructive || action.tone === "danger") return colors.danger;
  if (action.tone === "safety") return colors.safety;
  if (action.tone === "warning") return colors.warning;
  return colors.text;
}

const styles = StyleSheet.create({
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(3, 7, 14, 0.76)"
  },
  group: {
    ...StyleSheet.absoluteFillObject
  },
  reactionStrip: {
    position: "absolute",
    height: REACTION_STRIP_HEIGHT,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 10,
    borderRadius: REACTION_STRIP_HEIGHT / 2,
    backgroundColor: colors.glassStrong,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border
  },
  reactionButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center"
  },
  reactionActive: {
    backgroundColor: colors.signalDim,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.accent
  },
  reactionMore: {
    backgroundColor: colors.signalSoft
  },
  reactionGlyph: {
    fontSize: 24,
    lineHeight: 30
  },
  focusBubble: {
    position: "absolute",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 18,
    overflow: "hidden",
    borderWidth: StyleSheet.hairlineWidth
  },
  focusBubbleMine: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  focusBubbleTheirs: {
    backgroundColor: colors.glassStrong,
    borderColor: colors.border
  },
  focusBubbleText: {
    color: colors.text,
    fontSize: 15,
    lineHeight: 21
  },
  menu: {
    position: "absolute",
    borderRadius: 16,
    overflow: "hidden",
    backgroundColor: colors.glassStrong,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border
  },
  menuContent: {
    paddingVertical: MENU_PADDING
  },
  actionRow: {
    height: ACTION_ROW_HEIGHT,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16
  },
  actionRowDivided: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border
  },
  actionLabel: {
    fontSize: 15,
    fontWeight: "500",
    flexShrink: 1,
    marginRight: FOCUS_GAP
  },
  pressed: {
    opacity: 0.6
  }
});
