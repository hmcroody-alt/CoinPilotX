export type ValidationResult = {
  valid: boolean;
  message: string;
};

export function validateEmail(value: string): ValidationResult {
  const email = value.trim();
  if (!email) return fail("Email is required.");
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return fail("Enter a valid email address.");
  return pass();
}

export function validateIdentifier(value: string): ValidationResult {
  const identifier = value.trim();
  if (!identifier) return fail("Email or username is required.");
  if (identifier.includes("@")) return validateEmail(identifier);
  if (!/^[A-Za-z0-9_.-]{3,40}$/.test(identifier.replace(/^@/, ""))) return fail("Enter a valid username or email.");
  return pass();
}

export function validateSignup(payload: {
  fullName: string;
  username: string;
  email: string;
  password: string;
  confirmPassword: string;
}): ValidationResult {
  if (!payload.fullName.trim()) return fail("Name is required.");
  if (!/^[A-Za-z0-9_.-]{3,40}$/.test(payload.username.trim().replace(/^@/, ""))) return fail("Username must be 3-40 letters, numbers, dots, underscores, or dashes.");
  const email = validateEmail(payload.email);
  if (!email.valid) return email;
  return validatePasswordPair(payload.password, payload.confirmPassword);
}

export function validatePasswordPair(password: string, confirmPassword: string): ValidationResult {
  if (password.length < 8) return fail("Use at least 8 characters for your password.");
  if (password !== confirmPassword) return fail("Passwords do not match.");
  return pass();
}

function fail(message: string): ValidationResult {
  return { valid: false, message };
}

function pass(): ValidationResult {
  return { valid: true, message: "" };
}
