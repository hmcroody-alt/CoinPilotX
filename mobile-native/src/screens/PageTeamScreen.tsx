import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View } from "react-native";
import {
  changePageMemberRole,
  getPageTeam,
  invitePageMember,
  PAGE_ROLE_SUMMARY,
  PageMember,
  pageRoleLabel,
  PageRole,
  PageTeam,
  removePageMember,
  transferPageOwnership
} from "../api/pages";
import { PulseApiError } from "../api/pulseApi";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "PageTeam">;

/**
 * Team & Access — who can act for this presence, and on whose authority.
 *
 * A presence is rarely one person: a label runs an artist's page, an agency
 * runs a brand's ads, a manager posts while the owner is on stage. The server
 * has modelled that since the beginning — seven roles, invites with an
 * expiry, role changes, removal, and a confirmed ownership transfer — and not
 * one of those six calls had a caller anywhere in the app. The capability was
 * real and unreachable, so in practice every presence was a single-account
 * presence and the only way to share one was to share a password.
 *
 * Two rules shape this screen:
 *
 * Every control here is one the server has already said yes to. `can_manage`,
 * `can_change_role`, `can_remove`, `can_receive_ownership`, the assignable
 * role list and the transfer phrase all arrive from `team_view`, derived from
 * the same permission table the mutating calls check. Nothing is inferred from
 * the role name, because a client that re-derives permission drifts from the
 * server and starts rendering buttons that 403.
 *
 * Ownership is not a role you can be given. It moves only through an explicit,
 * typed confirmation, to someone who is already an active member — that is the
 * server's rule, and this screen states it rather than discovering it.
 */

function memberName(member: PageMember) {
  return member.name || (member.handle ? `@${member.handle}` : `Member ${member.user_id}`);
}

