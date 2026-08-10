/**
 * Policy Center — `BusinessOsAdvertising { mode: "policy" }`.
 *
 * The server-backed review board via `api/adsPolicyCenter`: account and
 * verification status, the four counts, rejected creatives with the reason and
 * the affected component, restrictions, and the appeal flow. The one rule that
 * matters most here: `appealable: false` means an appeal is already open, so
 * the compose box never renders in that state — the server would 409 the
 * submit. Fix guidance points at the affected component, and the resubmit path
 * goes through the Creative Library (edit resets moderation to draft, then
 * submit again).
 */

import { useCallback, useEffect, useState } from "react";
import {
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
  AdPolicyCenter,
  AdPolicyComponent,
  AdPolicyRejection,
  createAdAppeal,
  getAdPolicyCenter,
  openAppealForCreative
} from "../api/adsPolicyCenter";
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

const NS = "commerce:adsPolicy";

const COMPONENT_KEYS: Record<AdPolicyComponent, { label: string; guidance: string }> = {
  destination: { label: "componentDestination", guidance: "guidanceDestination" },
  media: { label: "componentMedia", guidance: "guidanceMedia" },
  targeting: { label: "componentTargeting", guidance: "guidanceTargeting" },
  creative_text: { label: "componentCreativeText", guidance: "guidanceCreativeText" }
};

