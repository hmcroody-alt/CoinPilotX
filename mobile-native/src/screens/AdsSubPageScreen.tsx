/**
 * The three Advertising sub-pages that used to be locked tiles.
 *
 * ## Why this file exists
 *
 * The manager's Tools grid had two tiles — Audiences and Creative library —
 * rendered `disabled: true` with the subtitle "Not available in the app yet".
 * That was honest and it was still a dead end: the tile occupied the place
 * where an answer should be, refused to open, and left the reader with no way
 * to find out what the feature is, what already governs their campaigns
 * without it, or what they can do in the meantime. `NO_DEAD_ENDS` is not
 * satisfied by an accurate refusal. It is satisfied by a destination.
 *
 * So both tiles now open, and what they open is a page that reports the real
 * state of the feature. Nothing here fabricates a campaign, an audience, an
 * asset or a number. Each page says what the server already enforces (which is
 * substantial — targeting and creative validation are fully built server-side,
 * they are simply not exposed in this app yet), what is not exposed, and where
 * the equivalent work happens today.
 *
 * The third page, Account details, is here for a different reason. The account
 * strip on the manager used to read `ROODY CHERIE Growth · Ad account 8`, and
 * removing that number from the header was only defensible if the number went
 * somewhere — it is what support asks for and what an audit log cites. This is
 * where it went.
 *
 * ## The facts on these pages are read from the server implementation
 *
 * The allowlists quoted below are not aspirational copy. They are what
 * `services/business_os/advertising/targeting.py` and `…/creatives.py` accept
 * and reject today:
 *
 *   • placements   — `PLACEMENTS_ALLOWED = ("feed", "reels")`
 *   • audience     — `AUDIENCE_ALLOWED_FIELDS`, `DEVICE_CLASSES_ALLOWED`,
 *                    `CONNECTIONS_ALLOWED`, `AGE_MIN_FLOOR = 18`
 *   • prohibited   — `AUDIENCE_PROHIBITED_FIELDS`
 *   • creatives    — `CREATIVE_TYPES`, `DESTINATION_TYPES`, and the media
 *                    ownership check against `pulse_media_assets`
 *
 * If those lists change, these pages are wrong, and that is the intended
 * failure mode: the page is a report on server rules, so it must be updated
 * with them rather than drifting into marketing.
 *
 * ## Two of the pages have since stopped being reports
 *
 * `policy` — the Policy Center — reads `portal.review_board` and shows the
 * actual decision made about each of this advertiser's creatives: the verdict,
 * who made it, the risk score and the written reason. §37 names an inaccessible
 * policy reason as a completion blocker, and that reason had been one unmade
 * HTTP call away.
 *
 * It offers no Appeal button. The appeals endpoint exists only on the canonical
 * advertising surface, which is dark in this deployment — see `api/adsPolicy`
 * for the reasoning and for what it offers instead.
 *
 * `creatives` went the same way. It listed nothing while `portal.creatives`
 * carried the whole library, so a tile called "Creative library" opened a
 * rulebook — the empty locked card with no useful destination §37 forbids. It
 * now lists the real creatives, grouped by what the reader has to do about
 * them, with the four actions the server accepts. The rules it used to be are
 * still on the page, below the library, because they are the thing a rejection
 * has to be read against. See `api/adsCreatives` for which actions are offered
 * and which two are deliberately absent.
 */

