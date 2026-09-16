/**
 * The Guests step: who the meeting is actually with.
 *
 * Before this step existed there was nowhere on the device to type an invitee,
 * which made a Private Office meeting with an accountant, a lawyer or a client
 * — most of what the feature is for — inexpressible. The step is small, and
 * every interesting thing about it is a way it could quietly drop somebody.
 *
 * The mutations in view:
 *
 *   - **a guest typed but not "added" is silently discarded.** The host fills
 *     in the last address, presses Next because the form looks complete, and
 *     watches Review omit a person they will not think to re-check. Caught by
 *     pressing Next with a valid address still in the field.
 *   - **Review counts guests instead of naming them.** "3 guests" is precisely
 *     the summary that lets a typo through unread, and the typo is the one
 *     failure that is free to fix at that moment and impossible afterwards.
 *   - **the same address is invited twice**, which on the server is one invite
 *     and on screen is two people — so the host believes someone was invited
 *     who never appears.
 *   - **the step is offered during a reschedule**, where it would show an
 *     existing meeting's invitees as an empty list and then discard whatever
 *     was typed. A wizard that throws away input is the same lie as one that
 *     closes without booking.
 *
 * `t` returns the key, per the convention in the sibling suites: the assertions
 * survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react-native";

jest.mock("../../../i18n", () => ({
  useTranslation: () => ({
    /**
     * The key, plus any interpolated values.
     *
     * The sibling suites return the bare key, which is the right default. It
     * cannot work here: "edit this guest" and "remove this guest" are one key
     * each for the whole list, and their labels are only unique once the
     * guest's name is in them. Returning the key alone would make every row's
     * button identical and `getByLabelText` would match all of them.
     */
    t: (key: string, options?: Record<string, unknown>) => {
      if (!options) return key;
      const values = Object.entries(options)
        .filter(([name]) => name !== "defaultValue")
        .map(([, value]) => String(value));
      if (!values.length) return (options.defaultValue as string) || key;
      return `${key}:${values.join(",")}`;
    }
  })
}));

jest.mock("../api", () => ({
  fetchCalendar: jest.fn(async () => ({
    start: "",
    end: "",
    timezone: "UTC",
    days: {},
    meetings: [],
    truncated: false
  }))
}));

import {
  EDIT_STEPS,
  NEW_MEETING_STEPS,
  ScheduleDraft,
  ScheduleWizard,
  guestEmailLooksValid,
  normalizeGuestEmail
} from "../ScheduleWizard";
import { todayIn } from "../calendar";
import { longDateLabel } from "../calendarLabels";

const K = "premium:privateOffice.meetings.wizard";
const NEXT = `${K}.next`;
const BACK = `${K}.back`;
const NAME = `${K}.guestName`;
const EMAIL = `${K}.guestEmail`;
const ADD = `${K}.addGuest`;
const UPDATE = `${K}.updateGuest`;
const SCHEDULE = "premium:privateOffice.meetings.schedule";

/** The per-row labels, which carry the guest's name so each row is findable. */
const editLabel = (who: string) => `${K}.editGuest:${who}`;
const removeLabel = (who: string) => `${K}.removeGuest:${who}`;

/** Presses from the first step to Guests — derived, so a new step cannot rot it. */
const STEPS_TO_GUESTS = NEW_MEETING_STEPS.indexOf("GUESTS");

async function openWizard(onSubmit: (draft: ScheduleDraft) => void) {
  const view = render(
    <ScheduleWizard busy={false} onSubmit={onSubmit} onCancel={() => undefined} />
  );
  await act(async () => undefined);
  return view;
}

/** Walk to the Guests step, taking every default on the way. */
function walkToGuests() {
  fireEvent.press(screen.getByLabelText(longDateLabel(todayIn())));
  for (let step = 0; step < STEPS_TO_GUESTS; step += 1) {
    fireEvent.press(screen.getByLabelText(NEXT));
  }
}

function typeGuest(name: string, email: string) {
  if (name) fireEvent.changeText(screen.getByLabelText(NAME), name);
  fireEvent.changeText(screen.getByLabelText(EMAIL), email);
}

function addGuest(name: string, email: string) {
  typeGuest(name, email);
  fireEvent.press(screen.getByLabelText(ADD));
}

/** Guests, as the submitted draft reports them. */
async function submitWithGuests(fill: () => void): Promise<ScheduleDraft> {
  const submitted: ScheduleDraft[] = [];
  await openWizard((draft) => submitted.push(draft));
  walkToGuests();
  fill();
  fireEvent.press(screen.getByLabelText(NEXT));
  fireEvent.press(screen.getByLabelText(SCHEDULE));
  expect(submitted).toHaveLength(1);
  return submitted[0];
}

