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
 * ## The Audiences page used to report the wrong server
 *
 * This block used to say the page quoted `PLACEMENTS_ALLOWED = ("feed",
 * "reels")`, `AUDIENCE_ALLOWED_FIELDS` and `AUDIENCE_PROHIBITED_FIELDS` from
 * `services/business_os/advertising/targeting.py`, and that those lists were
 * "what the server accepts and rejects today". Every one of those constants is
 * real. None of them governs a single campaign this app can see.
 *
 * PulseSoc has two advertising stacks. `business_os/advertising` — where that
 * targeting module lives — validates audiences properly, refuses prohibited
 * fields by name, and writes `business_os_ad_sets`. That table is read by
 * exactly three files, all inside its own package: `schema.py`, `readiness.py`
 * and `ad_sets.py`. No delivery path touches it, so nothing validated by those
 * allowlists has ever been shown to a viewer.
 *
 * The live stack is `pulse_ads_service` / `pulse_advertiser_portal`, which is
 * what `/api/pulse/ads/portal` serves and what every campaign on these screens
 * belongs to. It seeds twelve placements, not two, and none of them is Reels.
 * Its audience table, `pulse_ad_targeting`, has no write path anywhere in the
 * repository — so `_matches_targeting` receives an all-NULL row on every
 * candidate and returns true unconditionally, for every viewer, always.
 *
 * The result was a page telling advertisers which audience dimensions were
 * refused by name, when their campaigns had no audience and nothing was being
 * refused. Accurate about one system, addressed to users of the other. The
 * Audiences copy now describes the stack the reader is actually in, and the
 * placement list is fetched from `portal.placements` rather than written here
 * so it cannot drift from `seed_placements` again.
 *
 * The creative rules below are unaffected — those were checked against the live
 * stack and hold: `create_ad_media_asset` verifies `chat_media_uploads`
 * ownership and raises 403, and `select_ads` gates delivery on approved
 * moderation for the creative, its media asset and its thumbnail.
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
import {
  AdAccount,
  listAdAccounts,
  loadCachedAdAccounts,
  requestAdAccountVerification
} from "../api/businessOs";
import { adAccountDisplay, adAccountStanding, primaryAdAccount } from "../api/adsDashboard";
import { accountVerificationState } from "../api/adsDelivery";
import {
  AdCreative,
  AdsPortal,
  PlacementCatalogueEntry,
  getAdsPortal,
  placementCatalogue
} from "../api/adsPortal";
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
  // Was: "Audience controls aren't editable in the app yet. Everything below is
  // already enforced on PulseSoc's servers…" — which read as though an audience
  // existed and this app merely couldn't edit it. No campaign has an audience.
  // "Not editable yet" is a much smaller admission than the true one, and it is
  // the difference between an advertiser who plans around no targeting and one
  // who waits for a control that would change nothing about their live spend.
  audiences:
    "There are no audience controls to edit — here or anywhere else in PulseSoc. This page reports what actually decides who sees your ads.",
  // No note. The library lists real creatives and runs real actions against
  // them, so a "preview" disclaimer over it would misdescribe the page — and
  // what the page still can't do is said where it matters, on the rows.
  creatives: null,
  account: null,
  // No note. The Policy Center reports real decisions rather than describing a
  // feature's state, so a preview disclaimer over it would be inaccurate.
  policy: null
};

/**
 * What this page is allowed to say about audiences.
 *
 * The version this replaces described a targeting system that does not exist.
 * It claimed the platform "accepts" a list of dimensions and "refuses by name"
 * anything outside it, and that prohibited dimensions "are refused with a
 * specific reason rather than ignored". There is no code that refuses anything,
 * because there is nothing to refuse: `pulse_ad_targeting` has no write path
 * anywhere in the repository — a search for INSERT/UPDATE/REPLACE against that
 * table returns nothing, and the only references are the CREATE TABLE, an
 * index, the auto-PK registry, `select_ads`'s LEFT JOIN and a staff COUNT.
 *
 * The consequence is that `_matches_targeting` (pulse_ads_service.py:1306)
 * receives an all-NULL row on every candidate and returns true on every branch,
 * for every viewer, always. Two of the columns it would honour — `min_age` and
 * `max_age` — it never reads at all, so "Age, from 18 upward" was untrue even
 * on the hypothetical where the table were populated.
 *
 * That leaves one honest thing to say about audiences and one about placements,
 * and the placement list is real, so it is fetched rather than written here.
 */
