/**
 * Pure, dependency-free validators for the PulseSoc account-creation flow.
 *
 * These mirror the authoritative server rules (bot.py `api_mobile_auth_register`
 * / `create_account`) so the client can fail fast and guide the user, but the
 * backend remains the source of truth for uniqueness and final acceptance. No
 * React, no IO — unit-tested in isolation.
 */

// Server contract: `^[A-Za-z0-9_.-]{3,40}$` (letters, numbers, dot, underscore, dash).
export const USERNAME_MIN = 3;
export const USERNAME_MAX = 40;
const USERNAME_RE = /^[A-Za-z0-9_.-]{3,40}$/;

// Server contract: at least 8 characters. We never impose an upper bound and
// never truncate — long passphrases from password managers must pass through.
export const PASSWORD_MIN = 8;

export type FieldCheck = { valid: boolean; message?: string };

/**
 * Full name: accept any real human name across languages/scripts. We only trim
 * surrounding whitespace and require at least one visible character — never a
 * two-word "Western" shape, never silent character stripping.
 */
export function validateFullName(raw: string): FieldCheck {
  const trimmed = normalizeFullName(raw);
  if (trimmed.length === 0) return { valid: false, message: "Enter your name so people can recognize you." };
  if (trimmed.length > 160) return { valid: false, message: "That name is too long (160 characters max)." };
  return { valid: true };
}

/** Trim only outer whitespace + collapse runs; preserve all Unicode letters/marks. */
export function normalizeFullName(raw: string): string {
  return raw.replace(/\s+/g, " ").trim();
}

/** Strip a leading "@" and outer whitespace; case is preserved (server compares case-insensitively). */
export function normalizeUsername(raw: string): string {
  return raw.trim().replace(/^@+/, "");
}

export function validateUsername(raw: string): FieldCheck {
  const handle = normalizeUsername(raw);
  if (handle.length === 0) return { valid: false, message: "Choose a username." };
  if (handle.length < USERNAME_MIN) return { valid: false, message: `Usernames need at least ${USERNAME_MIN} characters.` };
  if (handle.length > USERNAME_MAX) return { valid: false, message: `Usernames can be at most ${USERNAME_MAX} characters.` };
  if (!USERNAME_RE.test(handle)) {
    return { valid: false, message: "Use only letters, numbers, dots, underscores, or dashes." };
  }
  return { valid: true };
}

/**
 * Email format check. Deliberately conservative: exactly one @, non-empty
 * local part, a dotted domain with a 2+ char TLD, no whitespace. The backend
 * performs authoritative validation + verification.
 */
export function normalizeEmail(raw: string): string {
  return raw.trim().toLowerCase();
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function validateEmail(raw: string): FieldCheck {
  const email = normalizeEmail(raw);
  if (email.length === 0) return { valid: false, message: "Enter your email address." };
  if (!EMAIL_RE.test(email)) return { valid: false, message: "That doesn't look like a valid email address." };
  return { valid: true };
}

export type PasswordRequirement = { key: "length" | "number" | "symbol"; label: string; met: boolean };

export type PasswordStrength = {
  /** 0 (empty) … 4 (strong). */
  score: 0 | 1 | 2 | 3 | 4;
  label: "Empty" | "Weak" | "Fair" | "Good" | "Strong";
  /** True once the backend's minimum (8 chars) is satisfied — gates submission. */
  meetsMinimum: boolean;
  requirements: PasswordRequirement[];
};

/**
 * Local, honest password strength. `meetsMinimum` reflects only the real server
 * rule (>= 8 chars); the score/label are advisory UI feedback and never block a
 * password the server would accept.
 */
export function evaluatePassword(password: string): PasswordStrength {
  const hasLength = password.length >= PASSWORD_MIN;
  const hasNumber = /[0-9]/.test(password);
  const hasSymbol = /[^A-Za-z0-9]/.test(password);
  const hasUpperLower = /[a-z]/.test(password) && /[A-Z]/.test(password);

  const requirements: PasswordRequirement[] = [
    { key: "length", label: `${PASSWORD_MIN}+ characters`, met: hasLength },
    { key: "number", label: "1 number", met: hasNumber },
    { key: "symbol", label: "1 symbol", met: hasSymbol }
  ];

  if (password.length === 0) {
    return { score: 0, label: "Empty", meetsMinimum: false, requirements };
  }

  let points = 0;
  if (hasLength) points += 1;
  if (hasNumber) points += 1;
  if (hasSymbol) points += 1;
  if (hasUpperLower || password.length >= 12) points += 1;

  const score = Math.max(1, Math.min(4, points)) as 1 | 2 | 3 | 4;
  const label = (["Weak", "Weak", "Fair", "Good", "Strong"] as const)[score];
  return { score, label, meetsMinimum: hasLength, requirements };
}

/** True when every field required to move past the identity step is valid. */
export function isIdentityStepValid(fullName: string, username: string): boolean {
  return validateFullName(fullName).valid && validateUsername(username).valid;
}

/** True when credentials + required legal consent are all satisfied. */
export function isCredentialsStepValid(email: string, password: string, acceptedLegal: boolean): boolean {
  return validateEmail(email).valid && evaluatePassword(password).meetsMinimum && acceptedLegal;
}

export type RegisterFieldTarget = "username" | "email" | "password" | "form";

/**
 * Map a backend register error message back onto the field that should receive
 * focus, so the user is taken to the exact step/input to fix — without leaking
 * raw backend text where we can do better. Falls back to the server message.
 */
export function classifyRegisterError(message: string): { target: RegisterFieldTarget; message: string } {
  const lower = message.toLowerCase();
  if (lower.includes("handle") || lower.includes("username")) {
    return { target: "username", message: "That username is already taken. Try another." };
  }
  if (lower.includes("already exists") || lower.includes("contact method") || lower.includes("email")) {
    return { target: "email", message: "We couldn't create an account with that email. Try signing in instead." };
  }
  if (lower.includes("password")) {
    return { target: "password", message };
  }
  if (lower.includes("age")) {
    return { target: "form", message };
  }
  return { target: "form", message };
}
