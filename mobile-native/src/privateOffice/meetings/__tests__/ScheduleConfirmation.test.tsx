/**
 * What the host is allowed to be told after a booking succeeds.
 *
 * The bug this screen exists for was not "the meeting was not created". It was
 * "the wizard closed and said nothing", which is the same picture whether the
 * row landed or was rolled away underneath a `201`. Adding a confirmation screen
 * only helps if every line on it came from the server; a screen that recites the
 * draft back is the same fake success with better production values.
 *
 * So these tests are all one assertion wearing different clothes: **nothing here
 * may be computed from what the host typed.** The mutations in view:
 *
 *   - **the reminder ladder rendered from the constant.** `DEFAULT_REMINDER_OFFSETS`
 *     is intent; `_plan_reminders` is non-fatal, so the rows can be empty while
 *     the constant still reads 24h/1h/15m. That mutation promises three mails in
 *     exactly the case where none were scheduled. Caught by the absent / empty /
 *     `SKIPPED` trio below.
 *   - **guests counted instead of named.** "3 guests invited" is precisely the
 *     summary that lets a wrong address through unread, and this is the last
 *     moment the host can fix one for free.
 *   - **a skipped guest rendered as an invited one**, or with a generic apology
 *     instead of the reason. Someone who was refused must read as refused.
 *   - **an unknown skip reason rendered as a wrong explanation.** An untranslated
 *     machine code on screen is ugly; a confident wrong sentence is worse.
 *
 * `guestLines` and `skipReasonLabel` take `t` as an argument, so the pure
 * sections hand them a catalog directly and never touch the module mock.
 */

import React from "react";
import { act, render, screen, fireEvent } from "@testing-library/react-native";

const K = "premium:privateOffice.meetings.confirmed";

jest.mock("../../../i18n", () => ({
  useTranslation: () => ({
    /**
     * The key, plus any interpolated values — except the skip-reason family.
     *
     * Those resolve to `reason:<name>`, because the thing worth pinning through
     * a render is that the backend's `email_invalid` arrives at the catalog as
     * `emailInvalid`. (It has to: the i18n validator reads a trailing `_many` as
     * a plural suffix, so `too_many` would be the key `too` missing five of
     * Arabic's six forms.) Returning the bare key here would make the transform
     * invisible — every reason would fall back to itself and the test would
     * pass with the transform deleted.
     */
    t: (key: string, options?: Record<string, unknown>) => {
      const skip = key.match(/\.confirmed\.skipped\.(\w+)$/);
      if (skip) return `reason:${skip[1]}`;
      if (!options) return key;
      const values = Object.entries(options)
        .filter(([name]) => name !== "defaultValue")
        .map(([, value]) => String(value));
      return values.length ? `${key}:${values.join(",")}` : key;
    }
  })
}));

import {
  ScheduleConfirmation,
  guestLines,
  skipReasonLabel
} from "../ScheduleConfirmation";
import { meetingWhenLabel } from "../calendarLabels";
import { MeetingInviteResult, PrivateMeeting } from "../types";

/** A catalog that knows two keys, so the fallbacks are reachable. */
const CATALOG: Record<string, string> = {
  [`${K}.skipped.emailInvalid`]: "Not invited — that address is not usable",
  [`${K}.memberFallback`]: "Member {{id}}",
  [`${K}.someone`]: "One of your guests"
};

function t(key: string, vars?: Record<string, unknown>): string {
  const text = CATALOG[key];
  if (text === undefined) return key;
  if (!vars) return text;
  return text.replace(/\{\{(\w+)\}\}/g, (all, name: string) =>
    name in vars ? String(vars[name]) : all
  );
}

function inviteResult(parts: Partial<MeetingInviteResult> = {}): MeetingInviteResult {
  return {
    invited: [],
    invited_members: [],
    invited_contacts: [],
    skipped: [],
    ...parts
  };
}

function meeting(overrides: Partial<PrivateMeeting> = {}): PrivateMeeting {
  return {
    public_id: "mtg_9f3ac1",
    title: "Quarterly review",
    status: "SCHEDULED",
    waiting_room_enabled: true,
    locked: false,
    scheduled_start_at: "2026-11-04T15:00:00Z",
    scheduled_timezone: "America/New_York",
    schedule_version: 1,
    agenda: "",
    duration_minutes: 45,
    started_at: "",
    ended_at: "",
    end_reason: "",
    owner_user_id: 101,
    me: null,
    participants: [],
    recording_active: false,
    capabilities: {
      screen_share: { available: false, reason: "unsupported" },
      captions: { available: false, reason: "unsupported" },
      recording: { available: false, reason: "unsupported" }
    },
    created_at: "2026-09-15T10:00:00Z",
    ...overrides
  };
}

