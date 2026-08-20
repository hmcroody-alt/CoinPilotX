import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Platform, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Device from "expo-device";
import {
  SettingsEmptyState,
  SettingsHeader,
  SettingsSearchField,
  SettingsSection,
  SettingsShell,
  animateNextLayout
} from "../../settings/components/SettingsShell";
import {
  SettingsBadge,
  SettingsButton,
  SettingsRow,
  SettingsTextField,
  SettingsValue
} from "../../settings/components/SettingsControls";
import {
  createSupportTicket,
  listSupportTickets,
  loadCachedSupportState,
  type SupportTicket
} from "../../api/support";
import { APP_VERSION, PULSE_API_BASE_URL } from "../../api/config";
import { useTranslation } from "../../i18n";
import { useAuth } from "../../session/auth";
import { useTheme } from "../../theme/ThemeContext";

/* -------------------------------------------------------------------------- */
/*                                     FAQ                                     */
/* -------------------------------------------------------------------------- */

type FaqEntry = {
  id: string;
  question: string;
  /** Rendered as separate paragraphs so steps stay readable. */
  answer: string[];
  /** Extra search terms users type instead of the words in the question. */
  keywords: string;
};

/**
 * Real answers, not a link farm.
 *
 * Every entry here is a question support actually receives, and each answer
 * either resolves the problem outright or names the exact screen that does.
 * Answers that point at a Settings destination spell out the full path, because
 * "check your settings" is the single least useful sentence in a help centre.
 */
