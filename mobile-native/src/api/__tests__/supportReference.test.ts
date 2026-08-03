/**
 * The support reference exists because "it was broken" is not something a seller
 * can usefully tell support about their own money.
 *
 * It is a timestamp rather than an opaque token, and that is the design decision
 * these tests protect. No endpoint behind this screen issues a correlation id,
 * so minting a random string would produce a code that *looks* like a record
 * identifier support can look up, when no such record exists. A timestamp is
 * honest about being "when this happened to you" and is still correlatable
 * against server logs — which is the entire job.
 */
import { supportReferenceFor } from "../paymentsHub";

describe("supportReferenceFor", () => {
  it("produces a reference a seller can read down a phone line", () => {
    const reference = supportReferenceFor(new Date(2026, 7, 3, 9, 14, 0));
    // Groups of digits, no lowercase, no characters that are ambiguous aloud.
    expect(reference).toMatch(/^PAY-\d{8}-\d{4}-[0-9A-Z]{2}$/);
    expect(reference.startsWith("PAY-20260803-0914-")).toBe(true);
  });

  /**
   * Minute resolution, deliberately. Seconds would make two references minted
   * moments apart look like two unrelated incidents, and would add two more
   * characters to something a person has to say out loud.
   */
  it("gives the same reference to two failures in the same minute", () => {
    const a = supportReferenceFor(new Date(2026, 7, 3, 9, 14, 1));
    const b = supportReferenceFor(new Date(2026, 7, 3, 9, 14, 59));
    expect(a).toBe(b);
  });

  it("distinguishes failures in different minutes", () => {
    const a = supportReferenceFor(new Date(2026, 7, 3, 9, 14, 0));
    const b = supportReferenceFor(new Date(2026, 7, 3, 9, 15, 0));
    expect(a).not.toBe(b);
  });

  /**
   * The trailing pair is the device's UTC offset. Without it, support reading
   * "0914" has no way to know which 09:14 the seller means, and the local clock
   * is the only one the seller can see.
   */
  it("carries the offset without a sign, so it never breaks the character set", () => {
    const reference = supportReferenceFor(new Date(2026, 7, 3, 9, 14, 0));
    const tail = reference.split("-")[3];
    expect(tail).toHaveLength(2);
    expect(tail).toMatch(/^[0-9A-Z]{2}$/);
  });

  /**
   * An invalid date must not produce "PAY-NaNNaNNaN-…". A reference that is
   * visibly broken tells a seller already looking at a failure that a second
   * thing is broken too, which is both untrue and alarming. Returning "" makes
   * the screen render no reference line at all.
   */
  it("returns nothing rather than a malformed code for an invalid date", () => {
    expect(supportReferenceFor(new Date("not a date"))).toBe("");
  });

  it("defaults to now, so a caller cannot forget to pass the failure time", () => {
    expect(supportReferenceFor()).toMatch(/^PAY-\d{8}-\d{4}-[0-9A-Z]{2}$/);
  });
});