describe("collecting guests", () => {
  it("carries every added guest into the draft, in the order they were typed", async () => {
    const draft = await submitWithGuests(() => {
      addGuest("Dana Reeves", "dana@outside.example");
      addGuest("Sam Okafor", "sam@outside.example");
    });

    expect(draft.invitees).toEqual([
      { name: "Dana Reeves", email: "dana@outside.example" },
      { name: "Sam Okafor", email: "sam@outside.example" }
    ]);
  });

  it("adds the guest still in the field when Next is pressed", async () => {
    // The failure this prevents is entirely silent: the host types the last
    // address, presses Next because the form looks finished, and Review omits
    // a person they have no reason to look for.
    const draft = await submitWithGuests(() => {
      typeGuest("Dana Reeves", "dana@outside.example");
    });

    expect(draft.invitees).toEqual([
      { name: "Dana Reeves", email: "dana@outside.example" }
    ]);
  });

  it("normalizes the address it sends, so case is not a second person", async () => {
    const draft = await submitWithGuests(() => {
      addGuest("Dana Reeves", "  Dana@Outside.Example  ");
    });

    expect(draft.invitees[0].email).toBe("dana@outside.example");
  });

  it("keeps an empty name rather than inventing one", async () => {
    // The server treats the name as a label and never as an identity. A client
    // that filled it in from the address would be supplying the one field the
    // relationship graph must not merge on.
    const draft = await submitWithGuests(() => {
      addGuest("", "dana@outside.example");
    });

    expect(draft.invitees).toEqual([{ name: "", email: "dana@outside.example" }]);
  });
});

describe("refusing a guest the host cannot have meant", () => {
  it("will not advance past an address that is not an address", async () => {
    const submitted: ScheduleDraft[] = [];
    await openWizard((draft) => submitted.push(draft));
    walkToGuests();
    typeGuest("Dana Reeves", "dana@outside");

    fireEvent.press(screen.getByLabelText(NEXT));

    // Still on Guests: Review has not appeared, and nothing was submitted.
    expect(screen.queryByLabelText(SCHEDULE)).toBeNull();
    expect(screen.getByText(`${K}.guestEmailInvalid`)).toBeTruthy();
    expect(submitted).toHaveLength(0);
  });

  it("refuses the same address twice", async () => {
    const submitted: ScheduleDraft[] = [];
    await openWizard((draft) => submitted.push(draft));
    walkToGuests();
    addGuest("Dana Reeves", "dana@outside.example");
    addGuest("Dana again", "DANA@outside.example");

    expect(screen.getByText(`${K}.guestDuplicate`)).toBeTruthy();

    // And Next does not settle the refusal by discarding the text. A wizard
    // that advanced here would leave the host believing a second person had
    // been added, with a message they had already looked away from as the only
    // record that one had not.
    fireEvent.press(screen.getByLabelText(NEXT));
    expect(screen.queryByLabelText(SCHEDULE)).toBeNull();

    // Clearing the fields is the way out, and it is the host's own doing.
    fireEvent.changeText(screen.getByLabelText(EMAIL), "");
    fireEvent.changeText(screen.getByLabelText(NAME), "");
    fireEvent.press(screen.getByLabelText(NEXT));
    fireEvent.press(screen.getByLabelText(SCHEDULE));

    // One person. Two rows here would be one invite on the server, and the
    // host would be waiting on a second guest who was never asked.
    expect(submitted[0].invitees).toEqual([
      { name: "Dana Reeves", email: "dana@outside.example" }
    ]);
  });

  it("will not advance past a name with no address", async () => {
    // Not a typo the host can be expected to catch on their own: it is a guest
    // they began and did not finish, and dropping it is invisible. Review
    // lists everyone who *was* added, so the screen looks entirely correct
    // with that person missing from it. Refusing costs one cleared field;
    // advancing costs a guest, and costs them silently.
    const submitted: ScheduleDraft[] = [];
    await openWizard((draft) => submitted.push(draft));
    walkToGuests();
    addGuest("Sam Okafor", "sam@outside.example");
    fireEvent.changeText(screen.getByLabelText(NAME), "Dana");

    fireEvent.press(screen.getByLabelText(NEXT));

    expect(screen.queryByLabelText(SCHEDULE)).toBeNull();
    expect(submitted).toHaveLength(0);
  });

  it("treats a field the host cleared as nothing to add", async () => {
    // The complement of the test above, and the reason that one is not simply
    // "block whenever the fields are not empty": a host who starts typing a
    // guest and thinks better of it has to be able to leave. Nobody is added,
    // and in particular no nameless, addressless row is appended.
    const draft = await submitWithGuests(() => {
      addGuest("Sam Okafor", "sam@outside.example");
      typeGuest("Dana", "dana@outside.example");
      fireEvent.changeText(screen.getByLabelText(NAME), "");
      fireEvent.changeText(screen.getByLabelText(EMAIL), "");
    });

    expect(draft.invitees).toEqual([
      { name: "Sam Okafor", email: "sam@outside.example" }
    ]);
  });
});

