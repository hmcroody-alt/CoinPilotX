/**
 * The Advertising "Create campaign" wizard — the screen behind
 * `BusinessOsAdvertising { mode: "create" }`.
 *
 * One route, seven internal steps (Objective → Setup → Audience → Placements →
 * Creative → Budget & Delivery → Review) driven by step state inside the
 * persisted draft rather than by stack routes, exactly like the marketplace
 * listing composer this screen is modelled on. Draft persistence, the
 * Resume / Start over prompt and the success stage all mirror
 * `SellerListingComposerScreen`.
 *
 * Design system: WHITE commerce surface. Every colour comes from
 * `theme/storeLight`; the dark Advertising manager header is untouched because
 * this screen never renders it.
 *
 * Publish posts the whole draft to `POST /api/pulse/ads/campaigns/full` with
 * the idempotency key minted at draft creation, so a retry after a network
 * failure cannot double-create; `duplicate: true` is treated as success.
 */

import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  AD_CALL_TO_ACTIONS,
  AD_CANONICAL_OBJECTIVES,
  AD_PLACEMENT_KEYS,
  AdAudience,
  AdContentItem,
  AdContentKind,
  adMediaUploadId,
  AdReachEstimate,
  createFullAdCampaign,
  getAdContentInventory,
  listAdAudiences,
  previewAdTargetingEstimate,
  uploadAdMedia
} from "../api/adsOs";
import {
  AdAccount,
  adAccountCanTransact,
  AdWallet,
  formatCents,
  getAdWallet,
  listAdAccounts,
  loadCachedAdAccounts
} from "../api/businessOs";
import {
  WizardCard,
  WizardErrorText,
  WizardHint,
  WizardOption,
  WizardPrimaryButton,
  WizardRadioGroup,
  WizardSecondaryButton,
  WizardSegmented,
  WizardSelect,
  WizardSectionTitle,
  WizardStepper,
  WizardTextField
} from "../components/listingWizard/controls";
import { invalidateNativeSync } from "../core/eventSync";
import { useFormatters, useTranslation } from "../i18n";
import {
  AD_OBJECTIVE_METADATA,
  AD_PLACEMENT_LABEL_KEYS,
  AD_SPECIAL_CATEGORIES,
  buildCampaignTargetingPayload,
  buildFullCampaignPayload,
  CAMPAIGN_WIZARD_STEPS,
  CampaignDraft,
  CampaignDraftIssue,
  campaignDraftIssueFor,
  CampaignWizardStep,
  HEADLINE_MAX_LENGTH,
  MAX_TARGET_AGE,
  MIN_TARGET_AGE,
  PRIMARY_TEXT_MAX_LENGTH,
  validateCampaignStep
} from "../advertising/campaignDraft";
import {
  clearCampaignDraft,
  hydrateCampaignDraft,
  persistCampaignDraft,
  updateCampaignDraft,
  useCampaignDraft
} from "../advertising/campaignDraftStore";
import { useNativeMediaUpload } from "../media/useNativeMediaUpload";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { storeLight } from "../theme/storeLight";

type Props = {
  route?: { params?: { title?: string; accountId?: number } };
  navigation?: {
    navigate: (...args: any[]) => void;
    goBack?: () => void;
  };
};

const ESTIMATE_DEBOUNCE_MS = 700;

const NS = "commerce:adsWizard";

const CONTENT_KIND_LABEL_KEYS: Record<AdContentKind, string> = {
  post: "kindPost",
  reel: "kindReel",
  video: "kindVideo",
  event: "kindEvent",
  listing: "kindListing"
};

