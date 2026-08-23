/**
 * Legal document bodies, stored as structured native content.
 *
 * PulseSoc's Settings platform contains no WebView, so legal text cannot be
 * "rendered" by pointing a browser view at pulsesoc.com. Each document is
 * therefore modelled as `{heading, paragraphs[]}` sections that `LegalSettings`
 * lays out with real native typography — selectable, searchable by the screen
 * reader, and correctly themed in light and dark.
 *
 * What lives here is the plain-language summary that governs the in-app
 * experience. The canonical, legally-operative version is the one published at
 * `canonicalUrl`; every document says so, and the screen offers an external
 * browser handoff to reach it. Keep `effectiveDate` in step with the published
 * document whenever this copy changes.
 */

export type LegalDocumentKey = "terms" | "privacy" | "guidelines" | "cookies" | "licenses";

export type LegalSection = {
  heading: string;
  paragraphs: string[];
};

export type LegalDocument = {
  key: LegalDocumentKey;
  /** Row and page title. */
  title: string;
  /** One-line description used on the index. */
  blurb: string;
  /** Human-readable effective date, shown at the top of the document. */
  effectiveDate: string;
  /** Canonical published version, opened in the external browser. */
  canonicalUrl: string;
  sections: LegalSection[];
};

/* -------------------------------------------------------------------------- */
/*                          Open-source dependencies                           */
/* -------------------------------------------------------------------------- */

export type OpenSourceDependency = {
  name: string;
  version: string;
  license: string;
  /** What PulseSoc actually uses it for — an acknowledgement, not a manifest. */
  purpose: string;
};

/**
 * The significant third-party packages PulseSoc ships, with the licence each is
 * distributed under. Versions mirror `package.json`; this list is shared by the
 * About screen's acknowledgements and the Licences legal document so the two can
 * never disagree.
 */
export const OPEN_SOURCE_DEPENDENCIES: OpenSourceDependency[] = [
  { name: "React", version: "19.1.0", license: "MIT", purpose: "UI runtime and component model." },
  { name: "React Native", version: "0.81.5", license: "MIT", purpose: "Draws the app's interface on your phone." },
  { name: "Expo", version: "54.0.36", license: "MIT", purpose: "Device features, build tooling, and app updates." },
  { name: "React Navigation", version: "6.1.18", license: "MIT", purpose: "Stack and tab navigation." },
  { name: "@react-native-async-storage/async-storage", version: "2.2.0", license: "MIT", purpose: "On-device cache and preference snapshots." },
  { name: "react-native-gesture-handler", version: "2.28.0", license: "MIT", purpose: "Recognises taps, swipes, and other gestures." },
  { name: "react-native-screens", version: "4.16.0", license: "MIT", purpose: "Screen containers used when you move between screens." },
  { name: "react-native-safe-area-context", version: "5.6.2", license: "MIT", purpose: "Safe-area insets across notches and home bars." },
  { name: "react-native-svg", version: "15.12.1", license: "MIT", purpose: "Vector drawing." },
  { name: "react-native-qrcode-svg", version: "6.3.21", license: "MIT", purpose: "Profile and share QR codes." },
  { name: "@expo/vector-icons", version: "15.1.1", license: "MIT", purpose: "Ionicons and related icon sets." },
  { name: "expo-av", version: "16.0.8", license: "MIT", purpose: "Audio and video playback." },
  { name: "expo-camera", version: "17.0.10", license: "MIT", purpose: "Camera capture for posts, reels, and stories." },
  { name: "expo-notifications", version: "0.32.17", license: "MIT", purpose: "Push notification delivery and handling." },
  { name: "expo-secure-store", version: "15.0.8", license: "MIT", purpose: "Keychain/Keystore storage for session credentials." },
  { name: "@egjs/hammerjs", version: "2.0.17", license: "MIT", purpose: "Gesture primitives used by gesture-handler on web." },
  { name: "nullthrows", version: "1.1.1", license: "MIT", purpose: "Null-assertion helper used by the React Native toolchain." },
  { name: "@babel/runtime", version: "7.29.7", license: "MIT", purpose: "Shared helpers emitted by the compiler." },
  { name: "react-native-web", version: "0.21.2", license: "MIT", purpose: "Web target used for QA and previews." }
];

