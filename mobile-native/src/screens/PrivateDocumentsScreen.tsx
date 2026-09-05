/**
 * Document Intelligence — the member's private documents and what was read
 * out of them.
 *
 * The extraction state on every row is the server's word (EXTRACTED,
 * NO_CLAIMS, PROVIDER_REQUIRED, FAILED) rendered without improvement: a
 * document the extractor could not read says so, it does not quietly show
 * zero facts. Proposed claims are exactly that — proposed. Nothing becomes a
 * private fact until the member accepts it here, one claim at a time, and
 * the accept/reject verbs go to the server, which is where the decision is
 * recorded.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { pickDocument } from "../native/documents";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  PrivateClaim,
  PrivateDocument,
  PrivateDocumentsResult,
  getPrivateDocument,
  getPrivateDocuments,
  reviewPrivateClaim,
  uploadPrivateDocument
} from "../api/privateFeatures";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import {
  FeatureEmptyPanel,
  FeatureLoadingPanel,
  FeatureRefusalPanel
} from "../privateOffice/FeatureStatePanels";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateDocuments">;

const STATE_ICONS: Record<string, keyof typeof Ionicons.glyphMap> = {
  EXTRACTED: "checkmark-circle-outline",
  NO_CLAIMS: "remove-circle-outline",
  PROVIDER_REQUIRED: "cloud-offline-outline",
  FAILED: "alert-circle-outline"
};

export function PrivateDocumentsScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateDocumentsBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateDocumentsBody(_props: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [result, setResult] = useState<PrivateDocumentsResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [openDocumentId, setOpenDocumentId] = useState<number>(0);

  const load = useCallback(async () => {
    const next = await getPrivateDocuments();
    if (next.state === "LOCKED") lockOfficeLocally();
    setResult(next);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  const pickAndUpload = useCallback(async () => {
    const picked = await pickDocument();
    if (!picked.ok) {
      if (picked.reason !== "cancelled") {
        Alert.alert(t("premium:privateOffice.documents.uploadFailed"));
      }
      return;
    }
    setUploading(true);
    try {
      const written = await uploadPrivateDocument({
        uri: picked.document.uri,
        name: picked.document.name,
        mimeType: picked.document.mimeType
      });
      if (written.state === "LOCKED") {
        lockOfficeLocally();
        return;
      }
      if (written.state === "SAVED") {
        if (written.duplicate) {
          Alert.alert(
            t("premium:privateOffice.documents.duplicate.title"),
            t("premium:privateOffice.documents.duplicate.body")
          );
        }
        await load();
        setOpenDocumentId(written.document.id);
        return;
      }
      Alert.alert(
        t("premium:privateOffice.documents.uploadFailed"),
        written.state === "REJECTED" && written.message
          ? written.message
          : t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setUploading(false);
    }
  }, [load, t]);

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={[
        styles.content,
        { paddingBottom: Math.max(insets.bottom, 18) + BOTTOM_NAV_CONTENT_CLEARANCE }
      ]}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("premium:privateOffice.features.documentIntelligence.label")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.documents.subtitle")}</Text>
      </View>

      {result === null ? <FeatureLoadingPanel /> : null}

      {result && result.state === "READY" ? (
        <Pressable
          style={styles.primary}
          onPress={pickAndUpload}
          disabled={uploading}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.documents.upload")}
        >
          {uploading ? (
            <ActivityIndicator color={colors.accentStrong} />
          ) : (
            <Ionicons name="add-circle-outline" size={18} color={colors.accentStrong} />
          )}
          <Text style={styles.primaryText}>
            {uploading
              ? t("premium:privateOffice.documents.uploading")
              : t("premium:privateOffice.documents.upload")}
          </Text>
        </Pressable>
      ) : null}

      {result && result.state === "READY" && result.documents.length === 0 ? (
        <FeatureEmptyPanel
          title={t("premium:privateOffice.documents.empty.title")}
          body={t("premium:privateOffice.documents.empty.body")}
        />
      ) : null}

      {result && result.state === "READY"
        ? result.documents.map((document) => (
            <DocumentRow
              key={document.id}
              document={document}
              open={openDocumentId === document.id}
              onToggle={() =>
                setOpenDocumentId(openDocumentId === document.id ? 0 : document.id)
              }
              onChanged={load}
            />
          ))
        : null}

      {result && result.state === "NOT_ENTITLED" ? (
        <FeatureRefusalPanel state="NOT_ENTITLED" minimumTier={result.minimumTier} />
      ) : null}
      {result && result.state === "FEATURE_DISABLED" ? (
        <FeatureRefusalPanel state="FEATURE_DISABLED" />
      ) : null}
      {result && result.state === "NOT_IMPLEMENTED" ? (
        <FeatureRefusalPanel state="NOT_IMPLEMENTED" />
      ) : null}
      {result && result.state === "UNAVAILABLE" ? (
        <FeatureRefusalPanel state="UNAVAILABLE" onRetry={onRefresh} />
      ) : null}
      {result && result.state === "ERROR" ? (
        <FeatureRefusalPanel state="ERROR" onRetry={onRefresh} />
      ) : null}
    </ScrollView>
  );
}

function DocumentRow({
  document,
  open,
  onToggle,
  onChanged
}: {
  document: PrivateDocument;
  open: boolean;
  onToggle: () => void;
  onChanged: () => Promise<void>;
}) {
  const { t } = useTranslation();
  return (
    <View style={styles.card}>
      <Pressable
        style={styles.cardHead}
        onPress={onToggle}
        accessibilityRole="button"
        accessibilityLabel={document.title || document.originalName}
      >
        <Ionicons
          name={STATE_ICONS[document.extractionState] || "document-outline"}
          size={20}
          color={document.extractionState === "FAILED" ? colors.danger : colors.accent}
        />
        <View style={styles.cardBody}>
          <Text style={styles.cardTitle} numberOfLines={1}>
            {document.title || document.originalName}
          </Text>
          <Text style={styles.cardHint} numberOfLines={1}>
            {document.originalName} · {document.createdAt.slice(0, 10)}
          </Text>
        </View>
        <Text style={styles.stateMark}>
          {t(`premium:privateOffice.documents.states.${document.extractionState}`, {
            defaultValue: document.extractionState
          })}
        </Text>
      </Pressable>
      {open ? <DocumentDetail documentId={document.id} onChanged={onChanged} /> : null}
    </View>
  );
}

/**
 * The claims panel. Loaded when opened, so the list read stays one request.
 * `extractionNote` is the server explaining itself (why nothing was read,
 * which provider is missing) and renders verbatim.
 */
