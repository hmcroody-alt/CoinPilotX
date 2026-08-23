/**
 * The marketplace "Add a Listing" wizard — the screen behind the
 * `MarketplaceCreateGateway` route every Store surface, deep link
 * (`pulse/marketplace/create`) and dashboard action already navigates to.
 *
 * One route, four internal stages (type picker → details → preview → publish),
 * driven by step state inside the persisted draft rather than by stack routes,
 * so all nine existing call sites keep working unchanged.
 *
 * Design system: this is a WHITE commerce surface. Every colour comes from
 * `theme/storeLight`, matching the Store dashboard; the dark `colors` theme is
 * deliberately not imported.
 *
 * Server authority is unchanged: publish posts to the same
 * `/api/pulse/marketplace/listings/create` review pipeline, now with the typed
 * `listing_type` / `listing_metadata` contract, and the listing appears in
 * Store surfaces through `listMarketplaceSellerListings` once created.
 */

import { Ionicons } from "@expo/vector-icons";
import * as DocumentPicker from "expo-document-picker";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  attachMarketplaceProductMedia,
  createMarketplaceListing,
  MarketplaceListingType,
  submitMarketplaceSellerListing,
  updateMarketplaceSellerListing,
  uploadMarketplaceDigitalFile
} from "../api/marketplace";
import {
  WizardCard,
  WizardChip,
  WizardErrorText,
  WizardHint,
  WizardInlineAddButton,
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
  BOOKING_BUFFERS,
  BOOKING_DURATIONS,
  BOOKING_WEEKDAYS,
  buildListingCreatePayload,
  DESCRIPTION_MAX_LENGTH,
  LISTING_CATEGORY_KEYS,
  LISTING_CATEGORY_VALUES,
  LISTING_CURRENCIES,
  ListingDraft,
  ListingDraftIssue,
  listingDraftIssueFor,
  SERVICE_DELIVERY_DAYS,
  validateListingDraft
} from "../marketplace/listingDraft";
import {
  clearListingDraft,
  hydrateListingDraft,
  persistListingDraft,
  updateListingDraft,
  useListingDraft
} from "../marketplace/listingDraftStore";
import { useNativeMediaUpload } from "../media/useNativeMediaUpload";
import { uploadResultMediaId } from "../media/nativeMediaUpload";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { storeLight } from "../theme/storeLight";

type Props = {
  navigation: {
    navigate: (...args: any[]) => void;
    goBack?: () => void;
  };
};

const TYPE_ICONS: Record<MarketplaceListingType, keyof typeof Ionicons.glyphMap> = {
  physical: "cube-outline",
  digital: "cloud-download-outline",
  service: "construct-outline",
  event: "calendar-outline",
  booking: "time-outline"
};

/** 30-minute grid the event and availability time selects offer. */
const TIME_OPTIONS: string[] = Array.from({ length: 48 }, (_, index) => {
  const hours = String(Math.floor(index / 2)).padStart(2, "0");
  const minutes = index % 2 === 0 ? "00" : "30";
  return `${hours}:${minutes}`;
});

