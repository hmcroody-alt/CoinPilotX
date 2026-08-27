/**
 * Progress Center — the PulseSoc Founding Path and what comes after it.
 *
 * Twelve layers on one scroll: overview, Live Creator standing, unlocks,
 * referrals, referral detail, invite, missions, Founding Member, legacy,
 * insights, activity, how it works, FAQ. They are sections rather than twelve
 * routes because the whole point of the surface is that a member can see the
 * relationship between their invites, their ladder position and what they have
 * unlocked without navigating between screens to hold two numbers in their head.
 *
 * Three rules this file follows without exception:
 *
 * 1. **Nothing is calculated here.** Certified counts, percentages, unlock
 *    state, Live eligibility and badge eligibility all arrive decided by the
 *    server. The only arithmetic below is `progressBarPercent` clamping a
 *    width. If this screen could compute a count it would eventually disagree
 *    with the server, and the member would trust the wrong one — the one on
 *    their own phone.
 *
 * 2. **Gold means earned.** `progressTheme.gold` is applied only to state the
 *    server reports as `UNLOCKED` / `COMPLETE`. A rung that is merely reached
 *    but not yet awarded renders violet, not gold, because gold on an unearned
 *    rung is the one lie this surface cannot afford.
 *
 * 3. **This is the owner's screen.** It refuses on a visitor route rather than
 *    rendering the viewer's own referrals under someone else's name, matching
 *    `GrowthCenterScreen`. The tile is already hidden from visitors and the
 *    server accepts no target user, so this is the third independent layer —
 *    kept because the first two are about routing and this one is about what
 *    ends up on screen.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, Share, StyleSheet, Text, View } from "react-native";
import {
  getProgressActivity,
  getProgressFaq,
  getProgressHowItWorks,
  getProgressInvite,
  getProgressMilestones,
  getProgressMissions,
  getProgressOverview,
  getProgressReferralDetail,
  getProgressReferrals,
  progressBarPercent,
  type ProgressActivityItem,
  type ProgressChecklistItem,
  type ProgressFaq,
  type ProgressHowItWorks,
  type ProgressInvite,
  type ProgressMilestone,
  type ProgressMilestones,
  type ProgressMission,
  type ProgressMissions,
  type ProgressOverview,
  type ProgressReferral,
  type ProgressReferralDetail,
  type ProgressReferralTab
} from "../api/progress";
import { useFormatters, useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { progressTheme } from "../theme/progressTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "ProgressCenter">;

/**
 * Server keys → catalog keys.
 *
 * The server ships stable identifiers (`progress.missions.hostFirstLive`,
 * `progress.faq.whatCounts`) and never display copy, so the app owns every
 * translated string. Only the first dot becomes the namespace separator.
 */
function catalogKey(serverKey: string): string {
  const key = String(serverKey || "");
  return key.startsWith("progress.") ? `progress:${key.slice("progress.".length)}` : key;
}

