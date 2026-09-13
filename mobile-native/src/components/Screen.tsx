import { ReactNode } from "react";
import { ActivityIndicator, ScrollView, StyleProp, StyleSheet, Text, View, ViewStyle, useWindowDimensions } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BOTTOM_NAV_CONTENT_CLEARANCE, useBottomNavScrollVisibility } from "../navigation/BottomNavVisibility";
import { colors } from "../theme/colors";
import { logiNexus, LogiNexusTone, toneColor } from "../theme/logiNexus";
import { storeLight } from "../theme/storeLight";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  title: string;
  subtitle?: string;
  children: ReactNode;
  /**
   * Which backdrop this screen sits on. Defaults to `"shared"` — the app's root
   * `PulseBackground` — because that is right for every screen that is not
   * subject to the business palette lock.
   *
   * See `BUSINESS_SURFACE` below for why the business surfaces need the other
   * one, and why the default has to be `"shared"` rather than the reverse.
   */
  surface?: "shared" | "business";
};

type StatePanelKind = "loading" | "empty" | "offline" | "error" | "success" | "permission" | "unsupported" | "maintenance";

/**
 * The opaque near-black the business surfaces sit on, instead of the shared
 * `PulseBackground`.
 *
 * The seller-facing screens are locked to black, white and green, and
 * `PulseBackground` is an indigo-to-violet field. Left transparent, each of them
 * shows a purple gradient behind and below its cards — which is the violation
 * the lock exists to prevent, and it is the one the user reported.
 *
 * The fill is here rather than in `PulseBackground` because that component is
 * mounted once at the app root (asserted by `navigation/__tests__/
 * backgroundSurfaces.test.ts`) and its gradient is app-wide: recolouring it to
 * satisfy the business screens would repaint all ninety-eight.
 *
 * It is opt-in per caller rather than a property of this shell, and that is not
 * defensiveness — `Screen` genuinely has a non-business caller. `RegionTimeScreen`
 * (language, region and time preferences) uses it too and must keep the shared
 * backdrop. Making the fill unconditional silently blacked that screen out; the
 * caller-list assertion in `backgroundSurfaces.test.ts` is what caught it.
 *
 * The value is `storeLight.bg.headerFrom`, not a new near-black, so these screens
 * match the header of the rebuilt Store rather than approximating it.
 */
const BUSINESS_SURFACE = storeLight.bg.headerFrom;

export function Screen({ title, subtitle, children, surface = "shared" }: Props) {
  const insets = useSafeAreaInsets();
  const bottomNavScroll = useBottomNavScrollVisibility();
  return (
    <ScrollView
      style={surface === "business" ? styles.businessRoot : styles.root}
      contentContainerStyle={[styles.content, { paddingBottom: Math.max(insets.bottom, 18) + BOTTOM_NAV_CONTENT_CLEARANCE }]}
      keyboardShouldPersistTaps="handled"
      onScroll={bottomNavScroll.onScroll}
      onScrollBeginDrag={bottomNavScroll.onScrollBeginDrag}
      scrollEventThrottle={bottomNavScroll.scrollEventThrottle}
    >
      <View style={styles.header}>
        <Text style={styles.title}>{title}</Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      </View>
      {children}
    </ScrollView>
  );
}

export function LogiNexusScreenShell({ children, style, contentStyle, bottomDock = true }: { children: ReactNode; style?: StyleProp<ViewStyle>; contentStyle?: StyleProp<ViewStyle>; bottomDock?: boolean }) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.shell, { paddingTop: 0, paddingBottom: bottomDock ? Math.max(insets.bottom, 12) : 0 }, style]}>
      <View style={[styles.shellContent, contentStyle]}>{children}</View>
    </View>
  );
}

export function LogiNexusScrollContainer({
  children,
  style,
  contentStyle,
  bottomDock = true
}: {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
  contentStyle?: StyleProp<ViewStyle>;
  bottomDock?: boolean;
}) {
  const insets = useSafeAreaInsets();
  const bottomPadding = bottomDock ? Math.max(insets.bottom, 12) + BOTTOM_NAV_CONTENT_CLEARANCE : Math.max(insets.bottom, 12) + 24;
  const bottomNavScroll = useBottomNavScrollVisibility({ enabled: bottomDock });
  return (
    <ScrollView
      style={[styles.root, style]}
      contentContainerStyle={[styles.content, { paddingBottom: bottomPadding }, contentStyle]}
      keyboardShouldPersistTaps="handled"
      onScroll={bottomNavScroll.onScroll}
      onScrollBeginDrag={bottomNavScroll.onScrollBeginDrag}
      scrollEventThrottle={bottomNavScroll.scrollEventThrottle}
    >
      {children}
    </ScrollView>
  );
}