describe("changing your mind", () => {
  it("removes the guest that was named, not the one at that position", async () => {
    const draft = await submitWithGuests(() => {
      addGuest("Dana Reeves", "dana@outside.example");
      addGuest("Sam Okafor", "sam@outside.example");
      addGuest("Ola Bright", "ola@outside.example");
      fireEvent.press(screen.getByLabelText(removeLabel("Sam Okafor")));
    });

    expect(draft.invitees).toEqual([
      { name: "Dana Reeves", email: "dana@outside.example" },
      { name: "Ola Bright", email: "ola@outside.example" }
    ]);
  });

  it("edits a guest in place instead of adding a second one", async () => {
    const draft = await submitWithGuests(() => {
      addGuest("Dana Reeves", "dana@outside.example");
      fireEvent.press(screen.getByLabelText(editLabel("Dana Reeves")));
      fireEvent.changeText(screen.getByLabelText(EMAIL), "dana.reeves@outside.example");
      fireEvent.press(screen.getByLabelText(UPDATE));
    });

    expect(draft.invitees).toEqual([
      { name: "Dana Reeves", email: "dana.reeves@outside.example" }
    ]);
  });

  it("does not call an edited guest a duplicate of themselves", async () => {
    // The clash check has to exempt the row being edited. Without that, fixing
    // a misspelled name while leaving the address alone is rejected as a
    // duplicate of the very row it is about to replace — and the correction is
    // dropped, so the invitation goes out under the wrong name.
    const draft = await submitWithGuests(() => {
      addGuest("Dana Reves", "dana@outside.example");
      fireEvent.press(screen.getByLabelText(editLabel("Dana Reves")));
      fireEvent.changeText(screen.getByLabelText(NAME), "Dana Reeves");
      fireEvent.press(screen.getByLabelText(UPDATE));
    });

    expect(draft.invitees).toEqual([
      { name: "Dana Reeves", email: "dana@outside.example" }
    ]);
  });
});

describe("what Review says about them", () => {
  it("names every guest rather than counting them", async () => {
    await openWizard(() => undefined);
    walkToGuests();
    addGuest("Dana Reeves", "dana@outside.example");
    addGuest("", "sam@outside.example");
    fireEvent.press(screen.getByLabelText(NEXT));

    const row = screen.getByText(
      "Dana Reeves (dana@outside.example)\nsam@outside.example"
    );
    expect(row).toBeTruthy();
  });

  it("says so plainly when nobody was added", async () => {
    await openWizard(() => undefined);
    walkToGuests();
    fireEvent.press(screen.getByLabelText(NEXT));

    expect(screen.getByText(`${K}.guestsNoneShort`)).toBeTruthy();
  });
});

describe("rescheduling", () => {
  it("has no Guests step at all", () => {
    // Not a rendering detail: the guest list rides the create request and
    // nothing else, so an edit that offered the step would show an existing
    // meeting's invitees as empty and then discard whatever was typed in.
    expect(NEW_MEETING_STEPS).toContain("GUESTS");
    expect(EDIT_STEPS).not.toContain("GUESTS");
  });

  it("never reaches a guest field when seeded", async () => {
    render(
      <ScheduleWizard
        busy={false}
        initial={{
          date: todayIn(),
          time: { hour: 9, minute: 0 },
          timezone: "UTC",
          durationMinutes: 30,
          title: "Estate review",
          agenda: ""
        }}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />
    );
    await act(async () => undefined);

    for (let step = 0; step < EDIT_STEPS.length - 1; step += 1) {
      expect(screen.queryByLabelText(EMAIL)).toBeNull();
      fireEvent.press(screen.getByLabelText(NEXT));
    }
    expect(screen.queryByLabelText(EMAIL)).toBeNull();
    // And it did reach the end, so the loop above was not passing by never
    // having gone anywhere.
    expect(screen.queryByLabelText(BACK)).toBeTruthy();
  });
});

describe("the address check itself", () => {
  it("accepts an ordinary address and trims around it", () => {
    expect(normalizeGuestEmail("  Dana@Outside.Example ")).toBe("dana@outside.example");
    expect(guestEmailLooksValid(" dana@outside.example ")).toBe(true);
  });

  it("rejects the shapes that are certainly wrong", () => {
    for (const bad of ["", "dana", "dana@", "@outside.example", "dana@outside",
                       "dana @outside.example", "dana@out side.example"]) {
      expect(guestEmailLooksValid(bad)).toBe(false);
    }
  });

  it("does not try to be the server", () => {
    // Deliberately permissive. The check exists so a typo is caught while the
    // host is still looking at the field; deciding whether an address is
    // deliverable, already a member, or blocked is the server's ruling, and a
    // client that refused something the server would have accepted would be
    // inventing a rule nobody can see.
    expect(guestEmailLooksValid("a.b+tag@sub.domain.example")).toBe(true);
    expect(guestEmailLooksValid("x@y.z")).toBe(true);
  });
});
