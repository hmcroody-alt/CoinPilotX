import { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { createMarketplaceListing } from "../api/marketplace";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  navigation: {
    navigate: (...args: any[]) => void;
  };
};

const productTypes = ["digital", "course", "service", "physical"] as const;

export function SellerListingComposerScreen({ navigation }: Props) {
  const [title, setTitle] = useState("");
  const [shortDescription, setShortDescription] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("Education");
  const [priceLabel, setPriceLabel] = useState("Request access");
  const [productType, setProductType] = useState<(typeof productTypes)[number]>("digital");
  const [mediaIdsText, setMediaIdsText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [createdListingId, setCreatedListingId] = useState(0);

  const mediaIds = useMemo(
    () =>
      mediaIdsText
        .split(/[\s,]+/)
        .map((value) => Number(value.trim()))
        .filter((value, index, list) => Number.isFinite(value) && value > 0 && list.indexOf(value) === index),
    [mediaIdsText]
  );
  const canPublish = title.trim().length > 0 && description.trim().length > 0 && mediaIds.length > 0 && !busy;

  async function publishListing() {
    setBusy(true);
    setMessage("");
    setCreatedListingId(0);
    try {
      const result = await createMarketplaceListing({
        title: title.trim(),
        short_description: shortDescription.trim(),
        description: description.trim(),
        category: category.trim() || "Education",
        price_label: priceLabel.trim() || "Request access",
        product_type: productType,
        media_ids: mediaIds
      });
      const listingId = Number(result.listing_id || 0);
      setCreatedListingId(listingId);
      setMessage(result.message || "Listing saved for safety review.");
      if (listingId) {
        navigation.navigate("SellerStore", { title: "Seller / Store" });
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Listing could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen title="Create Listing" subtitle="Native seller listing draft gateway using PulseSoc marketplace approval, media moderation, and safety review.">
      {message ? <Text style={message.toLowerCase().includes("saved") ? styles.notice : styles.error}>{message}</Text> : null}

      <Panel>
        <View style={styles.hero}>
          <Text style={styles.kicker}>Marketplace Forge</Text>
          <Text style={styles.heroTitle}>Shape a product for review</Text>
          <Text style={styles.copy}>Listing approval, risk scoring, media moderation, checkout, refunds, disputes, and payouts remain server-authoritative.</Text>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Listing details</Text>
        <TextInput style={styles.input} value={title} onChangeText={setTitle} placeholder="Title" placeholderTextColor={colors.muted} />
        <TextInput style={styles.input} value={shortDescription} onChangeText={setShortDescription} placeholder="Short description" placeholderTextColor={colors.muted} />
        <TextInput
          style={[styles.input, styles.textArea]}
          value={description}
          onChangeText={setDescription}
          placeholder="Full description"
          placeholderTextColor={colors.muted}
          multiline
        />
        <View style={styles.twoCol}>
          <TextInput style={[styles.input, styles.flex]} value={category} onChangeText={setCategory} placeholder="Category" placeholderTextColor={colors.muted} />
          <TextInput style={[styles.input, styles.flex]} value={priceLabel} onChangeText={setPriceLabel} placeholder="Price label" placeholderTextColor={colors.muted} />
        </View>
        <View style={styles.typeRow}>
          {productTypes.map((type) => (
            <Pressable key={type} style={[styles.typeButton, productType === type && styles.typeButtonActive]} onPress={() => setProductType(type)}>
              <Text style={[styles.typeText, productType === type && styles.typeTextActive]}>{type}</Text>
            </Pressable>
          ))}
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Product media</Text>
        <Text style={styles.copy}>Use Camera Studio to create marketplace draft media, then attach the returned product media IDs here. The backend rejects listings without an approved merchant and cover photo.</Text>
        <TextInput
          style={styles.input}
          value={mediaIdsText}
          onChangeText={setMediaIdsText}
          placeholder="Product media IDs, comma separated"
          placeholderTextColor={colors.muted}
          keyboardType="numbers-and-punctuation"
        />
        <Text style={styles.meta}>{mediaIds.length ? `${mediaIds.length} media ID${mediaIds.length === 1 ? "" : "s"} ready for submit` : "A cover image media ID is required."}</Text>
        <View style={styles.actionRow}>
          <Pressable style={styles.primaryButton} onPress={() => navigation.navigate("CameraStudio", { target: "marketplace", title: "Marketplace Media" })}>
            <Text style={styles.primaryText}>Capture Media</Text>
          </Pressable>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Review handoff</Text>
        <Text style={styles.copy}>Publish sends the draft to existing PulseSoc marketplace review. Advanced editing, bank onboarding, tax, fulfillment, disputes, and payment provider actions stay on safe web/provider flows.</Text>
        <View style={styles.actionRow}>
          <Pressable style={[styles.primaryButton, !canPublish && styles.disabled]} disabled={!canPublish} onPress={publishListing}>
            <Text style={styles.primaryText}>{busy ? "Submitting..." : "Submit for Review"}</Text>
          </Pressable>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("SellerStore", { title: "Seller / Store" })}>
            <Text style={styles.secondaryText}>Back to Store</Text>
          </Pressable>
        </View>
        {createdListingId ? <Text style={styles.meta}>Created listing #{createdListingId}. Marketplace detail opened when available.</Text> : null}
      </Panel>
    </Screen>
  );
}

const styles = createThemedStyles(() => ({
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  copy: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21
  },
  disabled: {
    opacity: 0.55
  },
  error: {
    backgroundColor: "rgba(255, 107, 107, 0.12)",
    borderColor: "rgba(255, 107, 107, 0.28)",
    borderRadius: 8,
    borderWidth: 1,
    color: colors.danger,
    fontWeight: "800",
    padding: 12
  },
  flex: {
    flex: 1
  },
  hero: {
    backgroundColor: "rgba(37, 208, 167, 0.08)",
    borderColor: "rgba(37, 208, 167, 0.28)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
    padding: 14
  },
  heroTitle: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900",
    lineHeight: 29
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 46,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
    textTransform: "uppercase"
  },
  meta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18
  },
  notice: {
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: "rgba(37, 208, 167, 0.28)",
    borderRadius: 8,
    borderWidth: 1,
    color: colors.accent,
    fontWeight: "800",
    padding: 12
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flexGrow: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900",
    textAlign: "center"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexGrow: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900",
    textAlign: "center"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  textArea: {
    minHeight: 118,
    textAlignVertical: "top"
  },
  twoCol: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  typeButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 11,
    paddingVertical: 9
  },
  typeButtonActive: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent
  },
  typeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  typeText: {
    color: colors.muted,
    fontWeight: "900",
    textTransform: "capitalize"
  },
  typeTextActive: {
    color: colors.accent
  }
}));