import { useCallback, useEffect, useState } from "react";
import { Animated, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { AdAccount, listAdAccounts, loadCachedAdAccounts } from "../api/businessOs";
import { adAccountDisplay, adAccountStanding, primaryAdAccount } from "../api/adsDashboard";
import { AdCreative, AdsPortal, getAdsPortal } from "../api/adsPortal";
import {
  PolicyCenterModel,
  PolicyDecisionView,
  policyCenterModel,
  policyDecisionView
} from "../api/adsPolicy";
import {
  CreativeAction,
  canActOnCreative,
  creativeActionOffers,
  creativeGuidance,
  creativeLibraryModel,
  creativeStateLabel,
  creativeStateTone,
  creativeWriteBlockedReason,
  runCreativeAction
} from "../api/adsCreatives";
import { AdsPreviewNote, AdsSectionError, AdsSkeletonBlock, AdsStatusPill } from "../components/ads";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { adsLight } from "../theme/adsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance } from "../theme/storeMotion";

/** Which of the four pages this route is showing. */
export type AdsSubPage = "audiences" | "creatives" | "account" | "policy";

type Props = {
  surface: AdsSubPage;
  route?: { params?: { title?: string; accountId?: number } };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/**
 * A block of page copy. `points` are statements of fact about server rules, not
 * feature bullets — each one is something that will reject a request today.
 */
type Block = { title: string; body?: string; points?: string[] };

const PAGE_TITLE: Record<AdsSubPage, string> = {
  audiences: "Audiences",
  creatives: "Creative library",
  account: "Account details",
  policy: "Policy Center"
};

/**
 * The standing disclosure at the top of each preview page. It states the one
 * thing a reader must not misread: this page describes rules, it does not let
 * you change anything, and nothing on it costs money.
 */
const PAGE_NOTE: Record<AdsSubPage, string | null> = {
  audiences:
    "Audience controls aren't editable in the app yet. Everything below is already enforced on PulseSoc's servers for every campaign you run — this page reports it, it doesn't change it.",
  // No note. The library lists real creatives and runs real actions against
  // them, so a "preview" disclaimer over it would misdescribe the page — and
  // what the page still can't do is said where it matters, on the rows.
  creatives: null,
  account: null,
  // No note. The Policy Center reports real decisions rather than describing a
  // feature's state, so a preview disclaimer over it would be inaccurate.
  policy: null
};

const AUDIENCE_BLOCKS: Block[] = [
  {
    title: "Where your ads can appear",
    body: "Every campaign selects at least one placement. PulseSoc supports two, and a campaign that asks for anything else is rejected rather than quietly delivered somewhere you didn't choose.",
    points: ["Feed", "Reels"]
  },
  {
    title: "What an audience will be able to narrow",
    body: "These are the only dimensions the platform accepts. Anything outside this list is refused by name, so no client — including a future version of this app — can widen it without a server change.",
    points: [
      "Country and language",
      "Age, from 18 upward",
      "Device: mobile, tablet or desktop",
      "Connection: people who already follow you, or who engaged with you before",
      "Exclusions, expressed with the same fields"
    ]
  },
  {
    title: "What will never be targetable",
    body: "These are refused with a specific reason rather than ignored. Some are prohibited outright; the rest would need a consent-backed dataset PulseSoc does not collect.",
    points: [
      "Health, religion, politics, race or ethnicity, sexual orientation, gender identity",
      "Income or financial hardship",
      "Anyone under 18",
      "Precise location or a location radius",
      "Uploaded customer lists, lookalikes and retargeting pixels"
    ]
  },
  {
    title: "Until it's in the app",
    body: "Your campaigns deliver to the placements you pick when you create them. No audience narrowing is applied, so a campaign reaches the general eligible audience for its placements."
  }
];

const CREATIVE_BLOCKS: Block[] = [
  {
    title: "What counts as a creative",
    body: "A creative is one image or video plus its headline, body, call to action and destination. It belongs to a campaign, and it carries its own review status separate from the campaign's.",
    points: ["Image", "Video", "Reels video"]
  },
  {
    title: "Media has to be yours",
    body: "The creative references media you already uploaded to PulseSoc, by its canonical id. The server checks that the asset exists, that you own it, that it finished processing and that its type matches the creative type. There is no way to point an ad at a file you don't own."
  },
  {
    title: "Where a creative can send people",
    body: "A destination is either somewhere on PulseSoc, checked for existence, or an external link that must be HTTPS.",
    points: [
      "Your profile",
      "A post or a Reel",
      "A Marketplace listing",
      "An external HTTPS address"
    ]
  },
  {
    title: "Review is per creative, and edits are versioned",
    body: "A draft or rejected creative is edited in place. Once a creative has been submitted or approved, changing anything material creates a new version instead of overwriting the reviewed one — so a rejection reason always still refers to the thing that was rejected."
  },
  {
    title: "What you still have to do in the campaign editor",
    body: "New creatives are attached when you create or edit a campaign, and so are replacement images and rewritten headlines — the app has no media uploader yet, and the server has no route for changing a creative's words on this surface. Duplicating a rejected creative gives you a copy to correct there."
  }
];

export function AdsSubPageScreen({ surface, route, navigation }: Props) {
  const insets = useSafeAreaInsets();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(4, reducedMotion);

  const goBack = useCallback(() => {
    navigation?.goBack?.();
  }, [navigation]);

  const openClassic = useCallback(
    (title: string) => {
      navigation?.navigate("BusinessOsAdvertising", { title, mode: "classic" });
    },
    [navigation]
  );

  const openReports = useCallback(() => {
    navigation?.navigate("BusinessOsInsights", { title: "Ad reports" });
  }, [navigation]);

  const openAccount = useCallback(() => {
    navigation?.navigate("BusinessOsAdvertising", {
      title: "Account details",
      mode: "account"
    });
  }, [navigation]);

  const openWallet = useCallback(
    (accountId?: number) => {
      navigation?.navigate("BusinessOsPayments", { title: "Ad wallet", accountId });
    },
    [navigation]
  );

  const openVerification = useCallback(() => {
    navigation?.navigate("VerificationCenter", {
      title: "Verification Center",
      track: "business"
    });
  }, [navigation]);

  const openCreativeRules = useCallback(() => {
    navigation?.navigate("BusinessOsAdvertising", {
      title: "Creative library",
      mode: "creatives"
    });
  }, [navigation]);

  const title = route?.params?.title || PAGE_TITLE[surface];
  const note = PAGE_NOTE[surface];

  return (
    <View style={styles.root}>
      <LinearGradient
        colors={[adsLight.bg.headerFrom, adsLight.bg.headerTo]}
        style={[styles.header, { paddingTop: insets.top + 8 }]}
      >
        <Pressable
          onPress={goBack}
          style={styles.iconButton}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={6}
        >
          <Ionicons name="chevron-back" size={24} color={adsLight.text.onDark} />
        </Pressable>
        <Text style={styles.headerTitle} numberOfLines={1} accessibilityRole="header">
          {title}
        </Text>
      </LinearGradient>

      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: bottomPad(insets.bottom) }]}
        showsVerticalScrollIndicator={false}
      >
        {note ? (
          <Animated.View style={[styles.stack, entrance.styleFor(0)]}>
            <AdsPreviewNote text={note} />
          </Animated.View>
        ) : null}

        {surface === "account" ? (
          <AccountDetails
            requestedAccountId={route?.params?.accountId}
            reducedMotion={reducedMotion}
            entranceStyle={entrance.styleFor(1)}
            onWallet={openWallet}
            onVerify={openVerification}
          />
        ) : surface === "policy" ? (
          <PolicyCenter reducedMotion={reducedMotion} entranceStyle={entrance.styleFor(1)} />
        ) : surface === "creatives" ? (
          <>
            <CreativeLibrary reducedMotion={reducedMotion} entranceStyle={entrance.styleFor(1)} />
            {/* The rules stay on the page, under the library rather than
                instead of it. A rejection reason is only actionable against the
                rule it cites, and the Policy Center's "Creative rules" link
                lands here. */}
            <Animated.View style={[styles.stack, entrance.styleFor(2)]}>
              <Text style={styles.sectionTitle}>What every creative is checked against</Text>
              {CREATIVE_BLOCKS.map((block) => (
                <BlockCard key={block.title} block={block} />
              ))}
            </Animated.View>
          </>
        ) : (
          <Animated.View style={[styles.stack, entrance.styleFor(1)]}>
            {AUDIENCE_BLOCKS.map((block) => (
              <BlockCard key={block.title} block={block} />
            ))}
          </Animated.View>
        )}

        <Animated.View style={[styles.stack, entrance.styleFor(2)]}>
          <Text style={styles.sectionTitle}>What you can do now</Text>
          {surface === "account" ? (
            <>
              <ActionButton
                label="Open ad wallet"
                onPress={() => openWallet(route?.params?.accountId)}
                reducedMotion={reducedMotion}
                primary
              />
              <ActionButton
                label="Verification Center"
                onPress={openVerification}
                reducedMotion={reducedMotion}
              />
            </>
          ) : surface === "policy" ? (
            <>
              {/* Editing a rejected creative happens in the campaign editor,
                  which is the classic screen. Sending someone to "your
                  campaigns" when the instruction was "edit the creative" is the
                  dead end §37 forbids. */}
              <ActionButton
                label="Edit a campaign's creative"
                onPress={() => openClassic("Campaigns")}
                reducedMotion={reducedMotion}
                primary
              />
              <ActionButton
                label="Creative rules"
                onPress={openCreativeRules}
                reducedMotion={reducedMotion}
              />
            </>
          ) : (
            <>
              <ActionButton
                label="Create campaign"
                onPress={() => openClassic("Create campaign")}
                reducedMotion={reducedMotion}
                primary
              />
              <ActionButton
                label={surface === "audiences" ? "Account details" : "Ad reports"}
                onPress={surface === "audiences" ? openAccount : openReports}
                reducedMotion={reducedMotion}
              />
            </>
          )}
        </Animated.View>
      </ScrollView>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Account details — the one page here that reads from the server
 * ------------------------------------------------------------------ */

/**
 * The number, and everything else support will ask for.
 *
 * Loading and failure are distinguished rather than collapsed: an account that
 * has not arrived yet shows skeletons, and one whose request failed says so and
 * offers a retry. Neither shows an account number, because a stale or absent id
 * quoted to support is worse than none.
 */
function AccountDetails({
  requestedAccountId,
  reducedMotion,
  entranceStyle,
  onWallet,
  onVerify
}: {
  requestedAccountId?: number;
  reducedMotion: boolean;
  entranceStyle: any;
  onWallet: (accountId?: number) => void;
  onVerify: () => void;
}) {
  const [account, setAccount] = useState<AdAccount | null>(null);
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");

  const load = useCallback(async () => {
    setStatus("loading");
    try {
      const res = await listAdAccounts();
      const picked =
        res.accounts.find((item) => item.id === requestedAccountId) ||
        primaryAdAccount(res.accounts);
      setAccount(picked);
      setStatus("ok");
    } catch {
      // The cache is shown when it has something, and labelled by the caller's
      // own copy rather than presented as fresh. With nothing cached this is a
      // failure, not an empty account.
      const cached = await loadCachedAdAccounts().catch(() => [] as AdAccount[]);
      const picked =
        cached.find((item) => item.id === requestedAccountId) || primaryAdAccount(cached);
      setAccount(picked);
      setStatus(picked ? "ok" : "error");
    }
  }, [requestedAccountId]);

  useEffect(() => {
    let active = true;
    load().catch(() => {
      if (active) setStatus("error");
    });
    return () => {
      active = false;
    };
  }, [load]);

  if (status === "loading") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <View style={styles.card}>
          <AdsSkeletonBlock width="60%" height={16} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="40%" height={12} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="75%" height={12} reducedMotion={reducedMotion} />
        </View>
      </Animated.View>
    );
  }

  if (status === "error" || !account) {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <AdsSectionError
          message="Your account details didn't load."
          onRetry={() => {
            load().catch(() => setStatus("error"));
          }}
          reducedMotion={reducedMotion}
        />
      </Animated.View>
    );
  }

  const display = adAccountDisplay(account, { accountCount: 1 });
  const standing = adAccountStanding(account);

  return (
    <Animated.View style={[styles.stack, entranceStyle]}>
      <View style={styles.card}>
        <Text style={styles.cardTitle}>{display.name}</Text>
        <View style={styles.standingRow}>
          <View
            style={[styles.dot, { backgroundColor: adsLight.status[standing.tone] }]}
            accessibilityElementsHidden
            importantForAccessibility="no"
          />
          <Text style={styles.cardBody}>{standing.line}</Text>
        </View>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>For support and billing</Text>
        <Text style={styles.cardBody}>
          Quote this number when you contact support about advertising, or when you're
          reconciling a charge. It also identifies this account in your billing history.
        </Text>
        <View style={styles.referenceBox}>
          <Text style={styles.referenceLabel}>Ad account number</Text>
          <Text style={styles.referenceValue} selectable>
            {account.id}
          </Text>
        </View>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>What this account's standing means</Text>
        <Text style={styles.cardBody}>
          {standingExplanation(standing.line)}
        </Text>
        <Pressable
          onPress={onVerify}
          accessibilityRole="button"
          accessibilityLabel="Open the Verification Center"
          hitSlop={6}
        >
          <Text style={styles.inlineLink}>Verification Center ›</Text>
        </Pressable>
        <Pressable
          onPress={() => onWallet(account.id)}
          accessibilityRole="button"
          accessibilityLabel="Open the ad wallet"
          hitSlop={6}
        >
          <Text style={styles.inlineLink}>Ad wallet and billing ›</Text>
        </Pressable>
      </View>
    </Animated.View>
  );
}