const MIT_NOTICE =
  "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, subject to the inclusion of the above copyright notice and this permission notice in all copies or substantial portions of the Software. The Software is provided “as is”, without warranty of any kind.";

const APACHE_NOTICE =
  "Licensed under the Apache License, Version 2.0. You may obtain a copy of the licence at apache.org/licenses/LICENSE-2.0. Unless required by applicable law or agreed to in writing, software distributed under the licence is distributed on an “as is” basis, without warranties or conditions of any kind, either express or implied.";

/* -------------------------------------------------------------------------- */
/*                                 Documents                                   */
/* -------------------------------------------------------------------------- */

const TERMS: LegalDocument = {
  key: "terms",
  title: "Terms of Service",
  blurb: "The agreement between you and PulseSoc for using the app.",
  effectiveDate: "1 March 2026",
  canonicalUrl: "https://pulsesoc.com/legal/terms",
  sections: [
    {
      heading: "1. Agreement to these terms",
      paragraphs: [
        "By creating a PulseSoc account or using the PulseSoc app, you agree to these Terms of Service. If you do not agree, do not use the service.",
        "We may update these terms as the product changes. Material changes are announced in the app at least 30 days before they take effect, and continuing to use PulseSoc after that date means you accept the revised terms."
      ]
    },
    {
      heading: "2. Who may use PulseSoc",
      paragraphs: [
        "You must be at least 13 years old, or the minimum age of digital consent in your country if that is higher, to hold an account.",
        "You must provide accurate registration information, keep your account credentials secure, and remain responsible for everything that happens under your account. One person may hold multiple accounts, but accounts created to evade an enforcement action are not permitted."
      ]
    },
    {
      heading: "3. Your content",
      paragraphs: [
        "You keep ownership of everything you post. Posting does not transfer copyright to us.",
        "To operate the service we need a limited licence: by posting, you grant PulseSoc a worldwide, non-exclusive, royalty-free licence to host, store, reproduce, adapt for display, and distribute your content solely for the purpose of running, improving, and promoting the service. This licence ends when you delete the content or your account, except for copies retained in backups for a limited period and copies others have lawfully re-shared.",
        "You are responsible for having the rights to everything you post, including music, images, and video you did not create yourself."
      ]
    },
    {
      heading: "4. Acceptable use",
      paragraphs: [
        "You agree not to use PulseSoc to break the law, to harass or endanger people, to distribute malware, to scrape the service without written permission, to circumvent rate limits or security controls, or to misrepresent your identity in order to deceive others.",
        "The Community Guidelines form part of these terms and describe what is and is not allowed in more detail."
      ]
    },
    {
      heading: "5. Enforcement and appeals",
      paragraphs: [
        "We may remove content, limit distribution, restrict features, or suspend or terminate an account that violates these terms or the Community Guidelines. Where the law allows and safety permits, we tell you what happened and which rule was applied.",
        "You can appeal any enforcement decision from the notice in your Activity inbox, or from Settings › Help. Appeals are reviewed by a person who was not involved in the original decision."
      ]
    },
    {
      heading: "6. Paid features",
      paragraphs: [
        "Some features are sold as subscriptions or one-off purchases. Prices, billing intervals, and renewal terms are shown before you buy.",
        "Purchases made through an app store are billed by that store and are governed by its refund policy; cancellations must be made in your app store account settings. Statutory withdrawal and refund rights are unaffected."
      ]
    },
    {
      heading: "7. Ending your use of PulseSoc",
      paragraphs: [
        "You may stop using PulseSoc and delete your account at any time from Settings › Account. Deletion is final after the grace period shown at the time you request it.",
        "We may suspend or end your access if you seriously or repeatedly breach these terms, if we are required to by law, or if continuing to provide the service to you would create a security or legal risk."
      ]
    },
    {
      heading: "8. Disclaimers and liability",
      paragraphs: [
        "PulseSoc is provided on an “as is” and “as available” basis. We do not warrant that the service will be uninterrupted, error-free, or that content posted by other people is accurate or lawful.",
        "To the fullest extent permitted by law, PulseSoc is not liable for indirect, incidental, or consequential losses, or for loss of profits, data, or goodwill. Nothing in these terms excludes liability that cannot lawfully be excluded, including liability for death or personal injury caused by negligence, or for fraud. Your statutory consumer rights are not affected."
      ]
    },
    {
      heading: "9. Disputes and governing law",
      paragraphs: [
        "These terms are governed by the laws of the jurisdiction stated in the published version of this document. If you are a consumer, you may also bring proceedings in the courts of your country of residence, and you keep the benefit of any mandatory consumer protections there.",
        "Before starting formal proceedings, please contact us through Settings › Help so we can try to resolve the issue directly."
      ]
    },
    {
      heading: "10. Contact",
      paragraphs: [
        "Questions about these terms can be sent from Settings › Help › Contact support, which reaches the same team as the published contact address."
      ]
    }
  ]
};

