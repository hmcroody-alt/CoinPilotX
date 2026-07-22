import { useMemo, useState } from "react";
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GlobalNavigationIdentity } from "../navigation/GlobalNavigation";
import { colors } from "../theme/colors";
import { logiNexus, LogiNexusTone } from "../theme/logiNexus";
import { LogiNexusBadge, LogiNexusCard, LogiNexusPanel, LogiNexusSignalIndicator } from "./LogiNexus";
import { masterNavigationSections, MasterNavigationAction, MasterNavigationSection } from "../navigation/masterNavigation";

type Props = {
  visible: boolean;
  identity?: GlobalNavigationIdentity;
  onClose: () => void;
  onOpenRoute: (route: string) => void;
  sections?: MasterNavigationSection[];
};

export function MasterNavigationDrawer({ visible, identity, onClose, onOpenRoute, sections = masterNavigationSections }: Props) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const insets = useSafeAreaInsets();

  const filteredSections = useMemo(() => {
    const clean = query.trim().toLowerCase();
    if (!clean) return sections;
    return sections
      .map((section) => ({
        ...section,
        actions: section.actions.filter((action) =>
          [section.title, action.label, action.description, action.route, action.status, action.badge || ""].join(" ").toLowerCase().includes(clean)
        )
      }))
      .filter((section) => section.actions.length);
  }, [query, sections]);

  function openAction(action: MasterNavigationAction) {
    onClose();
    onOpenRoute(action.route);
  }

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.drawerOverlay}>
        <Pressable accessibilityRole="button" accessibilityLabel="Close navigation drawer" style={styles.drawerScrim} onPress={onClose} />
        <LogiNexusPanel style={[styles.drawerPanel, { paddingTop: insets.top + logiNexus.spacing.md }]} tone="intelligence">
          <View style={styles.drawerHeader}>
            <View style={styles.drawerTitleRow}>
              <LogiNexusSignalIndicator tone="intelligence" />
              <View style={styles.drawerTitleText}>
                <Text style={styles.drawerKicker}>PULSESOC NETWORK</Text>
                <Text style={styles.drawerTitle}>PulseSoc Navigation</Text>
                <Text style={styles.drawerSubtitle}>Search, classify, and route every native subsystem.</Text>
                {identity ? <DrawerIdentity identity={identity} /> : null}
              </View>
            </View>
            <Pressable accessibilityRole="button" accessibilityLabel="Close navigation drawer" style={styles.drawerClose} onPress={onClose}>
              <Text style={styles.drawerCloseText}>x</Text>
            </Pressable>
          </View>
          <View style={styles.searchWrap}>
            <TextInput
              testID="master-navigation-search"
              accessibilityLabel="Search PulseSoc navigation"
              placeholder="Search routes, modules, providers..."
              placeholderTextColor={colors.muted}
              value={query}
              onChangeText={setQuery}
              style={styles.searchInput}
            />
          </View>
          <View style={styles.summaryRow}>
            <LogiNexusBadge label={`${sections.reduce((sum, section) => sum + section.actions.length, 0)} actions`} tone="default" />
            <LogiNexusBadge label="server authoritative" tone="safety" />
          </View>
          <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={[styles.drawerScroll, { paddingBottom: logiNexus.spacing.xxl + insets.bottom }]}>
            {filteredSections.map((section) => {
              const isCollapsed = Boolean(collapsed[section.title]) && !query.trim();
              return (
                <LogiNexusCard key={section.title} style={styles.drawerSection} tone={toneForSection(section.title)}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`${isCollapsed ? "Expand" : "Collapse"} ${section.title}`}
                    style={styles.sectionHeader}
                    onPress={() => setCollapsed((current) => ({ ...current, [section.title]: !current[section.title] }))}
                  >
                    <View style={styles.sectionText}>
                      <Text style={styles.drawerSectionTitle}>{section.title}</Text>
                      <Text style={styles.drawerSectionDescription}>{section.description}</Text>
                    </View>
                    <Text style={styles.sectionCount}>{section.actions.length}</Text>
                  </Pressable>
                  {!isCollapsed
                    ? section.actions.map((action) => (
                        <Pressable
                          key={`${section.title}-${action.route}-${action.label}`}
                          accessibilityRole="button"
                          accessibilityLabel={`Open ${action.label}`}
                          testID={`master-drawer-${action.label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}
                          style={styles.drawerItem}
                          onPress={() => openAction(action)}
                        >
                          <View style={styles.drawerItemMain}>
                            <Text style={styles.drawerItemText}>{action.label}</Text>
                            <Text style={styles.drawerItemDescription}>{action.description}</Text>
                            <Text style={styles.drawerItemRoute}>{action.route}</Text>
                          </View>
                          <View style={styles.drawerItemStatus}>
                            {action.badge ? <Text style={styles.drawerItemBadge}>{action.badge}</Text> : null}
                            <Text style={[styles.drawerStatus, statusStyle(action.status)]}>{action.status}</Text>
                          </View>
                        </Pressable>
                      ))
                    : null}
                </LogiNexusCard>
              );
            })}
            {!filteredSections.length ? (
              <LogiNexusCard tone="warning" style={styles.noResults}>
                <Text style={styles.noResultsTitle}>No navigation signals matched.</Text>
                <Text style={styles.noResultsText}>Try a module, route, or subsystem name.</Text>
              </LogiNexusCard>
            ) : null}
          </ScrollView>
        </LogiNexusPanel>
      </View>
    </Modal>
  );
}

function DrawerIdentity({ identity }: { identity: GlobalNavigationIdentity }) {
  const name = identity.displayName || identity.username || "PulseSoc member";
  const initials =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || "PS";

  return (
    <View style={styles.identityRow} testID="master-drawer-identity">
      <View style={styles.identityAvatar}>
        {identity.avatarUrl ? <Image source={{ uri: identity.avatarUrl }} style={styles.identityImage} /> : <Text style={styles.identityInitials}>{initials}</Text>}
      </View>
      <View style={styles.identityCopy}>
        <Text style={styles.identityName} numberOfLines={1}>
          {name}
        </Text>
        <Text style={styles.identityMeta} numberOfLines={1}>
          {identity.username ? `@${identity.username}` : "Authenticated PulseSoc account"}
        </Text>
      </View>
      {identity.verified ? <LogiNexusBadge label="verified" tone="default" /> : null}
      {identity.premium ? <LogiNexusBadge label="premium" tone="economy" /> : null}
    </View>
  );
}

function statusStyle(status: MasterNavigationAction["status"]) {
  if (status === "provider") return styles.drawerStatusFallback;
  if (status === "gated") return styles.drawerStatusGated;
  if (status === "shell") return styles.drawerStatusShell;
  return styles.drawerStatusNative;
}

function toneForSection(section: string): LogiNexusTone {
  if (section === "Creator / Business" || section === "Content") return "creator";
  if (section === "Economy") return "economy";
  if (section === "Trust") return "safety";
  if (section === "Intelligence") return "intelligence";
  return "default";
}

const styles = StyleSheet.create({
  drawerClose: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    height: 42,
    justifyContent: "center",
    width: 42
  },
  drawerCloseText: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  drawerHeader: {
    alignItems: "flex-start",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    justifyContent: "space-between",
    paddingBottom: logiNexus.spacing.md
  },
  drawerItem: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    justifyContent: "space-between",
    marginTop: logiNexus.spacing.sm,
    minHeight: 70,
    padding: logiNexus.spacing.md
  },
  drawerItemBadge: {
    color: colors.intelligence,
    fontSize: 10,
    fontWeight: "900",
    textAlign: "right",
    textTransform: "uppercase"
  },
  drawerItemDescription: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 3
  },
  drawerItemMain: {
    flex: 1
  },
  drawerItemRoute: {
    color: colors.disabled,
    fontSize: 10,
    fontWeight: "800",
    marginTop: 4
  },
  drawerItemStatus: {
    alignItems: "flex-end",
    gap: 4
  },
  drawerItemText: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  drawerKicker: {
    color: colors.intelligence,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 1.2
  },
  drawerOverlay: {
    backgroundColor: "rgba(1, 6, 14, 0.72)",
    flex: 1,
    flexDirection: "row"
  },
  drawerPanel: {
    borderBottomLeftRadius: 0,
    borderTopLeftRadius: 0,
    maxWidth: 430,
    padding: logiNexus.spacing.lg,
    width: "90%"
  },
  drawerScrim: {
    ...StyleSheet.absoluteFillObject
  },
  drawerScroll: {
    gap: logiNexus.spacing.md,
    paddingBottom: logiNexus.spacing.xxl
  },
  drawerSection: {
    padding: logiNexus.spacing.md
  },
  drawerSectionDescription: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 3
  },
  drawerSectionTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  drawerStatus: {
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    fontSize: 10,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 8,
    paddingVertical: 4,
    textTransform: "uppercase"
  },
  drawerStatusFallback: {
    borderColor: colors.warning,
    color: colors.warning
  },
  drawerStatusGated: {
    borderColor: colors.danger,
    color: colors.danger
  },
  drawerStatusNative: {
    borderColor: colors.accent,
    color: colors.accent
  },
  drawerStatusShell: {
    borderColor: colors.accentStrong,
    color: colors.accentStrong
  },
  drawerSubtitle: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 3
  },
  drawerTitle: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  drawerTitleRow: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: logiNexus.spacing.md
  },
  drawerTitleText: {
    flex: 1
  },
  identityAvatar: {
    alignItems: "center",
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 42,
    justifyContent: "center",
    overflow: "hidden",
    width: 42
  },
  identityCopy: {
    flex: 1,
    minWidth: 0
  },
  identityImage: {
    height: "100%",
    width: "100%"
  },
  identityInitials: {
    ...logiNexus.typography.label,
    color: colors.text
  },
  identityMeta: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  identityName: {
    ...logiNexus.typography.body,
    color: colors.text
  },
  identityRow: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexDirection: "row",
    gap: logiNexus.spacing.sm,
    marginTop: logiNexus.spacing.sm,
    padding: logiNexus.spacing.sm
  },
  noResults: {
    gap: logiNexus.spacing.sm
  },
  noResultsText: {
    color: colors.muted,
    fontSize: 13
  },
  noResultsTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  searchInput: {
    color: colors.text,
    fontSize: 15,
    minHeight: 46,
    paddingHorizontal: logiNexus.spacing.md
  },
  searchWrap: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    marginTop: logiNexus.spacing.md,
    overflow: "hidden"
  },
  sectionCount: {
    color: colors.accent,
    fontSize: 14,
    fontWeight: "900"
  },
  sectionHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    justifyContent: "space-between"
  },
  sectionText: {
    flex: 1
  },
  summaryRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: logiNexus.spacing.sm,
    marginVertical: logiNexus.spacing.md
  }
});