function DocumentDetail({
  documentId,
  onChanged
}: {
  documentId: number;
  onChanged: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [claims, setClaims] = useState<PrivateClaim[] | null>(null);
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const [busyClaimId, setBusyClaimId] = useState(0);

  const load = useCallback(async () => {
    const detail = await getPrivateDocument(documentId);
    if (detail.state === "LOCKED") lockOfficeLocally();
    if (detail.state === "READY") {
      setClaims(detail.claims);
      setNote(detail.document.extractionNote);
      setFailed(false);
    } else {
      setClaims([]);
      setFailed(true);
    }
  }, [documentId]);

  useEffect(() => {
    load();
  }, [load]);

  const review = useCallback(
    async (claim: PrivateClaim, decision: "accept" | "reject") => {
      setBusyClaimId(claim.id);
      try {
        const written = await reviewPrivateClaim(claim.id, decision);
        if (written.state === "LOCKED") {
          lockOfficeLocally();
          return;
        }
        if (written.state !== "OK") {
          Alert.alert(
            t("premium:privateOffice.documents.reviewFailed"),
            written.state === "REJECTED" && written.message
              ? written.message
              : t("premium:privateOffice.feature.error.body")
          );
          return;
        }
        await load();
        await onChanged();
      } finally {
        setBusyClaimId(0);
      }
    },
    [load, onChanged, t]
  );

  if (claims === null) {
    return (
      <View style={styles.detail}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <View style={styles.detail}>
      {note ? <Text style={styles.note}>{note}</Text> : null}
      {failed ? <Text style={styles.note}>{t("premium:privateOffice.feature.error.body")}</Text> : null}
      {!failed && claims.length === 0 ? (
        <Text style={styles.note}>{t("premium:privateOffice.documents.claims.none")}</Text>
      ) : null}
      {claims.length ? (
        <Text style={styles.detailTitle}>{t("premium:privateOffice.documents.claims.title")}</Text>
      ) : null}
      {claims.map((claim) => (
        <View key={claim.id} style={styles.claim}>
          <View style={styles.cardBody}>
            <Text style={styles.claimType}>{claim.factType}</Text>
            <Text style={styles.claimValue}>{claim.proposedValue}</Text>
          </View>
          {claim.status === "PROPOSED" ? (
            <View style={styles.claimActions}>
              <Pressable
                style={styles.claimAccept}
                onPress={() => review(claim, "accept")}
                disabled={busyClaimId === claim.id}
                accessibilityRole="button"
                accessibilityLabel={t("premium:privateOffice.documents.claims.accept")}
              >
                <Text style={styles.claimAcceptText}>
                  {t("premium:privateOffice.documents.claims.accept")}
                </Text>
              </Pressable>
              <Pressable
                style={styles.claimReject}
                onPress={() => review(claim, "reject")}
                disabled={busyClaimId === claim.id}
                accessibilityRole="button"
                accessibilityLabel={t("premium:privateOffice.documents.claims.reject")}
              >
                <Text style={styles.claimRejectText}>
                  {t("premium:privateOffice.documents.claims.reject")}
                </Text>
              </Pressable>
            </View>
          ) : (
            <Text style={styles.claimStatus}>
              {t(`premium:privateOffice.documents.claims.${claim.status}`, {
                defaultValue: claim.status
              })}
            </Text>
          )}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 14 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800", letterSpacing: 1 },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  primary: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    alignSelf: "flex-start",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  primaryText: { color: colors.accentStrong, fontSize: 14, fontWeight: "700" },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14
  },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14 },
  cardBody: { flex: 1, gap: 2 },
  cardTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  cardHint: { color: colors.muted, fontSize: 12 },
  stateMark: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.6,
    maxWidth: 100,
    textAlign: "right"
  },
  detail: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    padding: 14,
    gap: 10
  },
  detailTitle: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.2
  },
  note: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  claim: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 10,
    padding: 12
  },
  claimType: { color: colors.muted, fontSize: 11, fontWeight: "700", letterSpacing: 0.6 },
  claimValue: { color: colors.text, fontSize: 14, fontWeight: "600" },
  claimActions: { flexDirection: "row", gap: 8 },
  claimAccept: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: colors.signalDim
  },
  claimAcceptText: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  claimReject: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: colors.dangerSoft
  },
  claimRejectText: { color: colors.danger, fontSize: 12, fontWeight: "800" },
  claimStatus: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 0.6 }
});

export default PrivateDocumentsScreen;