/**
 * Plain-language consequence for each standing. Keyed off the line rather than
 * the raw status so it cannot describe a state the line above it doesn't show.
 */
function standingExplanation(line: string): string {
  if (line.endsWith("Active")) {
    return "Campaigns on this account can be submitted and can deliver, and spend is charged to your ad wallet.";
  }
  if (line.endsWith("Verification pending")) {
    return "You can create and edit campaign drafts. They won't deliver and nothing is charged until PulseSoc approves the account.";
  }
  if (line.endsWith("Restricted")) {
    return "This account can't deliver ads. Existing campaigns are stopped and new ones can't be submitted. The Verification Center has the reason and the appeal path.";
  }
  if (line.endsWith("Not configured")) {
    return "This account hasn't finished setup, so it can't deliver yet. Completing verification is the remaining step.";
  }
  return "PulseSoc reports a status for this account that this version of the app doesn't recognise, so nothing is assumed about whether it can deliver. The Verification Center has the current state.";
}

/* ------------------------------------------------------------------ *
 * Policy Center — the real decisions, not a description of the rules
 * ------------------------------------------------------------------ */

/**
 * Four states, and they say four different things.
 *
 * `loading` is skeletons. `unavailable` is a failed or unmade request, and it
 * must not read as "you have no policy issues" — an advertiser with a rejected
 * ad being told everything is fine is the failure this branch exists to
 * prevent. `empty` is a real empty board. `ready` renders it.
 *
 * The portal is loaded directly rather than through the manager's model because
 * this page is reachable by deep link, so it cannot assume the manager ran.
 */
