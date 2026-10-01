/**
 * The native seller application.
 *
 * Three rules shape this screen.
 *
 * The applicant is never told they are approved by anything that happens here.
 * Every status shown is the status the server last returned; there is no local
 * optimistic advance, because a screen that briefly says "approved" before the
 * server disagrees is worse than one that waits.
 *
 * Answers are saved as the applicant moves between steps, so closing the app
 * mid-application is not a loss. The autosave endpoint cannot change status, so
 * saving is always safe to do.
 *
 * Validation is shown per step and comes from the server, which is also the
 * thing that enforces it at submit. The client never decides an answer is good
 * enough — it only shows what the server already said.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  SellerApplicationFields,
  SellerApplicationStep,
  SellerApplicationView,
  captureSellerApplicationPhoto,
  emptySellerApplication,
  loadCachedSellerApplication,
  loadSellerApplication,
  pickSellerApplicationFile,
  removeSellerApplicationDocument,
  saveSellerApplicationDraft,
  sellerApplicationIsPending,
  sellerApplicationStatusTone,
  submitSellerApplication,
  uploadSellerApplicationDocument,
  withdrawSellerApplication
} from "../api/sellerApplication";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { registerSyncInvalidation } from "../core/eventSync";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  navigation: { navigate: (...args: any[]) => void };
};

const YES_NO: { value: string; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" }
];

/** Labels and helper text for every field the applicant fills in. */
const FIELD_COPY: Record<string, { label: string; hint?: string; placeholder?: string; multiline?: boolean; keyboard?: "default" | "email-address" | "phone-pad" | "url" }> = {
  full_name: { label: "Legal name", hint: "As it appears on your government ID.", placeholder: "Full legal name" },
  display_name: { label: "Store name", hint: "What buyers will see on your listings.", placeholder: "Your store or brand name" },
  country: { label: "Country", placeholder: "Country of residence or registration" },
  state_region: { label: "State or region", placeholder: "State, province, or region" },
  email: { label: "Contact email", hint: "Where we send decisions about this application.", placeholder: "you@example.com", keyboard: "email-address" },
  phone: { label: "Phone", hint: "Used only to reach you about this application.", placeholder: "+1 555 000 0000", keyboard: "phone-pad" },
  business_name: { label: "Registered business name", hint: "Required for brands and agencies.", placeholder: "Legal business name" },
  website: { label: "Website", placeholder: "https://", keyboard: "url" },
  social_links: { label: "Social links", hint: "Anywhere buyers can already see your work.", placeholder: "One link per line", multiline: true },
  years_experience: { label: "Years of experience", placeholder: "e.g. 3" },
  business_description: { label: "What you sell", hint: "A few sentences on what you offer and who it is for.", placeholder: "Describe your products or services", multiline: true },
  sold_online_before: { label: "Have you sold online before?" },
  banned_elsewhere: { label: "Have you ever been removed from another selling platform?", hint: "Answering yes does not disqualify you. Not disclosing it does." },
  guaranteed_profits: { label: "Do you promise guaranteed profits or returns?", hint: "PulseSoc does not allow guaranteed-return claims." },
  comply_rules: { label: "Will you follow PulseSoc marketplace rules?" },
  understand_claims: { label: "Do you understand the rules on claims and results?" },
  marketplace_rules: { label: "I agree to the PulseSoc marketplace rules." },
  anti_scam_agreement: { label: "I agree not to run scams, fake offers, or deceptive listings." },
  no_profit_guarantees: { label: "I will not guarantee profits, income, or results." }
};

const BOOLEAN_FIELDS = new Set([
  "sold_online_before",
  "banned_elsewhere",
  "guaranteed_profits",
  "comply_rules",
  "understand_claims"
]);

const AGREEMENT_FIELDS = new Set(["marketplace_rules", "anti_scam_agreement", "no_profit_guarantees"]);