const PRIVACY: LegalDocument = {
  key: "privacy",
  title: "Privacy Policy",
  blurb: "What data PulseSoc collects, why, and the control you have over it.",
  effectiveDate: "1 March 2026",
  canonicalUrl: "https://pulsesoc.com/legal/privacy",
  sections: [
    {
      heading: "What we collect",
      paragraphs: [
        "Account data: the name, username, email address, and (if you add one) phone number you register with, plus your profile details and settings.",
        "Content data: the posts, reels, stories, comments, messages, and media you create, along with the metadata needed to deliver them, such as timestamps and who they were shared with.",
        "Usage data: which screens you open, what you interact with, and diagnostic events such as crashes and slow requests. These are tied to your account so we can debug problems you report.",
        "Device data: device model, operating system version, app version, language, approximate region derived from your IP address, and a push notification token if you enable notifications.",
        "We do not collect precise location unless you explicitly attach a place to a post, and we do not read your contacts unless you turn on contact syncing."
      ]
    },
    {
      heading: "Why we use it",
      paragraphs: [
        "To operate the service — delivering your posts and messages, ranking your feed, and keeping your session signed in.",
        "To keep PulseSoc safe — detecting spam, scams, account takeovers, and content that breaks the Community Guidelines.",
        "To improve the product — understanding which features are used and where the app is slow or crashing.",
        "To communicate with you — security alerts, replies to support requests, and (only if you opt in) product announcements.",
        "Where the law requires a legal basis, we rely on performance of our contract with you for core service functions, legitimate interests for safety and product improvement, consent for optional features such as personalised advertising, and legal obligation where we must retain or disclose data."
      ]
    },
    {
      heading: "What we share",
      paragraphs: [
        "We do not sell your personal information.",
        "We share data with service providers who host, deliver, and secure the platform, under contracts that limit them to acting on our instructions. We share content with other users as directed by your own privacy settings. We disclose data to law enforcement only when we receive a valid legal request, and we publish the volume of such requests.",
        "If PulseSoc is ever involved in a merger or acquisition, we will tell you before your data becomes subject to a different privacy policy."
      ]
    },
    {
      heading: "How long we keep it",
      paragraphs: [
        "Content stays until you delete it. Deleted content is removed from the service immediately and purged from backups within 90 days.",
        "When you delete your account there is a grace period during which you can change your mind; after that, account data is deleted or irreversibly anonymised, except for records we are legally required to retain and a minimal record of enforcement actions used to prevent ban evasion."
      ]
    },
    {
      heading: "Your controls",
      paragraphs: [
        "Access and export: request a machine-readable copy of your data from Settings › Data and privacy. Exports are prepared asynchronously and delivered as a download link.",
        "Correction and deletion: edit your profile at any time, delete individual content, or delete the whole account from Settings › Account.",
        "Objection and restriction: turn off personalised advertising, analytics sharing, and crash reporting in Settings › Data and privacy.",
        "Complaints: if you are in a jurisdiction with a data protection authority, you have the right to complain to it. We would appreciate the chance to resolve the issue first."
      ]
    },
    {
      heading: "Security",
      paragraphs: [
        "Traffic between the app and our servers is encrypted in transit. Session credentials on your device are stored in the platform keychain or keystore, not in ordinary app storage.",
        "You can strengthen your account with two-factor authentication and biometric unlock in Settings › Security, and review every signed-in device in Settings › Security › Devices."
      ]
    },
    {
      heading: "Children",
      paragraphs: [
        "PulseSoc is not directed at children under 13, or under the local age of digital consent where that is higher. If we learn that we hold data from a child below that age, we delete the account and its data."
      ]
    },
    {
      heading: "International transfers",
      paragraphs: [
        "PulseSoc operates globally, so your data may be processed in countries other than your own. Where we transfer personal data out of the EEA or the UK, we rely on approved safeguards such as standard contractual clauses."
      ]
    }
  ]
};