export function AdsPolicyCenterScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(4, reducedMotion);

  const [accountId, setAccountId] = useState<number>(Number(route?.params?.accountId || 0));
  const [board, setBoard] = useState<AdPolicyCenter | null>(null);
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [errorText, setErrorText] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const [selected, setSelected] = useState<AdPolicyRejection | null>(null);
  const [appealText, setAppealText] = useState("");
  const [appealBusy, setAppealBusy] = useState(false);
  const [appealNote, setAppealNote] = useState("");

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
        const data = await getAdPolicyCenter(id);
        setBoard(data);
        setStatus("ok");
        if (selected) {
          const refreshed = data.rejected.find((rejection) => rejection.id === selected.id);
          setSelected(refreshed || null);
        }
      } catch (error) {
        setStatus("error");
        setErrorText(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
      } finally {
        if (asRefresh) setRefreshing(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [accountId, t]
  );

  useEffect(() => {
    load().catch(() => setStatus("error"));
  }, [load]);

  const submitAppeal = useCallback(async () => {
    if (!selected) return;
    if (!appealText.trim()) {
      setAppealNote(t(`${NS}.appealMissing`));
      return;
    }
    setAppealBusy(true);
    setAppealNote("");
    try {
      await createAdAppeal(selected.id, appealText.trim());
      setAppealText("");
      setAppealNote(t(`${NS}.appealSubmitted`));
      await load(true);
    } catch (error) {
      // Server sentences (open appeal 409, rate limit) shown verbatim.
      setAppealNote(error instanceof Error && error.message ? error.message : t(`${NS}.saveError`));
    } finally {
      setAppealBusy(false);
    }
  }, [appealText, load, selected, t]);

  const back = useCallback(() => {
    if (selected) {
      setSelected(null);
      setAppealNote("");
      setAppealText("");
      return;
    }
    navigation?.goBack?.();
  }, [navigation, selected]);

  const renderDetail = (rejection: AdPolicyRejection) => {
    const openAppeal = board ? openAppealForCreative(board.appeals, rejection.id) : null;
    const componentKeys = COMPONENT_KEYS[rejection.affected_component];
    return (
      <View style={s.stack}>
        <View style={s.card}>
          <Text style={s.cardTitle}>{rejection.title || `#${rejection.id}`}</Text>
          <Text style={s.meta}>
            {rejection.creative_type}
            {rejection.updated_at ? ` · ${rejection.updated_at.slice(0, 10)}` : ""}
          </Text>
          <View style={s.reasonBox}>
            <Text style={s.reasonLabel}>{t(`${NS}.reasonLabel`)}</Text>
            <Text style={s.cardBody}>
              {rejection.rejection_reason || t(`${NS}.noReasonRecorded`)}
            </Text>
          </View>
          <Text style={s.inputLabel}>{t(`${NS}.componentLabel`)}</Text>
          <Text style={s.notice}>{t(`${NS}.${componentKeys.label}`)}</Text>
          <Text style={s.cardBody}>{t(`${NS}.${componentKeys.guidance}`)}</Text>
          <Pressable
            style={s.secondaryBtn}
            onPress={() => navigation?.navigate("BusinessOsAdvertising", { mode: "creatives" })}
            accessibilityRole="button"
            accessibilityLabel={t(`${NS}.fixInLibrary`)}
          >
            <Text style={s.secondaryBtnText}>{t(`${NS}.fixInLibrary`)}</Text>
          </Pressable>
        </View>

        <View style={s.card}>
          <Text style={s.cardTitle}>{t(`${NS}.appealTitle`)}</Text>
          {openAppeal ? (
            <View style={s.reasonBox}>
              <Text style={s.reasonLabel}>{t(`${NS}.appealStatusOpen`)}</Text>
              <Text style={s.cardBody}>{openAppeal.message}</Text>
            </View>
          ) : rejection.appealable ? (
            <>
              <Text style={s.cardBody}>{t(`${NS}.appealBody`)}</Text>
              <TextInput
                style={[s.input, styles.appealInput]}
                value={appealText}
                onChangeText={setAppealText}
                multiline
                placeholder={t(`${NS}.appealPlaceholder`)}
                placeholderTextColor={adsLight.text.muted}
                accessibilityLabel={t(`${NS}.appealPlaceholder`)}
              />
              <Pressable
                style={[s.primaryBtn, appealBusy ? styles.busy : null]}
                onPress={submitAppeal}
                disabled={appealBusy}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.appealSubmit`)}
              >
                <Text style={s.primaryBtnText}>
                  {appealBusy ? t(`${NS}.working`) : t(`${NS}.appealSubmit`)}
                </Text>
              </Pressable>
            </>
          ) : (
            <Text style={s.cardBody}>{t(`${NS}.appealBlocked`)}</Text>
          )}
          {appealNote ? <AdsOfflineNote text={appealNote} /> : null}
        </View>
      </View>
    );
  };

  const renderBoard = (data: AdPolicyCenter) => (
    <>
      <Animated.View style={[s.stack, entrance.styleFor(0)]}>
        <View style={s.card}>
          <Text style={s.cardTitle}>{t(`${NS}.accountStatusTitle`)}</Text>
          <Text style={s.cardBody}>
            {t(`${NS}.accountStatusLabel`, { status: data.account_status || "—" })}
          </Text>
          <Text style={s.cardBody}>
            {t(`${NS}.verificationLabel`, { status: data.verification_status || "—" })}
          </Text>
          <View style={styles.countsRow}>
            {(
              [
                ["countInReview", data.counts.in_review],
                ["countApproved", data.counts.approved],
                ["countRejected", data.counts.rejected],
                ["countRestricted", data.counts.restricted]
              ] as const
            ).map(([key, value]) => (
              <View key={key} style={styles.countCell}>
                <Text style={styles.countValue}>{formatters.count(value)}</Text>
                <Text style={s.meta}>{t(`${NS}.${key}`)}</Text>
              </View>
            ))}
          </View>
        </View>
      </Animated.View>

      <Animated.View style={[s.stack, entrance.styleFor(1)]}>
        <Text style={s.sectionTitle}>{t(`${NS}.rejectedTitle`)}</Text>
        {data.rejected.length === 0 ? (
          <AdsEmpty
            title={t(`${NS}.rejectedEmptyTitle`)}
            body={t(`${NS}.rejectedEmptyBody`)}
            reducedMotion={reducedMotion}
          />
        ) : (
          data.rejected.map((rejection) => (
            <Pressable
              key={rejection.id}
              style={s.card}
              onPress={() => {
                setSelected(rejection);
                setAppealNote("");
                setAppealText("");
              }}
              accessibilityRole="button"
              accessibilityLabel={rejection.title || `#${rejection.id}`}
            >
              <View style={s.headRow}>
                <Text style={s.cardTitle} numberOfLines={1}>
                  {rejection.title || `#${rejection.id}`}
                </Text>
                <Text style={styles.componentTag}>
                  {t(`${NS}.${COMPONENT_KEYS[rejection.affected_component].label}`)}
                </Text>
              </View>
              <Text style={s.cardBody} numberOfLines={2}>
                {rejection.rejection_reason || t(`${NS}.noReasonRecorded`)}
              </Text>
              {!rejection.appealable ? (
                <Text style={s.meta}>{t(`${NS}.appealBlocked`)}</Text>
              ) : null}
            </Pressable>
          ))
        )}
      </Animated.View>

      <Animated.View style={[s.stack, entrance.styleFor(2)]}>
        <Text style={s.sectionTitle}>{t(`${NS}.restrictionsTitle`)}</Text>
        {data.restrictions.length === 0 ? (
          <View style={s.card}>
            <Text style={s.cardBody}>{t(`${NS}.restrictionsEmpty`)}</Text>
          </View>
        ) : (
          data.restrictions.map((restriction, index) => (
            <View key={`${restriction.creative_id}-${index}`} style={s.card}>
              <Text style={s.cardTitle}>{restriction.flag_type}</Text>
              <Text style={s.meta}>
                {restriction.severity}
                {restriction.created_at ? ` · ${restriction.created_at.slice(0, 10)}` : ""}
              </Text>
              {restriction.details ? <Text style={s.cardBody}>{restriction.details}</Text> : null}
            </View>
          ))
        )}
      </Animated.View>

      <Animated.View style={[s.stack, entrance.styleFor(3)]}>
        <Text style={s.sectionTitle}>{t(`${NS}.appealsTitle`)}</Text>
        {data.appeals.length === 0 ? (
          <View style={s.card}>
            <Text style={s.cardBody}>{t(`${NS}.appealsEmpty`)}</Text>
          </View>
        ) : (
          data.appeals.map((appeal) => (
            <View key={appeal.id} style={s.card}>
              <View style={s.headRow}>
                <Text style={s.cardTitle}>#{appeal.creative_id}</Text>
                <Text style={s.meta}>
                  {appeal.status.toLowerCase() === "open"
                    ? t(`${NS}.appealStatusOpen`)
                    : t(`${NS}.appealDecided`, { decision: appeal.decision || appeal.status })}
                </Text>
              </View>
              <Text style={s.cardBody} numberOfLines={3}>
                {appeal.message}
              </Text>
              {appeal.decision_reason ? (
                <View style={s.reasonBox}>
                  <Text style={s.reasonLabel}>{t(`${NS}.decisionReasonLabel`)}</Text>
                  <Text style={s.cardBody}>{appeal.decision_reason}</Text>
                </View>
              ) : null}
              <Text style={s.meta}>{appeal.created_at ? appeal.created_at.slice(0, 10) : ""}</Text>
            </View>
          ))
        )}
      </Animated.View>
    </>
  );

  return (
    <AdsScreenShell
      title={route?.params?.title || t(`${NS}.title`)}
      backLabel={t(`${NS}.back`)}
      onBack={back}
      refreshControl={
        !selected ? (
          <RefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor={adsLight.text.muted} />
        ) : undefined
      }
    >
      {status === "loading" ? (
        <View style={s.stack}>
          <AdsSkeletonBlock width="100%" height={120} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={96} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={96} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
        </View>
      ) : status === "error" || !board ? (
        <View style={s.stack}>
          <AdsSectionError
            message={errorText}
            onRetry={() => load()}
            reducedMotion={reducedMotion}
            retryLabel={t(`${NS}.retry`)}
          />
        </View>
      ) : selected ? (
        renderDetail(selected)
      ) : (
        renderBoard(board)
      )}
    </AdsScreenShell>
  );
}

const styles = StyleSheet.create({
  countsRow: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 4 },
  countCell: { minWidth: "40%", gap: 2 },
  countValue: { fontSize: 20, fontWeight: "800", color: adsLight.text.primary },
  componentTag: {
    fontSize: 11,
    fontWeight: "800",
    color: adsLight.text.muted,
    textTransform: "uppercase"
  },
  appealInput: { minHeight: 96, paddingTop: 10, textAlignVertical: "top" },
  busy: { opacity: 0.6 }
});

export default AdsPolicyCenterScreen;
