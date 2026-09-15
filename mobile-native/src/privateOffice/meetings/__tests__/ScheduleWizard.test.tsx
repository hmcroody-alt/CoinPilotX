/**
 * What the wizard hands to the network, and what it refuses to compute.
 *
 * `calendar.test.ts` pins the date arithmetic. This pins the contract that
 * arithmetic exists to protect: that the screen collects a wall clock, a civil
 * date and a zone name, and sends exactly those three things.
 *
 * The mutations in view:
 *
 *   - **the client resolves the instant itself.** Appending `Z`, or the
 *     device's current offset, pins a 2035 meeting using today's DST rules. It
 *     builds and ships and is an hour wrong for every booking on the far side
 *     of a changeover. Caught by asserting the payload carries no offset at all.
 *   - **a double-tapped Schedule creates two meetings.** Caught by submitting
 *     twice from the same review screen and comparing idempotency keys.
 *   - **reopening the wizard reuses the previous key**, which would make a
 *     genuine second meeting silently collapse into the first. Caught by the
 *     complement of the same assertion.
 *
 * `t` returns the key, per the convention in the other screen suites: the
 * assertions survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react-native";

jest.mock("../../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
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

import { ScheduleDraft, ScheduleWizard } from "../ScheduleWizard";
import { dayKey, todayIn } from "../calendar";
import { longDateLabel } from "../calendarLabels";

const NEXT = "premium:privateOffice.meetings.wizard.next";
const SCHEDULE = "premium:privateOffice.meetings.schedule";

/** Walk from the date step to review, taking every default on the way. */
function walkToReview() {
  // A day that certainly exists in the grid: today, which the month opens on.
  fireEvent.press(screen.getByLabelText(longDateLabel(todayIn())));
  for (let step = 0; step < 5; step += 1) {
    fireEvent.press(screen.getByLabelText(NEXT));
  }
}

/**
 * Render, then let the calendar's stubbed window fetch settle.
 *
 * Without the flush the grid's `setState` lands after the test body and React
 * reports it as an un-acted update. The warning is noise here, but a suite
 * that prints warnings on every run is a suite nobody reads the warnings of.
 */
async function renderWizard(onSubmit: (draft: ScheduleDraft) => void) {
  const view = render(
    <ScheduleWizard busy={false} onSubmit={onSubmit} onCancel={() => undefined} />
  );
  await act(async () => undefined);
  return view;
}

describe("what the wizard sends", () => {
  it("sends a naive local datetime with no offset, and the zone beside it", async () => {
    const submitted: ScheduleDraft[] = [];
    await renderWizard((draft) => submitted.push(draft));
    walkToReview();
    fireEvent.press(screen.getByLabelText(SCHEDULE));

    expect(submitted).toHaveLength(1);
    const draft = submitted[0];
    // The whole point: no `Z`, no `+05:30`. The server resolves it against
    // `timezone` using that zone's rules for that date.
    expect(draft.scheduledStartAt).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/);
    expect(draft.scheduledStartAt.endsWith("Z")).toBe(false);
    expect(draft.scheduledStartAt).not.toMatch(/[+-]\d{2}:\d{2}$/);
    expect(typeof draft.timezone).toBe("string");
  });

  it("sends the day the user pressed, not the one their UTC offset implies", async () => {
    const submitted: ScheduleDraft[] = [];
    await renderWizard((draft) => submitted.push(draft));
    walkToReview();
    fireEvent.press(screen.getByLabelText(SCHEDULE));

    const today = todayIn();
    expect(submitted[0].date).toEqual(today);
    expect(submitted[0].scheduledStartAt.startsWith(dayKey(today))).toBe(true);
  });

  it("sends a duration inside the range the server accepts", async () => {
    const submitted: ScheduleDraft[] = [];
    await renderWizard((draft) => submitted.push(draft));
    walkToReview();
    fireEvent.press(screen.getByLabelText(SCHEDULE));

    expect(submitted[0].durationMinutes).toBeGreaterThanOrEqual(5);
    expect(submitted[0].durationMinutes).toBeLessThanOrEqual(1440);
  });
});

describe("the idempotency key", () => {
  it("is the same on a second tap of the same Schedule button", async () => {
    const submitted: ScheduleDraft[] = [];
    await renderWizard((draft) => submitted.push(draft));
    walkToReview();
    fireEvent.press(screen.getByLabelText(SCHEDULE));
    fireEvent.press(screen.getByLabelText(SCHEDULE));

    expect(submitted).toHaveLength(2);
    expect(submitted[0].idempotencyKey).toBe(submitted[1].idempotencyKey);
    expect(submitted[0].idempotencyKey).not.toBe("");
  });

  it("rotates when the user steps back to change something", async () => {
    const submitted: ScheduleDraft[] = [];
    await renderWizard((draft) => submitted.push(draft));
    walkToReview();
    fireEvent.press(screen.getByLabelText(SCHEDULE));
    const first = submitted[0].idempotencyKey;

    fireEvent.press(screen.getByLabelText("premium:privateOffice.meetings.wizard.back"));
    fireEvent.press(screen.getByLabelText(NEXT));
    fireEvent.press(screen.getByLabelText(SCHEDULE));

    // Leaving Review is what ends an intent. If the first submit had in fact
    // landed despite looking like it failed, holding the key here would make
    // the server return the OLD meeting — and the correction the user just
    // made would vanish while the screen reported success.
    expect(submitted[1].idempotencyKey).not.toBe(first);
  });

  it("is different between two wizards, so a real second meeting is not collapsed", async () => {
    const submitted: ScheduleDraft[] = [];
    const first = await renderWizard((draft) => submitted.push(draft));
    walkToReview();
    fireEvent.press(screen.getByLabelText(SCHEDULE));
    first.unmount();

    await renderWizard((draft) => submitted.push(draft));
    walkToReview();
    fireEvent.press(screen.getByLabelText(SCHEDULE));

    expect(submitted[0].idempotencyKey).not.toBe(submitted[1].idempotencyKey);
  });
});

describe("the date step gates the rest", () => {
  it("will not advance until a day has been chosen", async () => {
    const submitted: ScheduleDraft[] = [];
    await renderWizard((draft) => submitted.push(draft));
    // No date pressed. Next is disabled, so five presses go nowhere and the
    // Schedule button never appears.
    for (let step = 0; step < 5; step += 1) {
      fireEvent.press(screen.getByLabelText(NEXT));
    }
    expect(screen.queryByLabelText(SCHEDULE)).toBeNull();
    expect(submitted).toHaveLength(0);
  });
});