function PolicyCenter({
  reducedMotion,
  entranceStyle
}: {
  reducedMotion: boolean;
  entranceStyle: any;
}) {
  const [model, setModel] = useState<PolicyCenterModel>({
    state: "loading",
    groups: [],
    actionCount: 0,
    reviewCount: 0
  });

  const load = useCallback(async () => {
    setModel((prev) => ({ ...prev, state: "loading" }));
    try {
      const { portal } = await getAdsPortal();
      setModel(policyCenterModel(portal as AdsPortal));
    } catch {
      setModel({ state: "unavailable", groups: [], actionCount: 0, reviewCount: 0 });
    }
  }, []);

  useEffect(() => {
    let active = true;
    load().catch(() => {
      if (active) {
        setModel({ state: "unavailable", groups: [], actionCount: 0, reviewCount: 0 });
      }
    });
    return () => {
      active = false;
    };
  }, [load]);

  if (model.state === "loading") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <View style={styles.card}>
          <AdsSkeletonBlock width="55%" height={16} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="85%" height={12} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="70%" height={12} reducedMotion={reducedMotion} />
        </View>
      </Animated.View>
    );
  }

  if (model.state === "unavailable") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <AdsSectionError
          message="Policy decisions didn't load. This doesn't mean there are none — try again."
          onRetry={() => {
            load().catch(() => undefined);
          }}
          reducedMotion={reducedMotion}
        />
      </Animated.View>
    );
  }

  if (model.state === "empty") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <View style={styles.card}>
          <Text style={styles.cardTitle}>No activity yet</Text>
          <Text style={styles.cardBody}>
            No creative of yours has been reviewed. Decisions appear here as soon as one is
            submitted, and every rejection arrives with the reason it was rejected.
          </Text>
        </View>
      </Animated.View>
    );
  }

  return (
    <Animated.View style={[styles.stack, entranceStyle]}>
      {/* Stated once, at the top, rather than repeated on each rejected card:
          §37 forbids the duplicate unavailable notice, and the appeal route is
          one fact about this page, not one fact per decision. */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>How a decision is made</Text>
        <Text style={styles.cardBody}>
          Every creative is checked automatically before it can deliver, and some are then
          reviewed by a person. A rejection always carries a reason. To act on one, edit the
          creative to address the reason and resubmit it — a resubmitted creative is reviewed
          again. If you believe a decision is wrong, contact support with the campaign name.
        </Text>
      </View>

      {model.groups.map((group) => (
        <View key={group.key} style={styles.groupBlock}>
          <Text style={styles.sectionTitle}>{group.title}</Text>
          <Text style={styles.groupCaption}>{group.caption}</Text>
          {group.entries.map((entry) => (
            <PolicyDecisionCard
              key={entry.review_id}
              view={policyDecisionView(entry)}
              reducedMotion={reducedMotion}
            />
          ))}
        </View>
      ))}
    </Animated.View>
  );
}