export function PageTeamScreen({ route }: Props) {
  const pageId = route.params.pageId;
  const [team, setTeam] = useState<PageTeam | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const [handle, setHandle] = useState("");
  const [inviteRole, setInviteRole] = useState<PageRole | "">("");
  const [expanded, setExpanded] = useState(0);
  const [transferTo, setTransferTo] = useState(0);
  const [confirmText, setConfirmText] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      setTeam(await getPageTeam(pageId));
    } catch (loadError) {
      setError(
        loadError instanceof PulseApiError ? loadError.message : "The team could not be loaded."
      );
    } finally {
      setLoading(false);
    }
  }, [pageId]);

  useEffect(() => {
    load();
  }, [load]);

  /**
   * Every mutation funnels through here so they share one rule: the server is
   * re-read afterwards rather than the local copy being patched. Changing one
   * person's role can change what is offered for everyone else — removing the
   * last admin, or promoting someone into a seat that makes them
   * transfer-eligible — and a screen that guesses at that goes stale silently.
   */
  async function act(key: string, run: () => Promise<unknown>, success: string) {
    if (busy) return;
    setBusy(key);
    setError("");
    setMessage("");
    try {
      await run();
      setMessage(success);
      await load();
    } catch (actionError) {
      setError(
        actionError instanceof PulseApiError ? actionError.message : "That change did not go through."
      );
    } finally {
      setBusy("");
    }
  }

  function invite() {
    const target = handle.trim().replace(/^@+/, "");
    if (!target) {
      setError("Enter the handle of the person you want to invite.");
      return;
    }
    if (!inviteRole) {
      setError("Choose what this person will be able to do.");
      return;
    }
    act("invite", () => invitePageMember(pageId, { handle: target }, inviteRole), `Invited @${target}.`).then(
      () => {
        setHandle("");
        setInviteRole("");
      }
    );
  }

  function transfer(member: PageMember) {
    if (!team) return;
    act(
      `transfer:${member.user_id}`,
      () => transferPageOwnership(pageId, member.user_id, confirmText.trim()),
      `${memberName(member)} now owns this presence.`
    ).then(() => {
      setTransferTo(0);
      setConfirmText("");
    });
  }

  if (loading) {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={presenceTheme.teal} size="large" />
      </View>
    );
  }

  if (!team) {
    return (
      <View style={[styles.root, styles.center]}>
        <Text style={styles.error}>{error || "The team could not be loaded."}</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.lead}>
        A presence can be run by more than one person. Everyone here acts as this presence, and every
        change below is recorded against whoever made it.
      </Text>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {message ? <Text style={styles.message}>{message}</Text> : null}

      {team.members.map((member) => {
        const isExpanded = expanded === member.user_id;
        const canDoSomething = Boolean(member.can_change_role || member.can_remove || member.can_receive_ownership);
        return (
          <View key={member.user_id} style={styles.card}>
            <View style={styles.cardHead}>
              <View style={styles.cardHeadText}>
                <Text style={styles.cardTitle}>
                  {memberName(member)}
                  {member.is_you ? " (you)" : ""}
                </Text>
                <Text style={styles.cardRole}>
                  {pageRoleLabel(member.role)}
                  {member.status === "invited" ? " · invite pending" : ""}
                </Text>
              </View>
              {canDoSomething ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Manage ${memberName(member)}`}
                  style={styles.manageButton}
                  onPress={() => setExpanded(isExpanded ? 0 : member.user_id)}
                >
                  <Text style={styles.manageButtonText}>{isExpanded ? "Done" : "Manage"}</Text>
                </Pressable>
              ) : null}
            </View>

            <Text style={styles.cardBody}>{PAGE_ROLE_SUMMARY[member.role] || ""}</Text>

            {member.is_owner ? (
              /* Not a disabled button with no explanation: the owner's seat is
                 genuinely immovable except through transfer, and saying so is
                 the difference between a rule and a bug. */
              <Text style={styles.note}>
                The owner's role can't be changed or removed here. Ownership moves only through a
                transfer.
              </Text>
            ) : null}

            {isExpanded ? (
              <View style={styles.panel}>
                {member.can_change_role ? (
                  <>
                    <Text style={styles.panelTitle}>Change what they can do</Text>
                    {team.assignable_roles
                      .filter((role) => role !== member.role)
                      .map((role) => (
                        <Pressable
                          key={role}
                          accessibilityRole="button"
                          /* The same role name appears in this panel and in
                             the invite form. Read aloud, "Manager" alone says
                             nothing about which person it would apply to. */
                          accessibilityLabel={`Make ${memberName(member)} ${pageRoleLabel(role)}`}
                          disabled={Boolean(busy)}
                          style={styles.option}
                          onPress={() =>
                            act(
                              `role:${member.user_id}:${role}`,
                              () => changePageMemberRole(pageId, member.user_id, role),
                              `${memberName(member)} is now ${pageRoleLabel(role)}.`
                            )
                          }
                        >
                          <View style={styles.optionText}>
                            <Text style={styles.optionLabel}>{pageRoleLabel(role)}</Text>
                            <Text style={styles.optionBody}>{PAGE_ROLE_SUMMARY[role] || ""}</Text>
                          </View>
                          <Text style={styles.optionAction}>
                            {busy === `role:${member.user_id}:${role}` ? "Saving…" : "Set"}
                          </Text>
                        </Pressable>
                      ))}
                  </>
                ) : null}

                {member.can_remove ? (
                  <Pressable
                    accessibilityRole="button"
                    disabled={Boolean(busy)}
                    style={styles.danger}
                    onPress={() =>
                      act(
                        `remove:${member.user_id}`,
                        () => removePageMember(pageId, member.user_id),
                        `${memberName(member)} no longer has access.`
                      )
                    }
                  >
                    <Text style={styles.dangerText}>
                      {busy === `remove:${member.user_id}`
                        ? "Removing…"
                        : `Remove ${memberName(member)} from the team`}
                    </Text>
                  </Pressable>
                ) : null}

                {member.can_receive_ownership ? (
                  <View style={styles.transfer}>
                    <Text style={styles.panelTitle}>Transfer ownership</Text>
                    <Text style={styles.cardBody}>
                      {memberName(member)} becomes the owner and you become an admin. This cannot be
                      undone from your side — only the new owner can transfer it back.
                    </Text>
                    {transferTo === member.user_id ? (
                      <>
                        <Text style={styles.confirmPrompt}>
                          Type {team.transfer_confirm_phrase} to confirm.
                        </Text>
                        <TextInput
                          accessibilityLabel="Ownership transfer confirmation"
                          autoCapitalize="characters"
                          autoCorrect={false}
                          onChangeText={setConfirmText}
                          placeholder={team.transfer_confirm_phrase}
                          placeholderTextColor={colors.muted}
                          style={styles.input}
                          value={confirmText}
                        />
                        <Pressable
                          accessibilityRole="button"
                          /* The phrase is checked server-side regardless; this
                             only avoids a round trip that is certain to fail. */
                          disabled={
                            Boolean(busy) ||
                            confirmText.trim().toUpperCase() !== team.transfer_confirm_phrase
                          }
                          style={styles.danger}
                          onPress={() => transfer(member)}
                        >
                          <Text style={styles.dangerText}>
                            {busy === `transfer:${member.user_id}`
                              ? "Transferring…"
                              : `Hand this presence to ${memberName(member)}`}
                          </Text>
                        </Pressable>
                      </>
                    ) : (
                      <Pressable
                        accessibilityRole="button"
                        style={styles.option}
                        onPress={() => {
                          setTransferTo(member.user_id);
                          setConfirmText("");
                        }}
                      >
                        <Text style={styles.optionLabel}>Make {memberName(member)} the owner</Text>
                        <Text style={styles.optionAction}>Start</Text>
                      </Pressable>
                    )}
                  </View>
                ) : null}
              </View>
            ) : null}
          </View>
        );
      })}

      {team.can_manage_members ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Invite someone</Text>
          <Text style={styles.cardBody}>
            They'll get an invite to accept. Nothing changes for this presence until they do.
          </Text>
          <TextInput
            accessibilityLabel="Handle to invite"
            autoCapitalize="none"
            autoCorrect={false}
            onChangeText={setHandle}
            placeholder="@handle"
            placeholderTextColor={colors.muted}
            style={styles.input}
            value={handle}
          />
          <Text style={styles.panelTitle}>What should they be able to do?</Text>
          {team.assignable_roles.map((role) => (
            <Pressable
              key={role}
              accessibilityRole="button"
              accessibilityLabel={`Invite as ${pageRoleLabel(role)}`}
              accessibilityState={{ selected: inviteRole === role }}
              style={[styles.option, inviteRole === role && styles.optionSelected]}
              onPress={() => setInviteRole(role)}
            >
              <View style={styles.optionText}>
                <Text style={styles.optionLabel}>{pageRoleLabel(role)}</Text>
                <Text style={styles.optionBody}>{PAGE_ROLE_SUMMARY[role] || ""}</Text>
              </View>
              {inviteRole === role ? <Text style={styles.optionAction}>Chosen</Text> : null}
            </Pressable>
          ))}
          <Pressable
            accessibilityRole="button"
            disabled={Boolean(busy)}
            style={styles.primary}
            onPress={invite}
          >
            <Text style={styles.primaryText}>{busy === "invite" ? "Inviting…" : "Send invite"}</Text>
          </Pressable>
        </View>
      ) : (
        /* An analyst or content manager can see who they are working with —
           that is not privileged — but has no seat to hand out. Saying which
           of the two it is beats an absent section. */
        <Text style={styles.note}>
          Only an owner or admin can invite people or change what someone can do.
        </Text>
      )}
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: presenceTheme.radius.card,
    borderWidth: 1,
    gap: 6,
    marginTop: 14,
    padding: 16
  },
  cardBody: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  cardHead: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  cardHeadText: {
    flex: 1,
    paddingRight: 12
  },
  cardRole: {
    color: presenceTheme.teal,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 2
  },
  cardTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    justifyContent: "center",
    padding: 24
  },
  confirmPrompt: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800",
    marginTop: 8
  },
  content: {
    padding: 16,
    paddingBottom: 48
  },
  danger: {
    alignItems: "center",
    borderColor: colors.danger,
    borderRadius: 10,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 10,
    minHeight: 48,
    paddingHorizontal: 14
  },
  dangerText: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "800",
    textAlign: "center"
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    marginTop: 12
  },
  input: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    color: colors.text,
    fontSize: 14,
    marginTop: 8,
    minHeight: 48,
    paddingHorizontal: 14
  },
  lead: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 20
  },
  manageButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  manageButtonText: {
    color: presenceTheme.teal,
    fontSize: 12,
    fontWeight: "900"
  },
  message: {
    color: presenceTheme.teal,
    fontSize: 13,
    fontWeight: "700",
    marginTop: 12
  },
  note: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 10
  },
  option: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 8,
    minHeight: 48,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  optionAction: {
    color: presenceTheme.teal,
    fontSize: 12,
    fontWeight: "900"
  },
  optionBody: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 2
  },
  optionLabel: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700"
  },
  optionSelected: {
    borderColor: presenceTheme.tealBorder
  },
  optionText: {
    flex: 1,
    paddingRight: 12
  },
  panel: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    marginTop: 12,
    paddingTop: 4
  },
  panelTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900",
    marginTop: 12
  },
  primary: {
    alignItems: "center",
    backgroundColor: presenceTheme.teal,
    borderRadius: 10,
    justifyContent: "center",
    marginTop: 14,
    minHeight: 48
  },
  primaryText: {
    color: colors.background,
    fontSize: 14,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  transfer: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    marginTop: 14,
    paddingTop: 4
  }
}));
