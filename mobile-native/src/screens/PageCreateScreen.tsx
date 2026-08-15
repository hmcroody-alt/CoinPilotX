import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
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
 * Page creation flow: type → name → handle (live availability) → category →
 * description → contact → explicit owner confirmation → create.
 *
 * The owner confirmation is not decoration: the server rejects creation
 * without `confirm_owner`, and ownership afterwards only moves through the
 * audited transfer flow. One user can own many pages — this never creates a
 * second login.
 */
export function PageCreateScreen({ navigation }: Props) {
  const [pageType, setPageType] = useState<PageType | "">("");
  const [name, setName] = useState("");
  const [handle, setHandle] = useState("");
  const [handleState, setHandleState] = useState<HandleCheck | null>(null);
  const [checkingHandle, setCheckingHandle] = useState(false);
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [email, setEmail] = useState("");
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

  const canSubmit =
    Boolean(pageType) &&
    name.trim().length >= 2 &&
    Boolean(handleState?.available) &&
    confirmOwner &&
    !submitting;

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
        email: email.trim(),
        website: website.trim(),
        location: location.trim(),
        confirm_owner: true
      });
      navigation.replace("Page", { handle: page.handle, title: page.name });
    } catch (submitError) {
      setError(
        submitError instanceof PulseApiError
          ? submitError.message
          : "Page could not be created. Please try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Text style={styles.sectionTitle}>What kind of page is this?</Text>
      <View style={styles.typeGrid}>
        {PAGE_TYPES.map((type) => (
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

      <Text style={styles.sectionTitle}>Page name</Text>
      <TextInput
        style={styles.input}
        value={name}
        onChangeText={setName}
        placeholder="e.g. Night Signal"
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
          placeholder="yourpage"
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

      <Text style={styles.sectionTitle}>Category (optional)</Text>
      <TextInput
        style={styles.input}
        value={category}
        onChangeText={setCategory}
        placeholder="e.g. Electronic music, Coffee shop"
        placeholderTextColor={colors.muted}
        maxLength={80}
      />

      <Text style={styles.sectionTitle}>Description (optional)</Text>
      <TextInput
        style={[styles.input, styles.multiline]}
        value={description}
        onChangeText={setDescription}
        placeholder="Tell people what this page is about"
        placeholderTextColor={colors.muted}
        multiline
        textAlignVertical="top"
        maxLength={1000}
      />

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
        placeholder="Location"
        placeholderTextColor={colors.muted}
      />

      <View style={styles.confirmRow}>
        <Switch
          value={confirmOwner}
          onValueChange={setConfirmOwner}
          trackColor={{ true: colors.accent, false: colors.border }}
          thumbColor={colors.text}
        />
        <Text style={styles.confirmText}>
          I confirm I will be the owner of this page. My personal account stays separate; the page is
          an identity I manage.
        </Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: !canSubmit }}
        style={[styles.submit, !canSubmit && styles.disabled]}
        disabled={!canSubmit}
        onPress={submit}
      >
        {submitting ? (
          <ActivityIndicator color={colors.background} />
        ) : (
          <Text style={styles.submitText}>Create Page</Text>
        )}
      </Pressable>
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
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
  submit: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 10,
    justifyContent: "center",
    marginTop: 16,
    minHeight: 46
  },
  submitText: {
    color: colors.background,
    fontSize: 16,
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