const AUDIENCE_BLOCKS: Block[] = [
  {
    title: "No audience narrowing is applied",
    body: "PulseSoc does not currently target ads. Not in this app, and not on the web — there is no audience editor anywhere in the product, and the server stores no audience for any campaign. Every campaign reaches the general eligible audience for the placements it runs in, and choosing a placement is the only way to influence who sees it."
  },
  {
    title: "What does decide who sees an ad",
    body: "Four things narrow delivery, and all four come from the campaign or the placement rather than from an audience.",
    points: [
      "The placements you attached to the campaign",
      "The devices that placement exists on",
      "Your schedule, budget and remaining wallet balance",
      "A per-placement frequency cap, so one viewer isn’t shown the same campaign repeatedly"
    ]
  },
  {
    title: "What isn’t collected, so can’t be targeted",
    body: "PulseSoc holds no advertising dataset for any of the following, which is a stronger position than refusing to target on them: there is nothing to target on. This is a statement about today’s product, not a policy commitment about a future one.",
    points: [
      "Health, religion, politics, race or ethnicity, sexual orientation, gender identity",
      "Income or financial hardship",
      "Precise location or a location radius",
      "Uploaded customer lists, lookalikes and retargeting pixels"
    ]
  },
  {
    title: "One thing viewers can turn off",
    body: "A viewer who opts out of personalised ads is excluded from country, language and premium-audience matching. That setting is honoured on the server today. It changes nothing about your campaigns right now, because none of those three is ever set on one."
  }
];

/**
 * What this page is allowed to say about creatives.
 *
 * Three of these blocks were checked against `create_creative` and
 * `validate_destination_url` in `services/pulse_ads_service.py` and three were
 * wrong, in the same way the audience copy was wrong: they described stricter
 * rules than the server has, which is the more dangerous direction to be wrong
 * in. An advertiser who believes a destination is checked for existence does
 * not check it themselves, and then pays for clicks into a 404.
 *
 *  - The type list said "Image, Video, Reels video". `VALID_CREATIVE_TYPES`
 *    (:52) is `{image, video, text, hologram, audio}`. Reels is not a creative
 *    type — as it is not a placement — and text, hologram and audio were all
 *    missing, which is three whole formats an advertiser could not discover.
 *  - "checked for existence" is not a thing `validate_destination_url` (:253)
 *    does. It is a prefix test: the path must start `/pulse/`, and `/pulse/admin`
 *    and `/pulse/api` are refused. Nothing is looked up.
 *  - "an external link that must be HTTPS" — the check is
 *    `parsed.scheme not in {"https", "http"}` (:272). Plain http is accepted.
 *
 * Block two survives mostly intact, because media rights genuinely are enforced:
 * `_owned_ad_media_asset` (:332) requires the asset to belong to the same owner
 * and ad account, `_asset_type_allowed` (:304) is called on creation (:907), and
 * pasted media URLs are refused outright (:894). The one claim dropped is
 * "finished processing" — the only readiness gate is a non-empty public URL
 * (:383), and `processing_status` is copied into metadata unvalidated.
 */