export function SellerListingComposerScreen({ navigation }: Props) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const insets = useSafeAreaInsets();
  const draft = useListingDraft();

  const [resumePrompt, setResumePrompt] = useState(false);
  const [issues, setIssues] = useState<ListingDraftIssue[]>([]);
  const [attempted, setAttempted] = useState(false);
  const [openSelect, setOpenSelect] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState("");
  const [published, setPublished] = useState(false);
  const [draftSavedNote, setDraftSavedNote] = useState(false);
  const [fileBusy, setFileBusy] = useState(false);
  const [fileError, setFileError] = useState("");
  const [mediaBusy, setMediaBusy] = useState(false);
  const [mediaError, setMediaError] = useState("");

  const media = useNativeMediaUpload({ contextType: "marketplace_product" });

  useEffect(() => {
    let cancelled = false;
    hydrateListingDraft()
      .then((stored) => {
        if (!cancelled && stored) setResumePrompt(true);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  /** Live re-validation once the seller has tried to continue. */
  useEffect(() => {
    if (attempted) setIssues(validateListingDraft(draft));
  }, [attempted, draft]);

  const issueFor = useCallback(
    (field: string) => {
      const key = listingDraftIssueFor(issues, field);
      return key ? t(key) : "";
    },
    [issues, t]
  );

  /* -------------------------------------------------------------- *
   * Navigation between stages
   * -------------------------------------------------------------- */

  const goToStep = useCallback((step: ListingDraft["step"]) => {
    updateListingDraft({ step });
    void persistListingDraft();
  }, []);

  const chooseType = useCallback(
    (type: MarketplaceListingType) => {
      updateListingDraft({ listingType: type, step: "details" });
      void persistListingDraft();
    },
    []
  );

  const saveDraftAndExit = useCallback(async () => {
    if (draft.title.trim()) {
      const payload = { ...buildListingCreatePayload(draft), submission_action: "draft" as const };
      if (draft.serverListingId > 0) {
        const { submission_action: _submissionAction, ...updatePayload } = payload;
        await updateMarketplaceSellerListing(draft.serverListingId, updatePayload);
      } else {
        const saved = await createMarketplaceListing(payload);
        const listingId = Number(saved.listing_id || 0);
        if (listingId > 0) updateListingDraft({ serverListingId: listingId });
      }
    }
    await persistListingDraft();
    setDraftSavedNote(true);
    navigation.goBack?.();
  }, [draft, navigation]);

  const continueToPreview = useCallback(() => {
    setAttempted(true);
    const found = validateListingDraft(draft);
    setIssues(found);
    if (!found.length) goToStep("preview");
  }, [draft, goToStep]);

  const backToStore = useCallback(() => {
    navigation.navigate("SellerStore", { mode: "dashboard", title: t("commerce:listingWizard.goToStore") });
  }, [navigation, t]);

  /* -------------------------------------------------------------- *
   * Media
   * -------------------------------------------------------------- */

  const addMedia = useCallback(
    async (kind: "image" | "video") => {
      setMediaError("");
      const asset = kind === "video" ? await media.chooseVideo() : await media.chooseImage();
      if (!asset) return;
      setMediaBusy(true);
      try {
        const isCover = draft.coverMediaId <= 0;
        // No `endpointPath`: that is what keeps the file out of the Flask
        // request body. The upload goes straight to R2 over a short-lived
        // signed URL (multipart for large video), and only the resulting id is
        // sent back to PulseSoc to attach.
        const result = await media.upload({ contextId: "draft", skipProcessingPoll: true }, asset);
        const uploadedId = result ? uploadResultMediaId(result) : 0;
        // Storing the object and attaching it are separate calls now, so the
        // attach can fail on its own. Reporting that as an upload failure is
        // honest — the seller has no listing media either way.
        const attached = uploadedId
          ? await attachMarketplaceProductMedia(
              uploadedId,
              kind === "video" ? "video" : isCover ? "cover" : "gallery"
            ).catch(() => null)
          : null;
        const mediaId = Number(attached?.media?.id || 0);
        if (mediaId > 0) {
          updateListingDraft((current) =>
            current.coverMediaId > 0
              ? { ...current, galleryMediaIds: [...current.galleryMediaIds, mediaId] }
              : { ...current, coverMediaId: mediaId, coverPreviewUri: asset.uri }
          );
          void persistListingDraft();
        } else {
          setMediaError(media.error || t("commerce:listingWizard.errorMediaUpload"));
        }
      } finally {
        setMediaBusy(false);
        media.reset();
      }
    },
    [draft.coverMediaId, media, t]
  );

  const removeCover = useCallback(() => {
    updateListingDraft((current) => {
      const [nextCover, ...rest] = current.galleryMediaIds;
      return {
        ...current,
        coverMediaId: nextCover || 0,
        coverPreviewUri: "",
        galleryMediaIds: nextCover ? rest : []
      };
    });
  }, []);

  /* -------------------------------------------------------------- *
   * Digital deliverables
   * -------------------------------------------------------------- */

  const addDigitalFile = useCallback(async () => {
    setFileError("");
    const picked = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true, multiple: false });
    if (picked.canceled || !picked.assets?.[0]) return;
    const asset = picked.assets[0];
    setFileBusy(true);
    try {
      const uploaded = await uploadMarketplaceDigitalFile({
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType
      });
      const fileId = Number(uploaded.file?.file_id || 0);
      if (fileId > 0) {
        updateListingDraft((current) => ({
          ...current,
          digital: {
            ...current.digital,
            files: [
              ...current.digital.files,
              {
                file_id: fileId,
                name: String(uploaded.file?.name || asset.name),
                size_bytes: Number(uploaded.file?.size_bytes || asset.size || 0)
              }
            ]
          }
        }));
      } else {
        setFileError(uploaded.message || t("commerce:listingWizard.errorFileUpload"));
      }
    } catch (error) {
      setFileError(error instanceof Error ? error.message : t("commerce:listingWizard.errorFileUpload"));
    } finally {
      setFileBusy(false);
    }
  }, [t]);

  /* -------------------------------------------------------------- *
   * Publish
   * -------------------------------------------------------------- */

  const publish = useCallback(async () => {
    setPublishing(true);
    setPublishError("");
    try {
      const payload = buildListingCreatePayload(draft);
      let listingId = draft.serverListingId;
      if (listingId > 0) {
        await updateMarketplaceSellerListing(listingId, payload);
        await submitMarketplaceSellerListing(listingId);
      } else {
        const result = await createMarketplaceListing(payload);
        listingId = Number(result.listing_id || 0);
      }
      await clearListingDraft();
      await invalidateNativeSync(["seller_inventory", "marketplace", "activity"], "listing_wizard_publish", [
        { event_type: "marketplace_listing_submitted", entity_type: "marketplace_listing", entity_id: listingId }
      ]).catch(() => undefined);
      setPublished(true);
    } catch (error) {
      setPublishError(error instanceof Error ? error.message : t("commerce:listingWizard.errorPublish"));
    } finally {
      setPublishing(false);
    }
  }, [draft, t]);

  /* -------------------------------------------------------------- *
   * Derived labels
   * -------------------------------------------------------------- */

  const weekdayLabels = useMemo(
    () => formatters.weekdayNames("short", { startOnMonday: true }),
    [formatters]
  );

  const priceSuffix = useMemo(() => {
    if (draft.listingType === "booking") {
      return t("commerce:listingWizard.perSession", { minutes: draft.booking.durationMinutes });
    }
    if (draft.listingType === "service" && draft.service.pricingMode === "hourly") {
      return t("commerce:listingWizard.perHour");
    }
    return "";
  }, [draft, t]);

  const priceLabelPreview = useMemo(() => {
    const amount = Number(draft.price);
    if (!draft.price.trim() || !Number.isFinite(amount)) return "";
    const base = formatters.currency(amount, { currency: draft.currency });
    const prefix =
      draft.listingType === "service" && draft.service.pricingMode === "starting_at"
        ? `${t("commerce:listingWizard.pricingStartingAt")} `
        : "";
    return `${prefix}${base}${priceSuffix}`;
  }, [draft, formatters, priceSuffix, t]);

  /* -------------------------------------------------------------- *
   * Stage renderers
   * -------------------------------------------------------------- */

  if (published) {
    return (
      <View style={[styles.root, styles.centerFill]}>
        <View style={styles.successBlock}>
          <View style={styles.successBadge}>
            <Ionicons name="checkmark" size={40} color={storeLight.cta.text} />
          </View>
          <Text style={styles.successTitle}>{t("commerce:listingWizard.successTitle")}</Text>
          <Text style={styles.successBody}>{t("commerce:listingWizard.successBody")}</Text>
          <View style={styles.successActions}>
            <WizardPrimaryButton label={t("commerce:listingWizard.goToStore")} onPress={backToStore} />
          </View>
        </View>
      </View>
    );
  }

  if (resumePrompt) {
    return (
      <View style={[styles.root, styles.centerFill]}>
        <WizardCard style={styles.resumeCard}>
          <Ionicons name="document-text-outline" size={30} color={storeLight.accent.brandOnLight} />
          <Text style={styles.resumeTitle}>{t("commerce:listingWizard.resumeTitle")}</Text>
          <Text style={styles.resumeBody}>{t("commerce:listingWizard.resumeBody")}</Text>
          <WizardPrimaryButton
            label={t("commerce:listingWizard.resume")}
            onPress={() => setResumePrompt(false)}
          />
          <WizardSecondaryButton
            label={t("commerce:listingWizard.startOver")}
            onPress={() => {
              void clearListingDraft();
              setAttempted(false);
              setIssues([]);
              setResumePrompt(false);
            }}
          />
        </WizardCard>
      </View>
    );
  }

  const stepIndex = draft.step === "type" ? 0 : draft.step === "details" ? 1 : 2;

  return (
    <View style={styles.root}>
      <View style={styles.wizardBar}>
        {draft.step !== "type" ? (
          <Pressable
            style={styles.barButton}
            onPress={() => goToStep(draft.step === "preview" ? "details" : "type")}
            accessibilityRole="button"
            accessibilityLabel={t("commerce:listingWizard.backLabel")}
          >
            <Ionicons name="chevron-back" size={22} color={storeLight.text.primary} />
          </Pressable>
        ) : (
          <View style={styles.barButton} />
        )}
        <View style={styles.stepIndicator}>
          {(["type", "details", "preview"] as const).map((step, index) => (
            <View key={step} style={styles.stepItem}>
              {index > 0 ? <Text style={styles.stepDot}>·</Text> : null}
              <Text style={[styles.stepText, index === stepIndex && styles.stepTextActive]}>
                {`${index + 1} ${t(`commerce:listingWizard.steps.${step}`)}`}
              </Text>
            </View>
          ))}
        </View>
        {draft.step !== "type" ? (
          <Pressable
            style={styles.barAction}
            onPress={() => void saveDraftAndExit()}
            accessibilityRole="button"
            accessibilityLabel={t("commerce:listingWizard.saveDraft")}
          >
            <Text style={styles.barActionText}>{t("commerce:listingWizard.saveDraft")}</Text>
          </Pressable>
        ) : (
          <View style={styles.barAction} />
        )}
      </View>
      {draftSavedNote ? <Text style={styles.savedNote}>{t("commerce:listingWizard.draftSaved")}</Text> : null}

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={[
          styles.scrollContent,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        keyboardShouldPersistTaps="handled"
      >
        {draft.step === "type" ? renderTypePicker() : null}
        {draft.step === "details" ? renderDetails() : null}
        {draft.step === "preview" ? renderPreview() : null}
      </ScrollView>
    </View>
  );

  /* -------------------------------------------------------------- *
   * Stage 1 — type picker
   * -------------------------------------------------------------- */

  function renderTypePicker() {
    const types: MarketplaceListingType[] = ["physical", "digital", "service", "event", "booking"];
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>{t("commerce:listingWizard.typeTitle")}</Text>
        <Text style={styles.pageSubtitle}>{t("commerce:listingWizard.typeSubtitle")}</Text>
        {types.map((type) => (
          <Pressable
            key={type}
            style={styles.typeCard}
            onPress={() => chooseType(type)}
            accessibilityRole="button"
            accessibilityLabel={t(`commerce:listingWizard.types.${type}.title`)}
          >
            <View style={styles.typeIconWrap}>
              <Ionicons name={TYPE_ICONS[type]} size={24} color={storeLight.accent.brandOnLight} />
            </View>
            <View style={styles.typeTextBlock}>
              <Text style={styles.typeTitle}>{t(`commerce:listingWizard.types.${type}.title`)}</Text>
              <Text style={styles.typeLine}>{t(`commerce:listingWizard.types.${type}.line1`)}</Text>
              <Text style={styles.typeLineMuted}>{t(`commerce:listingWizard.types.${type}.line2`)}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={storeLight.text.muted} />
          </Pressable>
        ))}
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Stage 2 — details
   * -------------------------------------------------------------- */

  function renderDetails() {
    return (
      <View style={styles.stack}>
        {renderMediaCard()}
        {renderSharedFieldsCard()}
        {draft.listingType === "physical" ? renderPhysicalCard() : null}
        {draft.listingType === "digital" ? renderDigitalCard() : null}
        {draft.listingType === "service" ? renderServiceCard() : null}
        {draft.listingType === "event" ? renderEventCard() : null}
        {draft.listingType === "booking" ? renderBookingCard() : null}
        {attempted && issues.length > 0 ? (
          <Text style={styles.formError}>{t("commerce:listingWizard.fixBeforePreview")}</Text>
        ) : null}
        <WizardPrimaryButton
          label={t("commerce:listingWizard.continue")}
          onPress={continueToPreview}
          disabled={attempted && issues.length > 0}
        />
      </View>
    );
  }

  function renderMediaCard() {
    return (
      <WizardCard>
        <WizardSectionTitle text={t("commerce:listingWizard.media")} />
        <WizardHint text={t("commerce:listingWizard.mediaHint")} />
        <View style={styles.mediaRow}>
          {draft.coverMediaId > 0 ? (
            <View style={styles.coverWrap}>
              {draft.coverPreviewUri ? (
                <Image source={{ uri: draft.coverPreviewUri }} style={styles.coverImage} />
              ) : (
                <View style={[styles.coverImage, styles.coverFallback]}>
                  <Ionicons name="image-outline" size={26} color={storeLight.text.muted} />
                </View>
              )}
              <View style={styles.coverBadge}>
                <Text style={styles.coverBadgeText}>{t("commerce:listingWizard.cover")}</Text>
              </View>
              <Pressable
                style={styles.coverRemove}
                onPress={removeCover}
                accessibilityRole="button"
                accessibilityLabel={t("commerce:listingWizard.removeMedia")}
              >
                <Ionicons name="close" size={14} color={storeLight.text.onDark} />
              </Pressable>
            </View>
          ) : null}
          <Pressable
            style={styles.mediaAdd}
            onPress={() => void addMedia("image")}
            disabled={mediaBusy}
            accessibilityRole="button"
            accessibilityLabel={t("commerce:listingWizard.addPhoto")}
          >
            <Ionicons name="camera-outline" size={22} color={storeLight.text.muted} />
            <Text style={styles.mediaAddText}>{t("commerce:listingWizard.addPhoto")}</Text>
          </Pressable>
          <Pressable
            style={styles.mediaAdd}
            onPress={() => void addMedia("video")}
            disabled={mediaBusy}
            accessibilityRole="button"
            accessibilityLabel={t("commerce:listingWizard.addVideo")}
          >
            <Ionicons name="videocam-outline" size={22} color={storeLight.text.muted} />
            <Text style={styles.mediaAddText}>{t("commerce:listingWizard.addVideo")}</Text>
          </Pressable>
        </View>
        {draft.galleryMediaIds.length > 0 ? (
          <Text style={styles.mediaMeta}>
            {t("commerce:listingWizard.galleryCount", { count: draft.galleryMediaIds.length })}
          </Text>
        ) : null}
        {mediaBusy ? <Text style={styles.mediaMeta}>{t("commerce:listingWizard.uploading")}</Text> : null}
        <WizardErrorText text={mediaError || issueFor("media")} />
      </WizardCard>
    );
  }

  function renderSharedFieldsCard() {
    const categoryKey =
      LISTING_CATEGORY_KEYS.find((key) => LISTING_CATEGORY_VALUES[key] === draft.category) || "other";
    return (
      <WizardCard>
        <WizardSectionTitle text={t("commerce:listingWizard.detailsTitle")} />
        <WizardTextField
          label={t("commerce:listingWizard.titleLabel")}
          value={draft.title}
          onChangeText={(title) => updateListingDraft({ title })}
          placeholder={t("commerce:listingWizard.titlePlaceholder")}
          error={issueFor("title")}
          maxLength={120}
        />
        <View style={styles.twoColumns}>
          <View style={styles.columnWide}>
            <WizardTextField
              label={t("commerce:listingWizard.priceLabel")}
              value={draft.price}
              onChangeText={(price) => updateListingDraft({ price: price.replace(/[^0-9.]/g, "") })}
              placeholder={t("commerce:listingWizard.pricePlaceholder")}
              error={issueFor("price")}
              keyboardType="decimal-pad"
            />
          </View>
          <View style={styles.columnNarrow}>
            <WizardSelect
              label={t("commerce:listingWizard.currencyLabel")}
              sheetTitle={t("commerce:listingWizard.currencyLabel")}
              options={LISTING_CURRENCIES.map((code) => ({ key: code, label: code }))}
              selectedKey={draft.currency as (typeof LISTING_CURRENCIES)[number]}
              onSelect={(currency) => updateListingDraft({ currency })}
              open={openSelect === "currency"}
              onOpen={() => setOpenSelect("currency")}
              onClose={() => setOpenSelect(null)}
            />
          </View>
        </View>
        <WizardSelect
          label={t("commerce:listingWizard.categoryLabel")}
          sheetTitle={t("commerce:listingWizard.categoryLabel")}
          options={LISTING_CATEGORY_KEYS.map((key) => ({
            key,
            label: t(`commerce:listingWizard.categoryOptions.${key}`)
          }))}
          selectedKey={categoryKey}
          onSelect={(key) => updateListingDraft({ category: LISTING_CATEGORY_VALUES[key] })}
          open={openSelect === "category"}
          onOpen={() => setOpenSelect("category")}
          onClose={() => setOpenSelect(null)}
        />
        <WizardTextField
          label={t("commerce:listingWizard.descriptionLabel")}
          value={draft.description}
          onChangeText={(description) =>
            updateListingDraft({ description: description.slice(0, DESCRIPTION_MAX_LENGTH) })
          }
          placeholder={t("commerce:listingWizard.descriptionPlaceholder")}
          error={issueFor("description")}
          multiline
          maxLength={DESCRIPTION_MAX_LENGTH}
          counterText={t("commerce:listingWizard.charCount", {
            used: draft.description.length,
            max: DESCRIPTION_MAX_LENGTH
          })}
        />
      </WizardCard>
    );
  }

  function renderPhysicalCard() {
    const physical = draft.physical;
    const patch = (next: Partial<typeof physical>) =>
      updateListingDraft((current) => ({ ...current, physical: { ...current.physical, ...next } }));
    return (
      <WizardCard>
        <WizardSectionTitle text={t("commerce:listingWizard.types.physical.title")} />
        <WizardSegmented
          label={t("commerce:listingWizard.condition")}
          options={[
            { key: "new", label: t("commerce:listingWizard.conditionNew") },
            { key: "like_new", label: t("commerce:listingWizard.conditionLikeNew") },
            { key: "good", label: t("commerce:listingWizard.conditionGood") },
            { key: "fair", label: t("commerce:listingWizard.conditionFair") }
          ]}
          value={physical.condition}
          onChange={(condition) => patch({ condition })}
        />
        <WizardStepper
          label={t("commerce:listingWizard.quantity")}
          value={physical.quantity}
          onChange={(quantity) => patch({ quantity })}
          error={issueFor("quantity")}
        />
        <WizardSectionTitle text={t("commerce:listingWizard.variants")} />
        <WizardHint text={t("commerce:listingWizard.variantsHint")} />
        {physical.variants.map((variant, index) => (
          <View key={index} style={styles.rowLine}>
            <View style={styles.columnWide}>
              <WizardTextField
                value={variant.name}
                onChangeText={(name) =>
                  patch({ variants: physical.variants.map((row, i) => (i === index ? { ...row, name } : row)) })
                }
                placeholder={t("commerce:listingWizard.variantName")}
              />
            </View>
            <View style={styles.columnWide}>
              <WizardTextField
                value={variant.value}
                onChangeText={(value) =>
                  patch({ variants: physical.variants.map((row, i) => (i === index ? { ...row, value } : row)) })
                }
                placeholder={t("commerce:listingWizard.variantValue")}
              />
            </View>
            <Pressable
              style={styles.rowRemove}
              onPress={() => patch({ variants: physical.variants.filter((_, i) => i !== index) })}
              accessibilityRole="button"
              accessibilityLabel={t("commerce:listingWizard.removeMedia")}
            >
              <Ionicons name="trash-outline" size={18} color={storeLight.status.error} />
            </Pressable>
          </View>
        ))}
        <WizardInlineAddButton
          label={t("commerce:listingWizard.addVariant")}
          onPress={() => patch({ variants: [...physical.variants, { name: "", value: "" }] })}
        />
        <WizardRadioGroup
          label={t("commerce:listingWizard.deliveryOptions")}
          options={[
            { key: "pickup", label: t("commerce:listingWizard.deliveryPickup") },
            { key: "shipping", label: t("commerce:listingWizard.deliveryShipping") },
            { key: "both", label: t("commerce:listingWizard.bothLabel") }
          ]}
          value={physical.deliveryOption}
          onChange={(deliveryOption) => patch({ deliveryOption })}
        />
        <WizardTextField
          label={t("commerce:listingWizard.location")}
          value={physical.location}
          onChangeText={(location) => patch({ location })}
          placeholder={t("commerce:listingWizard.locationPlaceholder")}
          error={issueFor("location")}
        />
        <WizardSelect
          label={t("commerce:listingWizard.returnPolicy")}
          sheetTitle={t("commerce:listingWizard.returnPolicy")}
          options={[
            { key: "none", label: t("commerce:listingWizard.returnsNone") },
            { key: "7_days", label: t("commerce:listingWizard.returns7") },
            { key: "14_days", label: t("commerce:listingWizard.returns14") },
            { key: "30_days", label: t("commerce:listingWizard.returns30") }
          ]}
          selectedKey={physical.returnPolicy}
          onSelect={(returnPolicy) => patch({ returnPolicy })}
          open={openSelect === "returnPolicy"}
          onOpen={() => setOpenSelect("returnPolicy")}
          onClose={() => setOpenSelect(null)}
        />
      </WizardCard>
    );
  }

  function renderDigitalCard() {
    const digital = draft.digital;
    const patch = (next: Partial<typeof digital>) =>
      updateListingDraft((current) => ({ ...current, digital: { ...current.digital, ...next } }));
    return (
      <WizardCard>
        <WizardSectionTitle text={t("commerce:listingWizard.files")} />
        <WizardHint text={t("commerce:listingWizard.filesHint")} />
        {digital.files.map((file, index) => (
          <View key={file.file_id} style={styles.fileRow}>
            <Ionicons name="document-outline" size={18} color={storeLight.text.primary} />
            <View style={styles.fileTextBlock}>
              <Text style={styles.fileName} numberOfLines={1}>
                {file.name}
              </Text>
              <Text style={styles.fileMeta}>{formatters.fileSize(file.size_bytes)}</Text>
            </View>
            <Pressable
              style={styles.rowRemove}
              onPress={() => patch({ files: digital.files.filter((_, i) => i !== index) })}
              accessibilityRole="button"
              accessibilityLabel={t("commerce:listingWizard.removeMedia")}
            >
              <Ionicons name="trash-outline" size={18} color={storeLight.status.error} />
            </Pressable>
          </View>
        ))}
        {fileBusy ? <Text style={styles.mediaMeta}>{t("commerce:listingWizard.uploading")}</Text> : null}
        <WizardInlineAddButton label={t("commerce:listingWizard.addFile")} onPress={() => void addDigitalFile()} />
        <WizardErrorText text={fileError || issueFor("files")} />
        <View style={styles.staticRow}>
          <Text style={styles.fieldLabelText}>{t("commerce:listingWizard.deliveryLabel")}</Text>
          <View style={styles.staticValueWrap}>
            <Ionicons name="flash-outline" size={14} color={storeLight.status.success} />
            <Text style={styles.staticValue}>{t("commerce:listingWizard.deliveryAutomatic")}</Text>
          </View>
        </View>
        <WizardSegmented
          label={t("commerce:listingWizard.license")}
          options={[
            { key: "personal", label: t("commerce:listingWizard.licensePersonal") },
            { key: "commercial", label: t("commerce:listingWizard.licenseCommercial") }
          ]}
          value={digital.license}
          onChange={(license) => patch({ license })}
        />
        <WizardSegmented
          label={t("commerce:listingWizard.downloadLimit")}
          options={[
            { key: "unlimited", label: t("commerce:listingWizard.downloadUnlimited") },
            { key: "limited", label: t("commerce:listingWizard.downloadLimited") }
          ]}
          value={digital.downloadLimitMode}
          onChange={(downloadLimitMode) => patch({ downloadLimitMode })}
        />
        {digital.downloadLimitMode === "limited" ? (
          <WizardTextField
            value={digital.downloadLimit}
            onChangeText={(downloadLimit) => patch({ downloadLimit: downloadLimit.replace(/[^0-9]/g, "") })}
            placeholder={t("commerce:listingWizard.downloadLimitPlaceholder")}
            error={issueFor("downloadLimit")}
            keyboardType="numeric"
          />
        ) : null}
      </WizardCard>
    );
  }

  function renderServiceCard() {
    const service = draft.service;
    const patch = (next: Partial<typeof service>) =>
      updateListingDraft((current) => ({ ...current, service: { ...current.service, ...next } }));
    return (
      <WizardCard>
        <WizardSectionTitle text={t("commerce:listingWizard.types.service.title")} />
        <WizardSegmented
          label={t("commerce:listingWizard.pricingMode")}
          options={[
            { key: "fixed", label: t("commerce:listingWizard.pricingFixed") },
            { key: "starting_at", label: t("commerce:listingWizard.pricingStartingAt") },
            { key: "hourly", label: t("commerce:listingWizard.pricingHourly") }
          ]}
          value={service.pricingMode}
          onChange={(pricingMode) => patch({ pricingMode })}
        />
        <WizardSelect
          label={t("commerce:listingWizard.deliveryTime")}
          sheetTitle={t("commerce:listingWizard.deliveryTime")}
          options={SERVICE_DELIVERY_DAYS.map((days) => ({
            key: String(days),
            label: t("commerce:listingWizard.days", { count: days })
          }))}
          selectedKey={String(service.deliveryTimeDays)}
          onSelect={(key) => patch({ deliveryTimeDays: Number(key) })}
          open={openSelect === "deliveryTime"}
          onOpen={() => setOpenSelect("deliveryTime")}
          onClose={() => setOpenSelect(null)}
        />
        <WizardRadioGroup
          label={t("commerce:listingWizard.serviceLocation")}
          options={[
            { key: "remote", label: t("commerce:listingWizard.serviceRemote") },
            { key: "in_person", label: t("commerce:listingWizard.inPersonLabel") },
            { key: "both", label: t("commerce:listingWizard.bothLabel") }
          ]}
          value={service.serviceLocation}
          onChange={(serviceLocation) => patch({ serviceLocation })}
        />
        {service.serviceLocation !== "remote" ? (
          <WizardTextField
            label={t("commerce:listingWizard.location")}
            value={service.location}
            onChangeText={(location) => patch({ location })}
            placeholder={t("commerce:listingWizard.locationPlaceholder")}
            error={issueFor("location")}
          />
        ) : null}
        <WizardSectionTitle text={t("commerce:listingWizard.included")} />
        {service.included.map((item, index) => (
          <View key={index} style={styles.rowLine}>
            <View style={styles.columnWide}>
              <WizardTextField
                value={item}
                onChangeText={(value) =>
                  patch({ included: service.included.map((row, i) => (i === index ? value : row)) })
                }
                placeholder={t("commerce:listingWizard.includedPlaceholder")}
              />
            </View>
            <Pressable
              style={styles.rowRemove}
              onPress={() => patch({ included: service.included.filter((_, i) => i !== index) })}
              accessibilityRole="button"
              accessibilityLabel={t("commerce:listingWizard.removeMedia")}
            >
              <Ionicons name="trash-outline" size={18} color={storeLight.status.error} />
            </Pressable>
          </View>
        ))}
        <WizardInlineAddButton
          label={t("commerce:listingWizard.addIncluded")}
          onPress={() => patch({ included: [...service.included, ""] })}
        />
        <WizardSectionTitle text={t("commerce:listingWizard.addons")} />
        <WizardHint text={t("commerce:listingWizard.addonsHint")} />
        {service.addons.map((addon, index) => (
          <View key={index} style={styles.rowLine}>
            <View style={styles.columnWide}>
              <WizardTextField
                value={addon.title}
                onChangeText={(title) =>
                  patch({ addons: service.addons.map((row, i) => (i === index ? { ...row, title } : row)) })
                }
                placeholder={t("commerce:listingWizard.addonTitle")}
              />
            </View>
            <View style={styles.columnNarrow}>
              <WizardTextField
                value={addon.price}
                onChangeText={(price) =>
                  patch({
                    addons: service.addons.map((row, i) =>
                      i === index ? { ...row, price: price.replace(/[^0-9.]/g, "") } : row
                    )
                  })
                }
                placeholder={t("commerce:listingWizard.pricePlaceholder")}
                keyboardType="decimal-pad"
              />
            </View>
            <Pressable
              style={styles.rowRemove}
              onPress={() => patch({ addons: service.addons.filter((_, i) => i !== index) })}
              accessibilityRole="button"
              accessibilityLabel={t("commerce:listingWizard.removeMedia")}
            >
              <Ionicons name="trash-outline" size={18} color={storeLight.status.error} />
            </Pressable>
          </View>
        ))}
        <WizardInlineAddButton
          label={t("commerce:listingWizard.addAddon")}
          onPress={() => patch({ addons: [...service.addons, { title: "", price: "" }] })}
        />
      </WizardCard>
    );
  }

  function renderEventCard() {
    const event = draft.event;
    const patch = (next: Partial<typeof event>) =>
      updateListingDraft((current) => ({ ...current, event: { ...current.event, ...next } }));
    return (
      <WizardCard>
        <WizardSectionTitle text={t("commerce:listingWizard.types.event.title")} />
        <WizardTextField
          label={t("commerce:listingWizard.eventName")}
          value={event.name}
          onChangeText={(name) => patch({ name })}
          placeholder={t("commerce:listingWizard.eventNamePlaceholder")}
          error={issueFor("eventName")}
        />
        <WizardTextField
          label={t("commerce:listingWizard.eventDate")}
          value={event.date}
          onChangeText={(date) => patch({ date: date.replace(/[^0-9-]/g, "").slice(0, 10) })}
          placeholder={t("commerce:listingWizard.eventDateFormat")}
          error={issueFor("eventDate")}
          keyboardType="numbers-and-punctuation"
        />
        <View style={styles.twoColumns}>
          <View style={styles.columnWide}>
            <WizardSelect
              label={t("commerce:listingWizard.startTime")}
              sheetTitle={t("commerce:listingWizard.startTime")}
              options={TIME_OPTIONS.map((time) => ({ key: time, label: time }))}
              selectedKey={event.startTime}
              onSelect={(startTime) => patch({ startTime })}
              error={issueFor("eventTimes")}
              open={openSelect === "eventStart"}
              onOpen={() => setOpenSelect("eventStart")}
              onClose={() => setOpenSelect(null)}
            />
          </View>
          <View style={styles.columnWide}>
            <WizardSelect
              label={t("commerce:listingWizard.endTime")}
              sheetTitle={t("commerce:listingWizard.endTime")}
              options={TIME_OPTIONS.map((time) => ({ key: time, label: time }))}
              selectedKey={event.endTime}
              onSelect={(endTime) => patch({ endTime })}
              open={openSelect === "eventEnd"}
              onOpen={() => setOpenSelect("eventEnd")}
              onClose={() => setOpenSelect(null)}
            />
          </View>
        </View>
        <WizardSegmented
          label={t("commerce:listingWizard.whereLabel")}
          options={[
            { key: "in_person", label: t("commerce:listingWizard.inPersonLabel") },
            { key: "online", label: t("commerce:listingWizard.whereOnline") },
            { key: "pulsesoc_live", label: t("commerce:listingWizard.wherePulsesocLive") }
          ]}
          value={event.venueMode}
          onChange={(venueMode) => patch({ venueMode })}
        />
        {event.venueMode === "in_person" ? (
          <WizardTextField
            label={t("commerce:listingWizard.venue")}
            value={event.location}
            onChangeText={(location) => patch({ location })}
            placeholder={t("commerce:listingWizard.locationPlaceholder")}
            error={issueFor("location")}
          />
        ) : null}
        {event.venueMode === "online" ? (
          <WizardTextField
            label={t("commerce:listingWizard.onlineUrl")}
            value={event.onlineUrl}
            onChangeText={(onlineUrl) => patch({ onlineUrl })}
            placeholder={t("commerce:listingWizard.onlineUrlPlaceholder")}
            error={issueFor("onlineUrl")}
            keyboardType="url"
            autoCapitalize="none"
          />
        ) : null}
        <WizardSectionTitle text={t("commerce:listingWizard.tickets")} />
        {event.tickets.map((ticket, index) => (
          <View key={index} style={styles.ticketBlock}>
            <View style={styles.rowLine}>
              <View style={styles.columnWide}>
                <WizardTextField
                  value={ticket.name}
                  onChangeText={(name) =>
                    patch({ tickets: event.tickets.map((row, i) => (i === index ? { ...row, name } : row)) })
                  }
                  placeholder={t("commerce:listingWizard.ticketName")}
                />
              </View>
              {event.tickets.length > 1 ? (
                <Pressable
                  style={styles.rowRemove}
                  onPress={() => patch({ tickets: event.tickets.filter((_, i) => i !== index) })}
                  accessibilityRole="button"
                  accessibilityLabel={t("commerce:listingWizard.removeMedia")}
                >
                  <Ionicons name="trash-outline" size={18} color={storeLight.status.error} />
                </Pressable>
              ) : null}
            </View>
            <View style={styles.twoColumns}>
              <View style={styles.columnWide}>
                <WizardTextField
                  label={t("commerce:listingWizard.ticketPrice")}
                  value={ticket.price}
                  onChangeText={(price) =>
                    patch({
                      tickets: event.tickets.map((row, i) =>
                        i === index ? { ...row, price: price.replace(/[^0-9.]/g, "") } : row
                      )
                    })
                  }
                  placeholder={t("commerce:listingWizard.pricePlaceholder")}
                  keyboardType="decimal-pad"
                />
              </View>
              <View style={styles.columnWide}>
                <WizardTextField
                  label={t("commerce:listingWizard.ticketCapacity")}
                  value={ticket.capacity}
                  onChangeText={(capacity) =>
                    patch({
                      tickets: event.tickets.map((row, i) =>
                        i === index ? { ...row, capacity: capacity.replace(/[^0-9]/g, "") } : row
                      )
                    })
                  }
                  placeholder={t("commerce:listingWizard.ticketCapacityPlaceholder")}
                  keyboardType="numeric"
                />
              </View>
            </View>
          </View>
        ))}
        <WizardInlineAddButton
          label={t("commerce:listingWizard.addTicket")}
          onPress={() => patch({ tickets: [...event.tickets, { name: "", price: "", capacity: "" }] })}
        />
        <WizardErrorText text={issueFor("tickets")} />
      </WizardCard>
    );
  }

  function renderBookingCard() {
    const booking = draft.booking;
    const patch = (next: Partial<typeof booking>) =>
      updateListingDraft((current) => ({ ...current, booking: { ...current.booking, ...next } }));
    return (
      <WizardCard>
        <WizardSectionTitle text={t("commerce:listingWizard.types.booking.title")} />
        <WizardSelect
          label={t("commerce:listingWizard.duration")}
          sheetTitle={t("commerce:listingWizard.duration")}
          options={BOOKING_DURATIONS.map((minutes) => ({
            key: String(minutes),
            label: t("commerce:listingWizard.minutesShort", { minutes })
          }))}
          selectedKey={String(booking.durationMinutes)}
          onSelect={(key) => patch({ durationMinutes: Number(key) })}
          error={issueFor("duration")}
          open={openSelect === "duration"}
          onOpen={() => setOpenSelect("duration")}
          onClose={() => setOpenSelect(null)}
        />
        <WizardRadioGroup
          label={t("commerce:listingWizard.meetingMode")}
          options={[
            { key: "video", label: t("commerce:listingWizard.meetingVideo") },
            { key: "audio", label: t("commerce:listingWizard.meetingAudio") },
            { key: "in_person", label: t("commerce:listingWizard.inPersonLabel") }
          ]}
          value={booking.meetingMode}
          onChange={(meetingMode) => patch({ meetingMode })}
        />
        <WizardSectionTitle text={t("commerce:listingWizard.availability")} />
        <WizardHint text={t("commerce:listingWizard.availabilityHint")} />
        {BOOKING_WEEKDAYS.map((day, index) => {
          const ranges = booking.availability[day];
          const enabled = ranges.length > 0;
          const range = ranges[0] || { start: "09:00", end: "17:00" };
          return (
            <View key={day} style={styles.availabilityRow}>
              <Pressable
                style={styles.availabilityDay}
                onPress={() =>
                  patch({
                    availability: {
                      ...booking.availability,
                      [day]: enabled ? [] : [{ start: "09:00", end: "17:00" }]
                    }
                  })
                }
                accessibilityRole="switch"
                accessibilityState={{ checked: enabled }}
                accessibilityLabel={weekdayLabels[index]}
              >
                <View style={[styles.dayToggle, enabled && styles.dayToggleOn]}>
                  {enabled ? <Ionicons name="checkmark" size={12} color={storeLight.cta.text} /> : null}
                </View>
                <Text style={[styles.dayLabel, !enabled && styles.dayLabelOff]}>{weekdayLabels[index]}</Text>
              </Pressable>
              {enabled ? (
                <View style={styles.availabilityTimes}>
                  <WizardSelect
                    sheetTitle={t("commerce:listingWizard.startLabel")}
                    options={TIME_OPTIONS.map((time) => ({ key: time, label: time }))}
                    selectedKey={range.start}
                    onSelect={(start) =>
                      patch({
                        availability: { ...booking.availability, [day]: [{ ...range, start }] }
                      })
                    }
                    open={openSelect === `avail-${day}-start`}
                    onOpen={() => setOpenSelect(`avail-${day}-start`)}
                    onClose={() => setOpenSelect(null)}
                  />
                  <WizardSelect
                    sheetTitle={t("commerce:listingWizard.endLabel")}
                    options={TIME_OPTIONS.map((time) => ({ key: time, label: time }))}
                    selectedKey={range.end}
                    onSelect={(end) =>
                      patch({
                        availability: { ...booking.availability, [day]: [{ ...range, end }] }
                      })
                    }
                    open={openSelect === `avail-${day}-end`}
                    onOpen={() => setOpenSelect(`avail-${day}-end`)}
                    onClose={() => setOpenSelect(null)}
                  />
                </View>
              ) : (
                <Text style={styles.unavailableText}>{t("commerce:listingWizard.unavailable")}</Text>
              )}
            </View>
          );
        })}
        <WizardErrorText text={issueFor("availability")} />
        <WizardSelect
          label={t("commerce:listingWizard.buffer")}
          sheetTitle={t("commerce:listingWizard.buffer")}
          options={BOOKING_BUFFERS.map((minutes) => ({
            key: String(minutes),
            label: minutes === 0 ? t("commerce:listingWizard.bufferNone") : t("commerce:listingWizard.minutesShort", { minutes })
          }))}
          selectedKey={String(booking.bufferMinutes)}
          onSelect={(key) => patch({ bufferMinutes: Number(key) })}
          open={openSelect === "buffer"}
          onOpen={() => setOpenSelect("buffer")}
          onClose={() => setOpenSelect(null)}
        />
        <WizardSelect
          label={t("commerce:listingWizard.cancellation")}
          sheetTitle={t("commerce:listingWizard.cancellation")}
          options={[
            { key: "flexible", label: t("commerce:listingWizard.cancellationFlexible") },
            { key: "24_hours", label: t("commerce:listingWizard.cancellation24") },
            { key: "48_hours", label: t("commerce:listingWizard.cancellation48") },
            { key: "strict", label: t("commerce:listingWizard.cancellationStrict") }
          ]}
          selectedKey={booking.cancellationPolicy}
          onSelect={(cancellationPolicy) => patch({ cancellationPolicy })}
          open={openSelect === "cancellation"}
          onOpen={() => setOpenSelect("cancellation")}
          onClose={() => setOpenSelect(null)}
        />
      </WizardCard>
    );
  }

  /* -------------------------------------------------------------- *
   * Stage 3 — preview
   * -------------------------------------------------------------- */

  function renderPreviewChips() {
    switch (draft.listingType) {
      case "physical":
        return (
          <>
            <WizardChip
              icon="pricetag-outline"
              label={t(`commerce:listingWizard.condition${conditionSuffix(draft.physical.condition)}`)}
            />
            <WizardChip
              icon="cube-outline"
              label={
                draft.physical.deliveryOption === "pickup"
                  ? t("commerce:listingWizard.deliveryPickup")
                  : draft.physical.deliveryOption === "shipping"
                    ? t("commerce:listingWizard.deliveryShipping")
                    : t("commerce:listingWizard.bothLabel")
              }
            />
            {draft.physical.quantity > 0 ? (
              <WizardChip
                icon="layers-outline"
                label={t("commerce:listingWizard.inStockCount", { count: draft.physical.quantity })}
              />
            ) : null}
          </>
        );
      case "digital":
        return (
          <>
            <WizardChip icon="flash-outline" label={t("commerce:listingWizard.chipInstantDownload")} />
            <WizardChip
              icon="ribbon-outline"
              label={
                draft.digital.license === "commercial"
                  ? t("commerce:listingWizard.licenseCommercial")
                  : t("commerce:listingWizard.licensePersonal")
              }
            />
            <WizardChip
              icon="documents-outline"
              label={t("commerce:listingWizard.fileCount", { count: draft.digital.files.length })}
            />
          </>
        );
      case "service":
        return (
          <>
            <WizardChip
              icon="time-outline"
              label={t("commerce:listingWizard.days", { count: draft.service.deliveryTimeDays })}
            />
            <WizardChip
              icon="location-outline"
              label={
                draft.service.serviceLocation === "remote"
                  ? t("commerce:listingWizard.serviceRemote")
                  : draft.service.serviceLocation === "in_person"
                    ? t("commerce:listingWizard.inPersonLabel")
                    : t("commerce:listingWizard.bothLabel")
              }
            />
            {draft.service.included.filter((item) => item.trim()).map((item, index) => (
              <WizardChip key={index} icon="checkmark-circle-outline" label={item.trim()} />
            ))}
          </>
        );
      case "event": {
        const spots = draft.event.tickets.reduce((sum, ticket) => sum + (Number(ticket.capacity) || 0), 0);
        return (
          <>
            <WizardChip
              icon="calendar-outline"
              label={`${formatters.day(draft.event.date)} · ${draft.event.startTime}`}
            />
            <WizardChip
              icon="location-outline"
              label={
                draft.event.venueMode === "online"
                  ? t("commerce:listingWizard.whereOnline")
                  : draft.event.venueMode === "pulsesoc_live"
                    ? t("commerce:listingWizard.wherePulsesocLive")
                    : draft.event.location.trim() || t("commerce:listingWizard.inPersonLabel")
              }
            />
            {spots > 0 ? (
              <WizardChip icon="people-outline" label={t("commerce:listingWizard.spots", { count: spots })} />
            ) : null}
          </>
        );
      }
      case "booking":
        return (
          <>
            <WizardChip
              icon="time-outline"
              label={t("commerce:listingWizard.minutesShort", { minutes: draft.booking.durationMinutes })}
            />
            <WizardChip
              icon={
                draft.booking.meetingMode === "video"
                  ? "videocam-outline"
                  : draft.booking.meetingMode === "audio"
                    ? "call-outline"
                    : "location-outline"
              }
              label={
                draft.booking.meetingMode === "video"
                  ? t("commerce:listingWizard.meetingVideo")
                  : draft.booking.meetingMode === "audio"
                    ? t("commerce:listingWizard.meetingAudio")
                    : t("commerce:listingWizard.inPersonLabel")
              }
            />
            <WizardChip
              icon="shield-checkmark-outline"
              label={t(`commerce:listingWizard.${cancellationKey(draft.booking.cancellationPolicy)}`)}
            />
          </>
        );
      default:
        return null;
    }
  }

  function renderPreview() {
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>{t("commerce:listingWizard.previewTitle")}</Text>
        <Text style={styles.pageSubtitle}>{t("commerce:listingWizard.previewSubtitle")}</Text>
        <WizardCard style={styles.previewCard}>
          {draft.coverPreviewUri ? (
            <Image source={{ uri: draft.coverPreviewUri }} style={styles.previewCover} />
          ) : (
            <View style={[styles.previewCover, styles.coverFallback]}>
              <Ionicons name="image-outline" size={34} color={storeLight.text.muted} />
            </View>
          )}
          <View style={styles.previewBody}>
            <Text style={styles.previewTitle}>
              {draft.listingType === "event" && draft.event.name.trim() ? draft.event.name.trim() : draft.title.trim()}
            </Text>
            {priceLabelPreview ? <Text style={styles.previewPrice}>{priceLabelPreview}</Text> : null}
            <View style={styles.previewChips}>{renderPreviewChips()}</View>
            <Text style={styles.previewDescription}>{draft.description.trim()}</Text>
          </View>
        </WizardCard>
        {publishError ? <Text style={styles.formError}>{publishError}</Text> : null}
        <WizardPrimaryButton
          label={publishing ? t("commerce:listingWizard.publishing") : t("commerce:listingWizard.publish")}
          onPress={() => void publish()}
          disabled={publishing}
        />
        <WizardSecondaryButton label={t("commerce:listingWizard.editDetails")} onPress={() => goToStep("details")} />
      </View>
    );
  }
}

function conditionSuffix(condition: string): "New" | "LikeNew" | "Good" | "Fair" {
  if (condition === "like_new") return "LikeNew";
  if (condition === "good") return "Good";
  if (condition === "fair") return "Fair";
  return "New";
}

function cancellationKey(policy: string): string {
  if (policy === "flexible") return "cancellationFlexible";
  if (policy === "48_hours") return "cancellation48";
  if (policy === "strict") return "cancellationStrict";
  return "cancellation24";
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  centerFill: { justifyContent: "center", padding: 20 },
  scroll: { flex: 1 },
  scrollContent: { padding: storeLight.space.card },
  stack: { gap: storeLight.space.section },
  pageTitle: { fontSize: 22, fontWeight: "800", color: storeLight.text.primary },
  pageSubtitle: { fontSize: 13, lineHeight: 19, color: storeLight.text.muted },
  wizardBar: {
    flexDirection: "row",
    alignItems: "center",
    minHeight: 48,
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline,
    paddingHorizontal: 6
  },
  barButton: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  barAction: { minWidth: 76, minHeight: 44, alignItems: "flex-end", justifyContent: "center", paddingHorizontal: 8 },
  barActionText: { fontSize: 13, fontWeight: "700", color: storeLight.text.link },
  stepIndicator: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 4 },
  stepItem: { flexDirection: "row", alignItems: "center", gap: 4 },
  stepDot: { color: storeLight.text.muted, fontSize: 13 },
  stepText: { fontSize: 12, fontWeight: "600", color: storeLight.text.muted },
  stepTextActive: { color: storeLight.text.primary, fontWeight: "800" },
  savedNote: {
    fontSize: 12,
    fontWeight: "600",
    color: storeLight.status.success,
    backgroundColor: storeLight.bg.card,
    paddingHorizontal: storeLight.space.card,
    paddingBottom: 8
  },
  typeCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    padding: storeLight.space.card
  },
  typeIconWrap: {
    width: 46,
    height: 46,
    borderRadius: 23,
    backgroundColor: storeLight.bg.skeleton,
    alignItems: "center",
    justifyContent: "center"
  },
  typeTextBlock: { flex: 1, gap: 2 },
  typeTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  typeLine: { fontSize: 12.5, color: storeLight.text.primary },
  typeLineMuted: { fontSize: 12, color: storeLight.text.muted },
  twoColumns: { flexDirection: "row", gap: 10 },
  columnWide: { flex: 1 },
  columnNarrow: { width: 110 },
  rowLine: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  rowRemove: { width: 40, height: storeLight.size.tapTarget, alignItems: "center", justifyContent: "center" },
  fieldLabelText: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  staticRow: { gap: 6 },
  staticValueWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    minHeight: 40,
    borderRadius: storeLight.radius.control,
    backgroundColor: storeLight.bg.skeleton,
    paddingHorizontal: 12
  },
  staticValue: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary },
  mediaRow: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  coverWrap: { position: "relative" },
  coverImage: {
    width: 92,
    height: 92,
    borderRadius: storeLight.radius.thumb,
    backgroundColor: storeLight.bg.skeleton
  },
  coverFallback: { alignItems: "center", justifyContent: "center" },
  coverBadge: {
    position: "absolute",
    bottom: 5,
    left: 5,
    backgroundColor: storeLight.bg.strip,
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 1
  },
  coverBadgeText: { fontSize: 10, fontWeight: "700", color: storeLight.text.onDark },
  coverRemove: {
    position: "absolute",
    top: -6,
    right: -6,
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: storeLight.bg.strip,
    alignItems: "center",
    justifyContent: "center"
  },
  mediaAdd: {
    width: 92,
    height: 92,
    borderRadius: storeLight.radius.thumb,
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center",
    gap: 4
  },
  mediaAddText: { fontSize: 11, fontWeight: "600", color: storeLight.text.muted },
  mediaMeta: { fontSize: 12, color: storeLight.text.muted },
  fileRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: storeLight.size.tapTarget,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    borderRadius: storeLight.radius.control,
    paddingHorizontal: 10
  },
  fileTextBlock: { flex: 1 },
  fileName: { fontSize: 13.5, fontWeight: "600", color: storeLight.text.primary },
  fileMeta: { fontSize: 11.5, color: storeLight.text.muted },
  ticketBlock: {
    gap: 8,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    borderRadius: storeLight.radius.control,
    padding: 10
  },
  availabilityRow: { flexDirection: "row", alignItems: "center", gap: 10, minHeight: 44 },
  availabilityDay: { flexDirection: "row", alignItems: "center", gap: 8, width: 92 },
  dayToggle: {
    width: 20,
    height: 20,
    borderRadius: 5,
    borderWidth: 2,
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center"
  },
  dayToggleOn: { backgroundColor: storeLight.accent.brandOnLight, borderColor: storeLight.accent.brandOnLight },
  dayLabel: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  dayLabelOff: { color: storeLight.text.muted },
  availabilityTimes: { flex: 1, flexDirection: "row", gap: 8 },
  unavailableText: { flex: 1, fontSize: 12.5, color: storeLight.text.muted },
  formError: { fontSize: 13, fontWeight: "600", color: storeLight.status.error },
  previewCard: { padding: 0, overflow: "hidden" },
  previewCover: { width: "100%", height: 210, backgroundColor: storeLight.bg.skeleton },
  previewBody: { padding: storeLight.space.card, gap: 8 },
  previewTitle: { fontSize: 18, fontWeight: "800", color: storeLight.text.primary },
  previewPrice: { fontSize: 20, fontWeight: "800", color: storeLight.text.primary },
  previewChips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  previewDescription: { fontSize: 13.5, lineHeight: 20, color: storeLight.text.primary },
  resumeCard: { alignItems: "stretch", gap: 12 },
  resumeTitle: { fontSize: 18, fontWeight: "800", color: storeLight.text.primary },
  resumeBody: { fontSize: 13.5, lineHeight: 20, color: storeLight.text.muted },
  successBlock: { alignItems: "center", gap: 12, paddingHorizontal: 8 },
  successBadge: {
    width: 76,
    height: 76,
    borderRadius: 38,
    backgroundColor: storeLight.cta.from,
    alignItems: "center",
    justifyContent: "center"
  },
  successTitle: { fontSize: 21, fontWeight: "800", color: storeLight.text.primary, textAlign: "center" },
  successBody: { fontSize: 14, lineHeight: 20, color: storeLight.text.muted, textAlign: "center" },
  successActions: { alignSelf: "stretch", gap: 10, marginTop: 8 }
});
