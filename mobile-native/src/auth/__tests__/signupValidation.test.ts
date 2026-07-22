import {
  USERNAME_MIN,
  USERNAME_MAX,
  PASSWORD_MIN,
  validateFullName,
  normalizeFullName,
  normalizeUsername,
  validateUsername,
  normalizeEmail,
  validateEmail,
  evaluatePassword,
  isIdentityStepValid,
  isCredentialsStepValid,
  classifyRegisterError
} from "../signupValidation";

describe("full name", () => {
  it("collapses interior whitespace runs and trims edges without stripping Unicode", () => {
    expect(normalizeFullName("  José   da  Silva  ")).toBe("José da Silva");
    expect(normalizeFullName("张   伟")).toBe("张 伟");
  });

  it("accepts single-word and non-Western names", () => {
    expect(validateFullName("Prince").valid).toBe(true);
    expect(validateFullName("محمد").valid).toBe(true);
  });

  it("rejects empty / whitespace-only", () => {
    expect(validateFullName("").valid).toBe(false);
    expect(validateFullName("    ").valid).toBe(false);
  });

  it("rejects absurdly long names", () => {
    expect(validateFullName("a".repeat(161)).valid).toBe(false);
    expect(validateFullName("a".repeat(160)).valid).toBe(true);
  });
});

describe("username", () => {
  it("strips a leading @ and surrounding whitespace but preserves case", () => {
    expect(normalizeUsername("  @Nova_Pulse  ")).toBe("Nova_Pulse");
    expect(normalizeUsername("@@double")).toBe("double");
  });

  it("enforces the server character contract", () => {
    expect(validateUsername("ok.name-1_2").valid).toBe(true);
    expect(validateUsername("no spaces").valid).toBe(false);
    expect(validateUsername("emoji😀").valid).toBe(false);
    expect(validateUsername("bad!char").valid).toBe(false);
  });

  it("enforces length bounds", () => {
    expect(validateUsername("a".repeat(USERNAME_MIN - 1)).valid).toBe(false);
    expect(validateUsername("a".repeat(USERNAME_MIN)).valid).toBe(true);
    expect(validateUsername("a".repeat(USERNAME_MAX)).valid).toBe(true);
    expect(validateUsername("a".repeat(USERNAME_MAX + 1)).valid).toBe(false);
  });

  it("rejects empty", () => {
    expect(validateUsername("").valid).toBe(false);
    expect(validateUsername("@").valid).toBe(false);
  });
});

describe("email", () => {
  it("lowercases and trims", () => {
    expect(normalizeEmail("  User@Example.COM ")).toBe("user@example.com");
  });

  it("accepts plausible addresses and rejects malformed ones", () => {
    expect(validateEmail("a@b.co").valid).toBe(true);
    expect(validateEmail("first.last+tag@sub.domain.io").valid).toBe(true);
    expect(validateEmail("no-at-sign").valid).toBe(false);
    expect(validateEmail("two@@at.com").valid).toBe(false);
    expect(validateEmail("no@tld").valid).toBe(false);
    expect(validateEmail("space in@email.com").valid).toBe(false);
    expect(validateEmail("").valid).toBe(false);
  });
});

describe("password strength", () => {
  it("is empty at zero length and never meets the minimum", () => {
    const s = evaluatePassword("");
    expect(s.score).toBe(0);
    expect(s.label).toBe("Empty");
    expect(s.meetsMinimum).toBe(false);
  });

  it("gates submission strictly on the 8-char server rule, not the score", () => {
    expect(evaluatePassword("1234567").meetsMinimum).toBe(false);
    expect(evaluatePassword("12345678").meetsMinimum).toBe(true);
  });

  it("never truncates or upper-bounds long passphrases", () => {
    const long = "correct-horse-battery-staple-9!".repeat(4);
    const s = evaluatePassword(long);
    expect(s.meetsMinimum).toBe(true);
    expect(s.score).toBe(4);
  });

  it("reports the real requirement chips", () => {
    const s = evaluatePassword("abcdefg1!");
    const byKey = Object.fromEntries(s.requirements.map((r) => [r.key, r.met]));
    expect(byKey.length).toBe(true);
    expect(byKey.number).toBe(true);
    expect(byKey.symbol).toBe(true);
  });

  it("scores a weak short password low and a rich long one high", () => {
    expect(evaluatePassword("aaaaaaaa").score).toBeLessThan(3);
    expect(evaluatePassword("Aa1!aa1!aa1!").score).toBe(4);
  });
});

describe("step gating", () => {
  it("identity step requires both name and a valid username", () => {
    expect(isIdentityStepValid("Ada Lovelace", "ada_l")).toBe(true);
    expect(isIdentityStepValid("", "ada_l")).toBe(false);
    expect(isIdentityStepValid("Ada Lovelace", "!!")).toBe(false);
  });

  it("credentials step requires email, an 8+ password, AND explicit legal consent", () => {
    expect(isCredentialsStepValid("a@b.co", "12345678", true)).toBe(true);
    expect(isCredentialsStepValid("a@b.co", "12345678", false)).toBe(false);
    expect(isCredentialsStepValid("a@b.co", "short", true)).toBe(false);
    expect(isCredentialsStepValid("bad", "12345678", true)).toBe(false);
  });
});

describe("register error classification", () => {
  it("routes taken-handle errors back to the username field", () => {
    expect(classifyRegisterError("That handle is already taken.").target).toBe("username");
    expect(classifyRegisterError("Username unavailable").target).toBe("username");
  });

  it("routes email/account-exists errors to the email field", () => {
    expect(classifyRegisterError("An account already exists").target).toBe("email");
    expect(classifyRegisterError("That contact method is in use").target).toBe("email");
    expect(classifyRegisterError("Invalid email address").target).toBe("email");
  });

  it("routes password errors to the password field and preserves the message", () => {
    const r = classifyRegisterError("Password must be at least 8 characters");
    expect(r.target).toBe("password");
    expect(r.message).toBe("Password must be at least 8 characters");
  });

  it("falls back to a form-level target for unknown errors", () => {
    expect(classifyRegisterError("Something exploded").target).toBe("form");
  });
});