/**
 * One decision.
 *
 * The reason is never omitted — `policyDecisionView` substitutes an explicit
 * "no reason was recorded" for a blank one, because a rejection with nothing
 * under it is the inaccessible policy reason §37 names.
 */
function PolicyDecisionCard({
  view,
  reducedMotion
}: {
  view: PolicyDecisionView;
  reducedMotion: boolean;
}) {
  return (
    <View style={styles.card}>
      <View style={styles.decisionHead}>
        <Text style={styles.cardTitle} numberOfLines={2}>
          {view.title}
        </Text>
        <AdsStatusPill label={view.statusLabel} tone={view.tone} reducedMotion={reducedMotion} />
      </View>

      {view.campaign ? <Text style={styles.decisionMeta}>{view.campaign}</Text> : null}

      {view.reason ? (
        <View style={styles.reasonBox}>
          <Text style={styles.referenceLabel}>Reason</Text>
          <Text style={styles.cardBody}>{view.reason}</Text>
        </View>
      ) : null}

      {view.decidedBy ? <Text style={styles.decisionMeta}>{view.decidedBy}</Text> : null}

      {/* Only when the server scored it. A zero on an unscored row would read as
          "we checked and found no risk", which nobody claimed. */}
      {view.riskScore !== null ? (
        <Text style={styles.decisionMeta}>{`Risk score ${view.riskScore} of 100`}</Text>
      ) : null}

      {view.remedy.kind !== "none" ? (
        <Text style={styles.remedyText}>{view.remedy.text}</Text>
      ) : null}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Creative Library — the real creatives, not a description of the rules
 * ------------------------------------------------------------------ */

/**
 * The library.
 *
 * Four states, same discipline as the Policy Center: `loading` is skeletons,
 * `unavailable` is a request that failed or was never made and says so, `empty`
 * is a real empty library, `ready` renders it. The distinction matters most
 * here — an advertiser whose creative was rejected must never be shown "You
 * haven't made any creatives yet" because a request fell over.
 *
 * Actions are authorised per creative against the account it belongs to, not
 * against a rolled-up role. The server re-derives the role per account and
 * answers 403, so a button offered on the strength of `roles.current` would
 * fail in a way this screen couldn't explain. Where a reader can't act, the
 * reason is printed rather than the buttons quietly omitted.
 *
 * After any action the portal is reloaded rather than the row patched locally.
 * `submit` moves a creative into review, `duplicate` creates one that isn't in
 * the list yet, and `delete_draft` removes one — guessing at the new list would
 * be a second opinion about state the server owns.
 */
function CreativeLibrary({
  reducedMotion,
  entranceStyle
}: {
  reducedMotion: boolean;
  entranceStyle: any;
}) {
  const [portal, setPortal] = useState<AdsPortal | null>(null);
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [running, setRunning] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setStatus("loading");
    try {
      const res = await getAdsPortal();
      setPortal((res.portal as AdsPortal) || null);
      setStatus("ok");
    } catch {
      setPortal(null);
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    let active = true;
    load().catch(() => {
      if (active) setStatus("error");
    });
    return () => {
      active = false;
    };
  }, [load]);

  const act = useCallback(
    async (creative: AdCreative, action: CreativeAction) => {
      setRunning(`${creative.id}:${action}`);
      setNotice(null);
      try {
        const res = await runCreativeAction(Number(creative.id), action);
        // The server's own refusal is shown verbatim when it sends one. A
        // generic "something went wrong" over a specific 409 would throw away
        // the only sentence that tells the reader what to do instead.
        if (res?.error) {
          setNotice(String(res.error));
        } else {
          setNotice(ACTION_DONE[action]);
          await load();
        }
      } catch {
        setNotice("That didn't go through. Nothing was changed — try again.");
      } finally {
        setRunning(null);
      }
    },
    [load]
  );

  const model = creativeLibraryModel(status === "ok" ? portal : null);
  const reasonStatedAbove = !model.canWrite && Boolean(model.writeBlockedReason);

  if (status === "loading") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <View style={styles.card}>
          <AdsSkeletonBlock width="55%" height={16} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="85%" height={12} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="70%" height={12} reducedMotion={reducedMotion} />
        </View>
      </Animated.View>
    );
  }

  if (model.state === "unavailable") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <AdsSectionError
          message="Your creatives didn't load. This doesn't mean you have none — try again."
          onRetry={() => {
            load().catch(() => setStatus("error"));
          }}
          reducedMotion={reducedMotion}
        />
      </Animated.View>
    );
  }

  if (model.state === "empty") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <View style={styles.card}>
          <Text style={styles.cardTitle}>No activity yet</Text>
          <Text style={styles.cardBody}>
            You haven't created any ad creatives. They're made in the campaign editor, and
            every one you make appears here with its review status.
          </Text>
        </View>
      </Animated.View>
    );
  }

  return (
    <Animated.View style={[styles.stack, entranceStyle]}>
      {notice ? <Text style={styles.noticeText}>{notice}</Text> : null}

      {/* Stated once, above the list, rather than on every read-only row —
          §37 forbids the duplicate unavailable notice. This card only appears
          when the reader can't act on *any* account; a reader who owns one and
          views another gets the reason on the rows it applies to, because a
          blanket notice would be wrong about half the list. */}
      {reasonStatedAbove ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>You can see these but can't change them</Text>
          <Text style={styles.cardBody}>{model.writeBlockedReason}</Text>
        </View>
      ) : null}

      {model.groups.map((group) => (
        <View key={group.key} style={styles.groupBlock}>
          <Text style={styles.sectionTitle}>{group.title}</Text>
          <Text style={styles.groupCaption}>{group.caption}</Text>
          {group.creatives.map((creative) => (
            <CreativeCard
              key={creative.id}
              creative={creative}
              portal={portal}
              running={running}
              reasonStatedAbove={reasonStatedAbove}
              onAct={act}
              reducedMotion={reducedMotion}
            />
          ))}
        </View>
      ))}
    </Animated.View>
  );
}