export function LogiNexusSection({ title, subtitle, children, style }: { title: string; subtitle?: string; children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[styles.section, style]}>
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {subtitle ? <Text style={styles.sectionSubtitle}>{subtitle}</Text> : null}
      </View>
      {children}
    </View>
  );
}

export function LogiNexusStatePanel({
  title,
  body,
  state = "empty",
  tone,
  loading,
  children,
  style
}: {
  title: string;
  body?: string;
  state?: StatePanelKind;
  tone?: LogiNexusTone;
  loading?: boolean;
  children?: ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const color = toneColor(tone || toneForState(state));
  return (
    <View style={[styles.statePanel, { borderColor: `${color}80` }, style]}>
      <View style={[styles.stateGlyph, { borderColor: color }]}>
        {loading || state === "loading" ? <ActivityIndicator color={color} /> : <Text style={[styles.stateGlyphText, { color }]}>{glyphForState(state)}</Text>}
      </View>
      <Text style={styles.stateTitle}>{title}</Text>
      {body ? <Text style={styles.stateBody}>{body}</Text> : null}
      {children}
    </View>
  );
}

export function LogiNexusResponsiveColumns({
  children,
  minColumnWidth = 280,
  style
}: {
  children: ReactNode;
  minColumnWidth?: number;
  style?: StyleProp<ViewStyle>;
}) {
  const { width } = useWindowDimensions();
  const columns = width >= minColumnWidth * 3 + 64 ? 3 : width >= minColumnWidth * 2 + 48 ? 2 : 1;
  return <View style={[styles.columns, columns > 1 && styles.columnsWide, style]}>{children}</View>;
}

function toneForState(state: StatePanelKind): LogiNexusTone {
  if (state === "error") return "danger";
  if (state === "offline" || state === "maintenance" || state === "unsupported") return "warning";
  if (state === "success") return "safety";
  if (state === "permission") return "intelligence";
  return "default";
}

function glyphForState(state: StatePanelKind) {
  if (state === "error") return "!";
  if (state === "offline") return "⌁";
  if (state === "success") return "✓";
  if (state === "permission") return "◇";
  if (state === "unsupported") return "↗";
  if (state === "maintenance") return "◌";
  return "·";
}

const styles = createThemedStyles(() => ({
  /**
   * Transparent, not `colors.background`. These two shells are the last opaque
   * layer between the app's root `PulseBackground` and the thirteen screens that
   * use them; a fill here would cover the shared background on its first frame
   * and the symptom would look like the background component being broken rather
   * than something above it being opaque.
   *
   * The shells deliberately do not *render* `PulseBackground` themselves. They
   * cover thirteen of ninety-eight screens, so it would be a partial fix, and it
   * would double up wherever a shell is nested inside a screen that already
   * draws its own atmosphere — which is what `ChatScreen` and `ProfileScreen`
   * do. The layer belongs at the root, once.
   */
  root: {
    flex: 1,
    backgroundColor: "transparent"
  },
  /**
   * `root` plus the opaque business fill — see `BUSINESS_SURFACE` above. Kept as
   * a separate key rather than a fill on `root` because `root` also backs
   * `LogiNexusScrollContainer` and `Screen`'s own default, both of which must
   * stay transparent for the reasons in the note on `root`.
   */
  businessRoot: {
    flex: 1,
    backgroundColor: BUSINESS_SURFACE
  },
  content: {
    padding: 18,
    gap: 14
  },
  columns: {
    gap: logiNexus.spacing.md
  },
  columnsWide: {
    alignItems: "stretch",
    flexDirection: "row",
    flexWrap: "wrap"
  },
  header: {
    gap: 6,
    paddingBottom: 4
  },
  section: {
    gap: logiNexus.spacing.md
  },
  sectionHeader: {
    gap: 4
  },
  sectionSubtitle: {
    ...logiNexus.typography.body,
    color: colors.muted
  },
  sectionTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text
  },
  /** Transparent for the same reason as `root` above. */
  shell: {
    backgroundColor: "transparent",
    flex: 1
  },
  shellContent: {
    flex: 1
  },
  stateBody: {
    ...logiNexus.typography.body,
    color: colors.muted,
    maxWidth: 520,
    textAlign: "center"
  },
  stateGlyph: {
    alignItems: "center",
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 48,
    justifyContent: "center",
    width: 48
  },
  stateGlyphText: {
    fontSize: 22,
    fontWeight: "900"
  },
  statePanel: {
    alignItems: "center",
    alignSelf: "stretch",
    backgroundColor: colors.glassStrong,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    flex: 1,
    gap: logiNexus.spacing.sm,
    justifyContent: "center",
    minHeight: 240,
    padding: logiNexus.spacing.xxl
  },
  stateTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text,
    textAlign: "center"
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "800"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 21
  }
}));
