/**
 * Creative library — `BusinessOsAdvertising { mode: "creatives" }`.
 *
 * Browses every creative the user owns via `api/adsLibrary` (user-scoped —
 * the endpoint takes no account id). Three local views: the filtered list,
 * the asset detail, and the metadata editor. The two honesty rules from the
 * data layer are kept visible here: the editor only appears when the server
 * said `editable`, and the "editing resets moderation, you must resubmit"
 * warning sits above the Edit button — before the tap, not after.
 *
 * Lifecycle actions (submit / duplicate / archive / delete draft) reuse
 * `runCreativeAction` from `adsCreatives`; which of them apply is decided by
 * `creativeActionOffers`, the same gate the wave-1 screen uses.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Animated,
  Image,
  Modal,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import { AdCampaign, listAdCampaigns } from "../api/businessOs";
import { CreativeAction, creativeActionOffers, runCreativeAction } from "../api/adsCreatives";
import {
  AD_LIBRARY_FILTERS,
  AdCreativeMetadataPatch,
  AdLibraryAssetDetail,
  AdLibraryFilter,
  AdLibraryItem,
  AdLibraryOverview,
  getAdLibrary,
  getAdLibraryAsset,
  updateAdCreativeMetadata,
  useAdCreativeInCampaign
} from "../api/adsLibrary";
import { campaignPhase } from "../api/adsDashboard";
import {
  AdsEmpty,
  AdsOfflineNote,
  AdsScreenShell,
  AdsSectionError,
  AdsSkeletonBlock,
  adsSubStyles as s
} from "../components/ads";
import { useFormatters, useTranslation } from "../i18n";
import { adsLight } from "../theme/adsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance } from "../theme/storeMotion";

type Props = {
  route?: { params?: { title?: string } };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

const NS = "commerce:adsLibrary";

type ScreenView = { kind: "list" } | { kind: "detail"; id: number } | { kind: "edit"; id: number };

const FILTER_KEYS: Record<AdLibraryFilter, string> = {
  all: "filterAll",
  images: "filterImages",
  videos: "filterVideos",
  posts: "filterPosts"
};

const ACTION_KEYS: Record<CreativeAction, string> = {
  submit: "actionSubmit",
  duplicate: "actionDuplicate",
  archive: "actionArchive",
  delete_draft: "actionDelete"
};

function stateKey(item: AdLibraryItem): string {
  const moderation = item.moderation_status.toLowerCase();
  const status = item.status.toLowerCase();
  if (status === "archived") return "stateArchived";
  if (moderation === "rejected" || moderation === "blocked") return "stateRejected";
  if (moderation === "approved") return "stateApproved";
  if (moderation === "pending" || status === "pending_review") return "stateInReview";
  return "stateDraft";
}

export function AdsLibraryScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(2, reducedMotion);

  const [filter, setFilter] = useState<AdLibraryFilter>("all");
  const [overview, setOverview] = useState<AdLibraryOverview | null>(null);
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [errorText, setErrorText] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [view, setView] = useState<ScreenView>({ kind: "list" });

  const [detail, setDetail] = useState<AdLibraryAssetDetail | null>(null);
  const [detailStatus, setDetailStatus] = useState<"loading" | "ok" | "error">("loading");
  const [detailError, setDetailError] = useState("");
  const [actionBusy, setActionBusy] = useState<CreativeAction | null>(null);
  const [detailNote, setDetailNote] = useState("");

  const [patch, setPatch] = useState<AdCreativeMetadataPatch>({});
  const [editBusy, setEditBusy] = useState(false);
  const [editNote, setEditNote] = useState("");

  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerCampaigns, setPickerCampaigns] = useState<AdCampaign[]>([]);
  const [pickerStatus, setPickerStatus] = useState<"loading" | "ok" | "error">("loading");
  const [pickerError, setPickerError] = useState("");
  const [pickerBusy, setPickerBusy] = useState(false);

  const load = useCallback(
    async (asRefresh = false, nextFilter?: AdLibraryFilter) => {
      const useFilter = nextFilter ?? filter;
      if (asRefresh) setRefreshing(true);
      else setStatus("loading");
      setErrorText("");
      try {
        const data = await getAdLibrary(useFilter);
        setOverview(data);
        setStatus("ok");
      } catch (error) {
        setStatus("error");
        setErrorText(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
      } finally {
        if (asRefresh) setRefreshing(false);
      }
    },
    [filter, t]
  );

  useEffect(() => {
    load().catch(() => setStatus("error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeFilter = useCallback(
    (next: AdLibraryFilter) => {
      setFilter(next);
      load(false, next).catch(() => setStatus("error"));
    },
    [load]
  );

  const openDetail = useCallback(
    async (id: number) => {
      setView({ kind: "detail", id });
      setDetailStatus("loading");
      setDetailError("");
      setDetailNote("");
      try {
        const data = await getAdLibraryAsset(id);
        setDetail(data);
        setDetailStatus("ok");
      } catch (error) {
        setDetailStatus("error");
        setDetailError(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
      }
    },
    [t]
  );

  const back = useCallback(() => {
    if (view.kind === "edit") {
      setView({ kind: "detail", id: view.id });
      return;
    }
    if (view.kind !== "list") {
      setView({ kind: "list" });
      load().catch(() => undefined);
      return;
    }
    navigation?.goBack?.();
  }, [load, navigation, view]);

  const runAction = useCallback(
    (action: CreativeAction) => {
      if (!detail) return;
      const perform = async () => {
        setActionBusy(action);
        setDetailNote("");
        try {
          const res = await runCreativeAction(detail.id, action);
          if (res.error) {
            setDetailNote(res.error);
            return;
          }
          if (action === "delete_draft") {
            setView({ kind: "list" });
            await load();
            return;
          }
          setDetailNote(t(`${NS}.actionDone`));
          await openDetail(detail.id);
        } catch (error) {
          setDetailNote(
            error instanceof Error && error.message ? error.message : t(`${NS}.saveError`)
          );
        } finally {
          setActionBusy(null);
        }
      };
      if (action === "archive" || action === "delete_draft") {
        Alert.alert(
          t(`${NS}.${action === "archive" ? "archiveConfirmTitle" : "deleteConfirmTitle"}`),
          t(`${NS}.${action === "archive" ? "archiveConfirmBody" : "deleteConfirmBody"}`),
          [
            { text: t(`${NS}.cancel`), style: "cancel" },
            { text: t(`${NS}.${ACTION_KEYS[action]}`), style: "destructive", onPress: perform }
          ]
        );
        return;
      }
      perform();
    },
    [detail, load, openDetail, t]
  );

  const startEdit = useCallback(() => {
    if (!detail) return;
    setPatch({
      title: detail.title,
      headline: detail.headline,
      body: detail.body,
      primary_text: detail.primary_text,
      call_to_action: detail.call_to_action,
      destination_url: detail.destination_url
    });
    setEditNote("");
    setView({ kind: "edit", id: detail.id });
  }, [detail]);

  const submitEdit = useCallback(() => {
    if (view.kind !== "edit") return;
    Alert.alert(t(`${NS}.editConfirmTitle`), t(`${NS}.editConfirmBody`), [
      { text: t(`${NS}.cancel`), style: "cancel" },
      {
        text: t(`${NS}.editSubmit`),
        onPress: async () => {
          setEditBusy(true);
          setEditNote("");
          try {
            const updated = await updateAdCreativeMetadata(view.id, patch);
            setDetail(updated);
            setView({ kind: "detail", id: view.id });
            setDetailNote(t(`${NS}.editSaved`));
          } catch (error) {
            setEditNote(
              error instanceof Error && error.message ? error.message : t(`${NS}.saveError`)
            );
          } finally {
            setEditBusy(false);
          }
        }
      }
    ]);
  }, [patch, t, view]);

  const openPicker = useCallback(async () => {
    setPickerOpen(true);
    setPickerStatus("loading");
    setPickerError("");
    try {
      const res = await listAdCampaigns();
      const open = (res.campaigns || []).filter(
        (campaign) => campaignPhase(campaign) !== "ended"
      );
      setPickerCampaigns(open);
      setPickerStatus("ok");
    } catch (error) {
      setPickerStatus("error");
      setPickerError(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
    }
  }, [t]);

  const useInCampaign = useCallback(
    async (campaignId: number) => {
      if (!detail || pickerBusy) return;
      setPickerBusy(true);
      setPickerError("");
      try {
        await useAdCreativeInCampaign(detail.id, campaignId);
        setPickerOpen(false);
        setDetailNote(t(`${NS}.useDone`));
      } catch (error) {
        setPickerError(error instanceof Error && error.message ? error.message : t(`${NS}.saveError`));
      } finally {
        setPickerBusy(false);
      }
    },
    [detail, pickerBusy, t]
  );

  /* ---------------------------------------------------------------- */

  const metricsLine = useCallback(
    (item: AdLibraryItem) =>
      t(`${NS}.metricsLine`, {
        impressions: formatters.count(item.performance.impressions),
        clicks: formatters.count(item.performance.clicks),
        ctr: formatters.percent(item.performance.ctr)
      }),
    [formatters, t]
  );

  const renderThumb = (item: AdLibraryItem) =>
    item.thumbnail_url || item.media_url ? (
      <Image
        source={{ uri: item.thumbnail_url || item.media_url }}
        style={styles.thumb}
        accessibilityIgnoresInvertColors
      />
    ) : (
      <View style={[styles.thumb, styles.thumbEmpty]}>
        <Text style={styles.thumbEmptyText}>{(item.creative_type || "?").slice(0, 1).toUpperCase()}</Text>
      </View>
    );

  const renderList = () => (
    <>
      <Animated.View style={[s.stack, entrance.styleFor(0)]}>
        <View style={s.chipRow}>
          {AD_LIBRARY_FILTERS.map((key) => {
            const active = filter === key;
            const count = overview?.counts?.[key] ?? 0;
            return (
              <Pressable
                key={key}
                style={[s.chip, active ? s.chipActive : null]}
                onPress={() => changeFilter(key)}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={t(`${NS}.${FILTER_KEYS[key]}`)}
              >
                <Text style={[s.chipText, active ? s.chipTextActive : null]}>
                  {t(`${NS}.${FILTER_KEYS[key]}`)} · {formatters.count(count)}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </Animated.View>
      <Animated.View style={[s.stack, entrance.styleFor(1)]}>
        {!overview || overview.creatives.length === 0 ? (
          <AdsEmpty
            title={t(`${NS}.emptyTitle`)}
            body={t(`${NS}.emptyBody`)}
            reducedMotion={reducedMotion}
          />
        ) : (
          overview.creatives.map((item) => (
            <Pressable
              key={item.id}
              style={s.card}
              onPress={() => openDetail(item.id)}
              accessibilityRole="button"
              accessibilityLabel={item.title || `#${item.id}`}
            >
              <View style={styles.itemRow}>
                {renderThumb(item)}
                <View style={styles.itemBody}>
                  <Text style={s.cardTitle} numberOfLines={1}>
                    {item.title || `#${item.id}`}
                  </Text>
                  <Text style={s.meta}>
                    {item.creative_type} · {t(`${NS}.${stateKey(item)}`)}
                    {!item.media_ready ? ` · ${t(`${NS}.mediaProcessing`)}` : ""}
                  </Text>
                  <Text style={s.meta}>
                    {item.campaign
                      ? t(`${NS}.usedIn`, { name: item.campaign.campaign_name || `#${item.campaign.campaign_id}` })
                      : t(`${NS}.notUsed`)}
                  </Text>
                  <Text style={s.meta}>{metricsLine(item)}</Text>
                  {item.policy_flags.length > 0 ? (
                    <Text style={styles.flagText}>
                      {t(`${NS}.flagsCount`, { count: item.policy_flags.length })}
                    </Text>
                  ) : null}
                </View>
              </View>
            </Pressable>
          ))
        )}
      </Animated.View>
    </>
  );

  const renderDetail = () => {
    if (detailStatus === "loading") {
      return (
        <View style={s.stack}>
          <AdsSkeletonBlock width="100%" height={220} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
        </View>
      );
    }
    if (detailStatus === "error" || !detail) {
      return (
        <View style={s.stack}>
          <AdsSectionError
            message={detailError}
            onRetry={() => view.kind === "detail" && openDetail(view.id)}
            reducedMotion={reducedMotion}
            retryLabel={t(`${NS}.retry`)}
          />
        </View>
      );
    }
    const offers = creativeActionOffers({
      id: detail.id,
      status: detail.status,
      moderation_status: detail.moderation_status
    });
    return (
      <View style={s.stack}>
        <View style={s.card}>
          {detail.thumbnail_url || detail.media_url ? (
            <Image
              source={{ uri: detail.thumbnail_url || detail.media_url }}
              style={styles.preview}
              accessibilityIgnoresInvertColors
            />
          ) : (
            <Text style={s.cardBody}>{t(`${NS}.previewUnavailable`)}</Text>
          )}
          <Text style={s.cardTitle}>{detail.title || `#${detail.id}`}</Text>
          <Text style={s.meta}>
            {detail.creative_type} · {t(`${NS}.${stateKey(detail)}`)}
            {!detail.media_ready ? ` · ${t(`${NS}.mediaProcessing`)}` : ""}
          </Text>
          {detail.rejection_reason ? (
            <View style={s.reasonBox}>
              <Text style={s.reasonLabel}>{t(`${NS}.rejectionLabel`)}</Text>
              <Text style={s.cardBody}>{detail.rejection_reason}</Text>
            </View>
          ) : null}
          {detailNote ? <AdsOfflineNote text={detailNote} /> : null}
        </View>

        <View style={s.card}>
          <Text style={s.cardTitle}>{t(`${NS}.fieldsTitle`)}</Text>
          {(
            [
              ["fieldHeadline", detail.headline],
              ["fieldBody", detail.body],
              ["fieldPrimaryText", detail.primary_text],
              ["fieldCta", detail.call_to_action],
              ["fieldDestination", detail.destination_url]
            ] as const
          ).map(([key, value]) =>
            value ? (
              <View key={key}>
                <Text style={s.inputLabel}>{t(`${NS}.${key}`)}</Text>
                <Text style={s.cardBody}>{value}</Text>
              </View>
            ) : null
          )}
        </View>

        <View style={s.card}>
          <Text style={s.cardTitle}>{t(`${NS}.metricsTitle`)}</Text>
          <Text style={s.cardBody}>{metricsLine(detail)}</Text>
          <Text style={s.meta}>
            {detail.campaign
              ? t(`${NS}.usedIn`, {
                  name: detail.campaign.campaign_name || `#${detail.campaign.campaign_id}`
                })
              : t(`${NS}.notUsed`)}
          </Text>
          {detail.campaign ? (
            <Pressable
              onPress={() =>
                navigation?.navigate("BusinessOsAdvertising", {
                  mode: "detail",
                  campaignId: detail.campaign!.campaign_id
                })
              }
              accessibilityRole="button"
              accessibilityLabel={t(`${NS}.openCampaign`)}
            >
              <Text style={s.inlineLink}>{t(`${NS}.openCampaign`)}</Text>
            </Pressable>
          ) : null}
        </View>

        <View style={s.card}>
          <Text style={s.cardTitle}>{t(`${NS}.historyTitle`)}</Text>
          {detail.moderation_history.length === 0 ? (
            <Text style={s.cardBody}>{t(`${NS}.historyEmpty`)}</Text>
          ) : (
            detail.moderation_history.map((entry, index) => (
              <View key={`${entry.source}-${entry.created_at}-${index}`} style={s.reasonBox}>
                <Text style={s.reasonLabel}>
                  {entry.source === "review_board"
                    ? t(`${NS}.sourceReviewBoard`)
                    : t(`${NS}.sourceModeration`)}
                  {entry.created_at ? ` · ${entry.created_at.slice(0, 10)}` : ""}
                </Text>
                <Text style={s.cardBody}>
                  {entry.status}
                  {entry.notes ? ` — ${entry.notes}` : ""}
                </Text>
              </View>
            ))
          )}
        </View>

        <View style={s.card}>
          {detail.editable ? (
            <>
              <Text style={s.meta}>{t(`${NS}.editWarn`)}</Text>
              <Pressable
                style={s.secondaryBtn}
                onPress={startEdit}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.editCta`)}
              >
                <Text style={s.secondaryBtnText}>{t(`${NS}.editCta`)}</Text>
              </Pressable>
            </>
          ) : (
            <Text style={s.meta}>{t(`${NS}.notEditable`)}</Text>
          )}
          <Pressable
            style={s.secondaryBtn}
            onPress={openPicker}
            accessibilityRole="button"
            accessibilityLabel={t(`${NS}.useCta`)}
          >
            <Text style={s.secondaryBtnText}>{t(`${NS}.useCta`)}</Text>
          </Pressable>
          {offers.map((offer) => (
            <Pressable
              key={offer.action}
              style={s.secondaryBtn}
              onPress={() => runAction(offer.action)}
              disabled={actionBusy !== null}
              accessibilityRole="button"
              accessibilityLabel={t(`${NS}.${ACTION_KEYS[offer.action]}`)}
            >
              <Text style={[s.secondaryBtnText, offer.destructive ? styles.destructive : null]}>
                {actionBusy === offer.action
                  ? t(`${NS}.working`)
                  : t(`${NS}.${ACTION_KEYS[offer.action]}`)}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>
    );
  };

  const renderEdit = () => (
    <View style={s.stack}>
      <View style={s.card}>
        <Text style={s.cardTitle}>{t(`${NS}.editTitle`)}</Text>
        <Text style={s.cardBody}>{t(`${NS}.editWarn`)}</Text>
        {(
          [
            ["fieldTitle", "title"],
            ["fieldHeadline", "headline"],
            ["fieldBody", "body"],
            ["fieldPrimaryText", "primary_text"],
            ["fieldCta", "call_to_action"],
            ["fieldDestination", "destination_url"]
          ] as const
        ).map(([labelKey, field]) => (
          <View key={field}>
            <Text style={s.inputLabel}>{t(`${NS}.${labelKey}`)}</Text>
            <TextInput
              style={s.input}
              value={String(patch[field] ?? "")}
              onChangeText={(text) => setPatch((prev) => ({ ...prev, [field]: text }))}
              accessibilityLabel={t(`${NS}.${labelKey}`)}
              autoCapitalize={field === "destination_url" ? "none" : "sentences"}
            />
          </View>
        ))}
        <Pressable
          style={[s.primaryBtn, editBusy ? styles.busy : null]}
          onPress={submitEdit}
          disabled={editBusy}
          accessibilityRole="button"
          accessibilityLabel={t(`${NS}.editSubmit`)}
        >
          <Text style={s.primaryBtnText}>{editBusy ? t(`${NS}.working`) : t(`${NS}.editSubmit`)}</Text>
        </Pressable>
        <Pressable onPress={back} accessibilityRole="button" accessibilityLabel={t(`${NS}.cancel`)}>
          <Text style={s.inlineLink}>{t(`${NS}.cancel`)}</Text>
        </Pressable>
        {editNote ? <AdsOfflineNote text={editNote} /> : null}
      </View>
    </View>
  );

  return (
    <AdsScreenShell
      title={route?.params?.title || t(`${NS}.title`)}
      backLabel={t(`${NS}.back`)}
      onBack={back}
      refreshControl={
        view.kind === "list" ? (
          <RefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor={adsLight.text.muted} />
        ) : undefined
      }
    >
      {status === "loading" ? (
        <View style={s.stack}>
          <AdsSkeletonBlock width="100%" height={44} radius={adsLight.radius.control} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={96} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={96} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
        </View>
      ) : status === "error" ? (
        <View style={s.stack}>
          <AdsSectionError
            message={errorText}
            onRetry={() => load()}
            reducedMotion={reducedMotion}
            retryLabel={t(`${NS}.retry`)}
          />
        </View>
      ) : view.kind === "list" ? (
        renderList()
      ) : view.kind === "detail" ? (
        renderDetail()
      ) : (
        renderEdit()
      )}

      <Modal
        visible={pickerOpen}
        transparent
        animationType={reducedMotion ? "none" : "fade"}
        onRequestClose={() => setPickerOpen(false)}
      >
        <View style={s.modalBackdrop}>
          <View style={s.modalCard}>
            <Text style={s.cardTitle}>{t(`${NS}.usePickerTitle`)}</Text>
            <Text style={s.cardBody}>{t(`${NS}.usePickerBody`)}</Text>
            {pickerStatus === "loading" ? (
              <AdsSkeletonBlock width="100%" height={44} radius={adsLight.radius.control} reducedMotion={reducedMotion} />
            ) : pickerStatus === "error" ? (
              <AdsSectionError
                message={pickerError}
                onRetry={openPicker}
                reducedMotion={reducedMotion}
                retryLabel={t(`${NS}.retry`)}
              />
            ) : pickerCampaigns.length === 0 ? (
              <Text style={s.cardBody}>{t(`${NS}.useEmpty`)}</Text>
            ) : (
              pickerCampaigns.map((campaign) => (
                <Pressable
                  key={campaign.id}
                  style={s.secondaryBtn}
                  onPress={() => useInCampaign(campaign.id)}
                  disabled={pickerBusy}
                  accessibilityRole="button"
                  accessibilityLabel={campaign.campaign_name || `#${campaign.id}`}
                >
                  <Text style={s.secondaryBtnText} numberOfLines={1}>
                    {campaign.campaign_name || `#${campaign.id}`}
                  </Text>
                </Pressable>
              ))
            )}
            {pickerError && pickerStatus === "ok" ? <AdsOfflineNote text={pickerError} /> : null}
            <Pressable
              onPress={() => setPickerOpen(false)}
              accessibilityRole="button"
              accessibilityLabel={t(`${NS}.cancel`)}
            >
              <Text style={s.inlineLink}>{t(`${NS}.cancel`)}</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </AdsScreenShell>
  );
}

const styles = StyleSheet.create({
  itemRow: { flexDirection: "row", gap: 12 },
  itemBody: { flex: 1, gap: 2 },
  thumb: {
    width: adsLight.size.thumb,
    height: adsLight.size.thumb,
    borderRadius: adsLight.radius.thumb,
    backgroundColor: adsLight.bg.skeleton
  },
  thumbEmpty: { alignItems: "center", justifyContent: "center" },
  thumbEmptyText: { fontSize: 18, fontWeight: "800", color: adsLight.text.muted },
  preview: {
    width: "100%",
    height: 180,
    borderRadius: adsLight.radius.thumb,
    backgroundColor: adsLight.bg.skeleton
  },
  flagText: { fontSize: 12, fontWeight: "700", color: adsLight.status.warning },
  destructive: { color: adsLight.status.error },
  busy: { opacity: 0.6 }
});

export default AdsLibraryScreen;