describe("who the screen says was invited", () => {
  it("names a member, and shows the typed address beside the name", () => {
    const lines = guestLines(
      meeting({
        invite_result: inviteResult({
          invited: [202],
          invited_members: [
            { user_id: 202, email: "dana@pulsesoc.com", name: "Dana Reeves" }
          ]
        })
      }),
      t
    );

    expect(lines).toEqual([
      {
        key: "u:202",
        label: "Dana Reeves",
        detail: "dana@pulsesoc.com",
        refused: false
      }
    ]);
  });

  it("falls back to the address when the host gave no name", () => {
    // And does not repeat it in `detail`: one line, said once.
    const lines = guestLines(
      meeting({
        invite_result: inviteResult({
          invited: [202],
          invited_members: [{ user_id: 202, email: "dana@pulsesoc.com", name: "" }]
        })
      }),
      t
    );

    expect(lines[0].label).toBe("dana@pulsesoc.com");
    expect(lines[0].detail).toBe("");
  });

  it("names the member id when a replay left no label at all", () => {
    // A replayed booking reads the invite rows back, and those hold an address
    // only for guests with no account — so a member the host invited by id and
    // never named has nothing to show. "Member 202" is thin, and visibly thin.
    // A blank row would read as a rendering bug instead of as thin data.
    const lines = guestLines(
      meeting({
        invite_result: inviteResult({
          invited: [202],
          invited_members: [{ user_id: 202, email: "", name: "" }]
        })
      }),
      t
    );

    expect(lines[0].label).toBe("Member 202");
    expect(lines[0].refused).toBe(false);
  });

  it("keeps members, outside guests and refusals apart and in that order", () => {
    const lines = guestLines(
      meeting({
        invite_result: inviteResult({
          invited: [202],
          invited_members: [{ user_id: 202, email: "", name: "Dana Reeves" }],
          invited_contacts: [{ email: "sam@outside.example", name: "" }],
          skipped: [{ email: "nope@", reason: "email_invalid" }]
        })
      }),
      t
    );

    expect(lines.map((line) => [line.label, line.refused])).toEqual([
      ["Dana Reeves", false],
      ["sam@outside.example", false],
      ["nope@", true]
    ]);
  });

  it("marks a refused guest as refused and says why", () => {
    const lines = guestLines(
      meeting({
        invite_result: inviteResult({
          skipped: [{ email: "nope@", name: "Nope", reason: "email_invalid" }]
        })
      }),
      t
    );

    expect(lines[0].refused).toBe(true);
    expect(lines[0].detail).toBe("Not invited — that address is not usable");
  });

  it("can still name a refusal the server described only by user id", () => {
    const lines = guestLines(
      meeting({ invite_result: inviteResult({ skipped: [{ user_id: 77, reason: "is_host" }] }) }),
      t
    );

    expect(lines[0].label).toBe("Member 77");
  });

  it("says nothing at all when the projection carries no invite result", () => {
    // `invite_result` is create-only. A read projection has none, and inferring
    // a guest list from `participants` here would be the screen doing its own
    // arithmetic — which is the whole thing this screen exists not to do.
    expect(guestLines(meeting(), t)).toEqual([]);
  });
});

describe("turning a machine reason into a sentence", () => {
  it("camel-cases the reason before looking it up", () => {
    // `email_invalid` must not reach the catalog as `email_invalid`: the i18n
    // validator would read `too_many` as the plural family `too`, and demand
    // five more Arabic forms of a key that is not a plural.
    expect(skipReasonLabel("email_invalid", t)).toBe(
      "Not invited — that address is not usable"
    );
  });

  it("renders an unknown reason as itself rather than as a wrong apology", () => {
    // A reason added on the server before the catalog catches up. The machine
    // code on screen is ugly; a confident wrong explanation of why someone was
    // not invited is worse, and is not visibly wrong.
    expect(skipReasonLabel("quota_exhausted", t)).toBe("quota_exhausted");
  });
});

/**
 * Render, then let the icon font's async load settle.
 *
 * Without the flush the check badge's `setState` lands after the test body and
 * React reports it as an un-acted update. The warning is noise here, but a suite
 * that prints warnings on every run is a suite nobody reads the warnings of.
 */
async function show(subject: PrivateMeeting, onDone: () => void = () => undefined) {
  const view = render(<ScheduleConfirmation meeting={subject} onDone={onDone} />);
  await act(async () => undefined);
  return view;
}