export function SellerApplicationScreen({ navigation }: Props) {
  const [view, setView] = useState<SellerApplicationView>(emptySellerApplication());
  const [draft, setDraft] = useState<SellerApplicationFields>({});
  const [stepIndex, setStepIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const [started, setStarted] = useState(false);
  // Tracks whether the applicant has changed anything since the last save, so
  // that leaving a step they only read does not fire a pointless write.
  const dirtyRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setMessage("");
    setOffline(false);
    try {
      const next = await loadSellerApplication();
      setView(next);
      setDraft(next.fields || {});
      setStarted(next.application_id > 0 && next.status !== "draft");
    } catch (error) {
      const cached = await loadCachedSellerApplication();
      if (cached) {
        setView(cached);
        setDraft(cached.fields || {});
        setOffline(true);
      } else {
        setMessage(error instanceof Error ? error.message : "The seller application could not load.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const unregister = registerSyncInvalidation("notifications", () => {
      load().catch(() => undefined);
    });
    return unregister;
  }, [load]);

  const steps = view.steps || [];
  const activeStep: SellerApplicationStep | undefined = steps[stepIndex];
  const isPending = sellerApplicationIsPending(view);
  const tone = sellerApplicationStatusTone(view.status);
  const editable = view.editable;

  function setField(key: keyof SellerApplicationFields, value: string | string[]) {
    dirtyRef.current = true;
    setSaveState("idle");
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function toggleIntent(intent: string) {
    const current = Array.isArray(draft.seller_intent) ? draft.seller_intent : [];
    const next = current.includes(intent) ? current.filter((entry) => entry !== intent) : [...current, intent];
    setField("seller_intent", next);
  }

  /**
   * Persist the current answers and adopt the server's re-validated view.
   *
   * Returns the fresh view so a caller that is about to navigate can decide
   * against it if the save failed and the errors it would show are stale.
   */
  const persist = useCallback(async () => {
    if (!dirtyRef.current) return view;
    setSaveState("saving");
    try {
      const next = await saveSellerApplicationDraft(draft);
      dirtyRef.current = false;
      setView(next);
      setSaveState("saved");
      return next;
    } catch (error) {
      setSaveState("failed");
      setMessage(error instanceof Error ? error.message : "Your answers could not be saved just now.");
      return null;
    }
  }, [draft, view]);

  async function goToStep(nextIndex: number) {
    const bounded = Math.max(0, Math.min(steps.length - 1, nextIndex));
    if (editable) await persist();
    setStepIndex(bounded);
  }

  async function beginApplication() {
    setBusy("start");
    // Every other action on this screen clears the banner before it runs. Without
    // it, a failure from the initial load stays on screen through a successful
    // start, so the applicant is told the application could not be reached while
    // looking at step one of the application.
    setMessage("");
    try {
      dirtyRef.current = true;
      const next = await saveSellerApplicationDraft(draft);
      setView(next);
      dirtyRef.current = false;
      setStarted(true);
      setStepIndex(0);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The application could not be started.");
    } finally {
      setBusy("");
    }
  }

  async function attachDocument(documentType: string, source: "camera" | "file") {
    setBusy(`doc-${documentType}`);
    setMessage("");
    try {
      const asset = source === "camera" ? await captureSellerApplicationPhoto() : await pickSellerApplicationFile();
      if (!asset) {
        setBusy("");
        return;
      }
      const result = await uploadSellerApplicationDocument(documentType, asset);
      setView(result.view);
      setMessage(result.message || "Document received.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "That document could not be uploaded.");
    } finally {
      setBusy("");
    }
  }

  async function detachDocument(documentId: number) {
    setBusy(`remove-${documentId}`);
    try {
      const result = await removeSellerApplicationDocument(documentId);
      setView(result.view);
      setMessage(result.message || "Document removed.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "That document could not be removed.");
    } finally {
      setBusy("");
    }
  }

  async function submit() {
    setBusy("submit");
    setMessage("");
    try {
      const saved = await persist();
      if (saved === null) {
        setBusy("");
        return;
      }
      const result = await submitSellerApplication();
      setView(result.view);
      setMessage(result.message || "Application submitted for review.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The application could not be submitted.");
    } finally {
      setBusy("");
    }
  }

  async function withdraw() {
    setBusy("withdraw");
    setMessage("");
    try {
      const result = await withdrawSellerApplication();
      setView(result.view);
      setMessage(result.message || "Application withdrawn.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The application could not be withdrawn.");
    } finally {
      setBusy("");
    }
  }

  const documentsByType = useMemo(() => {
    const map: Record<string, SellerApplicationView["documents"][number]> = {};
    for (const doc of view.documents || []) map[doc.type] = doc;
    return map;
  }, [view.documents]);

  if (loading) {
    return (
      <Screen title="Sell on PulseSoc" subtitle="Loading your application">
        <Panel>
          <ActivityIndicator color={colors.accent} />
        </Panel>
      </Screen>
    );
  }

  // Anyone who has submitted sees the status centre instead of the form. The
  // form is not hidden as a courtesy — while an application is being reviewed,
  // editing it underneath the reviewer is the thing we are preventing.
  if (isPending || view.status === "approved" || view.status === "suspended" || view.status === "withdrawn" || view.status === "expired") {
    return (
      <Screen title="Sell on PulseSoc" subtitle={view.status_title}>
        {offline ? <Panel><Text style={styles.notice}>Showing your last saved copy. Reconnect to refresh.</Text></Panel> : null}
        <StatusCentre
          view={view}
          tone={tone}
          busy={busy}
          message={message}
          onRefresh={() => load()}
          onWithdraw={isPending ? withdraw : undefined}
          onOpenSellerTools={view.status === "approved" ? () => navigation.navigate("SellerStore", { title: "Seller Tools" }) : undefined}
        />
      </Screen>
    );
  }

  if (!started && view.status === "draft" && !view.application_id) {
    return (
      <Screen title="Sell on PulseSoc" subtitle="Apply to open your store">
        <Introduction onBegin={beginApplication} busy={busy === "start"} />
        {message ? <Panel><Text style={styles.notice}>{message}</Text></Panel> : null}
      </Screen>
    );
  }

  return (
    <Screen title="Sell on PulseSoc" subtitle={editable ? "Your application" : view.status_title}>
      {offline ? <Panel><Text style={styles.notice}>Showing your last saved copy. Reconnect to refresh.</Text></Panel> : null}

      {/*
        A rejected applicant is editable, so they land on the form rather than
        the status centre — and would otherwise see a form with no explanation
        of why they are back on it. The decision the reviewer wrote is the first
        thing on the page whenever this is not a fresh draft.
      */}
      {view.status !== "draft" ? (
        <Panel>
          <View style={[styles.statusBadge, tone === "critical" ? styles.toneBad : tone === "warning" ? styles.toneWarn : styles.toneNeutral]}>
            <Text style={styles.statusBadgeText}>{view.status_title}</Text>
          </View>
          <Text style={styles.copy} accessibilityLiveRegion="polite">{view.status_message}</Text>
        </Panel>
      ) : null}

      {view.information_request ? (
        <Panel>
          <Text style={styles.sectionTitle}>A reviewer needs one more thing</Text>
          <Text style={styles.reviewerMessage}>{view.information_request}</Text>
          <Text style={styles.hint}>Update the answers below, then submit again.</Text>
        </Panel>
      ) : null}

      <Panel>
        <View style={styles.progressHeader}>
          <Text style={styles.sectionTitle}>Step {stepIndex + 1} of {steps.length || 1}</Text>
          <Text style={styles.progressValue} accessibilityLabel={`Application ${view.completeness} percent complete`}>
            {view.completeness}% complete
          </Text>
        </View>
        <View style={styles.progressTrack} accessible accessibilityRole="progressbar" accessibilityValue={{ min: 0, max: 100, now: view.completeness }}>
          <View style={[styles.progressFill, { width: `${Math.max(3, view.completeness)}%` }]} />
        </View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.stepStrip}>
          {steps.map((step, index) => (
            <Pressable
              key={step.key}
              accessibilityRole="tab"
              accessibilityState={{ selected: index === stepIndex }}
              accessibilityLabel={`${step.title}${step.complete ? ", complete" : ", incomplete"}`}
              style={[styles.stepChip, index === stepIndex && styles.stepChipActive, step.complete && styles.stepChipDone]}
              onPress={() => goToStep(index)}
            >
              <Text style={[styles.stepChipText, index === stepIndex && styles.stepChipTextActive]}>
                {step.complete ? "✓ " : ""}{step.title}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
        <Text style={styles.saveState}>
          {saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved" : saveState === "failed" ? "Not saved — we will retry when you continue." : "Your answers save as you go."}
        </Text>
      </Panel>

      {activeStep ? (
        <Panel>
          <Text style={styles.sectionTitle}>{activeStep.title}</Text>
          <Text style={styles.copy}>{activeStep.summary}</Text>

          {activeStep.key === "seller_type" ? (
            <SellerTypeStep view={view} draft={draft} onSelectType={(value) => setField("seller_type", value)} onToggleIntent={toggleIntent} disabled={!editable} />
          ) : activeStep.key === "documents" ? (
            <DocumentsStep
              view={view}
              documentsByType={documentsByType}
              busy={busy}
              disabled={!editable}
              onAttach={attachDocument}
              onRemove={detachDocument}
            />
          ) : activeStep.key === "review" ? (
            <ReviewStep view={view} steps={steps} onJump={goToStep} />
          ) : (
            <FieldsStep
              fields={activeStep.fields}
              errors={activeStep.errors}
              draft={draft}
              disabled={!editable}
              onChange={setField}
            />
          )}

          {Object.keys(activeStep.errors || {}).length ? (
            <View style={styles.errorBox} accessibilityLiveRegion="polite">
              {Object.entries(activeStep.errors).map(([key, text]) => (
                <Text key={key} style={styles.errorText}>{text}</Text>
              ))}
            </View>
          ) : null}
        </Panel>
      ) : null}

      {message ? <Panel><Text style={styles.notice} accessibilityLiveRegion="polite">{message}</Text></Panel> : null}

      <Panel>
        <View style={styles.actionRow}>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: stepIndex === 0 }}
            style={[styles.secondaryButton, stepIndex === 0 && styles.buttonDisabled]}
            disabled={stepIndex === 0}
            onPress={() => goToStep(stepIndex - 1)}
          >
            <Text style={styles.secondaryText}>Back</Text>
          </Pressable>
          {stepIndex < steps.length - 1 ? (
            <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => goToStep(stepIndex + 1)}>
              <Text style={styles.primaryText}>Continue</Text>
            </Pressable>
          ) : (
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ disabled: !view.can_submit || busy === "submit" }}
              accessibilityHint={view.can_submit ? "Sends your application to a PulseSoc reviewer" : "Finish the incomplete steps first"}
              style={[styles.primaryButton, (!view.can_submit || busy === "submit") && styles.buttonDisabled]}
              disabled={!view.can_submit || busy === "submit"}
              onPress={submit}
            >
              <Text style={styles.primaryText}>{busy === "submit" ? "Submitting…" : "Submit for review"}</Text>
            </Pressable>
          )}
        </View>
        <Text style={styles.hint}>
          A PulseSoc administrator reviews every application. Nothing is approved automatically, and we will tell you either way.
        </Text>
      </Panel>
    </Screen>
  );
}

function Introduction({ onBegin, busy }: { onBegin: () => void; busy: boolean }) {
  return (
    <>
      <Panel>
        <Text style={styles.sectionTitle}>Sell your work on PulseSoc</Text>
        <Text style={styles.copy}>
          Creators, teachers, brands, and businesses can open a store on PulseSoc. Applying takes a few minutes and you can
          stop and come back — your answers are saved as you go.
        </Text>
      </Panel>
      <Panel>
        <Text style={styles.sectionTitle}>What happens next</Text>
        {[
          { title: "You apply", copy: "Tell us who you are, what you sell, and upload identity documents." },
          { title: "A person reviews it", copy: "A PulseSoc administrator checks your identity and your business. No decision is automatic." },
          { title: "You hear back", copy: "We approve you, ask for more information, or explain why we cannot approve you yet." },
          { title: "Your store opens", copy: "Approved sellers get listing, order, and payout tools." }
        ].map((entry, index) => (
          <View key={entry.title} style={styles.timelineRow}>
            <View style={styles.timelineDot}><Text style={styles.timelineIndex}>{index + 1}</Text></View>
            <View style={styles.timelineCopy}>
              <Text style={styles.timelineTitle}>{entry.title}</Text>
              <Text style={styles.copy}>{entry.copy}</Text>
            </View>
          </View>
        ))}
      </Panel>
      <Panel>
        <Text style={styles.sectionTitle}>What you will need</Text>
        <Text style={styles.copy}>A government ID, a selfie, and a short description of what you sell. Businesses should also have their registration details to hand.</Text>
        <Text style={styles.hint}>Your documents are stored privately and are only ever seen by the administrators reviewing your application.</Text>
        <View style={styles.actionRow}>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: busy }}
            style={[styles.primaryButton, busy && styles.buttonDisabled]}
            disabled={busy}
            onPress={onBegin}
          >
            <Text style={styles.primaryText}>{busy ? "Starting…" : "Start application"}</Text>
          </Pressable>
        </View>
      </Panel>
    </>
  );
}