export function ProgressCenterScreen({ navigation, route }: Props) {
  const { authState } = useAuth();
  const { t } = useTranslation();
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);

  const [overview, setOverview] = useState<ProgressOverview | null>(null);
  const [milestones, setMilestones] = useState<ProgressMilestones | null>(null);
  const [missions, setMissions] = useState<ProgressMissions | null>(null);
  const [activity, setActivity] = useState<ProgressActivityItem[]>([]);
  const [invite, setInvite] = useState<ProgressInvite | null>(null);
  const [howItWorks, setHowItWorks] = useState<ProgressHowItWorks | null>(null);
  const [faq, setFaq] = useState<ProgressFaq | null>(null);

  const [tab, setTab] = useState<ProgressReferralTab>("all");
  const [referrals, setReferrals] = useState<ProgressReferral[]>([]);
  const [openRef, setOpenRef] = useState<string>(route?.params?.ref || "");
  const [detail, setDetail] = useState<ProgressReferralDetail | null>(null);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      // The static layers (how it works, FAQ) are allowed to fail without
      // taking the screen down — they are reference copy, not state. The live
      // layers are not: a member must never be shown a stale or partial count.
      const [nextOverview, nextMilestones, nextMissions] = await Promise.all([
        getProgressOverview(),
        getProgressMilestones(),
        getProgressMissions()
      ]);
      setOverview(nextOverview);
      setMilestones(nextMilestones);
      setMissions(nextMissions);

      const [nextActivity, nextInvite, nextHow, nextFaq] = await Promise.all([
        getProgressActivity().catch(() => null),
        getProgressInvite().catch(() => null),
        getProgressHowItWorks().catch(() => null),
        getProgressFaq().catch(() => null)
      ]);
      setActivity(nextActivity?.activity || []);
      setInvite(nextInvite);
      setHowItWorks(nextHow);
      setFaq(nextFaq);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : t("progress:loadError"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  const loadReferrals = useCallback(async (nextTab: ProgressReferralTab) => {
    try {
      const list = await getProgressReferrals(nextTab);
      setReferrals(list.referrals || []);
    } catch {
      setReferrals([]);
    }
  }, []);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    load("initial");
  }, [load, routeContext.isOwnProfile]);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    loadReferrals(tab);
  }, [loadReferrals, tab, routeContext.isOwnProfile]);

  useEffect(() => {
    if (!openRef) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    getProgressReferralDetail(openRef)
      .then((next) => { if (!cancelled) setDetail(next); })
      .catch(() => { if (!cancelled) setDetail(null); });
    return () => { cancelled = true; };
  }, [openRef]);

  const campaignTarget = overview?.path?.target ?? overview?.campaign?.target ?? 0;
  const liveThreshold = howItWorks?.live_threshold ?? 0;

  const onCopy = useCallback(async () => {
    const link = invite?.referral_link;
    if (!link) return;
    await Clipboard.setStringAsync(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [invite?.referral_link]);

  const onShare = useCallback(async () => {
    const link = invite?.referral_link;
    if (!link) return;
    await Share.share({ message: link }).catch(() => undefined);
  }, [invite?.referral_link]);

  // Both routes already exist and are reachable from Creator Studio and the
  // Live tab today. The card below is only ever rendered once the server has
  // granted the unlock, so neither action can be a dead end.
  const onStartLive = useCallback(() => {
    navigation.navigate("LiveStudio", { title: "Live Studio" });
  }, [navigation]);

  const onExploreLive = useCallback(() => {
    navigation.navigate("Tabs", { screen: "Live" });
  }, [navigation]);

  // Visitor route with no visitor variant. All hooks above have already run.
  if (!routeContext.isOwnProfile) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>{PRIVATE_CONTENT_MESSAGE}</Text>
      </View>
    );
  }

  if (loading && !overview) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={progressTheme.violet} />
        <Text style={styles.centerText}>{t("progress:loading")}</Text>
      </View>
    );
  }

  if (error && !overview) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>{t("progress:loadError")}</Text>
        <Pressable accessibilityRole="button" style={styles.retry} onPress={() => load("initial")}>
          <Text style={styles.retryText}>{t("progress:retry")}</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} tintColor={progressTheme.violet} />}
    >
      {/* 1 — Founding Path overview */}
      <OverviewSection overview={overview} />

      {/* 2 — Live Creator standing */}
      <LiveCreatorSection overview={overview} onStartLive={onStartLive} onExploreLive={onExploreLive} />

      {/* 3 — Your unlocks */}
      <UnlocksSection milestones={milestones?.milestones || []} />

      {/* 4 — Referrals list, 5 — Referral detail */}
      <ReferralsSection
        referrals={referrals}
        tab={tab}
        onTab={setTab}
        openRef={openRef}
        detail={detail}
        onOpen={setOpenRef}
      />

      {/* 6 — Invite Friends */}
      <InviteSection invite={invite} copied={copied} onCopy={onCopy} onShare={onShare} />

      {/* 7 — Missions */}
      <MissionsSection missions={missions} />

      {/* 8 — Founding Member */}
      <FoundingSection overview={overview} target={campaignTarget} />

      {/* 9 — Your founding legacy */}
      <LegacySection overview={overview} />

      {/* 10 — Insights */}
      <InsightsSection overview={overview} />

      {/* 11 — Activity Feed */}
      <ActivitySection activity={activity} />

      {/* 12 — How It Works */}
      <HowItWorksSection howItWorks={howItWorks} />

      {/* 13 — FAQ */}
      <FaqSection faq={faq} target={campaignTarget} live={liveThreshold} />

      <Text style={styles.footnote}>{t("progress:privateNote")}</Text>
      {overview?.not_verification ? <Text style={styles.footnote}>{overview.not_verification}</Text> : null}
    </ScrollView>
  );
}

/* -------------------------------------------------------------------------- *
 * 1 — Founding Path overview
 * -------------------------------------------------------------------------- */