describe("the rendered screen", () => {
  it("quotes the server's instant in the server's zone", async () => {
    await show(meeting());

    // Not the wall clock the wizard submitted, and not the device's zone: the
    // instant the server resolved, rendered in the zone it stored. If those
    // disagree — a DST boundary, a zone guessed wrong — the host finds out
    // here, while the meeting is still trivially changeable.
    expect(
      screen.getByText(meetingWhenLabel("2026-11-04T15:00:00Z", "America/New_York"))
    ).toBeTruthy();
  });

  it("names every guest rather than counting them", async () => {
    await show(
      meeting({
          invite_result: inviteResult({
            invited: [202],
            invited_members: [
              { user_id: 202, email: "dana@pulsesoc.com", name: "Dana Reeves" }
            ],
            invited_contacts: [
              { email: "sam@outside.example", name: "" },
              { email: "ola@outside.example", name: "Ola Bright" }
            ]
          })
        })
    );

    expect(screen.getByText("Dana Reeves")).toBeTruthy();
    expect(screen.getByText("dana@pulsesoc.com")).toBeTruthy();
    expect(screen.getByText("sam@outside.example")).toBeTruthy();
    expect(screen.getByText("Ola Bright")).toBeTruthy();
    expect(screen.getByText("ola@outside.example")).toBeTruthy();
  });

  it("shows a refused address and the reason it was refused", async () => {
    await show(
      meeting({
          invite_result: inviteResult({
            invited_contacts: [{ email: "sam@outside.example", name: "" }],
            skipped: [{ email: "dana@nowhere", reason: "email_invalid" }]
          })
        })
    );

    expect(screen.getByText("dana@nowhere")).toBeTruthy();
    // Through the screen, so the camel-casing is exercised end to end.
    expect(screen.getByText("reason:emailInvalid")).toBeTruthy();
  });

  it("says plainly that nobody was invited", async () => {
    await show(meeting({ invite_result: inviteResult() }));

    expect(screen.getByText(`${K}.guestsNone`)).toBeTruthy();
  });

  it("shows both identifiers, because they are not interchangeable", async () => {
    await show(meeting({ meeting_code: "7QK-2M4" }));

    expect(screen.getByText("mtg_9f3ac1")).toBeTruthy();
    expect(screen.getByText("7QK-2M4")).toBeTruthy();
  });

  it("omits the invite code when the projection has none", async () => {
    // `meeting_code` is host-only. A guest's projection does not carry it, and
    // a row rendering an empty code would look like a code that failed to mint.
    await show(meeting());

    expect(screen.queryByText(`${K}.meetingCode`)).toBeNull();
    expect(screen.getByText(`${K}.meetingId`)).toBeTruthy();
  });

  it("hands the host a way out", async () => {
    const closed: number[] = [];
    await show(meeting(), () => closed.push(1));

    fireEvent.press(screen.getByLabelText(`${K}.done`));

    expect(closed).toHaveLength(1);
  });
});

describe("what the screen claims about reminders", () => {
  it("shows the rows the server planned", async () => {
    await show(
      meeting({
          reminders: [
            { offset_minutes: 1440, send_at: "2026-11-03T15:00:00Z", status: "PENDING" },
            { offset_minutes: 60, send_at: "2026-11-04T14:00:00Z", status: "PENDING" }
          ]
        })
    );

    expect(
      screen.getByText(meetingWhenLabel("2026-11-03T15:00:00Z", "America/New_York"))
    ).toBeTruthy();
    expect(
      screen.getByText(meetingWhenLabel("2026-11-04T14:00:00Z", "America/New_York"))
    ).toBeTruthy();
  });

  it("marks a reminder that was already too late to send", async () => {
    // A meeting booked twenty minutes out cannot honour a 24h reminder. The
    // row exists and says SKIPPED, and the host is told rather than left to
    // infer it from a mail that never comes.
    await show(
      meeting({
        reminders: [
          { offset_minutes: 1440, send_at: "2026-11-03T15:00:00Z", status: "SKIPPED" }
        ]
      })
    );

    expect(screen.getByText(new RegExp(`${K}\\.reminderSkipped$`))).toBeTruthy();
  });

  it("says none rather than reciting the default ladder", async () => {
    // THE test. The server's intent is 24h/1h/15m and planning them is
    // deliberately non-fatal, so an empty list is a real outcome — and a screen
    // that rendered the constant would promise three mails in precisely the
    // case where none were scheduled. Same shape of lie as the wizard that
    // closed on a meeting that had been rolled away, moved one screen later.
    await show(meeting({ reminders: [] }));

    expect(screen.getByText(`${K}.remindersNone`)).toBeTruthy();
  });

  it("stays silent when the server did not mention reminders", async () => {
    // Absent is not empty. A read projection carries no `reminders` key, and
    // "no reminders are scheduled" would be a claim the server never made.
    await show(meeting());

    expect(screen.queryByText(`${K}.reminders`)).toBeNull();
    expect(screen.queryByText(`${K}.remindersNone`)).toBeNull();
  });
});