function SellerTypeStep({
  view,
  draft,
  onSelectType,
  onToggleIntent,
  disabled
}: {
  view: SellerApplicationView;
  draft: SellerApplicationFields;
  onSelectType: (value: string) => void;
  onToggleIntent: (intent: string) => void;
  disabled: boolean;
}) {
  const selectedIntents = Array.isArray(draft.seller_intent) ? draft.seller_intent : [];
  return (
    <View style={styles.stepBody}>
      <Text style={styles.fieldLabel}>What kind of seller are you?</Text>
      <View style={styles.optionGrid}>
        {view.seller_types.map((option) => (
          <Pressable
            key={option.key}
            accessibilityRole="radio"
            accessibilityState={{ selected: draft.seller_type === option.key, disabled }}
            style={[styles.option, draft.seller_type === option.key && styles.optionSelected, disabled && styles.buttonDisabled]}
            disabled={disabled}
            onPress={() => onSelectType(option.key)}
          >
            <Text style={[styles.optionText, draft.seller_type === option.key && styles.optionTextSelected]}>{option.label}</Text>
          </Pressable>
        ))}
      </View>
      <Text style={styles.fieldLabel}>What do you plan to sell?</Text>
      <Text style={styles.hint}>Choose everything that applies.</Text>
      <View style={styles.optionGrid}>
        {view.selling_intents.map((intent) => (
          <Pressable
            key={intent}
            accessibilityRole="checkbox"
            accessibilityState={{ checked: selectedIntents.includes(intent), disabled }}
            style={[styles.option, selectedIntents.includes(intent) && styles.optionSelected, disabled && styles.buttonDisabled]}
            disabled={disabled}
            onPress={() => onToggleIntent(intent)}
          >
            <Text style={[styles.optionText, selectedIntents.includes(intent) && styles.optionTextSelected]}>{intent}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

function FieldsStep({
  fields,
  errors,
  draft,
  disabled,
  onChange
}: {
  fields: string[];
  errors: Record<string, string>;
  draft: SellerApplicationFields;
  disabled: boolean;
  onChange: (key: keyof SellerApplicationFields, value: string) => void;
}) {
  return (
    <View style={styles.stepBody}>
      {fields.map((key) => {
        const copy = FIELD_COPY[key] || { label: key.replace(/_/g, " ") };
        const value = String((draft as Record<string, unknown>)[key] || "");
        const error = errors?.[key];
        if (BOOLEAN_FIELDS.has(key) || AGREEMENT_FIELDS.has(key)) {
          const choices = AGREEMENT_FIELDS.has(key) ? [{ value: "yes", label: "I agree" }] : YES_NO;
          return (
            <View key={key} style={styles.field}>
              <Text style={styles.fieldLabel}>{copy.label}</Text>
              {copy.hint ? <Text style={styles.hint}>{copy.hint}</Text> : null}
              <View style={styles.optionRow}>
                {choices.map((choice) => (
                  <Pressable
                    key={choice.value}
                    accessibilityRole={AGREEMENT_FIELDS.has(key) ? "checkbox" : "radio"}
                    accessibilityState={AGREEMENT_FIELDS.has(key) ? { checked: value === choice.value, disabled } : { selected: value === choice.value, disabled }}
                    style={[styles.option, value === choice.value && styles.optionSelected, disabled && styles.buttonDisabled]}
                    disabled={disabled}
                    onPress={() => onChange(key as keyof SellerApplicationFields, value === choice.value && AGREEMENT_FIELDS.has(key) ? "" : choice.value)}
                  >
                    <Text style={[styles.optionText, value === choice.value && styles.optionTextSelected]}>
                      {value === choice.value ? "✓ " : ""}{choice.label}
                    </Text>
                  </Pressable>
                ))}
              </View>
              {error ? <Text style={styles.errorText}>{error}</Text> : null}
            </View>
          );
        }
        return (
          <View key={key} style={styles.field}>
            <Text style={styles.fieldLabel}>{copy.label}</Text>
            {copy.hint ? <Text style={styles.hint}>{copy.hint}</Text> : null}
            <TextInput
              style={[styles.input, copy.multiline && styles.textArea, error && styles.inputError]}
              value={value}
              editable={!disabled}
              onChangeText={(text) => onChange(key as keyof SellerApplicationFields, text)}
              placeholder={copy.placeholder}
              placeholderTextColor={colors.muted}
              multiline={Boolean(copy.multiline)}
              keyboardType={copy.keyboard === "email-address" ? "email-address" : copy.keyboard === "phone-pad" ? "phone-pad" : copy.keyboard === "url" ? "url" : "default"}
              autoCapitalize={copy.keyboard === "email-address" || copy.keyboard === "url" ? "none" : "sentences"}
              accessibilityLabel={copy.label}
              accessibilityHint={copy.hint}
            />
            {error ? <Text style={styles.errorText}>{error}</Text> : null}
          </View>
        );
      })}
    </View>
  );
}

function DocumentsStep({
  view,
  documentsByType,
  busy,
  disabled,
  onAttach,
  onRemove
}: {
  view: SellerApplicationView;
  documentsByType: Record<string, SellerApplicationView["documents"][number]>;
  busy: string;
  disabled: boolean;
  onAttach: (documentType: string, source: "camera" | "file") => void;
  onRemove: (documentId: number) => void;
}) {
  const slots = [
    ...view.required_documents.map((doc) => ({ ...doc, required: true })),
    ...view.optional_documents.map((doc) => ({ ...doc, required: false }))
  ];
  return (
    <View style={styles.stepBody}>
      <Text style={styles.hint}>
        Documents are stored privately and are only opened by the administrator reviewing your application. We never post them
        and never share them.
      </Text>
      {slots.map((slot) => {
        const existing = documentsByType[slot.key];
        const uploading = busy === `doc-${slot.key}`;
        return (
          <View key={slot.key} style={styles.documentRow}>
            <View style={styles.documentCopy}>
              <Text style={styles.fieldLabel}>{slot.label}{slot.required ? "" : " (optional)"}</Text>
              {existing ? (
                <Text style={styles.hint} accessibilityLabel={`${slot.label} received`}>
                  {existing.filename || "Uploaded"} · {existing.size_kb} KB · {existing.state}
                </Text>
              ) : (
                <Text style={styles.hint}>{slot.required ? "Required" : "Add this if you have it"}</Text>
              )}
            </View>
            <View style={styles.documentActions}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${existing ? "Replace" : "Take a photo for"} ${slot.label}`}
                accessibilityState={{ disabled: disabled || uploading }}
                style={[styles.chipButton, (disabled || uploading) && styles.buttonDisabled]}
                disabled={disabled || uploading}
                onPress={() => onAttach(slot.key, "camera")}
              >
                <Text style={styles.chipButtonText}>{uploading ? "…" : "Camera"}</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${existing ? "Replace" : "Choose a file for"} ${slot.label}`}
                accessibilityState={{ disabled: disabled || uploading }}
                style={[styles.chipButton, (disabled || uploading) && styles.buttonDisabled]}
                disabled={disabled || uploading}
                onPress={() => onAttach(slot.key, "file")}
              >
                <Text style={styles.chipButtonText}>File</Text>
              </Pressable>
              {existing ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Remove ${slot.label}`}
                  accessibilityState={{ disabled: disabled || busy === `remove-${existing.id}` }}
                  style={[styles.chipButton, styles.chipButtonDanger, disabled && styles.buttonDisabled]}
                  disabled={disabled || busy === `remove-${existing.id}`}
                  onPress={() => onRemove(existing.id)}
                >
                  <Text style={styles.chipButtonDangerText}>Remove</Text>
                </Pressable>
              ) : null}
            </View>
          </View>
        );
      })}
    </View>
  );
}

function ReviewStep({
  view,
  steps,
  onJump
}: {
  view: SellerApplicationView;
  steps: SellerApplicationStep[];
  onJump: (index: number) => void;
}) {
  return (
    <View style={styles.stepBody}>
      <Text style={styles.hint}>Check each section before you submit. Tap any section to go back and change it.</Text>
      {steps.slice(0, -1).map((step, index) => (
        <Pressable
          key={step.key}
          accessibilityRole="button"
          accessibilityLabel={`${step.title}, ${step.complete ? "complete" : "needs attention"}. Tap to edit.`}
          style={[styles.reviewRow, !step.complete && styles.reviewRowIncomplete]}
          onPress={() => onJump(index)}
        >
          <View style={styles.documentCopy}>
            <Text style={styles.fieldLabel}>{step.title}</Text>
            <Text style={styles.hint}>
              {step.complete ? "Complete" : Object.values(step.errors || {})[0] || "Needs attention"}
            </Text>
          </View>
          <Text style={[styles.reviewMark, step.complete ? styles.reviewMarkDone : styles.reviewMarkTodo]}>
            {step.complete ? "✓" : "!"}
          </Text>
        </Pressable>
      ))}
      {!view.can_submit ? (
        <Text style={styles.errorText} accessibilityLiveRegion="polite">
          Finish the sections marked above before submitting.
        </Text>
      ) : (
        <Text style={styles.hint}>
          Submitting sends your application to a PulseSoc administrator. You can withdraw it while it is waiting.
        </Text>
      )}
    </View>
  );
}

function StatusCentre({
  view,
  tone,
  busy,
  message,
  onRefresh,
  onWithdraw,
  onOpenSellerTools
}: {
  view: SellerApplicationView;
  tone: "positive" | "warning" | "critical" | "neutral";
  busy: string;
  message: string;
  onRefresh: () => void;
  onWithdraw?: () => void;
  onOpenSellerTools?: () => void;
}) {
  const toneStyle = tone === "positive" ? styles.toneGood : tone === "critical" ? styles.toneBad : tone === "warning" ? styles.toneWarn : styles.toneNeutral;
  return (
    <>
      <Panel>
        <View style={[styles.statusBadge, toneStyle]}>
          <Text style={styles.statusBadgeText}>{view.status_title}</Text>
        </View>
        <Text style={styles.copy}>{view.status_message}</Text>
        {view.submitted_at ? <Text style={styles.hint}>Submitted {view.submitted_at}</Text> : null}
        {view.information_request ? (
          <>
            <Text style={styles.fieldLabel}>What the reviewer asked for</Text>
            <Text style={styles.reviewerMessage}>{view.information_request}</Text>
          </>
        ) : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Where you are</Text>
        {[
          { key: "submitted", label: "Application received", done: Boolean(view.submitted_at) },
          { key: "review", label: "Under administrator review", done: ["under_review", "approved", "rejected", "suspended"].includes(view.status) },
          { key: "decision", label: "Decision", done: ["approved", "rejected", "suspended"].includes(view.status) }
        ].map((entry) => (
          <View key={entry.key} style={styles.timelineRow}>
            <View style={[styles.timelineDot, entry.done && styles.timelineDotDone]}>
              <Text style={styles.timelineIndex}>{entry.done ? "✓" : "·"}</Text>
            </View>
            <View style={styles.timelineCopy}>
              <Text style={styles.timelineTitle}>{entry.label}</Text>
            </View>
          </View>
        ))}
        <Text style={styles.hint}>Every application is read by a person. Nothing here is decided automatically.</Text>
      </Panel>

      {view.documents.length ? (
        <Panel>
          <Text style={styles.sectionTitle}>Documents you sent</Text>
          {view.documents.map((doc) => (
            <Text key={doc.id} style={styles.hint}>{doc.label} · {doc.state}</Text>
          ))}
        </Panel>
      ) : null}

      {message ? <Panel><Text style={styles.notice} accessibilityLiveRegion="polite">{message}</Text></Panel> : null}

      <Panel>
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={onRefresh}>
            <Text style={styles.secondaryText}>Refresh status</Text>
          </Pressable>
          {onOpenSellerTools ? (
            <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={onOpenSellerTools}>
              <Text style={styles.primaryText}>{view.next_action.label}</Text>
            </Pressable>
          ) : null}
          {onWithdraw ? (
            <Pressable
              accessibilityRole="button"
              accessibilityHint="Removes your application from the review queue"
              accessibilityState={{ disabled: busy === "withdraw" }}
              style={[styles.secondaryButton, busy === "withdraw" && styles.buttonDisabled]}
              disabled={busy === "withdraw"}
              onPress={onWithdraw}
            >
              <Text style={styles.secondaryText}>{busy === "withdraw" ? "Withdrawing…" : "Withdraw application"}</Text>
            </Pressable>
          ) : null}
        </View>
      </Panel>
    </>
  );
}

const styles = createThemedStyles(() => ({
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  buttonDisabled: {
    opacity: 0.5
  },
  chipButton: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    minWidth: 72,
    paddingHorizontal: 12
  },
  chipButtonDanger: {
    borderColor: colors.danger
  },
  chipButtonDangerText: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "800"
  },
  chipButtonText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  copy: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  documentActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6
  },
  documentCopy: {
    flex: 1,
    gap: 3,
    minWidth: 140
  },
  documentRow: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    minHeight: 66,
    padding: 10
  },
  errorBox: {
    backgroundColor: colors.dangerSoft,
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: 1,
    gap: 4,
    padding: 10
  },
  errorText: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "700"
  },
  field: {
    gap: 5
  },
  fieldLabel: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  hint: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 15,
    minHeight: 48,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  inputError: {
    borderColor: colors.danger
  },
  notice: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 19
  },
  option: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 14
  },
  optionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  optionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  optionSelected: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  optionText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  },
  optionTextSelected: {
    color: colors.accent
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flexGrow: 1,
    justifyContent: "center",
    minHeight: 48,
    paddingHorizontal: 16
  },
  primaryText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  progressFill: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    height: "100%"
  },
  progressHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  progressTrack: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 999,
    height: 8,
    overflow: "hidden",
    width: "100%"
  },
  progressValue: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "900"
  },
  reviewMark: {
    fontSize: 18,
    fontWeight: "900"
  },
  reviewMarkDone: {
    color: colors.accent
  },
  reviewMarkTodo: {
    color: colors.warning
  },
  reviewRow: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 58,
    padding: 10
  },
  reviewRowIncomplete: {
    borderColor: colors.warning
  },
  reviewerMessage: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 20
  },
  saveState: {
    color: colors.muted,
    fontSize: 12
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  statusBadge: {
    alignSelf: "flex-start",
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 6
  },
  statusBadgeText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  stepBody: {
    gap: 12
  },
  stepChip: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 40,
    paddingHorizontal: 13
  },
  stepChipActive: {
    borderColor: colors.accent
  },
  stepChipDone: {
    backgroundColor: colors.signalDim
  },
  stepChipText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  stepChipTextActive: {
    color: colors.text
  },
  stepStrip: {
    flexDirection: "row",
    gap: 8,
    paddingVertical: 2
  },
  secondaryButton: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 48,
    paddingHorizontal: 16
  },
  secondaryText: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800"
  },
  textArea: {
    minHeight: 96,
    textAlignVertical: "top"
  },
  timelineCopy: {
    flex: 1,
    gap: 3
  },
  timelineDot: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    height: 28,
    justifyContent: "center",
    width: 28
  },
  timelineDotDone: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  timelineIndex: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  timelineRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  timelineTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  toneBad: {
    backgroundColor: colors.dangerSoft,
    borderColor: colors.danger
  },
  toneGood: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  toneNeutral: {
    backgroundColor: colors.signalSoft,
    borderColor: colors.border
  },
  toneWarn: {
    backgroundColor: colors.warningSoft,
    borderColor: colors.warning
  }
}));
