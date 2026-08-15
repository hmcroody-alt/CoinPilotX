import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Switch,
  Text,
  TextInput,
  View
} from "react-native";
import {
  checkPageHandle,
  createPage,
  HandleCheck,
  PAGE_TYPES,
  PageType,
  pageTypeLabel
} from "../api/pages";
import { PulseApiError } from "../api/pulseApi";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<any, any>;

/**
 * Creation is a three-step wizard: IDENTITY → DETAILS → REVIEW.
 *
 * The `flavor` route param ("artist" | "business") presets the wizard for the
 * Presence entry points: a short category list instead of the full 16-type
 * grid, and flavor-appropriate detail fields. Every flavor lands on the same
 * canonical `createPage` call — there is exactly one creation backend.
 *
 * The owner confirmation on the review step is not decoration: the server
 * rejects creation without `confirm_owner`, and ownership afterwards only
 * moves through the audited transfer flow. One user can own many pages; this
 * never creates a second login.
 */

type FlavorCategory = { label: string; pageType: PageType };

const ARTIST_CATEGORIES: FlavorCategory[] = [
  { label: "Music Artist", pageType: "ARTIST" },
  { label: "Band / Group", pageType: "ARTIST" },
  { label: "DJ", pageType: "ARTIST" },
  { label: "Producer", pageType: "ARTIST" },
  { label: "Songwriter", pageType: "ARTIST" },
  { label: "Other Artist", pageType: "CREATOR" }
];

const BUSINESS_CATEGORIES: FlavorCategory[] = [
  { label: "Company", pageType: "BUSINESS" },
  { label: "Brand", pageType: "BRAND" },
  { label: "Local Business", pageType: "LOCAL_BUSINESS" },
  { label: "Restaurant", pageType: "RESTAURANT" },
  { label: "Store", pageType: "STORE" },
  { label: "Professional Service", pageType: "PROFESSIONAL_SERVICE" },
  { label: "Organization", pageType: "ORGANIZATION" },
  { label: "Nonprofit", pageType: "NONPROFIT" },
  { label: "Other Business", pageType: "OTHER" }
];

const STEPS = ["Identity", "Details", "Review"] as const;