const FAQ: FaqEntry[] = [
  {
    id: "recovery",
    question: "I can't sign in. How do I get back into my account?",
    answer: [
      "On the sign-in screen choose “Can't sign in?” and enter the email address or phone number on the account. We send a recovery link that is valid for 30 minutes.",
      "If you no longer have access to that email address, use the same flow and pick “I can't access this email”. You'll be asked to confirm details only the account holder would know — roughly when you created the account, recent devices, and people you message often.",
      "If you had two-factor authentication on and lost the device, use one of the backup codes you saved when you turned it on. Each code works once.",
      "Recovery decisions are made by a person, not automatically, and usually take under 48 hours. We never ask for your password over email or chat — anyone who does is not us."
    ],
    keywords: "locked out login password reset forgot 2fa two factor backup codes"
  },
  {
    id: "post-removed",
    question: "Why was my post removed?",
    answer: [
      "Posts are removed when they break the Community Guidelines. You'll find a notice in your Activity inbox naming the specific rule that was applied — the most common are hate speech, harassment, graphic violence, and unlicensed music in reels.",
      "Removal for a first, low-severity violation is usually just that: the content goes, and nothing else happens. Repeat violations reduce how widely your posts are distributed before they lead to feature restrictions.",
      "If you think we got it wrong, open the notice and choose “Appeal”. Appeals are reviewed by someone who was not part of the original decision, and a successful appeal restores the post and removes the strike."
    ],
    keywords: "deleted taken down strike violation guidelines appeal moderation"
  },
  {
    id: "report",
    question: "How do I report someone or something?",
    answer: [
      "Open the ••• menu on the post, reel, story, comment, message, or profile and choose “Report”. Pick the closest category — that routes it to the team trained on it, which is the difference between hours and days.",
      "Reports are confidential. The person you report is never told who reported them.",
      "For anything involving immediate physical danger, contact your local emergency services first. We can remove content quickly, but we cannot dispatch help.",
      "You can also block or mute the account from the same menu; doing so does not withdraw your report."
    ],
    keywords: "abuse harassment flag report spam scam block"
  },
  {
    id: "hacked",
    question: "I think my account was hacked. What should I do?",
    answer: [
      "Do this in order. First, go to Settings › Sessions & devices and sign out every session you don't recognise — that immediately cuts off whoever is in there.",
      "Second, change your password from Settings › Security. Use a password you have never used anywhere else.",
      "Third, turn on two-factor authentication in Settings › Security and save the backup codes somewhere that isn't your phone.",
      "Fourth, check Settings › Account for an email address or phone number you didn't add, and remove it — attackers add their own so they can take the account back.",
      "If you've already lost access, use “Can't sign in?” on the sign-in screen and choose the compromised-account option. Report it from Settings › Help › Report a problem so we can freeze the account while we verify you."
    ],
    keywords: "compromised stolen breach suspicious login unauthorised takeover"
  },
  {
    id: "data-export",
    question: "How do I download a copy of my data?",
    answer: [
      "Go to Settings › Data & personalization › Download your data. Choose whether you want everything or a date range, and pick a format.",
      "Exports are built in the background because they include your media. You'll get a notification when it's ready, and the download link stays valid for seven days.",
      "The export contains your posts, reels, stories, comments, messages, profile information, followers and following lists, and your settings. It does not contain other people's private messages to groups you left, or content that has already been permanently deleted."
    ],
    keywords: "export archive gdpr download my information copy data portability"
  },
  {
    id: "verification",
    question: "How do I get verified?",
    answer: [
      "Apply from Settings › Account › Verification. You'll need a photo of a government ID, or official documentation if you're applying as a business or organisation.",
      "Verification confirms you are who you say you are on an account of public interest. It is not a quality rating, and it is not for sale — anyone offering to sell you a badge is running a scam.",
      "Most decisions land within a week. If you're turned down you can reapply after 30 days; reapplying sooner doesn't speed anything up.",
      "A verified badge can be removed if you change the account's name or purpose substantially, or if the account is later found to have been misrepresented."
    ],
    keywords: "blue check badge verified identity id business government"
  },
  {
    id: "block-vs-mute",
    question: "What's the difference between blocking and muting?",
    answer: [
      "Muting is quiet and one-way: their posts, reels, and stories stop appearing in your feed. You still follow each other, they can still message and call you, and they are never told.",
      "Blocking cuts contact in both directions: they can't see your profile or content, can't message or call you, and you won't see them either. They aren't notified, but they can usually tell.",
      "Manage both lists in Settings › Blocked accounts and Settings › Muted accounts, under Privacy & Safety."
    ],
    keywords: "block mute unfollow restrict hide difference"
  },
  {
    id: "notifications",
    question: "How do I quieten notifications without missing messages?",
    answer: [
      "Go to Settings › Notifications. Each category — likes, comments, mentions, follows, messages, calls, live, groups — has its own push, email, and in-app switch, so you can leave messages and calls on while turning everything else off.",
      "Quiet hours in the same screen silences push between the times you set, while still delivering everything to your in-app inbox.",
      "If you get nothing at all after enabling push, check that PulseSoc has notification permission in your device's own settings — the app can't grant that to itself."
    ],
    keywords: "push alerts silence mute quiet hours badge sound email"
  },
  {
    id: "reach",
    question: "Why is my post reaching fewer people than usual?",
    answer: [
      "Reach varies with when you post, how quickly people engage in the first hour, and how much other content your followers are seeing that day. A quiet post is not by itself evidence of a penalty.",
      "Distribution is genuinely reduced in a few cases: content that was reported and found borderline, reposted content that already circulated widely, unlicensed music, and accounts with a recent guidelines violation.",
      "Settings › Account › Account status shows whether any restriction is currently applied to your account, and Creator Studio shows per-post reach so you can compare against your own baseline."
    ],
    keywords: "shadowban reach views algorithm distribution suppressed engagement"
  },
  {
    id: "impersonation",
    question: "Someone is impersonating me or using my photos.",
    answer: [
      "Report the account from its profile via ••• › Report › Impersonation. Choose whether they're pretending to be you or using your content without permission, because the two go to different teams.",
      "You'll be asked for proof of identity if you're reporting impersonation of yourself, or proof you own the material for a copyright claim.",
      "Impersonation reports are prioritised. If the account is also messaging your contacts asking for money, say so in the report — that escalates it to the scam team the same day."
    ],
    keywords: "fake account copyright stolen photos identity theft catfish dmca"
  },
  {
    id: "delete-account",
    question: "How do I delete my account, and what happens to my content?",
    answer: [
      "Settings › Account › Delete account. You'll be asked to type a confirmation phrase, because this cannot be undone by support afterwards.",
      "There is a grace period before deletion becomes permanent — sign in again during that window and the account is restored intact. After it, your posts, messages, and profile are deleted or irreversibly anonymised.",
      "Download your data first if you want to keep it: once the grace period ends we can't produce an export.",
      "If you only want a break, Settings › Account › Deactivate hides your profile and content without deleting anything."
    ],
    keywords: "delete deactivate close remove account permanently data"
  }
];

/* -------------------------------------------------------------------------- */
/*                                Diagnostics                                  */
/* -------------------------------------------------------------------------- */

type Diagnostic = { label: string; value: string };

