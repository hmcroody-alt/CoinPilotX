/**
 * Regression guard (Phase 46): one owner per native capability.
 *
 * Fails when a NEW direct import of an owned native module appears outside
 * its canonical owner. Pre-existing call sites are baselined — do NOT add
 * to a baseline; import from src/native instead.
 */
import * as fs from "fs";
import * as path from "path";

const SRC = path.resolve(__dirname, "../..");

type Guard = {
  module: string;
  owner: RegExp;
  baseline: Set<string>;
};

const GUARDS: Guard[] = [
  {
    module: "expo-haptics",
    owner: /^native\//,
    baseline: new Set([
      "calls/callSignalMedia.ts",
      "settings/components/SettingsControls.tsx",
      "navigation/GlobalNavigation.tsx",
      "emoji/EmojiPicker.tsx",
      "screens/settings/NotificationSettingsScreen.tsx",
      "screens/settings/PrivacySettingsScreen.tsx",
      "screens/UserDashboardScreen.tsx",
      "screens/SignupScreen.tsx",
      "screens/LiveHostSessionScreen.tsx",
      "screens/LoginScreen.tsx",
      "components/StatusActionRail.tsx",
      "components/PostCard.tsx",
      "components/auth/signup/VerifyEmailStep.tsx",
      "components/WelcomeUfoOverlay.tsx",
      "components/ProfileHeader.tsx",
      "live/liveHostUi.tsx",
      "spatial/motion/MotionOnboarding.tsx",
      "spatial/motion/useTiltNavigation.ts",
      "spatial/SpatialCreateConsole.tsx"
    ])
  },
  {
    module: "expo-clipboard",
    owner: /^native\//,
    // All legacy expo-clipboard call sites were migrated to native/clipboard.
    baseline: new Set([])
  },
  {
    module: "expo-document-picker",
    owner: /^native\//,
    baseline: new Set([
      "screens/MusicScreen.tsx",
      "screens/SellerListingComposerScreen.tsx",
      "screens/ChatScreen.tsx",
      "api/sellerApplication.ts",
      "api/verification.ts"
    ])
  },
  {
    module: "expo-local-authentication",
    owner: /^(native\/|session\/biometricAuth)/,
    baseline: new Set([])
  },
  {
    module: "expo-secure-store",
    owner: /^(native\/|session\/sessionStore|api\/push)/,
    baseline: new Set([])
  }
];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "node_modules" || entry.name === "__dbg__") continue;
      walk(full, out);
    } else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.includes(".backup-")) {
      out.push(full);
    }
  }
  return out;
}

const files = walk(SRC);

describe("native capability ownership guard", () => {
  for (const guard of GUARDS) {
    it(`no new direct ${guard.module} imports outside its owner`, () => {
      const pattern = new RegExp(`from\\s+["']${guard.module}["']`);
      const offenders: string[] = [];
      for (const file of files) {
        const rel = path.relative(SRC, file).split(path.sep).join("/");
        if (guard.owner.test(rel) || guard.baseline.has(rel)) continue;
        if (pattern.test(fs.readFileSync(file, "utf8"))) offenders.push(rel);
      }
      expect(offenders).toEqual([]);
    });
  }

  it("no second permissions orchestrator appears outside native/", () => {
    const offenders: string[] = [];
    for (const file of files) {
      const rel = path.relative(SRC, file).split(path.sep).join("/");
      if (rel.startsWith("native/")) continue;
      // Legacy call sites that predate the orchestrator are baselined:
      // settings screen, media foundation upload owner, seller verification flow.
      if (
        rel === "screens/settings/PermissionsSettingsScreen.tsx" ||
        rel === "media/nativeMediaUpload.ts" ||
        rel === "api/sellerApplication.ts"
      ) {
        continue;
      }
      const source = fs.readFileSync(file, "utf8");
      if (/requestCameraPermissionsAsync|requestMicrophonePermissionsAsync/.test(source) && !/screens\/(CameraStudioScreen|LiveStudioScreen)/.test(rel)) {
        // Camera/Live studio own their capture permission flows (protected surfaces).
        if (!rel.startsWith("screens/CameraStudioScreen") && !rel.startsWith("screens/LiveStudioScreen") && !rel.startsWith("core/") && !rel.startsWith("live/") && !rel.startsWith("calls/")) {
          offenders.push(rel);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
