import { readFileSync } from "fs";
import { join } from "path";
import {
  EngineerAccessDiagnostic,
  emitEngineerAccessDiagnostic,
  engineerAccessDiagnosticsEnabled,
  setEngineerAccessDiagnosticsSink
} from "../engineerAccessDiagnostics";

const PASSCODE = "70041852";

describe("engineerAccessDiagnostics", () => {
  let events: EngineerAccessDiagnostic[] = [];
  let restore: () => void = () => undefined;

  beforeEach(() => {
    events = [];
    restore = setEngineerAccessDiagnosticsSink((event) => events.push(event));
  });
  afterEach(() => restore());

  it("is on in a development build", () => {
    expect(engineerAccessDiagnosticsEnabled()).toBe(true);
  });

  it("emits every stage the mission requires", () => {
    const stages: EngineerAccessDiagnostic["stage"][] = [
      "button_tapped",
      "modal_opened",
      "input_length",
      "verification_started",
      "verification_source",
      "authorization_result",
      "destination_requested",
      "navigation_completed"
    ];
    stages.forEach((stage) => emitEngineerAccessDiagnostic({ stage }));
    expect(events.map((event) => event.stage)).toEqual(stages);
  });

  it("reports input as a count, never as digits", () => {
    emitEngineerAccessDiagnostic({ stage: "input_length", inputLength: PASSCODE.length });
    expect(events[0].inputLength).toBe(8);
    expect(JSON.stringify(events)).not.toContain(PASSCODE);
  });

  it("survives a sink that throws", () => {
    // A broken sink must never take down the gate it is observing.
    restore();
    restore = setEngineerAccessDiagnosticsSink(() => { throw new Error("sink exploded"); });
    expect(() => emitEngineerAccessDiagnostic({ stage: "button_tapped" })).not.toThrow();
  });

  it("has no field a passcode could be placed in", () => {
    const source = readFileSync(join(__dirname, "..", "engineerAccessDiagnostics.ts"), "utf8");
    // The constraint is enforced by the type, not by everyone remembering it.
    expect(source).not.toMatch(/passcode\??:\s*string/i);
    expect(source).not.toMatch(/(?<![\w])\d{8}(?![\w])/);
  });
});