const GUIDELINES: LegalDocument = {
  key: "guidelines",
  title: "Community Guidelines",
  blurb: "What is and isn't allowed on PulseSoc, and how we enforce it.",
  effectiveDate: "1 March 2026",
  canonicalUrl: "https://pulsesoc.com/legal/guidelines",
  sections: [
    {
      heading: "The short version",
      paragraphs: [
        "Post as though the people you are talking about can see it, because they usually can. Disagreement is fine; targeting people is not.",
        "These guidelines apply everywhere on PulseSoc — posts, reels, stories, comments, direct messages, live broadcasts, group content, marketplace listings, profile text, and usernames."
      ]
    },
    {
      heading: "Safety",
      paragraphs: [
        "No credible threats of violence, incitement to violence, or glorification of violent extremism.",
        "No content that encourages suicide or self-harm. Discussion of recovery and lived experience is allowed; instructions and encouragement are not. Searches on these topics surface local crisis resources.",
        "No sexual content involving minors, in any form, real or generated. This is reported to the relevant authorities and results in permanent removal.",
        "No coordinated harassment, no doxxing, and no sharing of intimate images without the consent of everyone in them."
      ]
    },
    {
      heading: "Authenticity",
      paragraphs: [
        "Do not impersonate another person, brand, or organisation. Parody and fan accounts are allowed when they are clearly labelled as such in both the name and bio.",
        "Do not buy, sell, or artificially inflate engagement, and do not run networks of accounts to make a viewpoint look more popular than it is.",
        "Synthetic media that realistically depicts a real person saying or doing something they did not must be labelled. Unlabelled deceptive synthetic media is removed."
      ]
    },
    {
      heading: "Respect",
      paragraphs: [
        "No hate speech: content that attacks or dehumanises people based on race, ethnicity, national origin, caste, religion, disability, disease, sexual orientation, gender, or gender identity.",
        "No adult sexual content in public feeds. Nudity in artistic, educational, medical, and breastfeeding contexts is permitted.",
        "Graphic violence is limited and age-gated when it has clear newsworthy or documentary value, and removed when it is shared for shock value."
      ]
    },
    {
      heading: "Marketplace and commerce",
      paragraphs: [
        "No listings for weapons, drugs, prescription medication, live animals, counterfeit goods, stolen property, or anything else restricted where the buyer or seller is located.",
        "Describe what you are actually selling. Deceptive listings, fake scarcity, and off-platform payment requests intended to defeat buyer protection are all prohibited."
      ]
    },
    {
      heading: "Reporting and enforcement",
      paragraphs: [
        "Report anything from the ••• menu on the post, profile, comment, or message. Reports are confidential — the person you report is not told who reported them.",
        "Consequences scale with severity and history: a warning, removal of the content, reduced distribution, a temporary feature restriction, or permanent removal of the account. Severe violations skip straight to removal.",
        "If you think we got it wrong, appeal from the notice in your Activity inbox. Appeals are reviewed by someone who was not part of the original decision."
      ]
    }
  ]
};