const CREATIVE_BLOCKS: Block[] = [
  {
    title: "What counts as a creative",
    body: "A creative is a headline, body, call to action and destination, plus media for the formats that need it. It belongs to a campaign and carries its own review status, separate from the campaign's. Every placement accepts every one of these five formats.",
    points: [
      "Image — needs an uploaded image",
      "Video — needs an uploaded video",
      "Audio — needs an uploaded audio file",
      "Text — no media",
      "Hologram — no media"
    ]
  },
  {
    title: "Media has to be yours",
    body: "A creative references media you already uploaded to PulseSoc, by id. The server checks it belongs to you and to this ad account, that it hasn't been deleted, and that its type matches the creative type — a video creative will not accept an image. Custom thumbnails have to be images. Pasting a media URL instead of uploading is refused outright, so there is no way to point an ad at a file you don't own."
  },
  {
    title: "Where a creative can send people",
    body: "A destination is required, and it is checked for shape rather than for whether it works. That distinction is worth knowing: a link to a post you later delete is still accepted, and you will still pay for the clicks. Test your destinations yourself.",
    points: [
      "A PulseSoc path starting /pulse/ — anything else on the site is refused",
      "/pulse/admin and /pulse/api are refused",
      "An external http or https address — http is accepted, so use https yourself",
      "Local and loopback addresses are refused, as are javascript:, data: and file: links"
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
          <>
            <PlacementCatalogue reducedMotion={reducedMotion} entranceStyle={entrance.styleFor(1)} />
            <Animated.View style={[styles.stack, entrance.styleFor(2)]}>
              {AUDIENCE_BLOCKS.map((block) => (
                <BlockCard key={block.title} block={block} />
              ))}
            </Animated.View>
          </>
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
              {/* Was "Verification Center", which opens the profile-badge
                  track at `/api/dashboard/account/verification/request`. That
                  flow never writes `pulse_ad_accounts.status`, and `select_ads`
                  reads nothing else — so an advertiser could finish it, be
                  approved, and still deliver nothing. This goes to the account
                  surface instead, which states the standing, renders the
                  rejection reason, and carries the request the server accepts. */}
              <ActionButton
                label="Account standing and verification"
                onPress={openAccount}
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
  onWallet
}: {
  requestedAccountId?: number;
  reducedMotion: boolean;
  entranceStyle: any;
  onWallet: (accountId?: number) => void;
}) {
  const [account, setAccount] = useState<AdAccount | null>(null);
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [verifying, setVerifying] = useState(false);
  const [verifyNote, setVerifyNote] = useState("");

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

  /**
   * The request that actually moves this record.
   *
   * The link here used to open the Verification Center, which decides a profile
   * badge through `/api/dashboard/account/verification/request`. Nothing in that
   * flow writes `pulse_ad_accounts.status`, and `select_ads` reads nothing else
   * — so the page that exists to explain this account's standing pointed at a
   * process that could not change it.
   */
  const requestVerification = useCallback(async () => {
    if (!account || verifying) return;
    setVerifying(true);
    setVerifyNote("");
    try {
      await requestAdAccountVerification(account.id);
      setVerifyNote("Verification requested. We'll tell you as soon as it's decided.");
      await load();
    } catch (error) {
      setVerifyNote(
        error instanceof Error ? error.message : "Verification couldn't be requested. Try again."
      );
    } finally {
      setVerifying(false);
    }
  }, [account, load, verifying]);

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
        <AccountVerificationAction
          account={account}
          busy={verifying}
          note={verifyNote}
          onRequest={requestVerification}
        />
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
 * The account's verification, stated and — where there is something to do —
 * actionable, on the page that exists to explain this account's standing.
 *
 * Four states, four different things to say. The rejection reason is rendered
 * here because a decision the advertiser cannot read is a decision they cannot
 * answer: §37 requires the policy reason and the appeal path to be reachable,
 * and for an ad account this page is where they live.
 */
function AccountVerificationAction({
  account,
  busy,
  note,
  onRequest
}: {
  account: AdAccount;
  busy: boolean;
  note: string;
  onRequest: () => void;
}) {
  const state = accountVerificationState(account);
  const reason = String((account as { verification_reason?: string }).verification_reason || "").trim();
  const requestable = state === "unverified" || state === "rejected";
  return (
    <View>
      {state === "rejected" ? (
        <Text style={styles.cardBody}>
          {reason
            ? `Why it was declined: ${reason}`
            : "No reason was recorded with the decision. Check your business details before requesting review again."}
        </Text>
      ) : null}
      {state === "pending" ? (
        <Text style={styles.cardBody}>
          Your request is in review. Nothing is charged while you wait, and drafts can still be
          created and edited.
        </Text>
      ) : null}
      {requestable ? (
        <Pressable
          onPress={busy ? undefined : onRequest}
          accessibilityRole="button"
          accessibilityState={{ busy, disabled: busy }}
          accessibilityLabel="Request verification for this ad account"
          hitSlop={6}
        >
          <Text style={styles.inlineLink}>
            {busy
              ? "Sending…"
              : state === "rejected"
                ? "Request review again ›"
                : "Request verification ›"}
          </Text>
        </Pressable>
      ) : null}
      {note ? (
        <Text style={styles.cardBody} accessibilityLiveRegion="polite">
          {note}
        </Text>
      ) : null}
    </View>
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
    return "This account can't deliver ads. Existing campaigns are stopped and new ones can't be submitted. Contact support for the reason and the appeal path — a suspension is lifted by review, not by requesting verification again.";
  }
  if (line.endsWith("Not configured")) {
    return "This account hasn't finished setup, so it can't deliver yet. Completing verification is the remaining step.";
  }
  return "PulseSoc reports a status for this account that this version of the app doesn't recognise, so nothing is assumed about whether it can deliver.";
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
/**
 * Where a campaign can actually run, read from the server.
 *
 * This replaces a two-item hardcoded list — "Feed" and "Reels" — that was wrong
 * on both counts. `seed_placements` writes twelve rows and none of them is
 * Reels; the ten the list omitted include Marketplace, Search and Pulse Radio,
 * which is to say the advertiser choosing where to spend could not see most of
 * the options. The portal already carried the real table as `portal.placements`
 * and no screen had ever read it.
 *
 * A failed fetch renders `Unavailable` with a retry rather than the old static
 * list, because a hardcoded fallback here is the §31 fake-value case: it would
 * present a guess in the shape of a fact, and the guess was already wrong once.
 */
function PlacementCatalogue({
  reducedMotion,
  entranceStyle
}: {
  reducedMotion: boolean;
  entranceStyle: any;
}) {
  const [state, setState] = useState<"loading" | "ready" | "unavailable">("loading");
  const [entries, setEntries] = useState<PlacementCatalogueEntry[]>([]);

  const load = useCallback(async () => {
    setState("loading");
    try {
      const { portal } = await getAdsPortal();
      const list = placementCatalogue(portal as AdsPortal);
      setEntries(list);
      // An empty catalogue from a successful call is still not something to
      // present as "there are no placements" — the portal ships a constant, so
      // an empty one means the payload changed shape, not that ads have nowhere
      // to run. Treating it as unavailable keeps the retry available.
      setState(list.length ? "ready" : "unavailable");
    } catch {
      setState("unavailable");
    }
  }, []);

  useEffect(() => {
    let active = true;
    load().catch(() => {
      if (active) setState("unavailable");
    });
    return () => {
      active = false;
    };
  }, [load]);

  if (state === "loading") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <View style={styles.card}>
          <AdsSkeletonBlock width="60%" height={16} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="90%" height={12} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="75%" height={12} reducedMotion={reducedMotion} />
        </View>
      </Animated.View>
    );
  }

  if (state === "unavailable") {
    return (
      <Animated.View style={[styles.stack, entranceStyle]}>
        <AdsSectionError
          message="The list of placements didn’t load. Your campaigns are unaffected — this is the catalogue, not their delivery."
          onRetry={() => {
            load().catch(() => undefined);
          }}
          reducedMotion={reducedMotion}
        />
      </Animated.View>
    );
  }

  return (
    <Animated.View style={[styles.stack, entranceStyle]}>
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Where your ads can appear</Text>
        <Text style={styles.cardBody}>
          Every campaign runs in the placements attached to it, and nowhere else. These are the{" "}
          {entries.length} that PulseSoc serves. Placements are attached in the campaign editor —
          this page reports them, it doesn’t change them.
        </Text>
      </View>
      {entries.map((entry) => (
        <View key={entry.key} style={styles.card}>
          <Text style={styles.cardTitle}>{entry.name}</Text>
          <Text style={styles.cardBody}>
            {entry.devices}
            {entry.maxFrequency
              ? ` · at most ${entry.maxFrequency} view${entry.maxFrequency === 1 ? "" : "s"} of one campaign per person`
              : ""}
          </Text>
        </View>
      ))}
    </Animated.View>
  );
}

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