/**
 * The context support asks for on every second ticket. Collected automatically
 * and shown to the user before they submit — attaching diagnostics silently
 * would be the wrong trade even though it is technically easier.
 *
 * Deliberately excludes anything identifying beyond the account the ticket is
 * already filed under: no device name, no IP, no token.
 */
function collectDiagnostics(): Diagnostic[] {
  return [
    { label: "App version", value: APP_VERSION || "unknown" },
    { label: "Platform", value: Platform.OS },
    { label: "OS version", value: `${Device.osName || Platform.OS} ${Device.osVersion || String(Platform.Version)}` },
    { label: "Device model", value: Device.modelName || "Unknown" },
    { label: "Physical device", value: Device.isDevice ? "Yes" : "No (simulator)" },
    { label: "API host", value: PULSE_API_BASE_URL.replace(/^https?:\/\//i, "") }
  ];
}

function diagnosticsBlock(diagnostics: Diagnostic[]): string {
  return diagnostics.map((entry) => `${entry.label}: ${entry.value}`).join("\n");
}

/* -------------------------------------------------------------------------- */
/*                                  FAQ row                                    */
/* -------------------------------------------------------------------------- */

function FaqRow({ entry, expanded, onToggle }: { entry: FaqEntry; expanded: boolean; onToggle: () => void }) {
  const theme = useTheme();
  return (
    <View>
      <SettingsRow
        testID={`help-faq-${entry.id}`}
        title={entry.question}
        icon="help-circle-outline"
        accessibilityRole="button"
        accessibilityState={{ selected: expanded }}
        accessibilityHint={expanded ? "Collapses this answer." : "Expands the answer."}
        onPress={onToggle}
        accessory={
          <Ionicons name={expanded ? "chevron-up" : "chevron-down"} size={theme.scaleFont(18)} color={theme.colors.muted} />
        }
      />
      {expanded ? (
        <View style={[styles.answer, { paddingHorizontal: theme.metrics.rowPaddingHorizontal }]}>
          {entry.answer.map((paragraph, index) => (
            <Text
              key={index}
              selectable
              style={{
                color: theme.colors.muted,
                fontSize: theme.scaleFont(14),
                lineHeight: theme.scaleFont(21),
                marginBottom: index === entry.answer.length - 1 ? 0 : 10
              }}
            >
              {paragraph}
            </Text>
          ))}
        </View>
      ) : null}
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Screen                                    */
/* -------------------------------------------------------------------------- */

type FormKind = "contact" | "problem";

const TICKET_STATUS_TONE: Record<string, "accent" | "warning" | "muted"> = {
  open: "warning",
  pending: "warning",
  in_progress: "warning",
  resolved: "accent",
  closed: "muted"
};

export function HelpSettingsScreen() {
  const theme = useTheme();
  const { t } = useTranslation();
  const { authState } = useAuth();

  const [query, setQuery] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [openForm, setOpenForm] = useState<FormKind | null>(null);

  const [email, setEmail] = useState(authState.user?.email || "");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [problem, setProblem] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const mounted = useRef(true);

  const diagnostics = useMemo(collectDiagnostics, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadTickets = useCallback(async () => {
    // Show the cached list first so the section does not pop in late, then
    // reconcile with the server. Both paths are best-effort: a support outage
    // must not stop the user reading the FAQ.
    const cached = await loadCachedSupportState().catch(() => null);
    if (mounted.current && cached?.tickets?.length) setTickets(cached.tickets);
    try {
      const state = await listSupportTickets();
      if (mounted.current) setTickets(state.tickets);
    } catch {
      // Keep whatever the cache gave us.
    }
  }, []);

  useEffect(() => {
    void loadTickets();
  }, [loadTickets]);

  const filteredFaq = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return FAQ;
    return FAQ.filter((entry) =>
      `${entry.question} ${entry.answer.join(" ")} ${entry.keywords}`.toLowerCase().includes(needle)
    );
  }, [query]);

  const toggleFaq = useCallback(
    (id: string) => {
      animateNextLayout(theme.reduceMotion);
      setExpandedId((current) => (current === id ? null : id));
    },
    [theme.reduceMotion]
  );

  const toggleForm = useCallback(
    (kind: FormKind) => {
      animateNextLayout(theme.reduceMotion);
      setFormError(null);
      setOpenForm((current) => (current === kind ? null : kind));
    },
    [theme.reduceMotion]
  );

  const submit = useCallback(
    async (kind: FormKind) => {
      const trimmedEmail = email.trim();
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) {
        setFormError("Enter the email address we should reply to.");
        return;
      }

      const body = kind === "contact" ? message.trim() : problem.trim();
      if (body.length < 20) {
        setFormError("Tell us a bit more — at least a sentence or two so we can act on it.");
        return;
      }

      const ticketSubject = kind === "contact" ? subject.trim() || "Support request" : "App problem report";

      setSubmitting(true);
      setFormError(null);
      try {
        const result = await createSupportTicket({
          name: authState.user?.display_name || authState.user?.full_name || undefined,
          email: trimmedEmail,
          issue_type: kind === "contact" ? "support" : "bug",
          subject: ticketSubject,
          // Diagnostics are appended for both kinds: a support agent needs the
          // build number just as often as a triage engineer does.
          message: `${body}\n\n---\nDiagnostics (attached automatically)\n${diagnosticsBlock(diagnostics)}`
        });
        if (!mounted.current) return;
        animateNextLayout(theme.reduceMotion);
        setSubject("");
        setMessage("");
        setProblem("");
        setOpenForm(null);
        // Quote the server-issued reference (PS-YYYY-XXXXXXXX) when we have
        // one, and stay honest either way: the confirmation email is sent by
        // the backend, so it is promised rather than asserted as delivered.
        const reference = typeof result.reference === "string" ? result.reference.trim() : "";
        Alert.alert(
          t("settings:supportTicket.sentTitle"),
          reference
            ? t("settings:supportTicket.sentWithReference", { reference, email: trimmedEmail })
            : t("settings:supportTicket.sentBody", { email: trimmedEmail })
        );
        void loadTickets();
      } catch (caught) {
        if (!mounted.current) return;
        const detail = caught instanceof Error && caught.message ? caught.message : "Please check your connection and try again.";
        setFormError(`Couldn't send that. ${detail}`);
      } finally {
        if (mounted.current) setSubmitting(false);
      }
    },
    [authState.user, diagnostics, email, loadTickets, message, problem, subject, t, theme.reduceMotion]
  );

  const openTickets = tickets.filter((ticket) => ticket.status !== "closed");

  return (
    <SettingsShell bottomDock={false} onRefresh={loadTickets}>
      <SettingsHeader
        title="Help"
        subtitle="Answers to the things people actually get stuck on, and a direct line to support when they don't cover it."
      />

      <SettingsSearchField value={query} onChangeText={setQuery} placeholder="Search help" />

      {filteredFaq.length ? (
        <SettingsSection
          title={query.trim() ? `${filteredFaq.length} ${filteredFaq.length === 1 ? "answer" : "answers"}` : "Common questions"}
        >
          {filteredFaq.map((entry) => (
            <FaqRow key={entry.id} entry={entry} expanded={expandedId === entry.id} onToggle={() => toggleFaq(entry.id)} />
          ))}
        </SettingsSection>
      ) : (
        <SettingsEmptyState
          icon="search-outline"
          title="No answer for that yet"
          body={`Nothing in the help centre matches “${query.trim()}”. Contact support below and we'll answer it directly.`}
          action={
            <SettingsButton
              testID="help-clear-search"
              label="Clear search"
              variant="secondary"
              full={false}
              onPress={() => setQuery("")}
            />
          }
        />
      )}

      <SettingsSection
        title="Get in touch"
        description="Support replies by email. Everything you send includes the diagnostics listed below."
      >
        <SettingsRow
          testID="help-contact-support"
          title="Contact support"
          subtitle="Account, billing, safety, or anything the answers above didn't cover."
          icon="chatbubble-ellipses-outline"
          accessibilityRole="button"
          accessibilityState={{ selected: openForm === "contact" }}
          onPress={() => toggleForm("contact")}
          accessory={
            <Ionicons
              name={openForm === "contact" ? "chevron-up" : "chevron-down"}
              size={theme.scaleFont(18)}
              color={theme.colors.muted}
            />
          }
        />
        <SettingsRow
          testID="help-report-problem"
          title="Report a problem"
          subtitle="Something in the app is broken, slow, or behaving strangely."
          icon="bug-outline"
          accessibilityRole="button"
          accessibilityState={{ selected: openForm === "problem" }}
          onPress={() => toggleForm("problem")}
          accessory={
            <Ionicons
              name={openForm === "problem" ? "chevron-up" : "chevron-down"}
              size={theme.scaleFont(18)}
              color={theme.colors.muted}
            />
          }
        />
      </SettingsSection>

      {openForm ? (
        <SettingsSection
          title={openForm === "contact" ? "Contact support" : "Report a problem"}
          description={
            openForm === "contact"
              ? "Describe what you were trying to do and what happened instead."
              : "What did you do, what did you expect, and what happened? Screen names and timings help."
          }
          busy={submitting}
        >
          <SettingsTextField
            testID="help-form-email"
            label="Reply to"
            value={email}
            onChangeText={setEmail}
            placeholder="you@example.com"
            keyboardType="email-address"
            autoCapitalize="none"
            helperText="We'll only use this to answer your request."
            editable={!submitting}
          />

          {openForm === "contact" ? (
            <SettingsTextField
              testID="help-form-subject"
              label="Subject"
              value={subject}
              onChangeText={setSubject}
              placeholder="Short summary"
              autoCapitalize="sentences"
              maxLength={120}
              editable={!submitting}
            />
          ) : null}

          <SettingsTextField
            testID={openForm === "contact" ? "help-form-message" : "help-form-problem"}
            label={openForm === "contact" ? "Message" : "What went wrong"}
            value={openForm === "contact" ? message : problem}
            onChangeText={openForm === "contact" ? setMessage : setProblem}
            placeholder={
              openForm === "contact"
                ? "Tell us what you need help with."
                : "Example: opening a reel from the notification tab shows a black screen until I background the app."
            }
            multiline
            autoCapitalize="sentences"
            maxLength={4000}
            errorText={formError || undefined}
            editable={!submitting}
          />

          <View style={[styles.diagnostics, { paddingHorizontal: theme.metrics.rowPaddingHorizontal }]}>
            <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(12), fontWeight: "800", letterSpacing: 0.6 }}>
              ATTACHED AUTOMATICALLY
            </Text>
            {diagnostics.map((entry) => (
              <View key={entry.label} style={styles.diagnosticRow}>
                <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(13) }}>{entry.label}</Text>
                <Text
                  numberOfLines={1}
                  style={{ color: theme.colors.text, flexShrink: 1, fontSize: theme.scaleFont(13), fontWeight: "600" }}
                >
                  {entry.value}
                </Text>
              </View>
            ))}
          </View>

          <View style={[styles.submit, { paddingHorizontal: theme.metrics.rowPaddingHorizontal }]}>
            <SettingsButton
              testID={openForm === "contact" ? "help-submit-contact" : "help-submit-problem"}
              label={openForm === "contact" ? "Send to support" : "Send report"}
              icon="send-outline"
              busy={submitting}
              onPress={() => void submit(openForm)}
            />
          </View>
        </SettingsSection>
      ) : null}

      {openTickets.length ? (
        <SettingsSection title="Your open requests" footnote="Replies arrive by email at the address on each request.">
          {openTickets.map((ticket) => (
            <SettingsRow
              key={ticket.id}
              testID={`help-ticket-${ticket.id}`}
              title={ticket.subject || "Support request"}
              subtitle={`#${ticket.id}${ticket.created_at ? ` · opened ${ticket.created_at.slice(0, 10)}` : ""}`}
              icon="receipt-outline"
              accessory={
                <SettingsBadge
                  label={(ticket.status || "open").replace(/_/g, " ")}
                  tone={TICKET_STATUS_TONE[ticket.status || "open"] || "muted"}
                />
              }
            />
          ))}
        </SettingsSection>
      ) : null}

      {/*
        Informational only, deliberately. An accessory here would read as a
        tappable action, and PulseSoc cannot reliably determine a user's local
        emergency number or place a call on their behalf — a row that looks like
        it dials and doesn't is worse than no row at all in this situation.
      */}
      <SettingsSection title="Emergencies">
        <SettingsRow
          testID="help-emergency"
          title="Immediate danger"
          subtitle="If someone is at risk of harm right now, contact your local emergency services directly. We can remove content quickly, but we can't send help."
          icon="alert-circle-outline"
          iconColor={theme.colors.danger}
        />
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  answer: { paddingBottom: 14, paddingTop: 2 },
  diagnostics: { paddingBottom: 4, paddingTop: 8 },
  diagnosticRow: { flexDirection: "row", gap: 12, justifyContent: "space-between", marginTop: 6 },
  submit: { paddingBottom: 14, paddingTop: 12 }
});
