/**
 * Optimization insights — `BusinessOsAdvertising { mode: "insights" }`.
 *
 * The account-wide recommendation feed from `listAdInsights` (adsDetail), each
 * with the "why" the server computed and an Apply button that goes through an
 * explicit confirmation dialog before `applyAdInsight` posts `approve: true` —
 * never from an automatic path. A stale insight answers 409 with a sentence;
 * that sentence is shown verbatim and the feed reloads so the list stops
 * offering an action the server already refused.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Animated,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View
} from "react-native";

import { primaryAdAccount } from "../api/adsDashboard";
import { AdInsight, applyAdInsight, listAdInsights } from "../api/adsDetail";
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

const NS = "commerce:adsInsights";

export function AdsInsightsScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(2, reducedMotion);

  const [accountId, setAccountId] = useState<number>(Number(route?.params?.accountId || 0));
  const [insights, setInsights] = useState<AdInsight[]>([]);
  const [dataNote, setDataNote] = useState("");
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [errorText, setErrorText] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [applyNotes, setApplyNotes] = useState<Record<string, string>>({});

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
        const data = await listAdInsights(id);
        setInsights(data.recommendations.filter((insight) => insight.id.length > 0));
        setDataNote(String(data.data_status?.note || ""));
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

  const confirmApply = useCallback(
    (insight: AdInsight) => {
      Alert.alert(t(`${NS}.applyConfirmTitle`), insight.title, [
        { text: t(`${NS}.cancel`), style: "cancel" },
        {
          text: t(`${NS}.applyCta`),
          onPress: async () => {
            setApplyingId(insight.id);
            try {
              const res = await applyAdInsight(accountId, insight.id);
              if (res.error) {
                setApplyNotes((prev) => ({ ...prev, [insight.id]: res.error as string }));
                return;
              }
              setApplyNotes((prev) => ({ ...prev, [insight.id]: t(`${NS}.applied`) }));
              await load(true);
            } catch (error) {
              // A stale insight answers 409 with a sentence — shown verbatim,
              // then the feed reloads so the offer disappears.
              setApplyNotes((prev) => ({
                ...prev,
                [insight.id]:
                  error instanceof Error && error.message ? error.message : t(`${NS}.applyError`)
              }));
              await load(true);
            } finally {
              setApplyingId(null);
            }
          }
        }
      ]);
    },
    [accountId, load, t]
  );

  return (
    <AdsScreenShell
      title={route?.params?.title || t(`${NS}.title`)}
      backLabel={t(`${NS}.back`)}
      onBack={() => navigation?.goBack?.()}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor={adsLight.text.muted} />
      }
    >
      {status === "loading" ? (
        <View style={s.stack}>
          <AdsSkeletonBlock width="100%" height={110} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={110} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
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
      ) : (
        <>
          <Animated.View style={[s.stack, entrance.styleFor(0)]}>
            {dataNote ? <AdsOfflineNote text={dataNote} /> : null}
            <Text style={s.meta}>
              {t(`${NS}.countLine`, { count: insights.length })}
            </Text>
          </Animated.View>
          <Animated.View style={[s.stack, entrance.styleFor(1)]}>
            {insights.length === 0 ? (
              <AdsEmpty
                title={t(`${NS}.emptyTitle`)}
                body={t(`${NS}.emptyBody`)}
                reducedMotion={reducedMotion}
              />
            ) : (
              insights.map((insight) => (
                <View key={insight.id} style={s.card}>
                  <View style={s.headRow}>
                    <Text style={s.cardTitle}>{insight.title}</Text>
                    <View
                      style={[
                        styles.severityChip,
                        insight.severity === "warning" ? styles.severityWarning : styles.severityOpportunity
                      ]}
                    >
                      <Text style={styles.severityText}>
                        {insight.severity === "warning"
                          ? t(`${NS}.severityWarning`)
                          : t(`${NS}.severityOpportunity`)}
                      </Text>
                    </View>
                  </View>
                  <View style={s.reasonBox}>
                    <Text style={s.reasonLabel}>{t(`${NS}.whyLabel`)}</Text>
                    <Text style={s.cardBody}>{insight.why}</Text>
                  </View>
                  <View style={s.chipRow}>
                    <Pressable
                      style={[s.primaryBtn, applyingId === insight.id ? styles.busy : null]}
                      onPress={() => confirmApply(insight)}
                      disabled={applyingId !== null}
                      accessibilityRole="button"
                      accessibilityLabel={t(`${NS}.applyCta`)}
                    >
                      <Text style={s.primaryBtnText}>
                        {applyingId === insight.id ? t(`${NS}.working`) : t(`${NS}.applyCta`)}
                      </Text>
                    </Pressable>
                    {insight.campaign_id > 0 ? (
                      <Pressable
                        style={s.secondaryBtn}
                        onPress={() =>
                          navigation?.navigate("BusinessOsAdvertising", {
                            mode: "detail",
                            campaignId: insight.campaign_id
                          })
                        }
                        accessibilityRole="button"
                        accessibilityLabel={t(`${NS}.openCampaign`)}
                      >
                        <Text style={s.secondaryBtnText}>{t(`${NS}.openCampaign`)}</Text>
                      </Pressable>
                    ) : null}
                  </View>
                  {applyNotes[insight.id] ? <AdsOfflineNote text={applyNotes[insight.id]} /> : null}
                </View>
              ))
            )}
          </Animated.View>
        </>
      )}
    </AdsScreenShell>
  );
}

const styles = StyleSheet.create({
  severityChip: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: adsLight.radius.pill
  },
  severityOpportunity: { backgroundColor: adsLight.bg.strip },
  severityWarning: { backgroundColor: adsLight.bg.warning },
  severityText: { fontSize: 11, fontWeight: "800", color: adsLight.text.primary },
  busy: { opacity: 0.6 }
});

export default AdsInsightsScreen;
