/**
 * Audience manager — `BusinessOsAdvertising { mode: "audiences" }`.
 *
 * One screen, five views (list / create / lookalike / detail / edit) driven by
 * local state — the route contract stays a single mode, like the other wave-2
 * sub-pages. Data through `api/adsAudiences`, which mirrors the backend rules:
 * engagement sources come from `AD_AUDIENCE_SOURCES` (the server validates the
 * list, the client must not invent one), lookalikes need a seed of ≥
 * `AD_LOOKALIKE_MIN_SEED`, and archiving is confirmed client-side via
 * `audienceArchiveWarning` because the server archives an in-use audience
 * without blinking.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Animated,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import { primaryAdAccount } from "../api/adsDashboard";
import {
  AD_AUDIENCE_DEFAULT_WINDOW_DAYS,
  AD_AUDIENCE_MAX_WINDOW_DAYS,
  AD_AUDIENCE_SOURCES,
  AD_LOOKALIKE_MIN_SEED,
  AdAudience,
  AdAudienceDetail,
  AdAudienceSource,
  archiveAdAudience,
  audienceArchiveWarning,
  audienceSizeBand,
  createAdAudience,
  createAdLookalikeAudience,
  eligibleLookalikeSeeds,
  getAdAudienceDetail,
  listAdAccountAudiences,
  updateAdAudience
} from "../api/adsAudiences";
import { listAdAccounts, loadCachedAdAccounts } from "../api/businessOs";
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
  route?: { params?: { title?: string; accountId?: number } };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

const NS = "commerce:adsAudiences";

type ScreenView =
  | { kind: "list" }
  | { kind: "create" }
  | { kind: "lookalike" }
  | { kind: "detail"; id: number }
  | { kind: "edit"; id: number };

const SOURCE_KEYS: Record<AdAudienceSource, string> = {
  engaged_with_content: "sourceEngaged",
  video_viewers: "sourceVideo",
  marketplace_engagers: "sourceMarketplace",
  previous_customers: "sourceCustomers",
  profile_engagers: "sourceProfile",
  live_engagers: "sourceLive"
};

export function AdsAudiencesScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(3, reducedMotion);

  const [accountId, setAccountId] = useState<number>(Number(route?.params?.accountId || 0));
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [errorText, setErrorText] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [audiences, setAudiences] = useState<AdAudience[]>([]);
  const [view, setView] = useState<ScreenView>({ kind: "list" });

  // Detail state
  const [detail, setDetail] = useState<AdAudienceDetail | null>(null);
  const [detailStatus, setDetailStatus] = useState<"loading" | "ok" | "error">("loading");
  const [detailError, setDetailError] = useState("");

  // Form state (create / lookalike / edit share these)
  const [formName, setFormName] = useState("");
  const [formSource, setFormSource] = useState<AdAudienceSource>("engaged_with_content");
  const [formWindow, setFormWindow] = useState(String(AD_AUDIENCE_DEFAULT_WINDOW_DAYS));
  const [seedId, setSeedId] = useState<number>(0);
  const [breadth, setBreadth] = useState("5");
  const [formBusy, setFormBusy] = useState(false);
  const [formNote, setFormNote] = useState("");

  const load = useCallback(
    async (asRefresh = false) => {
      if (asRefresh) setRefreshing(true);
      else setStatus("loading");
      setErrorText("");
      try {
        let id = accountId;
        if (!id) {
          const res = await listAdAccounts().catch(async () => ({
            accounts: await loadCachedAdAccounts().catch(() => [])
          }));
          id = primaryAdAccount(res.accounts || [])?.id || 0;
          if (id) setAccountId(id);
        }
        if (!id) {
          setStatus("error");
          setErrorText(t(`${NS}.noAccount`));
          return;
        }
        const data = await listAdAccountAudiences(id);
        setAudiences(data.audiences);
        setStatus("ok");
      } catch (error) {
        setStatus("error");
        setErrorText(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
      } finally {
        if (asRefresh) setRefreshing(false);
      }
    },
    [accountId, t]
  );

  useEffect(() => {
    load().catch(() => setStatus("error"));
  }, [load]);

  const openDetail = useCallback(
    async (id: number) => {
      setView({ kind: "detail", id });
      setDetailStatus("loading");
      setDetailError("");
      try {
        const data = await getAdAudienceDetail(id);
        setDetail(data);
        setDetailStatus("ok");
      } catch (error) {
        setDetailStatus("error");
        setDetailError(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
      }
    },
    [t]
  );

  const resetForm = useCallback(() => {
    setFormName("");
    setFormSource("engaged_with_content");
    setFormWindow(String(AD_AUDIENCE_DEFAULT_WINDOW_DAYS));
    setSeedId(0);
    setBreadth("5");
    setFormNote("");
  }, []);

  const seeds = useMemo(() => eligibleLookalikeSeeds(audiences), [audiences]);
  const visibleAudiences = useMemo(() => audiences.filter((a) => !a.archived), [audiences]);
  const archivedCount = audiences.length - visibleAudiences.length;

  const kindLabel = useCallback(
    (kind: string) => {
      if (kind === "custom") return t(`${NS}.kindCustom`);
      if (kind === "saved") return t(`${NS}.kindSaved`);
      if (kind === "lookalike") return t(`${NS}.kindLookalike`);
      return kind;
    },
    [t]
  );

  const bandLabel = useCallback(
    (band: "narrow" | "good" | "broad") =>
      band === "narrow"
        ? t(`${NS}.bandNarrow`)
        : band === "broad"
          ? t(`${NS}.bandBroad`)
          : t(`${NS}.bandGood`),
    [t]
  );

  const submitCreate = useCallback(async () => {
    const windowDays = Math.round(Number(formWindow));
    if (!formName.trim()) {
      setFormNote(t(`${NS}.missingName`));
      return;
    }
    if (
      !Number.isFinite(windowDays) ||
      windowDays < 1 ||
      windowDays > AD_AUDIENCE_MAX_WINDOW_DAYS
    ) {
      setFormNote(t(`${NS}.invalidWindow`, { max: String(AD_AUDIENCE_MAX_WINDOW_DAYS) }));
      return;
    }
    setFormBusy(true);
    setFormNote("");
    try {
      await createAdAudience({
        account_id: accountId,
        name: formName.trim(),
        kind: "custom",
        definition: { source: formSource, window_days: windowDays }
      });
      resetForm();
      setView({ kind: "list" });
      await load();
    } catch (error) {
      setFormNote(error instanceof Error && error.message ? error.message : t(`${NS}.saveError`));
    } finally {
      setFormBusy(false);
    }
  }, [accountId, formName, formSource, formWindow, load, resetForm, t]);

  const submitLookalike = useCallback(async () => {
    const breadthPct = Math.round(Number(breadth));
    if (!formName.trim()) {
      setFormNote(t(`${NS}.missingName`));
      return;
    }
    if (!seedId) {
      setFormNote(t(`${NS}.missingSeed`));
      return;
    }
    if (!Number.isFinite(breadthPct) || breadthPct < 1 || breadthPct > 20) {
      setFormNote(t(`${NS}.invalidBreadth`));
      return;
    }
    setFormBusy(true);
    setFormNote("");
    try {
      await createAdLookalikeAudience({
        account_id: accountId,
        name: formName.trim(),
        seed_audience_id: seedId,
        breadth_pct: breadthPct
      });
      resetForm();
      setView({ kind: "list" });
      await load();
    } catch (error) {
      setFormNote(error instanceof Error && error.message ? error.message : t(`${NS}.saveError`));
    } finally {
      setFormBusy(false);
    }
  }, [accountId, breadth, formName, load, resetForm, seedId, t]);

  const submitEdit = useCallback(async () => {
    if (view.kind !== "edit") return;
    const windowDays = Math.round(Number(formWindow));
    if (!formName.trim()) {
      setFormNote(t(`${NS}.missingName`));
      return;
    }
    if (
      !Number.isFinite(windowDays) ||
      windowDays < 1 ||
      windowDays > AD_AUDIENCE_MAX_WINDOW_DAYS
    ) {
      setFormNote(t(`${NS}.invalidWindow`, { max: String(AD_AUDIENCE_MAX_WINDOW_DAYS) }));
      return;
    }
    setFormBusy(true);
    setFormNote("");
    try {
      await updateAdAudience(view.id, {
        name: formName.trim(),
        definition: { source: formSource, window_days: windowDays }
      });
      await load();
      await openDetail(view.id);
    } catch (error) {
      setFormNote(error instanceof Error && error.message ? error.message : t(`${NS}.saveError`));
    } finally {
      setFormBusy(false);
    }
  }, [formName, formSource, formWindow, load, openDetail, t, view]);

  const confirmArchive = useCallback(() => {
    if (!detail) return;
    const inUse = audienceArchiveWarning(detail);
    const body = inUse.length
      ? t(`${NS}.archiveInUse`, {
          count: inUse.length,
          names: inUse.map((ref) => ref.campaign_name || `#${ref.campaign_id}`).join(", ")
        })
      : t(`${NS}.archiveConfirmBody`);
    Alert.alert(t(`${NS}.archiveConfirmTitle`), body, [
      { text: t(`${NS}.cancel`), style: "cancel" },
      {
        text: t(`${NS}.archiveCta`),
        style: "destructive",
        onPress: async () => {
          try {
            await archiveAdAudience(detail.id);
            setView({ kind: "list" });
            await load();
          } catch (error) {
            setDetailError(
              error instanceof Error && error.message ? error.message : t(`${NS}.saveError`)
            );
          }
        }
      }
    ]);
  }, [detail, formatters, load, t]);

  const startEdit = useCallback(() => {
    if (!detail) return;
    setFormName(detail.name);
    const source = String(detail.definition?.source || "");
    setFormSource(
      (AD_AUDIENCE_SOURCES as readonly string[]).includes(source)
        ? (source as AdAudienceSource)
        : "engaged_with_content"
    );
    const windowDays = Number(detail.definition?.window_days);
    setFormWindow(
      Number.isFinite(windowDays) && windowDays > 0
        ? String(Math.round(windowDays))
        : String(AD_AUDIENCE_DEFAULT_WINDOW_DAYS)
    );
    setFormNote("");
    setView({ kind: "edit", id: detail.id });
  }, [detail]);

  const back = useCallback(() => {
    if (view.kind === "edit") {
      setView({ kind: "detail", id: view.id });
      return;
    }
    if (view.kind !== "list") {
      setView({ kind: "list" });
      return;
    }
    navigation?.goBack?.();
  }, [navigation, view]);

  /* ---------------------------------------------------------------- */

  const renderSourcePicker = () => (
    <>
      <Text style={s.inputLabel}>{t(`${NS}.sourceLabel`)}</Text>
      <View style={s.chipRow}>
        {AD_AUDIENCE_SOURCES.map((source) => {
          const active = formSource === source;
          return (
            <Pressable
              key={source}
              style={[s.chip, active ? s.chipActive : null]}
              onPress={() => setFormSource(source)}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              accessibilityLabel={t(`${NS}.${SOURCE_KEYS[source]}`)}
            >
              <Text style={[s.chipText, active ? s.chipTextActive : null]}>
                {t(`${NS}.${SOURCE_KEYS[source]}`)}
              </Text>
            </Pressable>
          );
        })}
      </View>
      <Text style={s.inputLabel}>
        {t(`${NS}.windowLabel`, { max: String(AD_AUDIENCE_MAX_WINDOW_DAYS) })}
      </Text>
      <TextInput
        style={s.input}
        value={formWindow}
        onChangeText={setFormWindow}
        keyboardType="number-pad"
        accessibilityLabel={t(`${NS}.windowLabel`, { max: String(AD_AUDIENCE_MAX_WINDOW_DAYS) })}
      />
    </>
  );

  const renderNameField = () => (
    <>
      <Text style={s.inputLabel}>{t(`${NS}.nameLabel`)}</Text>
      <TextInput
        style={s.input}
        value={formName}
        onChangeText={setFormName}
        placeholder={t(`${NS}.namePlaceholder`)}
        placeholderTextColor={adsLight.text.muted}
        accessibilityLabel={t(`${NS}.nameLabel`)}
      />
    </>
  );

  const renderForm = () => {
    const isLookalike = view.kind === "lookalike";
    const isEdit = view.kind === "edit";
    const submit = isLookalike ? submitLookalike : isEdit ? submitEdit : submitCreate;
    return (
      <View style={s.stack}>
        <View style={s.card}>
          <Text style={s.cardTitle}>
            {t(`${NS}.${isLookalike ? "lookalikeTitle" : isEdit ? "editTitle" : "createTitle"}`)}
          </Text>
          <Text style={s.cardBody}>
            {t(`${NS}.${isLookalike ? "lookalikeBody" : "createBody"}`)}
          </Text>
          {renderNameField()}
          {isLookalike ? (
            <>
              <Text style={s.inputLabel}>{t(`${NS}.seedLabel`)}</Text>
              {seeds.length === 0 ? (
                <Text style={s.cardBody}>
                  {t(`${NS}.noSeeds`, { min: formatters.count(AD_LOOKALIKE_MIN_SEED) })}
                </Text>
              ) : (
                <View style={s.chipRow}>
                  {seeds.map((seed) => {
                    const active = seedId === seed.id;
                    return (
                      <Pressable
                        key={seed.id}
                        style={[s.chip, active ? s.chipActive : null]}
                        onPress={() => setSeedId(seed.id)}
                        accessibilityRole="button"
                        accessibilityState={{ selected: active }}
                        accessibilityLabel={seed.name}
                      >
                        <Text style={[s.chipText, active ? s.chipTextActive : null]}>
                          {seed.name}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              )}
              <Text style={s.inputLabel}>{t(`${NS}.breadthLabel`)}</Text>
              <TextInput
                style={s.input}
                value={breadth}
                onChangeText={setBreadth}
                keyboardType="number-pad"
                accessibilityLabel={t(`${NS}.breadthLabel`)}
              />
            </>
          ) : (
            renderSourcePicker()
          )}
          <Pressable
            style={[s.primaryBtn, formBusy ? styles.busy : null]}
            onPress={submit}
            disabled={formBusy || (isLookalike && seeds.length === 0)}
            accessibilityRole="button"
            accessibilityLabel={t(`${NS}.${isEdit ? "editSubmit" : "createSubmit"}`)}
          >
            <Text style={s.primaryBtnText}>
              {formBusy ? t(`${NS}.working`) : t(`${NS}.${isEdit ? "editSubmit" : "createSubmit"}`)}
            </Text>
          </Pressable>
          <Pressable onPress={back} accessibilityRole="button" accessibilityLabel={t(`${NS}.cancel`)}>
            <Text style={s.inlineLink}>{t(`${NS}.cancel`)}</Text>
          </Pressable>
          {formNote ? <AdsOfflineNote text={formNote} /> : null}
        </View>
      </View>
    );
  };

  const renderDetail = () => (
    <View style={s.stack}>
      {detailStatus === "loading" ? (
        <AdsSkeletonBlock width="100%" height={180} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
      ) : detailStatus === "error" || !detail ? (
        <AdsSectionError
          message={detailError}
          onRetry={() => view.kind === "detail" && openDetail(view.id)}
          reducedMotion={reducedMotion}
          retryLabel={t(`${NS}.retry`)}
        />
      ) : (
        <>
          <View style={s.card}>
            <View style={s.headRow}>
              <Text style={s.cardTitle}>{detail.name}</Text>
              <View style={[styles.bandChip, styles[`band_${detail.estimate.band}`]]}>
                <Text style={styles.bandChipText}>{bandLabel(detail.estimate.band)}</Text>
              </View>
            </View>
            <Text style={s.meta}>{kindLabel(detail.kind)}</Text>
            <Text style={s.cardBody}>
              {t(`${NS}.detailEstimate`, { size: formatters.count(detail.estimate.estimated_size) })}
            </Text>
            {detail.warnings.map((warning) => (
              <View key={warning} style={s.reasonBox}>
                <Text style={s.cardBody}>{warning}</Text>
              </View>
            ))}
          </View>

          <View style={s.card}>
            <Text style={s.cardTitle}>{t(`${NS}.detailDefinition`)}</Text>
            {Object.entries(detail.definition).map(([key, value]) => (
              <Text key={key} style={s.cardBody}>
                {key}: {String(value)}
              </Text>
            ))}
            {Object.keys(detail.definition).length === 0 ? (
              <Text style={s.cardBody}>{t(`${NS}.detailNoDefinition`)}</Text>
            ) : null}
          </View>

          <View style={s.card}>
            <Text style={s.cardTitle}>{t(`${NS}.detailCampaigns`)}</Text>
            {detail.referenced_by_campaigns.length === 0 ? (
              <Text style={s.cardBody}>{t(`${NS}.detailNoCampaigns`)}</Text>
            ) : (
              detail.referenced_by_campaigns.map((ref) => (
                <Pressable
                  key={ref.campaign_id}
                  onPress={() =>
                    navigation?.navigate("BusinessOsAdvertising", {
                      mode: "detail",
                      campaignId: ref.campaign_id
                    })
                  }
                  accessibilityRole="button"
                  accessibilityLabel={ref.campaign_name || `#${ref.campaign_id}`}
                  style={styles.campaignRow}
                >
                  <Text style={s.notice}>{ref.campaign_name || `#${ref.campaign_id}`}</Text>
                  <Text style={s.meta}>
                    {ref.status}
                    {ref.roles.length
                      ? ` · ${ref.roles
                          .map((role) =>
                            role === "excluded" ? t(`${NS}.roleExcluded`) : t(`${NS}.roleIncluded`)
                          )
                          .join(", ")}`
                      : ""}
                  </Text>
                </Pressable>
              ))
            )}
            <Text style={s.meta}>{t(`${NS}.exclusionGuidance`)}</Text>
          </View>

          {!detail.archived ? (
            <View style={s.card}>
              <Pressable
                style={s.secondaryBtn}
                onPress={startEdit}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.editCta`)}
              >
                <Text style={s.secondaryBtnText}>{t(`${NS}.editCta`)}</Text>
              </Pressable>
              <Pressable
                style={s.secondaryBtn}
                onPress={confirmArchive}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.archiveCta`)}
              >
                <Text style={[s.secondaryBtnText, styles.destructive]}>{t(`${NS}.archiveCta`)}</Text>
              </Pressable>
            </View>
          ) : (
            <View style={s.card}>
              <Text style={s.cardBody}>{t(`${NS}.archivedNote`)}</Text>
            </View>
          )}
        </>
      )}
    </View>
  );

  const renderList = () => (
    <>
      <Animated.View style={[s.stack, entrance.styleFor(0)]}>
        <View style={s.chipRow}>
          <Pressable
            style={s.primaryBtn}
            onPress={() => {
              resetForm();
              setView({ kind: "create" });
            }}
            accessibilityRole="button"
            accessibilityLabel={t(`${NS}.createCta`)}
          >
            <Text style={s.primaryBtnText}>{t(`${NS}.createCta`)}</Text>
          </Pressable>
          <Pressable
            style={s.secondaryBtn}
            onPress={() => {
              resetForm();
              setView({ kind: "lookalike" });
            }}
            accessibilityRole="button"
            accessibilityLabel={t(`${NS}.lookalikeCta`)}
          >
            <Text style={s.secondaryBtnText}>{t(`${NS}.lookalikeCta`)}</Text>
          </Pressable>
        </View>
      </Animated.View>

      <Animated.View style={[s.stack, entrance.styleFor(1)]}>
        {visibleAudiences.length === 0 ? (
          <AdsEmpty
            title={t(`${NS}.emptyTitle`)}
            body={t(`${NS}.emptyBody`)}
            ctaLabel={t(`${NS}.createCta`)}
            onPress={() => {
              resetForm();
              setView({ kind: "create" });
            }}
            reducedMotion={reducedMotion}
          />
        ) : (
          visibleAudiences.map((audience) => {
            const band = audienceSizeBand(audience.estimated_size);
            return (
              <Pressable
                key={audience.id}
                style={s.card}
                onPress={() => openDetail(audience.id)}
                accessibilityRole="button"
                accessibilityLabel={audience.name}
              >
                <View style={s.headRow}>
                  <Text style={s.cardTitle} numberOfLines={1}>
                    {audience.name}
                  </Text>
                  <View style={[styles.bandChip, styles[`band_${band}`]]}>
                    <Text style={styles.bandChipText}>{bandLabel(band)}</Text>
                  </View>
                </View>
                <Text style={s.meta}>
                  {kindLabel(audience.kind)} ·{" "}
                  {t(`${NS}.sizeEstimate`, { size: formatters.count(audience.estimated_size) })}
                </Text>
              </Pressable>
            );
          })
        )}
        {archivedCount > 0 ? (
          <Text style={s.meta}>
            {t(`${NS}.archivedCount`, { count: archivedCount })}
          </Text>
        ) : null}
      </Animated.View>
    </>
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
          <AdsSkeletonBlock width="100%" height={52} radius={adsLight.radius.control} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={84} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={84} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
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
        renderForm()
      )}
    </AdsScreenShell>
  );
}

const styles = StyleSheet.create({
  bandChip: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.bg.strip
  },
  band_narrow: { backgroundColor: adsLight.bg.warning },
  band_good: { backgroundColor: adsLight.bg.strip },
  band_broad: { backgroundColor: adsLight.bg.warning },
  bandChipText: { fontSize: 11, fontWeight: "800", color: adsLight.text.primary },
  campaignRow: { paddingVertical: 6, gap: 2 },
  destructive: { color: adsLight.status.error },
  busy: { opacity: 0.6 }
});

export default AdsAudiencesScreen;