/** What to say after each action succeeded, named for what actually happened. */
const ACTION_DONE: Record<CreativeAction, string> = {
  submit: "Submitted for review. You'll see the decision in the Policy Center.",
  duplicate: "Duplicated. The copy is a draft — edit it in the campaign editor before submitting.",
  archive: "Archived. It no longer delivers.",
  delete_draft: "Draft deleted."
};

/**
 * One creative.
 *
 * The guidance line is never omitted, and a rejection always carries either the
 * server's reason or an explicit admission that none was recorded — §37 names
 * an inaccessible policy reason as a completion blocker, and a blank space
 * under a rejection is inaccessible in the way that counts.
 */
function CreativeCard({
  creative,
  portal,
  running,
  reasonStatedAbove,
  onAct,
  reducedMotion
}: {
  creative: AdCreative;
  portal: AdsPortal | null;
  running: string | null;
  /** The page already said why, once. Repeating it per row is the §37 duplicate notice. */
  reasonStatedAbove: boolean;
  onAct: (creative: AdCreative, action: CreativeAction) => void;
  reducedMotion: boolean;
}) {
  const allowed = canActOnCreative(portal, creative);
  const blockedReason =
    allowed || reasonStatedAbove ? null : creativeWriteBlockedReason(portal, creative);
  const offers = creativeActionOffers(creative);

  return (
    <View style={styles.card}>
      <View style={styles.decisionHead}>
        <Text style={styles.cardTitle} numberOfLines={2}>
          {creative.title || "Untitled creative"}
        </Text>
        <AdsStatusPill
          label={creativeStateLabel(creative)}
          tone={creativeStateTone(creative)}
          reducedMotion={reducedMotion}
        />
      </View>

      {creative.campaign_name ? (
        <Text style={styles.decisionMeta}>{creative.campaign_name}</Text>
      ) : null}

      <Text style={styles.cardBody}>{creativeGuidance(creative)}</Text>

      {allowed ? (
        <View style={styles.actionRow}>
          {offers.map((offer) => {
            const key = `${creative.id}:${offer.action}`;
            const busy = running === key;
            return (
              <Pressable
                key={offer.action}
                onPress={() => onAct(creative, offer.action)}
                disabled={running !== null}
                style={[
                  styles.chip,
                  offer.destructive ? styles.chipDestructive : null,
                  running !== null && !busy ? styles.chipDimmed : null
                ]}
                accessibilityRole="button"
                accessibilityState={{ disabled: running !== null, busy }}
                accessibilityLabel={`${offer.label}: ${creative.title || "Untitled creative"}`}
              >
                <Text
                  style={[styles.chipText, offer.destructive ? styles.chipTextDestructive : null]}
                >
                  {busy ? offer.pendingLabel : offer.label}
                </Text>
              </Pressable>
            );
          })}
        </View>
      ) : blockedReason ? (
        <Text style={styles.decisionMeta}>{blockedReason}</Text>
      ) : null}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Pieces
 * ------------------------------------------------------------------ */

function BlockCard({ block }: { block: Block }) {
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{block.title}</Text>
      {block.body ? <Text style={styles.cardBody}>{block.body}</Text> : null}
      {block.points?.length ? (
        <View style={styles.points}>
          {block.points.map((point) => (
            <View key={point} style={styles.pointRow}>
              <View
                style={styles.pointDot}
                accessibilityElementsHidden
                importantForAccessibility="no"
              />
              <Text style={styles.pointText}>{point}</Text>
            </View>
          ))}
        </View>
      ) : null}
    </View>
  );
}

function ActionButton({
  label,
  onPress,
  reducedMotion,
  primary = false
}: {
  label: string;
  onPress: () => void;
  reducedMotion: boolean;
  primary?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.action, primary ? styles.actionPrimary : styles.actionSecondary]}
      accessibilityRole="button"
      accessibilityLabel={label}
    >
      <Text style={[styles.actionText, primary ? styles.actionTextPrimary : null]}>{label}</Text>
      <Ionicons
        name="chevron-forward"
        size={16}
        color={primary ? adsLight.cta.text : adsLight.text.muted}
      />
    </Pressable>
  );
}