export function PageCreateScreen({ navigation, route }: Props) {
  const flavor: "artist" | "business" | undefined = route.params?.flavor;
  const flavorCategories =
    flavor === "artist" ? ARTIST_CATEGORIES : flavor === "business" ? BUSINESS_CATEGORIES : null;

  const [step, setStep] = useState(0);
  const [pageType, setPageType] = useState<PageType | "">("");
  const [name, setName] = useState("");
  const [handle, setHandle] = useState("");
  const [handleState, setHandleState] = useState<HandleCheck | null>(null);
  const [checkingHandle, setCheckingHandle] = useState(false);
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [genre, setGenre] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [website, setWebsite] = useState("");
  const [location, setLocation] = useState("");
  const [confirmOwner, setConfirmOwner] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const handleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (handleTimer.current) clearTimeout(handleTimer.current);
    const candidate = handle.trim();
    if (!candidate) {
      setHandleState(null);
      return;
    }
    setCheckingHandle(true);
    handleTimer.current = setTimeout(async () => {
      try {
        const result = await checkPageHandle(candidate);
        setHandleState(result);
      } catch {
        // Fail closed: an unknown state is "not available yet", never "available".
        setHandleState({ candidate, handle: candidate, available: false, reason: "Couldn't check that handle right now." });
      } finally {
        setCheckingHandle(false);
      }
    }, 450);
    return () => {
      if (handleTimer.current) clearTimeout(handleTimer.current);
    };
  }, [handle]);

  const identityComplete =
    Boolean(pageType) && name.trim().length >= 2 && Boolean(handleState?.available);
  const canSubmit = identityComplete && confirmOwner && !submitting;

  function selectFlavorCategory(item: FlavorCategory) {
    setPageType(item.pageType);
    setCategory(item.label);
  }

  async function submit() {
    if (!canSubmit || !pageType) return;
    setSubmitting(true);
    setError("");
    try {
      const page = await createPage({
        page_type: pageType,
        name: name.trim(),
        handle: handle.trim(),
        category: category.trim(),
        description: description.trim(),
        genre: genre.trim(),
        email: email.trim(),
        phone: phone.trim(),
        website: website.trim(),
        location: location.trim(),
        confirm_owner: true
      });
      // Flavored flows open management (per Presence); the generic flow opens
      // the new public page.
      if (flavor) {
        navigation.replace("PagesHub", { focusPageId: page.id });
      } else {
        navigation.replace("Page", { handle: page.handle, title: page.name });
      }
    } catch (submitError) {
      setError(
        submitError instanceof PulseApiError
          ? submitError.message
          : "Your presence could not be created. Please try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  const heading =
    flavor === "artist" ? "Artist Presence" : flavor === "business" ? "Business Presence" : "New Page";

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Text style={styles.heading}>{heading}</Text>
      <View style={styles.stepRow}>
        {STEPS.map((label, index) => (
          <View key={label} style={styles.stepItem}>
            <View style={[styles.stepDot, index <= step && styles.stepDotActive]}>
              <Text style={[styles.stepDotText, index <= step && styles.stepDotTextActive]}>{index + 1}</Text>
            </View>
            <Text style={[styles.stepLabel, index === step && styles.stepLabelActive]}>{label}</Text>
          </View>
        ))}
      </View>

      {step === 0 ? (
        <View>
          <Text style={styles.sectionTitle}>
            {flavor === "artist" ? "What kind of artist?" : flavor === "business" ? "What kind of business?" : "What kind of page is this?"}
          </Text>
          <View style={styles.typeGrid}>
            {flavorCategories
              ? flavorCategories.map((item) => (
                  <Pressable
                    key={item.label}
                    accessibilityRole="button"
                    accessibilityState={{ selected: category === item.label }}
                    style={[styles.typeChip, category === item.label && styles.typeChipActive]}
                    onPress={() => selectFlavorCategory(item)}
                  >
                    <Text style={[styles.typeChipText, category === item.label && styles.typeChipTextActive]}>
                      {item.label}
                    </Text>
                  </Pressable>
                ))
              : PAGE_TYPES.map((type) => (
                  <Pressable
                    key={type}
                    accessibilityRole="button"
                    accessibilityState={{ selected: pageType === type }}
                    style={[styles.typeChip, pageType === type && styles.typeChipActive]}
                    onPress={() => setPageType(type)}
                  >
                    <Text style={[styles.typeChipText, pageType === type && styles.typeChipTextActive]}>
                      {pageTypeLabel(type)}
                    </Text>
                  </Pressable>
                ))}
          </View>

          <Text style={styles.sectionTitle}>{flavor === "artist" ? "Artist / stage name" : "Name"}</Text>
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder={flavor === "artist" ? "e.g. Night Signal" : "e.g. CoinPlotXAI"}
            placeholderTextColor={colors.muted}
            maxLength={120}
          />

          <Text style={styles.sectionTitle}>Handle</Text>
          <View style={styles.handleRow}>
            <Text style={styles.handleAt}>@</Text>
            <TextInput
              style={[styles.input, styles.handleInput]}
              value={handle}
              onChangeText={(text) => setHandle(text.replace(/^@+/, ""))}
              placeholder={flavor === "business" ? "yourbusiness" : "yourname"}
              placeholderTextColor={colors.muted}
              autoCapitalize="none"
              autoCorrect={false}
              maxLength={40}
            />
            {checkingHandle ? <ActivityIndicator color={colors.accent} /> : null}
          </View>
          {handleState ? (
            <Text style={handleState.available ? styles.handleOk : styles.handleBad}>{handleState.reason}</Text>
          ) : null}
          <Text style={styles.note}>
            Profile and cover images can be added from management right after creation.
          </Text>
        </View>
      ) : null}

      {step === 1 ? (
        <View>
          <Text style={styles.sectionTitle}>{flavor === "business" ? "Description" : "Bio"} (optional)</Text>
          <TextInput
            style={[styles.input, styles.multiline]}
            value={description}
            onChangeText={setDescription}
            placeholder={flavor === "artist" ? "Tell fans who you are" : "Tell people what this is about"}
            placeholderTextColor={colors.muted}
            multiline
            textAlignVertical="top"
            maxLength={1000}
          />

          {flavor === "artist" ? (
            <View>
              <Text style={styles.sectionTitle}>Genres (optional)</Text>
              <TextInput
                style={styles.input}
                value={genre}
                onChangeText={setGenre}
                placeholder="e.g. Afrobeats, House"
                placeholderTextColor={colors.muted}
                maxLength={120}
              />
            </View>
          ) : null}

          {!flavorCategories ? (
            <View>
              <Text style={styles.sectionTitle}>Category (optional)</Text>
              <TextInput
                style={styles.input}
                value={category}
                onChangeText={setCategory}
                placeholder="e.g. Electronic music, Coffee shop"
                placeholderTextColor={colors.muted}
                maxLength={80}
              />
            </View>
          ) : null}

          <Text style={styles.sectionTitle}>Contact (optional)</Text>
          <TextInput
            style={styles.input}
            value={email}
            onChangeText={setEmail}
            placeholder="Email"
            placeholderTextColor={colors.muted}
            autoCapitalize="none"
            keyboardType="email-address"
          />
          {flavor === "business" ? (
            <TextInput
              style={styles.input}
              value={phone}
              onChangeText={setPhone}
              placeholder="Phone"
              placeholderTextColor={colors.muted}
              keyboardType="phone-pad"
            />
          ) : null}
          <TextInput
            style={styles.input}
            value={website}
            onChangeText={setWebsite}
            placeholder="Website"
            placeholderTextColor={colors.muted}
            autoCapitalize="none"
          />
          <TextInput
            style={styles.input}
            value={location}
            onChangeText={setLocation}
            placeholder={flavor === "business" ? "Location / address" : "Location"}
            placeholderTextColor={colors.muted}
          />
        </View>
      ) : null}

      {step === 2 ? (
        <View>
          <View style={styles.reviewCard}>
            <Text style={styles.reviewName}>{name.trim() || "—"}</Text>
            <Text style={styles.reviewMeta}>
              @{handle.trim() || "—"} · {category.trim() || (pageType ? pageTypeLabel(pageType) : "—")}
            </Text>
            {description.trim() ? <Text style={styles.reviewBody}>{description.trim()}</Text> : null}
            {genre.trim() ? <Text style={styles.reviewMeta}>Genres: {genre.trim()}</Text> : null}
            {location.trim() ? <Text style={styles.reviewMeta}>{location.trim()}</Text> : null}
            {website.trim() ? <Text style={styles.reviewMeta}>{website.trim()}</Text> : null}
          </View>

          <View style={styles.confirmRow}>
            <Switch
              value={confirmOwner}
              onValueChange={setConfirmOwner}
              trackColor={{ true: colors.accent, false: colors.border }}
              thumbColor={colors.text}
            />
            <Text style={styles.confirmText}>
              I confirm I will be the owner and manager of this presence. My personal account stays
              separate; this is an identity I manage.
            </Text>
          </View>

          {error ? <Text style={styles.error}>{error}</Text> : null}
        </View>
      ) : null}

      <View style={styles.navRow}>
        {step > 0 ? (
          <Pressable accessibilityRole="button" style={styles.backButton} onPress={() => setStep(step - 1)}>
            <Text style={styles.backButtonText}>Back</Text>
          </Pressable>
        ) : null}
        {step < 2 ? (
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: step === 0 && !identityComplete }}
            style={[styles.submit, styles.navNext, step === 0 && !identityComplete && styles.disabled]}
            disabled={step === 0 && !identityComplete}
            onPress={() => setStep(step + 1)}
          >
            <Text style={styles.submitText}>Next</Text>
          </Pressable>
        ) : (
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: !canSubmit }}
            style={[styles.submit, styles.navNext, !canSubmit && styles.disabled]}
            disabled={!canSubmit}
            onPress={submit}
          >
            {submitting ? (
              <ActivityIndicator color={colors.background} />
            ) : (
              <Text style={styles.submitText}>
                {flavor === "artist" ? "Create Artist Presence" : flavor === "business" ? "Create Business Presence" : "Create Page"}
              </Text>
            )}
          </Pressable>
        )}
      </View>
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  backButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 20
  },
  backButtonText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  confirmRow: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginTop: 18,
    padding: 14
  },
  confirmText: {
    color: colors.text,
    flex: 1,
    fontSize: 13,
    lineHeight: 19
  },
  content: {
    gap: 8,
    padding: 16,
    paddingBottom: 48
  },
  disabled: {
    opacity: 0.5
  },
  error: {
    color: colors.danger,
    fontWeight: "800",
    marginTop: 10
  },
  handleAt: {
    color: colors.accent,
    fontSize: 18,
    fontWeight: "900"
  },
  handleBad: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "700"
  },
  handleInput: {
    flex: 1
  },
  handleOk: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700"
  },
  handleRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  heading: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 15,
    marginBottom: 6,
    padding: 12
  },
  multiline: {
    minHeight: 90
  },
  navNext: {
    flex: 1
  },
  navRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 18
  },
  note: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 8
  },
  reviewBody: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 8
  },
  reviewCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    marginTop: 12,
    padding: 16
  },
  reviewMeta: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 2
  },
  reviewName: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900",
    marginTop: 12
  },
  stepDot: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    height: 24,
    justifyContent: "center",
    width: 24
  },
  stepDotActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  stepDotText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "900"
  },
  stepDotTextActive: {
    color: colors.background
  },
  stepItem: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6
  },
  stepLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  },
  stepLabelActive: {
    color: colors.text
  },
  stepRow: {
    flexDirection: "row",
    gap: 14,
    marginTop: 10
  },
  submit: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 10,
    justifyContent: "center",
    minHeight: 46
  },
  submitText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  typeChip: {
    borderColor: colors.border,
    borderRadius: 20,
    borderWidth: 1,
    paddingHorizontal: 13,
    paddingVertical: 8
  },
  typeChipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  typeChipText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  typeChipTextActive: {
    color: colors.background
  },
  typeGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  }
}));