function OverviewSection({ overview }: { overview: ProgressOverview | null }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const path = overview?.path;
  const certified = path?.certified ?? 0;
  const invites = overview?.invites;
  const nextUnlock = overview?.next_unlock;
  // Founding Member is an award row, not a count. The hero only turns gold once
  // the server says the rung was actually granted.
  const founding = Boolean(overview?.founding_member);

  return (
    <View style={[styles.hero, founding && styles.heroComplete]}>
      <Text style={styles.heroEyebrow}>{t("progress:subtitle")}</Text>
      <Text style={styles.heroValue}>{t("progress:overview.certified", { count: certified })}</Text>

      {nextUnlock ? (
        <>
          <Text style={styles.heroNext}>{t("progress:overview.nextUnlock", { label: nextUnlock.label })}</Text>
          <ProgressBar percent={progressBarPercent(nextUnlock.percent)} tone="violet" />
          <Text style={styles.heroCaption}>
            {t("progress:overview.unlockCounter", { current: nextUnlock.current, target: nextUnlock.threshold })}
            {" · "}
            {t("progress:overview.unlockRemaining", { count: nextUnlock.remaining })}
          </Text>
        </>
      ) : (
        <>
          <ProgressBar percent={progressBarPercent(path?.percent)} tone={founding ? "gold" : "violet"} />
          <Text style={[styles.heroNext, founding && styles.gold]}>
            {founding ? t("progress:overview.foundingMember") : t("progress:overview.complete")}
          </Text>
        </>
      )}

      <Text style={styles.heroCaption}>
        {t("progress:overview.pathCounter", { current: certified, target: path?.target ?? 0 })}
      </Text>

      <View style={styles.breakdownRow}>
        <Stat label={t("progress:breakdown.invited")} value={fmt.count(invites?.invited ?? 0)} />
        <Stat label={t("progress:breakdown.inProgress")} value={fmt.count(invites?.in_progress ?? 0)} />
        <Stat label={t("progress:breakdown.certified")} value={fmt.count(invites?.certified ?? certified)} tone="gold" />
      </View>
    </View>
  );
}

/* -------------------------------------------------------------------------- *
 * 2 — Live Creator standing
 * -------------------------------------------------------------------------- */

function LiveCreatorSection({
  overview, onStartLive, onExploreLive
}: { overview: ProgressOverview | null; onStartLive: () => void; onExploreLive: () => void }) {
  const { t } = useTranslation();
  // Server-decided, from an award row. The client never infers Live access from
  // a count, so the buttons below cannot appear before the privilege is real.
  if (!overview?.live_creator) return null;
  return (
    <View style={styles.liveCard}>
      <View style={styles.liveIcon}>
        <Ionicons name="radio" size={22} color={progressTheme.gold} />
      </View>
      <Text style={styles.liveEyebrow}>{t("progress:liveCreator.eyebrow")}</Text>
      <Text style={[styles.liveTitle, styles.gold]}>{t("progress:liveCreator.heading")}</Text>
      <Text style={styles.body}>{t("progress:liveCreator.body")}</Text>
      <View style={styles.actions}>
        <Pressable accessibilityRole="button" style={styles.primaryAction} onPress={onStartLive}>
          <Ionicons name="videocam-outline" size={15} color={colors.background} />
          <Text style={styles.primaryActionText}>{t("progress:liveCreator.start")}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryAction} onPress={onExploreLive}>
          <Ionicons name="compass-outline" size={15} color={progressTheme.violet} />
          <Text style={styles.secondaryActionText}>{t("progress:liveCreator.explore")}</Text>
        </Pressable>
      </View>
    </View>
  );
}

/* -------------------------------------------------------------------------- *
 * 3 — Your unlocks
 * -------------------------------------------------------------------------- */

/** Icon per unlock kind. Falls back to the ladder icon for a kind this build
 *  does not know yet, so a rung added server-side still renders. */
const UNLOCK_ICON: Record<string, keyof typeof Ionicons.glyphMap> = {
  live_access: "radio",
  creator_perk: "color-palette",
  recognition: "star",
  founding_status: "ribbon",
  one_time: "trophy"
};