function bottomPad(inset: number) {
  return Math.max(inset, 16) + BOTTOM_NAV_CONTENT_CLEARANCE;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: adsLight.bg.page },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: adsLight.space.card,
    paddingBottom: 12
  },
  iconButton: {
    minWidth: adsLight.size.tapTarget,
    minHeight: adsLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  headerTitle: { flex: 1, fontSize: 20, fontWeight: "700", color: adsLight.text.onDark },
  content: { paddingTop: 12, gap: 14 },
  stack: { gap: 10, paddingHorizontal: adsLight.space.card },
  sectionTitle: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary },
  card: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: adsLight.space.card,
    gap: 8
  },
  cardTitle: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary, lineHeight: 20 },
  cardBody: { fontSize: 13, color: adsLight.text.muted, lineHeight: 19 },
  standingRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  points: { gap: 6, marginTop: 2 },
  pointRow: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  pointDot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    marginTop: 8,
    backgroundColor: adsLight.text.muted
  },
  pointText: { flex: 1, fontSize: 13, color: adsLight.text.primary, lineHeight: 19 },
  // The number is set apart and selectable: it exists to be read aloud to
  // support or copied into a form, which is the only reason it is on a screen
  // at all.
  referenceBox: {
    marginTop: 2,
    padding: 12,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.strip,
    gap: 2
  },
  referenceLabel: { fontSize: 11, fontWeight: "700", color: adsLight.text.muted },
  groupBlock: { gap: 10 },
  groupCaption: { fontSize: 12, color: adsLight.text.muted, lineHeight: 17, marginTop: -4 },
  decisionHead: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 8 },
  decisionMeta: { fontSize: 12, color: adsLight.text.muted, lineHeight: 17 },
  reasonBox: {
    padding: 10,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.strip,
    gap: 2
  },
  remedyText: { fontSize: 13, fontWeight: "700", color: adsLight.text.primary, lineHeight: 19 },
  noticeText: { fontSize: 13, fontWeight: "700", color: adsLight.text.primary, lineHeight: 19 },
  actionRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 2 },
  chip: {
    minHeight: adsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 14,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    backgroundColor: adsLight.bg.strip
  },
  chipDestructive: { borderColor: adsLight.status.error },
  // Dimmed rather than hidden while another action runs: a control that
  // vanishes mid-tap reads as a bug, and §31 forbids an active-looking control
  // that can't complete.
  chipDimmed: { opacity: 0.45 },
  chipText: { fontSize: 13, fontWeight: "800", color: adsLight.text.primary },
  chipTextDestructive: { color: adsLight.status.error },
  referenceValue: { fontSize: 18, fontWeight: "800", color: adsLight.text.primary },
  inlineLink: { fontSize: 13, fontWeight: "700", color: adsLight.text.link, paddingVertical: 4 },
  action: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: adsLight.size.tapTarget,
    paddingHorizontal: 16,
    borderRadius: adsLight.radius.control
  },
  actionPrimary: { backgroundColor: adsLight.cta.from },
  actionSecondary: {
    backgroundColor: adsLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  actionText: { fontSize: 14, fontWeight: "800", color: adsLight.text.primary },
  actionTextPrimary: { color: adsLight.cta.text }
});

export default AdsSubPageScreen;