export function AdsCampaignWizardScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const insets = useSafeAreaInsets();
  const draft = useCampaignDraft();

  const [resumePrompt, setResumePrompt] = useState(false);
  const [issues, setIssues] = useState<CampaignDraftIssue[]>([]);
  const [attempted, setAttempted] = useState(false);
  const [openSelect, setOpenSelect] = useState<string | null>(null);

  const [account, setAccount] = useState<AdAccount | null>(null);

  const [audiences, setAudiences] = useState<AdAudience[]>([]);
  const [audiencesError, setAudiencesError] = useState(false);
  const [audiencesLoaded, setAudiencesLoaded] = useState(false);

  const [estimate, setEstimate] = useState<AdReachEstimate | null>(null);
  const [estimateLoading, setEstimateLoading] = useState(false);
  const [estimateFailed, setEstimateFailed] = useState(false);

  const [contentOpen, setContentOpen] = useState(false);
  const [contentItems, setContentItems] = useState<AdContentItem[]>([]);
  const [contentLoaded, setContentLoaded] = useState(false);
  const [contentError, setContentError] = useState(false);

  const [mediaBusy, setMediaBusy] = useState(false);
  const [mediaError, setMediaError] = useState("");

  const [wallet, setWallet] = useState<AdWallet | null>(null);

  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState("");
  const [published, setPublished] = useState(false);
  const [publishBlockers, setPublishBlockers] = useState<string[]>([]);
  const [draftSavedNote, setDraftSavedNote] = useState(false);

  const media = useNativeMediaUpload({ contextType: "ad_creative" });

  /* -------------------------------------------------------------- *
   * Hydration + account resolution
   * -------------------------------------------------------------- */

  useEffect(() => {
    let cancelled = false;
    hydrateCampaignDraft()
      .then((stored) => {
        if (!cancelled && stored) setResumePrompt(true);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const routeAccountId = Number(route?.params?.accountId || 0);

  useEffect(() => {
    let cancelled = false;
    const apply = (accounts: AdAccount[]) => {
      if (cancelled || !accounts.length) return;
      const chosen = accounts.find((row) => row.id === routeAccountId) || accounts[0];
      setAccount(chosen);
      updateCampaignDraft((current) =>
        current.accountId > 0 ? current : { ...current, accountId: chosen.id }
      );
    };
    loadCachedAdAccounts().then(apply).catch(() => undefined);
    listAdAccounts()
      .then((data) => apply(data.accounts))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [routeAccountId]);

  /** Live re-validation once the advertiser has tried to continue. */
  useEffect(() => {
    if (attempted) setIssues(validateCampaignStep(draft.step, draft));
  }, [attempted, draft]);

  const issueFor = useCallback(
    (field: string) => {
      const key = campaignDraftIssueFor(issues, field);
      return key ? t(key) : "";
    },
    [issues, t]
  );

  /* -------------------------------------------------------------- *
   * Step navigation
   * -------------------------------------------------------------- */

  const stepIndex = CAMPAIGN_WIZARD_STEPS.indexOf(draft.step);

  const goToStep = useCallback((step: CampaignWizardStep) => {
    setAttempted(false);
    setIssues([]);
    updateCampaignDraft({ step });
    void persistCampaignDraft();
  }, []);

  const continueFromStep = useCallback(() => {
    setAttempted(true);
    const found = validateCampaignStep(draft.step, draft);
    setIssues(found);
    if (found.length) return;
    const next = CAMPAIGN_WIZARD_STEPS[Math.min(stepIndex + 1, CAMPAIGN_WIZARD_STEPS.length - 1)];
    goToStep(next);
  }, [draft, goToStep, stepIndex]);

  const goBackStep = useCallback(() => {
    if (stepIndex <= 0) {
      navigation?.goBack?.();
      return;
    }
    goToStep(CAMPAIGN_WIZARD_STEPS[stepIndex - 1]);
  }, [goToStep, navigation, stepIndex]);

  const saveDraftAndExit = useCallback(async () => {
    await persistCampaignDraft();
    setDraftSavedNote(true);
    navigation?.goBack?.();
  }, [navigation]);

  const chooseObjective = useCallback((objective: (typeof AD_CANONICAL_OBJECTIVES)[number]) => {
    updateCampaignDraft((current) => ({
      ...current,
      objective,
      step: "setup",
      creative: {
        ...current.creative,
        callToAction: AD_OBJECTIVE_METADATA[objective].defaultCallToAction
      }
    }));
    void persistCampaignDraft();
  }, []);

  /* -------------------------------------------------------------- *
   * Audience data — saved audiences and the debounced reach estimate
   * -------------------------------------------------------------- */

  useEffect(() => {
    if (draft.step !== "audience" || audiencesLoaded || draft.accountId <= 0) return;
    let cancelled = false;
    listAdAudiences(draft.accountId)
      .then((data) => {
        if (cancelled) return;
        setAudiences(data.audiences);
        setAudiencesLoaded(true);
        setAudiencesError(false);
      })
      .catch(() => {
        if (!cancelled) {
          setAudiencesError(true);
          setAudiencesLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [draft.step, draft.accountId, audiencesLoaded]);

  const targetingSignature = JSON.stringify(buildCampaignTargetingPayload(draft));
  const estimateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (draft.step !== "audience") return;
    setEstimateLoading(true);
    if (estimateTimer.current) clearTimeout(estimateTimer.current);
    let cancelled = false;
    estimateTimer.current = setTimeout(() => {
      previewAdTargetingEstimate(JSON.parse(targetingSignature))
        .then((data) => {
          if (cancelled) return;
          setEstimate(data.estimate);
          setEstimateFailed(!data.estimate);
          setEstimateLoading(false);
        })
        .catch(() => {
          if (cancelled) return;
          setEstimateFailed(true);
          setEstimateLoading(false);
        });
    }, ESTIMATE_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      if (estimateTimer.current) {
        clearTimeout(estimateTimer.current);
        estimateTimer.current = null;
      }
    };
  }, [draft.step, targetingSignature]);

  /* -------------------------------------------------------------- *
   * Content inventory + ad media upload
   * -------------------------------------------------------------- */

  useEffect(() => {
    if (draft.step !== "creative" || contentLoaded) return;
    let cancelled = false;
    getAdContentInventory({ limit: 25 })
      .then((data) => {
        if (cancelled) return;
        setContentItems(data.items);
        setContentLoaded(true);
        setContentError(false);
      })
      .catch(() => {
        if (!cancelled) {
          setContentError(true);
          setContentLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [draft.step, contentLoaded]);

  const pickContent = useCallback((item: AdContentItem) => {
    if (!item.eligible) return;
    updateCampaignDraft((current) => ({
      ...current,
      creative: {
        ...current.creative,
        source: "existing",
        contentRefType: item.kind as AdContentKind,
        contentRefId: item.id,
        contentTitle: item.title,
        contentThumbnailUrl: item.thumbnail_url || ""
      }
    }));
    setContentOpen(false);
    void persistCampaignDraft();
  }, []);

  const addCreativeMedia = useCallback(
    async (kind: "image" | "video") => {
      setMediaError("");
      const asset = kind === "video" ? await media.chooseVideo() : await media.chooseImage();
      if (!asset) return;
      setMediaBusy(true);
      try {
        const uploaded = await uploadAdMedia(draft.accountId, {
          uri: asset.uri,
          name: asset.name,
          mimeType: asset.mimeType
        });
        const mediaId = adMediaUploadId(uploaded);
        if (mediaId > 0) {
          updateCampaignDraft((current) => ({
            ...current,
            creative: {
              ...current.creative,
              source: "new",
              newMediaKind: kind,
              mediaAssetId: mediaId,
              mediaPreviewUri: asset.uri
            }
          }));
          void persistCampaignDraft();
        } else {
          setMediaError(t(`${NS}.errorMediaUpload`));
        }
      } catch {
        setMediaError(t(`${NS}.errorMediaUpload`));
      } finally {
        setMediaBusy(false);
        media.reset();
      }
    },
    [draft.accountId, media, t]
  );

  const removeCreativeMedia = useCallback(() => {
    updateCampaignDraft((current) => ({
      ...current,
      creative: { ...current.creative, mediaAssetId: 0, mediaPreviewUri: "" }
    }));
  }, []);

  /* -------------------------------------------------------------- *
   * Wallet — loaded when the review step opens
   * -------------------------------------------------------------- */

  useEffect(() => {
    if (draft.step !== "review" || draft.accountId <= 0) return;
    let cancelled = false;
    getAdWallet(draft.accountId)
      .then((data) => {
        if (!cancelled) setWallet(data.wallet);
      })
      .catch(() => {
        if (!cancelled) setWallet({ unavailable: true });
      });
    return () => {
      cancelled = true;
    };
  }, [draft.step, draft.accountId]);

  /* -------------------------------------------------------------- *
   * Publish
   * -------------------------------------------------------------- */

  const publish = useCallback(async () => {
    setAttempted(true);
    const found = validateCampaignStep("review", draft);
    setIssues(found);
    if (found.length) return;
    setPublishing(true);
    setPublishError("");
    try {
      const payload = buildFullCampaignPayload(draft, { submit: true });
      const result = await createFullAdCampaign(payload);
      if (result.ok || result.duplicate) {
        setPublishBlockers(result.blockers);
        await clearCampaignDraft();
        await invalidateNativeSync(["ads", "marketplace", "verification"], "ads_wizard_publish", [
          {
            event_type: "ad_campaign_created",
            entity_type: "ad_campaign",
            entity_id: result.campaign?.id
          }
        ]).catch(() => undefined);
        setPublished(true);
      } else {
        setPublishBlockers(result.blockers);
        setPublishError(t(`${NS}.errorPublish`));
      }
    } catch (error) {
      // Draft is preserved on purpose — nothing was cleared.
      setPublishError(error instanceof Error ? error.message : t(`${NS}.errorPublish`));
    } finally {
      setPublishing(false);
    }
  }, [draft, t]);

  const openPayments = useCallback(() => {
    navigation?.navigate("BusinessOsPayments", { accountId: draft.accountId || account?.id });
  }, [account, draft.accountId, navigation]);

  const viewCampaigns = useCallback(() => {
    navigation?.navigate("BusinessOsAdvertising", { mode: "manager" });
  }, [navigation]);

  /* -------------------------------------------------------------- *
   * Derived labels
   * -------------------------------------------------------------- */

  const budgetSummary = useMemo(() => {
    const amount = Number(draft.setup.budgetAmount);
    if (!draft.setup.budgetAmount.trim() || !Number.isFinite(amount) || amount <= 0) return "";
    const formatted = formatCents(Math.round(amount * 100));
    return draft.setup.budgetType === "lifetime"
      ? t(`${NS}.budgetLifetimeSummary`, { amount: formatted })
      : t(`${NS}.budgetDailySummary`, { amount: formatted });
  }, [draft.setup.budgetAmount, draft.setup.budgetType, t]);

  const scheduleSummary = useMemo(() => {
    if (!draft.setup.startDate.trim()) return "";
    return draft.setup.endDate.trim()
      ? t(`${NS}.scheduleRange`, { start: draft.setup.startDate.trim(), end: draft.setup.endDate.trim() })
      : t(`${NS}.scheduleOpenEnded`, { start: draft.setup.startDate.trim() });
  }, [draft.setup.endDate, draft.setup.startDate, t]);

  /* -------------------------------------------------------------- *
   * Success + resume stages
   * -------------------------------------------------------------- */

  if (published) {
    return (
      <View style={[styles.root, styles.centerFill]}>
        <View style={styles.successBlock}>
          <View style={styles.successBadge}>
            <Ionicons name="checkmark" size={40} color={storeLight.cta.text} />
          </View>
          <Text style={styles.successTitle}>{t(`${NS}.successTitle`)}</Text>
          <View style={styles.statusPill}>
            <Text style={styles.statusPillText}>{t(`${NS}.successStatus`)}</Text>
          </View>
          <Text style={styles.successBody}>{t(`${NS}.successBody`)}</Text>
          {publishBlockers.length > 0 ? (
            <View style={styles.blockersBlock}>
              <Text style={styles.blockersTitle}>{t(`${NS}.blockersTitle`)}</Text>
              {publishBlockers.map((blocker) => (
                <Text key={blocker} style={styles.blockerLine}>
                  {`• ${blocker}`}
                </Text>
              ))}
            </View>
          ) : null}
          <View style={styles.successActions}>
            <WizardPrimaryButton label={t(`${NS}.viewCampaigns`)} onPress={viewCampaigns} />
          </View>
        </View>
      </View>
    );
  }

  if (resumePrompt) {
    return (
      <View style={[styles.root, styles.centerFill]}>
        <WizardCard style={styles.resumeCard}>
          <Ionicons name="megaphone-outline" size={30} color={storeLight.accent.brandOnLight} />
          <Text style={styles.resumeTitle}>{t(`${NS}.resumeTitle`)}</Text>
          <Text style={styles.resumeBody}>{t(`${NS}.resumeBody`)}</Text>
          <WizardPrimaryButton label={t(`${NS}.resume`)} onPress={() => setResumePrompt(false)} />
          <WizardSecondaryButton
            label={t(`${NS}.startOver`)}
            onPress={() => {
              void clearCampaignDraft().then(() => {
                if (account) updateCampaignDraft({ accountId: account.id });
              });
              setAttempted(false);
              setIssues([]);
              setResumePrompt(false);
            }}
          />
        </WizardCard>
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Frame
   * -------------------------------------------------------------- */

  return (
    <View style={styles.root}>
      <View style={styles.wizardBar}>
        <Pressable
          style={styles.barButton}
          onPress={goBackStep}
          accessibilityRole="button"
          accessibilityLabel={t(`${NS}.backLabel`)}
        >
          <Ionicons name="chevron-back" size={22} color={storeLight.text.primary} />
        </Pressable>
        <View style={styles.stepIndicator}>
          <Text style={styles.stepText}>
            {t(`${NS}.stepIndicator`, { current: stepIndex + 1, total: CAMPAIGN_WIZARD_STEPS.length })}
          </Text>
          <Text style={styles.stepLabel}>{t(`${NS}.steps.${draft.step}`)}</Text>
        </View>
        <Pressable
          style={styles.barAction}
          onPress={() => void saveDraftAndExit()}
          accessibilityRole="button"
          accessibilityLabel={t(`${NS}.saveDraft`)}
        >
          <Text style={styles.barActionText}>{t(`${NS}.saveDraft`)}</Text>
        </Pressable>
      </View>
      {draftSavedNote ? <Text style={styles.savedNote}>{t(`${NS}.draftSaved`)}</Text> : null}

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={[
          styles.scrollContent,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        keyboardShouldPersistTaps="handled"
      >
        {draft.step === "objective" ? renderObjective() : null}
        {draft.step === "setup" ? renderSetup() : null}
        {draft.step === "audience" ? renderAudience() : null}
        {draft.step === "placements" ? renderPlacements() : null}
        {draft.step === "creative" ? renderCreative() : null}
        {draft.step === "budget" ? renderBudget() : null}
        {draft.step === "review" ? renderReview() : null}
      </ScrollView>
    </View>
  );

  /* -------------------------------------------------------------- *
   * Step 1 — objective
   * -------------------------------------------------------------- */

  function renderObjective() {
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>{t(`${NS}.objectiveTitle`)}</Text>
        <Text style={styles.pageSubtitle}>{t(`${NS}.objectiveSubtitle`)}</Text>
        {AD_CANONICAL_OBJECTIVES.map((objective) => {
          const meta = AD_OBJECTIVE_METADATA[objective];
          const active = draft.objective === objective;
          return (
            <Pressable
              key={objective}
              style={[styles.objectiveCard, active && styles.objectiveCardActive]}
              onPress={() => chooseObjective(objective)}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              accessibilityLabel={t(`${NS}.${meta.titleKey}`)}
            >
              <View style={styles.objectiveIconWrap}>
                <Ionicons
                  name={meta.icon as keyof typeof Ionicons.glyphMap}
                  size={22}
                  color={storeLight.accent.brandOnLight}
                />
              </View>
              <View style={styles.objectiveTextBlock}>
                <Text style={styles.objectiveTitle}>{t(`${NS}.${meta.titleKey}`)}</Text>
                <Text style={styles.objectiveCaption}>{t(`${NS}.${meta.captionKey}`)}</Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={storeLight.text.muted} />
            </Pressable>
          );
        })}
        <WizardErrorText text={issueFor("objective")} />
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 2 — campaign setup
   * -------------------------------------------------------------- */

  function renderSetup() {
    return (
      <View style={styles.stack}>
        <WizardCard>
          <WizardSectionTitle text={t(`${NS}.setupTitle`)} />
          <WizardTextField
            label={t(`${NS}.nameLabel`)}
            value={draft.setup.name}
            onChangeText={(name) => updateCampaignDraft((c) => ({ ...c, setup: { ...c.setup, name } }))}
            placeholder={t(`${NS}.namePlaceholder`)}
            error={issueFor("name")}
            maxLength={120}
          />
          <WizardRadioGroup
            label={t(`${NS}.surfaceLabel`)}
            options={[
              {
                key: "post" as const,
                label: t(`${NS}.surfacePost`),
                caption: t(`${NS}.surfacePostCaption`)
              },
              {
                key: "marketplace" as const,
                label: t(`${NS}.surfaceMarketplace`),
                caption: t(`${NS}.surfaceMarketplaceCaption`)
              }
            ]}
            value={draft.setup.adSurface}
            onChange={(adSurface) => updateCampaignDraft((c) => ({ ...c, setup: { ...c.setup, adSurface } }))}
          />
          <WizardSegmented
            label={t(`${NS}.budgetTypeLabel`)}
            options={[
              { key: "daily" as const, label: t(`${NS}.budgetDaily`) },
              { key: "lifetime" as const, label: t(`${NS}.budgetLifetime`) }
            ]}
            value={draft.setup.budgetType}
            onChange={(budgetType) => updateCampaignDraft((c) => ({ ...c, setup: { ...c.setup, budgetType } }))}
          />
          <WizardTextField
            label={t(
              draft.setup.budgetType === "lifetime" ? `${NS}.budgetAmountLifetimeLabel` : `${NS}.budgetAmountDailyLabel`
            )}
            value={draft.setup.budgetAmount}
            onChangeText={(value) =>
              updateCampaignDraft((c) => ({
                ...c,
                setup: { ...c.setup, budgetAmount: value.replace(/[^0-9.]/g, "") }
              }))
            }
            placeholder={t(`${NS}.budgetPlaceholder`)}
            error={issueFor("budgetAmount")}
            keyboardType="decimal-pad"
          />
          <WizardTextField
            label={t(`${NS}.startDateLabel`)}
            value={draft.setup.startDate}
            onChangeText={(value) =>
              updateCampaignDraft((c) => ({
                ...c,
                setup: { ...c.setup, startDate: value.replace(/[^0-9-]/g, "").slice(0, 10) }
              }))
            }
            placeholder="YYYY-MM-DD"
            error={issueFor("startDate")}
            keyboardType="numbers-and-punctuation"
          />
          <WizardTextField
            label={t(
              draft.setup.budgetType === "lifetime" ? `${NS}.endDateLifetimeLabel` : `${NS}.endDateLabel`
            )}
            value={draft.setup.endDate}
            onChangeText={(value) =>
              updateCampaignDraft((c) => ({
                ...c,
                setup: { ...c.setup, endDate: value.replace(/[^0-9-]/g, "").slice(0, 10) }
              }))
            }
            placeholder="YYYY-MM-DD"
            error={issueFor("endDate")}
            keyboardType="numbers-and-punctuation"
          />
          <WizardHint text={t(`${NS}.dateHint`)} />
          <WizardSelect
            label={t(`${NS}.specialCategoryLabel`)}
            sheetTitle={t(`${NS}.specialCategoryLabel`)}
            options={AD_SPECIAL_CATEGORIES.map((key) => ({
              key: key || "none",
              label: t(`${NS}.${specialCategoryKey(key)}`)
            }))}
            selectedKey={draft.setup.specialCategory || "none"}
            onSelect={(key) =>
              updateCampaignDraft((c) => ({
                ...c,
                setup: {
                  ...c.setup,
                  specialCategory: key === "none" ? "" : (key as CampaignDraft["setup"]["specialCategory"])
                }
              }))
            }
            open={openSelect === "specialCategory"}
            onOpen={() => setOpenSelect("specialCategory")}
            onClose={() => setOpenSelect(null)}
          />
          <WizardHint text={t(`${NS}.specialCategoryHint`)} />
        </WizardCard>
        {renderContinue()}
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 3 — audience
   * -------------------------------------------------------------- */

  function renderAudience() {
    return (
      <View style={styles.stack}>
        <WizardCard>
          <WizardSectionTitle text={t(`${NS}.audienceTitle`)} />
          <WizardHint text={t(`${NS}.audienceHint`)} />
          {renderChipListEditor("countries", t(`${NS}.countriesLabel`), t(`${NS}.countryPlaceholder`))}
          {renderChipListEditor("languages", t(`${NS}.languagesLabel`), t(`${NS}.languagePlaceholder`))}
          <Text style={styles.groupLabel}>{t(`${NS}.ageRangeLabel`)}</Text>
          <View style={styles.twoColumns}>
            <View style={styles.columnHalf}>
              <WizardStepper
                label={t(`${NS}.minAgeLabel`)}
                value={draft.audience.minAge}
                min={MIN_TARGET_AGE}
                max={draft.audience.maxAge}
                onChange={(minAge) =>
                  updateCampaignDraft((c) => ({ ...c, audience: { ...c.audience, minAge } }))
                }
              />
            </View>
            <View style={styles.columnHalf}>
              <WizardStepper
                label={t(`${NS}.maxAgeLabel`)}
                value={draft.audience.maxAge}
                min={draft.audience.minAge}
                max={MAX_TARGET_AGE}
                onChange={(maxAge) =>
                  updateCampaignDraft((c) => ({ ...c, audience: { ...c.audience, maxAge } }))
                }
              />
            </View>
          </View>
          <WizardErrorText text={issueFor("ageRange")} />
          <WizardSegmented
            label={t(`${NS}.deviceLabel`)}
            options={[
              { key: "all" as const, label: t(`${NS}.deviceAll`) },
              { key: "mobile" as const, label: t(`${NS}.deviceMobile`) },
              { key: "desktop" as const, label: t(`${NS}.deviceDesktop`) }
            ]}
            value={draft.audience.deviceType}
            onChange={(deviceType) =>
              updateCampaignDraft((c) => ({ ...c, audience: { ...c.audience, deviceType } }))
            }
          />
          {renderChipListEditor("interests", t(`${NS}.interestsLabel`), t(`${NS}.interestPlaceholder`))}
          {renderChipListEditor("keywords", t(`${NS}.keywordsLabel`), t(`${NS}.keywordPlaceholder`))}
          <WizardSegmented
            label={t(`${NS}.audienceModeLabel`)}
            options={[
              { key: "everyone" as const, label: t(`${NS}.modeEveryone`) },
              { key: "followers" as const, label: t(`${NS}.modeFollowers`) },
              { key: "non_followers" as const, label: t(`${NS}.modeNonFollowers`) },
              { key: "engaged" as const, label: t(`${NS}.modeEngaged`) }
            ]}
            value={draft.audience.audienceMode}
            onChange={(audienceMode) =>
              updateCampaignDraft((c) => ({ ...c, audience: { ...c.audience, audienceMode } }))
            }
          />
        </WizardCard>

        <WizardCard>
          <WizardSectionTitle text={t(`${NS}.savedAudiencesLabel`)} />
          {audiencesError ? <WizardErrorText text={t(`${NS}.savedAudiencesError`)} /> : null}
          {!audiencesError && audiencesLoaded && audiences.length === 0 ? (
            <WizardHint text={t(`${NS}.savedAudiencesEmpty`)} />
          ) : null}
          {audiences.map((audience) => renderAudienceRow(audience, "savedAudienceIds"))}
          {audiences.length > 0 ? (
            <>
              <Text style={styles.groupLabel}>{t(`${NS}.excludedAudiencesLabel`)}</Text>
              {audiences.map((audience) => renderAudienceRow(audience, "excludedAudienceIds"))}
            </>
          ) : null}
        </WizardCard>

        {renderEstimateCard()}
        {renderContinue()}
      </View>
    );
  }

  function renderAudienceRow(audience: AdAudience, field: "savedAudienceIds" | "excludedAudienceIds") {
    const selected = draft.audience[field].includes(audience.id);
    return (
      <Pressable
        key={`${field}-${audience.id}`}
        style={styles.audienceRow}
        onPress={() =>
          updateCampaignDraft((c) => ({
            ...c,
            audience: {
              ...c.audience,
              [field]: selected
                ? c.audience[field].filter((id) => id !== audience.id)
                : [...c.audience[field], audience.id]
            }
          }))
        }
        accessibilityRole="checkbox"
        accessibilityState={{ checked: selected }}
        accessibilityLabel={audience.name}
      >
        <Ionicons
          name={selected ? "checkbox" : "square-outline"}
          size={20}
          color={selected ? storeLight.accent.brandOnLight : storeLight.text.muted}
        />
        <View style={styles.audienceTextBlock}>
          <Text style={styles.audienceName}>{audience.name}</Text>
          <Text style={styles.audienceMeta}>
            {t(`${NS}.peopleCount`, { count: audience.estimated_size || 0 })}
          </Text>
        </View>
      </Pressable>
    );
  }

  function renderChipListEditor(
    field: "countries" | "languages" | "interests" | "keywords",
    label: string,
    placeholder: string
  ) {
    return (
      <ChipListEditor
        key={field}
        label={label}
        placeholder={placeholder}
        addLabel={t(`${NS}.add`)}
        removeLabel={t(`${NS}.remove`)}
        values={draft.audience[field]}
        onChange={(values) =>
          updateCampaignDraft((c) => ({ ...c, audience: { ...c.audience, [field]: values } }))
        }
      />
    );
  }

  function renderEstimateCard() {
    const bandKey =
      estimate?.band === "narrow" ? "bandNarrow" : estimate?.band === "broad" ? "bandBroad" : "bandGood";
    return (
      <WizardCard>
        <WizardSectionTitle text={t(`${NS}.estimateTitle`)} />
        {estimateLoading ? <WizardHint text={t(`${NS}.estimateLoading`)} /> : null}
        {!estimateLoading && estimateFailed ? <WizardHint text={t(`${NS}.estimateUnavailable`)} /> : null}
        {!estimateLoading && !estimateFailed && estimate ? (
          <>
            <Text style={styles.estimateValue}>
              {t(`${NS}.estimateRange`, {
                min: formatters.number(estimate.estimated_min),
                max: formatters.number(estimate.estimated_max)
              })}
            </Text>
            <View
              style={[
                styles.bandPill,
                estimate.band === "good" ? styles.bandPillGood : styles.bandPillWarn
              ]}
            >
              <Text
                style={[
                  styles.bandPillText,
                  estimate.band === "good" ? styles.bandPillTextGood : styles.bandPillTextWarn
                ]}
              >
                {t(`${NS}.${bandKey}`)}
              </Text>
            </View>
            {estimate.band === "narrow" ? <WizardHint text={t(`${NS}.narrowWarning`)} /> : null}
            {estimate.band === "broad" ? <WizardHint text={t(`${NS}.broadWarning`)} /> : null}
          </>
        ) : null}
      </WizardCard>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 4 — placements
   * -------------------------------------------------------------- */

  function renderPlacements() {
    return (
      <View style={styles.stack}>
        <WizardCard>
          <WizardSectionTitle text={t(`${NS}.placementsTitle`)} />
          <WizardRadioGroup
            options={[
              {
                key: "automatic" as const,
                label: t(`${NS}.automaticTitle`),
                caption: t(`${NS}.automaticCaption`)
              },
              {
                key: "manual" as const,
                label: t(`${NS}.manualTitle`),
                caption: t(`${NS}.manualCaption`)
              }
            ]}
            value={draft.placements.mode}
            onChange={(mode) => updateCampaignDraft((c) => ({ ...c, placements: { ...c.placements, mode } }))}
          />
          {draft.placements.mode === "manual" ? (
            <View style={styles.placementList}>
              {AD_PLACEMENT_KEYS.map((key) => {
                const selected = draft.placements.keys.includes(key);
                return (
                  <Pressable
                    key={key}
                    style={styles.audienceRow}
                    onPress={() =>
                      updateCampaignDraft((c) => ({
                        ...c,
                        placements: {
                          ...c.placements,
                          keys: selected
                            ? c.placements.keys.filter((existing) => existing !== key)
                            : [...c.placements.keys, key]
                        }
                      }))
                    }
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: selected }}
                    accessibilityLabel={t(`${NS}.${AD_PLACEMENT_LABEL_KEYS[key]}`)}
                  >
                    <Ionicons
                      name={selected ? "checkbox" : "square-outline"}
                      size={20}
                      color={selected ? storeLight.accent.brandOnLight : storeLight.text.muted}
                    />
                    <Text style={styles.audienceName}>{t(`${NS}.${AD_PLACEMENT_LABEL_KEYS[key]}`)}</Text>
                  </Pressable>
                );
              })}
            </View>
          ) : null}
          <WizardErrorText text={issueFor("placements")} />
        </WizardCard>
        {renderContinue()}
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 5 — creative
   * -------------------------------------------------------------- */

  function renderCreative() {
    const meta = draft.objective ? AD_OBJECTIVE_METADATA[draft.objective] : null;
    const usesContentDestination = draft.creative.source === "existing" && draft.creative.contentRefId > 0;
    return (
      <View style={styles.stack}>
        <WizardCard>
          <WizardSectionTitle text={t(`${NS}.creativeTitle`)} />
          <WizardRadioGroup
            options={[
              {
                key: "existing" as const,
                label: t(`${NS}.sourceExisting`),
                caption: t(`${NS}.sourceExistingCaption`)
              },
              {
                key: "new" as const,
                label: t(`${NS}.sourceNew`),
                caption: t(`${NS}.sourceNewCaption`)
              }
            ]}
            value={draft.creative.source}
            onChange={(source) => updateCampaignDraft((c) => ({ ...c, creative: { ...c.creative, source } }))}
          />

          {draft.creative.source === "existing" ? (
            <View style={styles.contentBlock}>
              {draft.creative.contentRefId > 0 ? (
                <View style={styles.contentPickedRow}>
                  {draft.creative.contentThumbnailUrl ? (
                    <Image source={{ uri: draft.creative.contentThumbnailUrl }} style={styles.contentThumb} />
                  ) : (
                    <View style={[styles.contentThumb, styles.contentThumbFallback]}>
                      <Ionicons name="image-outline" size={20} color={storeLight.text.muted} />
                    </View>
                  )}
                  <View style={styles.contentPickedText}>
                    <Text style={styles.audienceName} numberOfLines={1}>
                      {draft.creative.contentTitle}
                    </Text>
                    <Text style={styles.audienceMeta}>
                      {draft.creative.contentRefType
                        ? t(`${NS}.${CONTENT_KIND_LABEL_KEYS[draft.creative.contentRefType]}`)
                        : ""}
                    </Text>
                  </View>
                  <WizardSecondaryButton label={t(`${NS}.changeContent`)} onPress={() => setContentOpen(true)} />
                </View>
              ) : (
                <WizardSecondaryButton label={t(`${NS}.pickContent`)} onPress={() => setContentOpen(true)} />
              )}
              <WizardErrorText text={issueFor("contentRef")} />
            </View>
          ) : (
            <View style={styles.contentBlock}>
              <View style={styles.mediaRow}>
                {draft.creative.mediaAssetId > 0 ? (
                  <View style={styles.coverWrap}>
                    {draft.creative.mediaPreviewUri ? (
                      <Image source={{ uri: draft.creative.mediaPreviewUri }} style={styles.coverImage} />
                    ) : (
                      <View style={[styles.coverImage, styles.contentThumbFallback]}>
                        <Ionicons name="image-outline" size={26} color={storeLight.text.muted} />
                      </View>
                    )}
                    <Pressable
                      style={styles.coverRemove}
                      onPress={removeCreativeMedia}
                      accessibilityRole="button"
                      accessibilityLabel={t(`${NS}.removeMedia`)}
                    >
                      <Ionicons name="close" size={14} color={storeLight.text.onDark} />
                    </Pressable>
                  </View>
                ) : null}
                <Pressable
                  style={styles.mediaAdd}
                  onPress={() => void addCreativeMedia("image")}
                  disabled={mediaBusy}
                  accessibilityRole="button"
                  accessibilityLabel={t(`${NS}.uploadImage`)}
                >
                  <Ionicons name="camera-outline" size={22} color={storeLight.text.muted} />
                  <Text style={styles.mediaAddText}>{t(`${NS}.uploadImage`)}</Text>
                </Pressable>
                <Pressable
                  style={styles.mediaAdd}
                  onPress={() => void addCreativeMedia("video")}
                  disabled={mediaBusy}
                  accessibilityRole="button"
                  accessibilityLabel={t(`${NS}.uploadVideo`)}
                >
                  <Ionicons name="videocam-outline" size={22} color={storeLight.text.muted} />
                  <Text style={styles.mediaAddText}>{t(`${NS}.uploadVideo`)}</Text>
                </Pressable>
              </View>
              {mediaBusy ? <WizardHint text={t(`${NS}.uploading`)} /> : null}
              <WizardErrorText text={mediaError || issueFor("media")} />
            </View>
          )}
        </WizardCard>

        <WizardCard>
          <WizardTextField
            label={t(`${NS}.adTitleLabel`)}
            value={draft.creative.title}
            onChangeText={(title) => updateCampaignDraft((c) => ({ ...c, creative: { ...c.creative, title } }))}
            placeholder={t(`${NS}.adTitlePlaceholder`)}
            maxLength={80}
          />
          <WizardTextField
            label={t(`${NS}.primaryTextLabel`)}
            value={draft.creative.primaryText}
            onChangeText={(primaryText) =>
              updateCampaignDraft((c) => ({ ...c, creative: { ...c.creative, primaryText } }))
            }
            placeholder={t(`${NS}.primaryTextPlaceholder`)}
            error={issueFor("primaryText")}
            multiline
            maxLength={PRIMARY_TEXT_MAX_LENGTH}
            counterText={t(`${NS}.counter`, {
              used: draft.creative.primaryText.length,
              max: PRIMARY_TEXT_MAX_LENGTH
            })}
          />
          <WizardTextField
            label={t(`${NS}.headlineLabel`)}
            value={draft.creative.headline}
            onChangeText={(headline) =>
              updateCampaignDraft((c) => ({ ...c, creative: { ...c.creative, headline } }))
            }
            placeholder={t(`${NS}.headlinePlaceholder`)}
            error={issueFor("headline")}
            maxLength={HEADLINE_MAX_LENGTH}
            counterText={t(`${NS}.counter`, {
              used: draft.creative.headline.length,
              max: HEADLINE_MAX_LENGTH
            })}
          />
          <WizardTextField
            label={t(`${NS}.bodyLabel`)}
            value={draft.creative.body}
            onChangeText={(body) => updateCampaignDraft((c) => ({ ...c, creative: { ...c.creative, body } }))}
            placeholder={t(`${NS}.bodyPlaceholder`)}
            multiline
          />
          <WizardSelect
            label={t(`${NS}.ctaLabel`)}
            sheetTitle={t(`${NS}.ctaLabel`)}
            options={AD_CALL_TO_ACTIONS.map((cta) => ({ key: cta, label: t(`${NS}.${ctaKey(cta)}`) }))}
            selectedKey={draft.creative.callToAction}
            onSelect={(callToAction) =>
              updateCampaignDraft((c) => ({ ...c, creative: { ...c.creative, callToAction } }))
            }
            open={openSelect === "cta"}
            onOpen={() => setOpenSelect("cta")}
            onClose={() => setOpenSelect(null)}
          />
          {usesContentDestination && !meta?.requiresDestinationUrl ? (
            <WizardHint text={t(`${NS}.destinationAutoHint`)} />
          ) : (
            <WizardTextField
              label={t(`${NS}.destinationLabel`)}
              value={draft.creative.destinationUrl}
              onChangeText={(destinationUrl) =>
                updateCampaignDraft((c) => ({ ...c, creative: { ...c.creative, destinationUrl } }))
              }
              placeholder={t(`${NS}.destinationPlaceholder`)}
              error={issueFor("destinationUrl")}
              keyboardType="url"
              autoCapitalize="none"
            />
          )}
        </WizardCard>

        {renderContentPickerModal()}
        {renderContinue()}
      </View>
    );
  }

  function renderContentPickerModal() {
    const sections = (["post", "reel", "video", "event", "listing"] as AdContentKind[])
      .map((kind) => ({ kind, items: contentItems.filter((item) => item.kind === kind) }))
      .filter((section) => section.items.length > 0);
    return (
      <Modal visible={contentOpen} transparent animationType="fade" onRequestClose={() => setContentOpen(false)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setContentOpen(false)}>
          <Pressable style={styles.modalSheet} onPress={(event) => event.stopPropagation()}>
            <Text style={styles.modalTitle}>{t(`${NS}.pickContent`)}</Text>
            <ScrollView style={styles.modalList}>
              {contentError ? <WizardErrorText text={t(`${NS}.contentError`)} /> : null}
              {!contentError && contentLoaded && contentItems.length === 0 ? (
                <Text style={styles.audienceMeta}>{t(`${NS}.contentEmpty`)}</Text>
              ) : null}
              {sections.map((section) => (
                <View key={section.kind}>
                  <Text style={styles.modalSection}>{t(`${NS}.${CONTENT_KIND_LABEL_KEYS[section.kind]}`)}</Text>
                  {section.items.map((item) => (
                    <Pressable
                      key={`${item.kind}-${item.id}`}
                      style={[styles.contentRow, !item.eligible && styles.contentRowDisabled]}
                      onPress={() => pickContent(item)}
                      disabled={!item.eligible}
                      accessibilityRole="button"
                      accessibilityLabel={item.title}
                    >
                      {item.thumbnail_url ? (
                        <Image source={{ uri: item.thumbnail_url }} style={styles.contentThumb} />
                      ) : (
                        <View style={[styles.contentThumb, styles.contentThumbFallback]}>
                          <Ionicons name="image-outline" size={18} color={storeLight.text.muted} />
                        </View>
                      )}
                      <View style={styles.contentPickedText}>
                        <Text style={styles.audienceName} numberOfLines={1}>
                          {item.title}
                        </Text>
                        <Text style={styles.audienceMeta}>
                          {item.eligible
                            ? t(`${NS}.viewsCount`, { count: item.metrics?.views || 0 })
                            : item.ineligible_reason || t(`${NS}.contentIneligible`)}
                        </Text>
                      </View>
                    </Pressable>
                  ))}
                </View>
              ))}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 6 — budget & delivery
   * -------------------------------------------------------------- */

  function renderBudget() {
    const meta = draft.objective ? AD_OBJECTIVE_METADATA[draft.objective] : null;
    return (
      <View style={styles.stack}>
        <WizardCard>
          <WizardSectionTitle text={t(`${NS}.deliveryTitle`)} />
          <View style={styles.summaryRow}>
            <Text style={styles.summaryLabel}>{t(`${NS}.optimizationLabel`)}</Text>
            <Text style={styles.summaryValue}>{meta ? t(`${NS}.${meta.optimizationKey}`) : "—"}</Text>
          </View>
          <WizardHint text={t(`${NS}.optimizationHint`)} />
          <View style={styles.summaryRow}>
            <Text style={styles.summaryLabel}>{t(`${NS}.budgetTypeLabel`)}</Text>
            <Text style={styles.summaryValue}>{budgetSummary || "—"}</Text>
          </View>
          <View style={styles.summaryRow}>
            <Text style={styles.summaryLabel}>{t(`${NS}.scheduleLabel`)}</Text>
            <Text style={styles.summaryValue}>{scheduleSummary || "—"}</Text>
          </View>
          <View style={styles.noteBox}>
            <Ionicons name="information-circle-outline" size={18} color={storeLight.text.muted} />
            <Text style={styles.noteText}>{t(`${NS}.chargeNote`)}</Text>
          </View>
        </WizardCard>
        {renderContinue()}
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 7 — review & publish
   * -------------------------------------------------------------- */

  function renderReview() {
    const meta = draft.objective ? AD_OBJECTIVE_METADATA[draft.objective] : null;
    const verificationOk = String(account?.verification_status || "") === "verified";
    const accountActive = adAccountCanTransact(account || undefined);
    const walletZero =
      wallet && !wallet.unavailable && Number(wallet.spendable_balance_cents || 0) <= 0;
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>{t(`${NS}.reviewTitle`)}</Text>

        {renderReviewCard(t(`${NS}.reviewGoal`), "objective", [
          meta ? t(`${NS}.${meta.titleKey}`) : "—"
        ])}
        {renderReviewCard(t(`${NS}.reviewSetup`), "setup", [
          draft.setup.name.trim() || "—",
          budgetSummary || "—",
          scheduleSummary || "—",
          draft.setup.specialCategory ? t(`${NS}.${specialCategoryKey(draft.setup.specialCategory)}`) : ""
        ])}
        {renderReviewCard(t(`${NS}.reviewAudience`), "audience", [
          t(`${NS}.${audienceModeKey(draft.audience.audienceMode)}`),
          t(`${NS}.ageSummary`, { min: draft.audience.minAge, max: draft.audience.maxAge }),
          draft.audience.countries.join(", "),
          draft.audience.interests.join(", ")
        ])}
        {renderReviewCard(
          t(`${NS}.reviewPlacements`),
          "placements",
          draft.placements.mode === "automatic"
            ? [t(`${NS}.automaticTitle`)]
            : draft.placements.keys.map((key) =>
                isKnownPlacement(key) ? t(`${NS}.${AD_PLACEMENT_LABEL_KEYS[key]}`) : key
              )
        )}
        {renderReviewCard(t(`${NS}.reviewCreative`), "creative", [
          draft.creative.source === "existing"
            ? draft.creative.contentTitle || t(`${NS}.sourceExisting`)
            : t(`${NS}.sourceNew`),
          draft.creative.headline.trim(),
          t(`${NS}.${ctaKey(draft.creative.callToAction)}`),
          draft.creative.destinationUrl.trim()
        ])}

        <WizardCard>
          <WizardSectionTitle text={t(`${NS}.walletTitle`)} />
          {wallet?.unavailable ? <WizardHint text={t(`${NS}.walletUnavailable`)} /> : null}
          {wallet && !wallet.unavailable ? (
            <Text style={styles.summaryValue}>
              {t(`${NS}.walletBalance`, {
                amount: formatCents(wallet.spendable_balance_cents, wallet.currency)
              })}
            </Text>
          ) : null}
          {walletZero ? (
            <>
              <View style={styles.warnBox}>
                <Ionicons name="alert-circle-outline" size={18} color={storeLight.status.warning} />
                <Text style={styles.warnText}>{t(`${NS}.walletZero`)}</Text>
              </View>
              <WizardSecondaryButton label={t(`${NS}.addFunds`)} onPress={openPayments} />
            </>
          ) : null}
          {!verificationOk || !accountActive ? (
            <View style={styles.warnBox}>
              <Ionicons name="shield-outline" size={18} color={storeLight.status.warning} />
              <Text style={styles.warnText}>{t(`${NS}.verificationWarning`)}</Text>
            </View>
          ) : null}
        </WizardCard>

        {attempted && issues.length > 0 ? (
          <Text style={styles.formError}>{t(`${NS}.fixBeforeContinue`)}</Text>
        ) : null}
        {publishError ? <Text style={styles.formError}>{publishError}</Text> : null}
        {publishBlockers.length > 0 && !published ? (
          <View style={styles.blockersBlock}>
            <Text style={styles.blockersTitle}>{t(`${NS}.blockersTitle`)}</Text>
            {publishBlockers.map((blocker) => (
              <Text key={blocker} style={styles.blockerLine}>
                {`• ${blocker}`}
              </Text>
            ))}
          </View>
        ) : null}
        <WizardPrimaryButton
          label={publishing ? t(`${NS}.publishing`) : t(`${NS}.publish`)}
          onPress={() => void publish()}
          disabled={publishing}
        />
      </View>
    );
  }

  function renderReviewCard(title: string, step: CampaignWizardStep, lines: string[]) {
    const content = lines.map((line) => line.trim()).filter(Boolean);
    return (
      <WizardCard>
        <View style={styles.reviewHeader}>
          <WizardSectionTitle text={title} />
          <Pressable
            onPress={() => goToStep(step)}
            accessibilityRole="button"
            accessibilityLabel={t(`${NS}.edit`)}
          >
            <Text style={styles.editLink}>{t(`${NS}.edit`)}</Text>
          </Pressable>
        </View>
        {content.map((line, index) => (
          <Text key={`${step}-${index}`} style={styles.reviewLine}>
            {line}
          </Text>
        ))}
      </WizardCard>
    );
  }

  function renderContinue() {
    return (
      <>
        {attempted && issues.length > 0 ? (
          <Text style={styles.formError}>{t(`${NS}.fixBeforeContinue`)}</Text>
        ) : null}
        <WizardPrimaryButton
          label={t(`${NS}.continue`)}
          onPress={continueFromStep}
          disabled={attempted && issues.length > 0}
        />
      </>
    );
  }
}

/* ------------------------------------------------------------------ *
 * Chip list editor — free-text chips with add/remove
 * ------------------------------------------------------------------ */

function ChipListEditor({
  label,
  placeholder,
  addLabel,
  removeLabel,
  values,
  onChange
}: {
  label: string;
  placeholder: string;
  addLabel: string;
  removeLabel: string;
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const [pending, setPending] = useState("");
  const add = () => {
    const value = pending.trim();
    if (!value || values.includes(value)) {
      setPending("");
      return;
    }
    onChange([...values, value]);
    setPending("");
  };
  return (
    <View style={styles.chipEditor}>
      <Text style={styles.groupLabel}>{label}</Text>
      {values.length > 0 ? (
        <View style={styles.chipWrap}>
          {values.map((value) => (
            <Pressable
              key={value}
              style={styles.editChip}
              onPress={() => onChange(values.filter((existing) => existing !== value))}
              accessibilityRole="button"
              accessibilityLabel={`${removeLabel} ${value}`}
            >
              <Text style={styles.editChipText}>{value}</Text>
              <Ionicons name="close" size={13} color={storeLight.text.primary} />
            </Pressable>
          ))}
        </View>
      ) : null}
      <View style={styles.chipInputRow}>
        <View style={styles.chipInputWrap}>
          <WizardTextField value={pending} onChangeText={setPending} placeholder={placeholder} />
        </View>
        <WizardSecondaryButton label={addLabel} onPress={add} />
      </View>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Key helpers
 * ------------------------------------------------------------------ */

function specialCategoryKey(category: CampaignDraft["setup"]["specialCategory"] | "none" | ""): string {
  switch (category) {
    case "credit":
      return "specialCredit";
    case "employment":
      return "specialEmployment";
    case "housing":
      return "specialHousing";
    case "social":
      return "specialSocial";
    case "elections":
      return "specialElections";
    default:
      return "specialNone";
  }
}

function audienceModeKey(mode: CampaignDraft["audience"]["audienceMode"]): string {
  switch (mode) {
    case "followers":
      return "modeFollowers";
    case "non_followers":
      return "modeNonFollowers";
    case "engaged":
      return "modeEngaged";
    default:
      return "modeEveryone";
  }
}

function ctaKey(cta: string): string {
  switch (cta) {
    case "Shop Now":
      return "ctaShopNow";
    case "Send Message":
      return "ctaSendMessage";
    case "Sign Up":
      return "ctaSignUp";
    case "Watch More":
      return "ctaWatchMore";
    case "Get Offer":
      return "ctaGetOffer";
    case "Book Now":
      return "ctaBookNow";
    default:
      return "ctaLearnMore";
  }
}

function isKnownPlacement(key: string): key is (typeof AD_PLACEMENT_KEYS)[number] {
  return (AD_PLACEMENT_KEYS as readonly string[]).includes(key);
}

/* ------------------------------------------------------------------ *
 * Styles
 * ------------------------------------------------------------------ */

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  centerFill: { alignItems: "center", justifyContent: "center", padding: 24 },
  scroll: { flex: 1 },
  scrollContent: { padding: storeLight.space.gutter, gap: storeLight.space.section },
  stack: { gap: storeLight.space.section },
  wizardBar: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 8,
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline
  },
  barButton: {
    width: storeLight.size.tapTarget,
    height: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  barAction: { minWidth: 76, alignItems: "flex-end", paddingHorizontal: 8, paddingVertical: 10 },
  barActionText: { fontSize: 13, fontWeight: "600", color: storeLight.text.link },
  stepIndicator: { flex: 1, alignItems: "center", gap: 1 },
  stepText: { fontSize: 11, color: storeLight.text.muted },
  stepLabel: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  savedNote: {
    fontSize: 12,
    color: storeLight.status.success,
    textAlign: "center",
    paddingVertical: 4,
    backgroundColor: storeLight.bg.card
  },
  pageTitle: { fontSize: 21, fontWeight: "800", color: storeLight.text.primary },
  pageSubtitle: { fontSize: 13, lineHeight: 19, color: storeLight.text.muted },
  groupLabel: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  objectiveCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    padding: storeLight.space.card
  },
  objectiveCardActive: { borderColor: storeLight.accent.brandOnLight, borderWidth: 1.5 },
  objectiveIconWrap: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: storeLight.bg.skeleton,
    alignItems: "center",
    justifyContent: "center"
  },
  objectiveTextBlock: { flex: 1, gap: 2 },
  objectiveTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  objectiveCaption: { fontSize: 12, lineHeight: 17, color: storeLight.text.muted },
  twoColumns: { flexDirection: "row", gap: 12 },
  columnHalf: { flex: 1 },
  audienceRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: storeLight.size.tapTarget,
    paddingVertical: 4
  },
  audienceTextBlock: { flex: 1, gap: 1 },
  audienceName: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary },
  audienceMeta: { fontSize: 12, color: storeLight.text.muted },
  estimateValue: { fontSize: 19, fontWeight: "800", color: storeLight.text.primary },
  bandPill: { alignSelf: "flex-start", borderRadius: storeLight.radius.pill, paddingHorizontal: 10, paddingVertical: 4 },
  bandPillGood: { backgroundColor: "#E6F6EF" },
  bandPillWarn: { backgroundColor: storeLight.bg.warning },
  bandPillText: { fontSize: 12, fontWeight: "700" },
  bandPillTextGood: { color: storeLight.status.success },
  bandPillTextWarn: { color: storeLight.status.warning },
  placementList: { gap: 2 },
  contentBlock: { gap: 8 },
  contentPickedRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  contentPickedText: { flex: 1, gap: 1 },
  contentThumb: { width: 48, height: 48, borderRadius: storeLight.radius.thumb },
  contentThumbFallback: {
    backgroundColor: storeLight.bg.skeleton,
    alignItems: "center",
    justifyContent: "center"
  },
  contentRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: storeLight.size.tapTarget + 8,
    paddingHorizontal: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: storeLight.border.hairline
  },
  contentRowDisabled: { opacity: 0.45 },
  mediaRow: { flexDirection: "row", gap: 10, flexWrap: "wrap" },
  coverWrap: { position: "relative" },
  coverImage: { width: 88, height: 88, borderRadius: storeLight.radius.thumb },
  coverRemove: {
    position: "absolute",
    top: -6,
    right: -6,
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: storeLight.text.primary,
    alignItems: "center",
    justifyContent: "center"
  },
  mediaAdd: {
    width: 88,
    height: 88,
    borderRadius: storeLight.radius.thumb,
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center",
    gap: 4
  },
  mediaAddText: { fontSize: 11, fontWeight: "600", color: storeLight.text.muted, textAlign: "center" },
  summaryRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  summaryLabel: { fontSize: 13, color: storeLight.text.muted },
  summaryValue: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary, flexShrink: 1, textAlign: "right" },
  noteBox: {
    flexDirection: "row",
    gap: 8,
    alignItems: "flex-start",
    backgroundColor: storeLight.bg.skeleton,
    borderRadius: storeLight.radius.control,
    padding: 10
  },
  noteText: { flex: 1, fontSize: 12, lineHeight: 17, color: storeLight.text.muted },
  warnBox: {
    flexDirection: "row",
    gap: 8,
    alignItems: "flex-start",
    backgroundColor: storeLight.bg.warning,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.warning,
    borderRadius: storeLight.radius.control,
    padding: 10
  },
  warnText: { flex: 1, fontSize: 12, lineHeight: 17, color: storeLight.text.primary },
  reviewHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  editLink: { fontSize: 13, fontWeight: "600", color: storeLight.text.link },
  reviewLine: { fontSize: 13, lineHeight: 19, color: storeLight.text.primary },
  formError: { fontSize: 13, fontWeight: "600", color: storeLight.status.error, textAlign: "center" },
  blockersBlock: {
    gap: 4,
    backgroundColor: storeLight.bg.warning,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.warning,
    borderRadius: storeLight.radius.control,
    padding: 12,
    alignSelf: "stretch"
  },
  blockersTitle: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  blockerLine: { fontSize: 12, lineHeight: 18, color: storeLight.text.primary },
  successBlock: { alignItems: "center", gap: 12, maxWidth: 420 },
  successBadge: {
    width: 76,
    height: 76,
    borderRadius: 38,
    backgroundColor: storeLight.accent.brandOnLight,
    alignItems: "center",
    justifyContent: "center"
  },
  successTitle: { fontSize: 21, fontWeight: "800", color: storeLight.text.primary, textAlign: "center" },
  successBody: { fontSize: 14, lineHeight: 20, color: storeLight.text.muted, textAlign: "center" },
  successActions: { alignSelf: "stretch", gap: 10, marginTop: 8 },
  statusPill: {
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.bg.skeleton,
    paddingHorizontal: 12,
    paddingVertical: 4
  },
  statusPillText: { fontSize: 12, fontWeight: "700", color: storeLight.text.primary },
  resumeCard: { alignItems: "stretch", gap: 12, alignSelf: "stretch" },
  resumeTitle: { fontSize: 18, fontWeight: "800", color: storeLight.text.primary },
  resumeBody: { fontSize: 13, lineHeight: 19, color: storeLight.text.muted },
  chipEditor: { gap: 6 },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  editChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: storeLight.bg.skeleton,
    borderRadius: storeLight.radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  editChipText: { fontSize: 12, fontWeight: "600", color: storeLight.text.primary },
  chipInputRow: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  chipInputWrap: { flex: 1 },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(15, 17, 17, 0.45)", justifyContent: "flex-end" },
  modalSheet: {
    backgroundColor: storeLight.bg.card,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingTop: 16,
    paddingBottom: 28,
    maxHeight: "75%"
  },
  modalTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: storeLight.text.primary,
    paddingHorizontal: 16,
    paddingBottom: 8
  },
  modalList: { paddingHorizontal: 12 },
  modalSection: {
    fontSize: 12,
    fontWeight: "700",
    color: storeLight.text.muted,
    textTransform: "uppercase",
    paddingTop: 12,
    paddingBottom: 4
  }
});

export default AdsCampaignWizardScreen;