function UnlocksSection({ milestones }: { milestones: ProgressMilestone[] }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  return (
    <Section title={t("progress:sections.unlocks")}>
      {milestones.map((milestone) => {
        // Gold only for UNLOCKED. A threshold that is reached but whose award
        // row has not been written yet is IN_PROGRESS and stays violet.
        const unlocked = milestone.state === "UNLOCKED";
        const locked = milestone.state === "LOCKED";
        return (
          <View key={milestone.key} style={[styles.unlockCard, unlocked && styles.unlockCardEarned, locked && styles.unlockCardLocked]}>
            <View style={styles.row}>
              <View style={[styles.rowIcon, unlocked ? styles.rowIconGold : locked ? styles.rowIconLocked : styles.rowIconViolet]}>
                <Ionicons
                  name={UNLOCK_ICON[milestone.kind] || "trophy"}
                  size={16}
                  color={unlocked ? progressTheme.gold : locked ? colors.muted : progressTheme.violet}
                />
              </View>
              <View style={styles.rowBody}>
                <Text style={[styles.rowTitle, unlocked && styles.gold]}>{milestone.label}</Text>
                <Text style={styles.rowMeta}>
                  {t("progress:unlocks.threshold", { count: milestone.threshold })}
                  {unlocked && milestone.earned_at
                    ? ` · ${t("progress:unlocks.unlockedOn", { date: fmt.date(milestone.earned_at) })}`
                    : ""}
                </Text>
              </View>
              <Text style={[styles.rowState, unlocked && styles.gold]}>
                {unlocked
                  ? t("progress:unlocks.unlocked")
                  : milestone.state === "IN_PROGRESS"
                    ? t("progress:unlocks.inProgress")
                    : t("progress:unlocks.locked")}
              </Text>
            </View>
            {milestone.description ? <Text style={styles.rowMeta}>{milestone.description}</Text> : null}
          </View>
        );
      })}
      <Text style={styles.note}>{t("progress:unlocks.note")}</Text>
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * 4 & 5 — Referrals list and certification checklist
 * -------------------------------------------------------------------------- */

const TABS: ProgressReferralTab[] = ["all", "qualified", "pending", "review"];
const TAB_LABEL: Record<ProgressReferralTab, string> = {
  all: "progress:referrals.tabAll",
  qualified: "progress:referrals.tabCertified",
  pending: "progress:referrals.tabPending",
  review: "progress:referrals.tabReview"
};

function ReferralsSection({
  referrals, tab, onTab, openRef, detail, onOpen
}: {
  referrals: ProgressReferral[];
  tab: ProgressReferralTab;
  onTab: (next: ProgressReferralTab) => void;
  openRef: string;
  detail: ProgressReferralDetail | null;
  onOpen: (ref: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <Section title={t("progress:sections.referrals")}>
      <View style={styles.tabs}>
        {TABS.map((key) => (
          <Pressable
            key={key}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === key }}
            style={[styles.tab, tab === key && styles.tabActive]}
            onPress={() => onTab(key)}
          >
            <Text style={[styles.tabText, tab === key && styles.tabTextActive]}>{t(TAB_LABEL[key])}</Text>
          </Pressable>
        ))}
      </View>

      {referrals.length === 0 ? (
        <Empty title={t("progress:referrals.empty")} body={t("progress:referrals.emptyBody")} />
      ) : (
        referrals.map((referral) => (
          <View key={referral.ref}>
            <Pressable
              accessibilityRole="button"
              style={styles.row}
              onPress={() => onOpen(openRef === referral.ref ? "" : referral.ref)}
            >
              <View style={[styles.rowIcon, referral.counts ? styles.rowIconGold : styles.rowIconViolet]}>
                <Ionicons
                  name={referral.counts ? "checkmark-circle" : "time-outline"}
                  size={16}
                  color={referral.counts ? progressTheme.gold : progressTheme.violet}
                />
              </View>
              <View style={styles.rowBody}>
                <Text style={styles.rowTitle}>{referral.name || t("progress:referrals.anonymous")}</Text>
                <Text style={styles.rowMeta}>{referral.summary}</Text>
              </View>
              <Text style={[styles.rowState, referral.counts && styles.gold]}>
                {referral.counts ? t("progress:referrals.counts") : t("progress:referrals.notCertifiedYet")}
              </Text>
            </Pressable>

            {openRef === referral.ref && detail ? <Checklist detail={detail} /> : null}
          </View>
        ))
      )}
    </Section>
  );
}

/**
 * Localized from `item.key`, never from `item.label`.
 *
 * The server sends an English `label` as a fallback for a key this build does
 * not recognize yet, so a checklist item added server-side still renders
 * something truthful instead of disappearing.
 */
const CHECKLIST_KEYS: Record<string, string> = {
  signed_up: "progress:checklist.signedUp",
  profile: "progress:checklist.profile",
  standing: "progress:checklist.standing",
  checks: "progress:checklist.checks"
};

function checklistLabel(item: ProgressChecklistItem, t: (key: string, options?: Record<string, unknown>) => string): string {
  const mapped = CHECKLIST_KEYS[item.key];
  if (mapped) return t(mapped);
  if (item.key.startsWith("posting_day_")) {
    return item.done ? t("progress:checklist.postingDay") : t("progress:checklist.postingDayNeeded");
  }
  return item.label;
}

function Checklist({ detail }: { detail: ProgressReferralDetail }) {
  const { t } = useTranslation();
  const inReview = String(detail.state || "") === "REVIEW_REQUIRED";
  return (
    <View style={styles.checklist}>
      <Text style={styles.checklistHeading}>{t("progress:referrals.detailHeading")}</Text>
      {(detail.checklist || []).map((item) => (
        <View key={item.key} style={styles.checkRow}>
          <Ionicons
            name={item.done ? "checkmark-circle" : "ellipse-outline"}
            size={15}
            color={item.done ? progressTheme.gold : progressTheme.state.pending}
          />
          <Text style={[styles.checkText, !item.done && styles.checkTextPending]}>
            {checklistLabel(item, t)}
          </Text>
        </View>
      ))}
      {inReview ? <Text style={styles.checklistNote}>{t("progress:checklist.reviewNote")}</Text> : null}
      {/*
        The security posture is stated, never itemized. Naming which signal
        fired would tell someone farming accounts exactly what to avoid, and
        would tell an innocent member they were suspected of something.
      */}
      <Text style={styles.checklistNote}>{t("progress:checklist.noSecurityDetail")}</Text>
    </View>
  );
}

/* -------------------------------------------------------------------------- *
 * 6 — Invite Friends
 * -------------------------------------------------------------------------- */

function InviteSection({
  invite, copied, onCopy, onShare
}: { invite: ProgressInvite | null; copied: boolean; onCopy: () => void; onShare: () => void }) {
  const { t } = useTranslation();
  const link = invite?.referral_link || "";
  return (
    <Section title={t("progress:sections.invite")}>
      <Text style={styles.body}>{t("progress:invite.body")}</Text>
      {link ? (
        <>
          <Text style={styles.label}>{t("progress:invite.yourLink")}</Text>
          <Text style={styles.link} numberOfLines={1}>{link}</Text>
          <View style={styles.actions}>
            <Pressable accessibilityRole="button" style={styles.primaryAction} onPress={onCopy}>
              <Ionicons name={copied ? "checkmark" : "copy-outline"} size={15} color={colors.background} />
              <Text style={styles.primaryActionText}>
                {copied ? t("progress:invite.copied") : t("progress:invite.copy")}
              </Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={styles.secondaryAction} onPress={onShare}>
              <Ionicons name="share-outline" size={15} color={progressTheme.violet} />
              <Text style={styles.secondaryActionText}>{t("progress:invite.share")}</Text>
            </Pressable>
          </View>
        </>
      ) : (
        <Text style={styles.body}>{t("progress:invite.unavailable")}</Text>
      )}
      <Text style={styles.note}>{t("progress:invite.fairness")}</Text>
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * 7 — Missions
 * -------------------------------------------------------------------------- */

function MissionsSection({ missions }: { missions: ProgressMissions | null }) {
  const { t } = useTranslation();
  const items = missions?.missions || [];
  const track = missions?.track === "newcomer"
    ? t("progress:missions.trackNewcomer")
    : t("progress:missions.trackCreator");

  return (
    <Section title={t("progress:sections.missions")}>
      <Text style={styles.label}>{track}</Text>
      {items.length === 0 ? (
        <Empty title={t("progress:missions.empty")} body="" />
      ) : (
        items.map((mission: ProgressMission) => {
          const done = mission.status === "COMPLETE";
          return (
            <View key={mission.mission_id} style={styles.missionRow}>
              <View style={styles.rowBody}>
                <Text style={[styles.rowTitle, done && styles.gold]}>{t(catalogKey(mission.title_key))}</Text>
                <Text style={styles.rowMeta}>
                  {t("progress:missions.progress", { current: mission.current_progress, target: mission.target })}
                  {/*
                    Surfaced rather than hidden: an unmeasurable objective shows
                    a recorded number, not a live one, and a bar that looks live
                    when it is not is a promise the app cannot keep.
                  */}
                  {mission.measurable ? "" : ` · ${t("progress:missions.notTracked")}`}
                </Text>
                <ProgressBar
                  percent={mission.target > 0 ? (mission.current_progress / mission.target) * 100 : 0}
                  tone={done ? "gold" : "violet"}
                />
              </View>
            </View>
          );
        })
      )}
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * 8 — Founding Member
 * -------------------------------------------------------------------------- */

function FoundingSection({ overview, target }: { overview: ProgressOverview | null; target: number }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  // Server-decided. The client never infers the standing from the count,
  // because the count and the award are separate facts.
  const earned = Boolean(overview?.founding_member);
  const founding = overview?.founding;
  return (
    <Section title={t("progress:sections.founding")}>
      <View style={[styles.badge, earned && styles.badgeEarned]}>
        <Ionicons name={earned ? "ribbon" : "ribbon-outline"} size={26} color={earned ? progressTheme.gold : colors.muted} />
        <Text style={[styles.badgeTitle, earned && styles.gold]}>{t("progress:founding.heading")}</Text>
        {earned && founding ? (
          <>
            <Text style={[styles.badgeGeneration, styles.gold]}>{t("progress:founding.generation")}</Text>
            {/*
              The number is the award row's position, not a minted counter, and
              it is only rendered when the server sends one. Nothing here
              invents a rank to fill the space.
            */}
            {founding.founding_number ? (
              <Text style={styles.badgeNumber}>
                {t("progress:founding.number", { number: fmt.count(founding.founding_number) })}
              </Text>
            ) : null}
            {founding.member_since ? (
              <Text style={styles.note}>
                {t("progress:founding.memberSince", { date: fmt.date(founding.member_since) })}
              </Text>
            ) : null}
          </>
        ) : null}
        <Text style={styles.body}>
          {earned ? t("progress:founding.earnedBody") : t("progress:founding.lockedBody", { target })}
        </Text>
      </View>
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * 9 — Your founding legacy
 * -------------------------------------------------------------------------- */

function LegacySection({ overview }: { overview: ProgressOverview | null }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const legacy = overview?.legacy;
  const certified = legacy?.certified ?? 0;
  // Only real rows are reported. There is no referral graph behind this and
  // none is inferred, so an empty legacy stays empty rather than filling with
  // plausible-looking numbers.
  const started = Boolean(legacy?.first_invite_at) || certified > 0;

  return (
    <Section title={t("progress:sections.legacy")}>
      {started ? (
        <>
          <View style={styles.breakdownRow}>
            <Stat label={t("progress:legacy.certified")} value={fmt.count(certified)} tone="gold" />
            <Stat
              label={t("progress:legacy.firstInvite")}
              value={legacy?.first_invite_at ? fmt.date(legacy.first_invite_at) : t("progress:legacy.noDate")}
            />
            <Stat
              label={t("progress:legacy.latestCertified")}
              value={legacy?.latest_certified_at ? fmt.date(legacy.latest_certified_at) : t("progress:legacy.noDate")}
            />
          </View>
          <Text style={styles.note}>{t("progress:legacy.note")}</Text>
        </>
      ) : (
        <Empty title={t("progress:legacy.empty")} body={t("progress:legacy.emptyBody")} />
      )}
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * 10 — Insights
 * -------------------------------------------------------------------------- */

function InsightsSection({ overview }: { overview: ProgressOverview | null }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const invites = overview?.invites || {};
  const certified = invites.certified ?? overview?.path?.certified ?? 0;
  const inProgress = invites.in_progress ?? 0;
  const invited = invites.invited ?? 0;
  const hasActivity = certified + inProgress + invited > 0;

  return (
    <Section title={t("progress:sections.insights")}>
      {hasActivity ? (
        <View style={styles.breakdownRow}>
          <Stat label={t("progress:insights.certified")} value={fmt.count(certified)} tone="gold" />
          <Stat label={t("progress:insights.stillGoing")} value={fmt.count(inProgress)} />
          <Stat label={t("progress:insights.inReview")} value={fmt.count(overview?.breakdown?.in_review ?? 0)} tone="review" />
        </View>
      ) : (
        <Empty title={t("progress:insights.empty")} body={t("progress:insights.emptyBody")} />
      )}
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * 11 — Activity Feed
 * -------------------------------------------------------------------------- */

const ACTIVITY_LABEL: Record<string, string> = {
  referral_signed_up: "progress:activity.referralSignedUp",
  referral_qualified: "progress:activity.referralCertified",
  milestone_earned: "progress:activity.unlockEarned"
};

function ActivitySection({ activity }: { activity: ProgressActivityItem[] }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  return (
    <Section title={t("progress:sections.activity")}>
      {activity.length === 0 ? (
        <Empty title={t("progress:activity.empty")} body="" />
      ) : (
        activity.map((item, index) => (
          <View key={`${item.event_type}-${item.created_at}-${index}`} style={styles.activityRow}>
            <Text style={styles.rowTitle}>
              {t(ACTIVITY_LABEL[item.event_type] || "progress:activity.genericEvent", {
                name: item.name || t("progress:referrals.anonymous")
              })}
            </Text>
            <Text style={styles.rowMeta}>{fmt.relative(item.created_at)}</Text>
          </View>
        ))
      )}
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * 12 — How It Works
 * -------------------------------------------------------------------------- */

const STEP_LABEL: Record<string, string> = {
  invite: "progress:howItWorks.invite",
  join: "progress:howItWorks.join",
  profile: "progress:howItWorks.profile",
  post_two_days: "progress:howItWorks.postTwoDays",
  qualified: "progress:howItWorks.certified"
};

function HowItWorksSection({ howItWorks }: { howItWorks: ProgressHowItWorks | null }) {
  const { t } = useTranslation();
  const steps = howItWorks?.steps || [];
  const days = howItWorks?.required_posting_days ?? 2;
  return (
    <Section title={t("progress:sections.howItWorks")}>
      {steps.map((step) => (
        <View key={step.key} style={styles.stepRow}>
          <View style={styles.stepIndex}>
            <Text style={styles.stepIndexText}>{step.order}</Text>
          </View>
          <Text style={styles.body}>
            {t(STEP_LABEL[step.key] || "progress:howItWorks.certified", { count: days })}
          </Text>
        </View>
      ))}
      {/*
        The fairness note is not fine print. Shared households, offices, schools
        and CGNAT are explicitly not fraud, and saying so here is what stops an
        honest member from reading a review as an accusation.
      */}
      <Text style={styles.note}>{t("progress:howItWorks.fairness")}</Text>
      <Text style={styles.note}>{t("progress:howItWorks.serverNote")}</Text>
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * 13 — FAQ
 * -------------------------------------------------------------------------- */

function FaqSection({ faq, target, live }: { faq: ProgressFaq | null; target: number; live: number }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<string>("");
  const items = faq?.faq || [];
  return (
    <Section title={t("progress:sections.faq")}>
      {items.map((item) => {
        const key = catalogKey(item.key);
        // Questions live beside their answers under the same leaf name, so a
        // server key the app has no question for still renders — the answer
        // becomes the row title rather than the row disappearing.
        const leaf = item.key.split(".").pop() || item.key;
        const expanded = open === item.key;
        return (
          <Pressable
            key={item.key}
            accessibilityRole="button"
            accessibilityState={{ expanded }}
            style={styles.faqRow}
            onPress={() => setOpen(expanded ? "" : item.key)}
          >
            <View style={styles.faqHeader}>
              <Text style={styles.faqQuestion}>
                {t(`progress:faq.questions.${leaf}`, { defaultValue: t(key, { target, live }) })}
              </Text>
              <Ionicons name={expanded ? "chevron-up" : "chevron-down"} size={15} color={colors.muted} />
            </View>
            {expanded ? (
              <Text style={styles.body}>{t(key, { target, live })}</Text>
            ) : null}
          </Pressable>
        );
      })}
    </Section>
  );
}

/* -------------------------------------------------------------------------- *
 * Shared pieces
 * -------------------------------------------------------------------------- */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function ProgressBar({ percent, tone }: { percent: number; tone: "violet" | "gold" }) {
  const width = progressBarPercent(percent);
  return (
    <View style={styles.barTrack}>
      <View
        style={[
          styles.barFill,
          { width: `${width}%`, backgroundColor: tone === "gold" ? progressTheme.gold : progressTheme.violet }
        ]}
      />
    </View>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "gold" | "review" }) {
  return (
    <View style={styles.stat}>
      <Text
        style={[
          styles.statValue,
          tone === "gold" && styles.gold,
          tone === "review" && { color: progressTheme.state.review }
        ]}
        numberOfLines={1}
      >
        {value}
      </Text>
      <Text style={styles.statLabel} numberOfLines={2}>{label}</Text>
    </View>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>{title}</Text>
      {body ? <Text style={styles.emptyBody}>{body}</Text> : null}
    </View>
  );
}

const styles = createThemedStyles(() => ({
  screen: { backgroundColor: colors.background, flex: 1 },
  content: { gap: 14, padding: 14, paddingBottom: 40 },
  center: { alignItems: "center", backgroundColor: colors.background, flex: 1, gap: 10, justifyContent: "center", padding: 24 },
  centerText: { color: colors.muted, fontSize: 14, textAlign: "center" },
  retry: {
    borderColor: progressTheme.violetBorder,
    borderRadius: progressTheme.radius.chip,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: progressTheme.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  retryText: { color: progressTheme.violet, fontSize: 14, fontWeight: "600" },

  hero: {
    backgroundColor: progressTheme.violetSoft,
    borderColor: progressTheme.violetBorder,
    borderRadius: progressTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 16
  },
  heroComplete: { backgroundColor: progressTheme.goldSoft, borderColor: progressTheme.goldBorder },
  heroEyebrow: { color: progressTheme.violet, fontSize: 12, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase" },
  heroValue: { color: colors.text, fontSize: 24, fontWeight: "700" },
  heroCaption: { color: colors.muted, fontSize: 13 },
  heroNext: { color: colors.text, fontSize: 13, fontWeight: "600" },

  liveCard: {
    alignItems: "center",
    backgroundColor: progressTheme.goldSoft,
    borderColor: progressTheme.goldBorder,
    borderRadius: progressTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 16
  },
  liveIcon: {
    alignItems: "center",
    backgroundColor: progressTheme.goldSoft,
    borderColor: progressTheme.goldBorder,
    borderRadius: progressTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  liveEyebrow: { color: progressTheme.gold, fontSize: 11, fontWeight: "700", letterSpacing: 0.8, textTransform: "uppercase" },
  liveTitle: { fontSize: 18, fontWeight: "700", textAlign: "center" },

  section: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: progressTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    padding: 14
  },
  sectionTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },

  row: { alignItems: "center", flexDirection: "row", gap: 10, minHeight: progressTheme.tapTarget, paddingVertical: 6 },
  rowIcon: { alignItems: "center", borderRadius: progressTheme.radius.chip, borderWidth: StyleSheet.hairlineWidth, height: 30, justifyContent: "center", width: 30 },
  rowIconViolet: { backgroundColor: progressTheme.violetSoft, borderColor: progressTheme.violetBorder },
  rowIconGold: { backgroundColor: progressTheme.goldSoft, borderColor: progressTheme.goldBorder },
  rowIconLocked: { backgroundColor: colors.surfaceRaised, borderColor: colors.border },
  rowBody: { flex: 1, gap: 3 },
  rowTitle: { color: colors.text, fontSize: 14, fontWeight: "600" },
  rowMeta: { color: colors.muted, fontSize: 12 },
  rowState: { color: colors.muted, fontSize: 11, maxWidth: 96, textAlign: "right" },

  unlockCard: {
    borderColor: colors.border,
    borderRadius: progressTheme.radius.tile,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 4
  },
  unlockCardEarned: { backgroundColor: progressTheme.goldSoft, borderColor: progressTheme.goldBorder },
  unlockCardLocked: { opacity: 0.72 },

  gold: { color: progressTheme.gold },

  barTrack: { backgroundColor: colors.surfaceRaised, borderRadius: progressTheme.radius.chip, height: 6, overflow: "hidden", width: "100%" },
  barFill: { borderRadius: progressTheme.radius.chip, height: 6 },

  breakdownRow: { flexDirection: "row", gap: 10, marginTop: 4 },
  stat: { flex: 1, gap: 2 },
  statValue: { color: colors.text, fontSize: 17, fontWeight: "700" },
  statLabel: { color: colors.muted, fontSize: 11 },

  tabs: { flexDirection: "row", gap: 6 },
  tab: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: progressTheme.radius.chip,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    justifyContent: "center",
    minHeight: 34,
    paddingHorizontal: 8
  },
  tabActive: { backgroundColor: progressTheme.violetSoft, borderColor: progressTheme.violetBorder },
  tabText: { color: colors.muted, fontSize: 12 },
  tabTextActive: { color: progressTheme.violet, fontWeight: "700" },

  checklist: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: progressTheme.radius.tile,
    gap: 7,
    marginBottom: 6,
    padding: 12
  },
  checklistHeading: { color: colors.text, fontSize: 13, fontWeight: "700" },
  checkRow: { alignItems: "center", flexDirection: "row", gap: 8 },
  checkText: { color: colors.text, flex: 1, fontSize: 13 },
  checkTextPending: { color: colors.muted },
  checklistNote: { color: colors.muted, fontSize: 11, lineHeight: 16 },

  body: { color: colors.text, fontSize: 13, lineHeight: 19 },
  label: { color: colors.muted, fontSize: 11, fontWeight: "700", letterSpacing: 0.4, textTransform: "uppercase" },
  note: { color: colors.muted, fontSize: 11, lineHeight: 16 },
  link: { color: progressTheme.violet, fontSize: 13, fontWeight: "600" },

  actions: { flexDirection: "row", gap: 8 },
  primaryAction: {
    alignItems: "center",
    backgroundColor: progressTheme.violet,
    borderRadius: progressTheme.radius.chip,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: progressTheme.tapTarget,
    paddingHorizontal: 16
  },
  primaryActionText: { color: colors.background, fontSize: 13, fontWeight: "700" },
  secondaryAction: {
    alignItems: "center",
    borderColor: progressTheme.violetBorder,
    borderRadius: progressTheme.radius.chip,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: progressTheme.tapTarget,
    paddingHorizontal: 16
  },
  secondaryActionText: { color: progressTheme.violet, fontSize: 13, fontWeight: "700" },

  missionRow: { flexDirection: "row", gap: 10, paddingVertical: 6 },

  badge: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: progressTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 7,
    padding: 16
  },
  badgeEarned: { backgroundColor: progressTheme.goldSoft, borderColor: progressTheme.goldBorder },
  badgeTitle: { color: colors.text, fontSize: 16, fontWeight: "700" },
  badgeGeneration: { fontSize: 12, fontWeight: "700", letterSpacing: 0.8, textTransform: "uppercase" },
  badgeNumber: { color: colors.text, fontSize: 22, fontWeight: "700" },

  activityRow: { gap: 3, paddingVertical: 7 },

  stepRow: { alignItems: "center", flexDirection: "row", gap: 10 },
  stepIndex: {
    alignItems: "center",
    backgroundColor: progressTheme.violetSoft,
    borderRadius: progressTheme.radius.chip,
    height: 24,
    justifyContent: "center",
    width: 24
  },
  stepIndexText: { color: progressTheme.violet, fontSize: 12, fontWeight: "700" },

  faqRow: { borderTopColor: colors.border, borderTopWidth: StyleSheet.hairlineWidth, gap: 7, paddingVertical: 10 },
  faqHeader: { alignItems: "center", flexDirection: "row", gap: 8, justifyContent: "space-between" },
  faqQuestion: { color: colors.text, flex: 1, fontSize: 13, fontWeight: "600" },

  empty: { alignItems: "center", gap: 4, paddingVertical: 18 },
  emptyTitle: { color: colors.text, fontSize: 14, fontWeight: "600" },
  emptyBody: { color: colors.muted, fontSize: 12, textAlign: "center" },

  footnote: { color: colors.muted, fontSize: 11, paddingHorizontal: 4, textAlign: "center" }
}));