const COOKIES: LegalDocument = {
  key: "cookies",
  title: "Cookie & Tracking Notice",
  blurb: "The identifiers PulseSoc stores on your device and why.",
  effectiveDate: "1 March 2026",
  canonicalUrl: "https://pulsesoc.com/legal/cookies",
  sections: [
    {
      heading: "How this applies in the app",
      paragraphs: [
        "The PulseSoc mobile app does not use browser cookies for its own screens. It uses the equivalent on-device storage: a session credential held in the platform keychain or keystore, and an application cache held in local app storage.",
        "Cookies in the traditional sense only appear when you follow a link out of PulseSoc into your browser — for example the “Open full document” action on this screen. Those cookies are governed by the site you land on."
      ]
    },
    {
      heading: "Strictly necessary storage",
      paragraphs: [
        "Session credential — keeps you signed in between launches and lets the app refresh your session without asking for your password. Removed when you sign out.",
        "Preference snapshot — a local copy of your settings so Settings renders instantly and survives being offline.",
        "Content cache — recently viewed feed items, avatars, and media, so scrolling back does not re-download everything. You can clear this from Settings › Storage."
      ]
    },
    {
      heading: "Analytics and diagnostics",
      paragraphs: [
        "A rotating installation identifier lets us group crash reports and performance samples from the same device without identifying you personally.",
        "Analytics sharing and crash reporting can each be turned off in Settings › Data and privacy. Turning them off does not affect any feature of the app."
      ]
    },
    {
      heading: "Advertising identifiers",
      paragraphs: [
        "PulseSoc only uses your device advertising identifier if you have granted app tracking permission at the operating-system level and left personalised ads enabled in Settings › Data and privacy.",
        "With either of those off, you still see ads, but they are selected from coarse, non-personal signals such as the language your app is set to."
      ]
    },
    {
      heading: "Your choices",
      paragraphs: [
        "Clear the content cache from Settings › Storage.",
        "Turn off personalised ads, analytics, and crash reporting from Settings › Data and privacy.",
        "Revoke tracking permission entirely in your device's own privacy settings. PulseSoc honours that immediately."
      ]
    }
  ]
};

const LICENSES: LegalDocument = {
  key: "licenses",
  title: "Open-source licences",
  blurb: "The third-party software PulseSoc is built on, and its licence terms.",
  effectiveDate: "1 March 2026",
  canonicalUrl: "https://pulsesoc.com/legal/licenses",
  sections: [
    {
      heading: "Acknowledgements",
      paragraphs: [
        "PulseSoc is built on open-source software maintained by people who owe us nothing. The packages below are shipped inside this app; their copyright remains with their respective authors.",
        ...OPEN_SOURCE_DEPENDENCIES.map((dependency) => `${dependency.name} ${dependency.version} — ${dependency.license}`)
      ]
    },
    {
      heading: "MIT License",
      paragraphs: [
        "Applies to the packages listed above as MIT.",
        MIT_NOTICE
      ]
    },
    {
      heading: "Apache License 2.0",
      paragraphs: [
        "Applies to the packages listed above as Apache-2.0.",
        APACHE_NOTICE
      ]
    },
    {
      heading: "Full licence texts",
      paragraphs: [
        "The complete, unabridged licence text for every dependency — including transitive dependencies not listed here — is published alongside each release. Open the full document to read it."
      ]
    }
  ]
};

export const LEGAL_DOCUMENTS: Record<LegalDocumentKey, LegalDocument> = {
  terms: TERMS,
  privacy: PRIVACY,
  guidelines: GUIDELINES,
  cookies: COOKIES,
  licenses: LICENSES
};

/** Index order — the order these appear on the Legal screen. */
export const LEGAL_DOCUMENT_ORDER: LegalDocumentKey[] = ["terms", "privacy", "guidelines", "cookies", "licenses"];

/** Narrow an untrusted route param (deep links can carry anything) to a key. */
export function isLegalDocumentKey(value: unknown): value is LegalDocumentKey {
  return typeof value === "string" && value in LEGAL_DOCUMENTS;
}
