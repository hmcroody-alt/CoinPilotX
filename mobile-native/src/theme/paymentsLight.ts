/**
 * Light palette for the Payments money hub (card #8 of the Business "Sections"
 * grid).
 *
 * Like `adsLight`, this extends `storeLight` rather than forking it — every
 * neutral (page, card, hairline, header navy, muted text, tap targets, radii,
 * spacing) is inherited so Payments reads as the same family as Store,
 * Marketplace and Advertising. Only the money-specific semantics live here.
 *
 * The colour system on this screen is not decoration; it is a claim about the
 * state of the seller's money, and each hue means exactly one thing:
 *
 *   • green  → cleared income          (money that is theirs, settled)
 *   • violet → held / escrow           (theirs, not yet released — NOT a loss)
 *   • blue   → movement to their bank  (a payout in transit)
 *   • amber  → ad spend                (money leaving for advertising)
 *   • red    → refunds and disputes    (money going back to a buyer)
 *
 * Two consequences follow, and both are enforced by how the tokens are shaped
 * rather than left to a reviewer's memory.
 *
 * First, **colour is never the only signal.** Every token that encodes a state
 * is exported alongside the word that state is called (`LEDGER_KIND_WORD`), and
 * the components are built to render both. A seller with a colour-vision
 * difference, or a screen-reader user, receives the same information from the
 * text that a sighted user receives from the hue.
 *
 * Second, **violet is deliberately not red and deliberately not grey.** An
 * escrow hold is the most misreadable state on the screen: it is the seller's
 * money, sitting still. Rendering it in a warning colour tells them something
 * is wrong, and rendering it in the neutral outflow ink tells them it is gone.
 * It gets its own hue and its own word, "held", and the amount carries no sign.
 */

import { storeLight } from "./storeLight";

/**
 * The "Pay out now" call-to-action fill.
 *
 * The reference design specifies a yellow gradient. Shipping that verbatim on a
 * commerce surface lands close to a well-known marketplace's trade dress, so —
 * exactly as `STORE_CTA` and `ADS_CTA` already decided for their own screens —
 * the default here is PulseSoc's own green, and the reference yellow is kept
 * beside it so the swap is a one-line change:
 *
 *     export const PAYMENTS_CTA = PAYMENTS_CTA_REFERENCE;
 *
 * `text` travels with the fill so contrast stays correct either way. This is
 * the single swappable trade-dress token the mission asks for; there is no
 * other yellow anywhere in this file, so changing this constant changes the
 * screen's trade dress completely and changes nothing else.
 */
export const PAYMENTS_CTA_PULSESOC = {
  from: "#2EE6A8",
  to: "#22C48D",
  text: "#04231A"
} as const;

/** The reference design's yellow. Kept so the swap above is a one-line change. */
export const PAYMENTS_CTA_REFERENCE = {
  from: "#FFD814",
  to: "#F7CA00",
  text: "#0F1111"
} as const;

export const PAYMENTS_CTA = PAYMENTS_CTA_PULSESOC;

/**
 * The five ledger row types, each as a circle fill/border pair plus the colour
 * its amount is written in.
 *
 * `amount` is the load-bearing field. Note that `payout` and `spend` both take
 * the neutral ink rather than their own hue: an outflow is an outflow, and
 * tinting the number as well as the icon would make a routine bank transfer
 * look like an alert. Only income earns a coloured amount (it is good news, and
 * the green is the same success green the rest of the app uses), and only
 * escrow earns a coloured amount for the opposite reason — it needs to not read
 * as either an inflow or an outflow.
 */
export const LEDGER_KIND_COLOR = {
  /** Cleared income — green. */
  income: {
    circleBg: "#EEF7F1",
    circleBorder: "#BFE0D3",
    amount: "#067D62"
  },
  /** Ad spend — amber. */
  spend: {
    circleBg: "#FDF6F0",
    circleBorder: "#F0D8B6",
    amount: storeLight.text.primary
  },
  /** Held in escrow — violet. Unsigned amount; see the module docstring. */
  escrow: {
    circleBg: "#F6F3FB",
    circleBorder: "#DDD2F0",
    amount: "#6B4FA3"
  },
  /** Movement to the seller's bank — blue. */
  payout: {
    circleBg: "#EEF3F8",
    circleBorder: "#CFDEEA",
    amount: storeLight.text.primary
  },
  /** Refunds and disputes — red. */
  refund: {
    circleBg: "#FDF3F3",
    circleBorder: "#ECCFCF",
    amount: storeLight.text.primary
  },
  /**
   * An entry type the server did not recognise.
   *
   * This exists so an unknown row can still render honestly. The alternative —
   * mapping anything unfamiliar onto, say, `income` — would paint a guess in a
   * colour that asserts a direction, and on a money screen a confident wrong
   * answer is worse than a plain one. Neutral circle, neutral ink, no sign.
   */
  other: {
    circleBg: storeLight.bg.strip,
    circleBorder: storeLight.border.hairline,
    amount: storeLight.text.primary
  }
} as const;

/**
 * The word each row type is called, in the seller's language.
 *
 * Exported next to the colours on purpose: these two constants are the pair
 * that makes "colour is never the only signal" true. If a component renders a
 * `LEDGER_KIND_COLOR` entry it should be rendering the matching
 * `LEDGER_KIND_WORD` too, and the accessibility label speaks the word.
 */
export const LEDGER_KIND_WORD = {
  income: "Income",
  spend: "Ad spend",
  escrow: "Held",
  payout: "Payout",
  refund: "Refund",
  other: "Ledger entry"
} as const;

export type LedgerKindToken = keyof typeof LEDGER_KIND_COLOR;

export const paymentsLight = {
  bg: {
    /** Inherited neutrals — same page/card/header family as Store and Ads. */
    page: storeLight.bg.page,
    card: storeLight.bg.card,
    headerFrom: storeLight.bg.headerFrom,
    headerTo: storeLight.bg.headerTo,
    strip: storeLight.bg.strip,
    warning: storeLight.bg.warning,
    skeleton: storeLight.bg.skeleton,
    /**
     * The escrow balance card's surface — a violet so faint it barely reads as
     * a tint, which is the point. It should distinguish the card from its
     * neighbours without dressing held money up as an alert.
     */
    escrowCard: "#FBFAFD"
  },
  border: {
    hairline: storeLight.border.hairline,
    secondaryButton: storeLight.border.secondaryButton,
    warning: storeLight.border.warning,
    /** The escrow card's edge, one step darker than its fill. */
    escrowCard: "#E6E0F2"
  },
  text: {
    primary: storeLight.text.primary,
    muted: storeLight.text.muted,
    link: storeLight.text.link,
    linkActive: storeLight.text.linkActive,
    onDark: storeLight.text.onDark,
    onDarkMuted: storeLight.text.onDarkMuted
  },
  status: {
    success: storeLight.status.success,
    warning: storeLight.status.warning,
    error: storeLight.status.error,
    neutral: storeLight.status.neutral
  },
  /**
   * The hero balance, which sits on the navy header.
   *
   * `amount` is white rather than the green income colour deliberately. The
   * hero is a *balance*, not a transaction — it has no direction — and giving
   * it the inflow colour would imply money just arrived. Colour on this screen
   * is reserved for things that moved.
   *
   * `unavailable` is the colour of the em dash shown when a balance fetch
   * fails. It is muted, not red: a failed read is not a financial event, and an
   * alarming dash would suggest something happened to the money.
   */
  hero: {
    amount: storeLight.text.onDark,
    label: storeLight.text.onDarkMuted,
    subline: storeLight.text.onDarkMuted,
    unavailable: storeLight.text.onDarkMuted,
    /** The pinging dot beside "next payout" — only while genuinely scheduled. */
    scheduledDot: "#4FC3F7",
    /** A caption stating the figure is cached, e.g. "as of 09:14". */
    staleLabel: "#FFB74D"
  },
  /**
   * The secondary balance cards under the hero: Processing, Escrow, Ad wallet.
   * Each is a value plus a label; the accent tints only the label and any
   * indicator, never the figure, for the same reason the hero's figure is white.
   */
  balance: {
    processingAccent: "#2B6DA8",
    escrowAccent: "#6B4FA3",
    adWalletAccent: "#F0A93B",
    label: storeLight.text.muted,
    value: storeLight.text.primary
  },
  /**
   * The payout-method card. `connected` and `incomplete` are states of a real
   * Stripe connection; there is no "bank" state because this platform stores no
   * bank account number — see PAYMENTS_MOCK_DATA_GAPS in api/paymentsHub.ts.
   */
  payoutMethod: {
    connected: storeLight.status.success,
    incomplete: storeLight.status.warning,
    missing: storeLight.status.error,
    /** The masked reference ("····9999"), which is a reference, not a number. */
    mask: storeLight.text.muted
  },
  /**
   * The refund / dispute action banner. Red-adjacent but soft: it needs to be
   * noticed without reading as a system failure, since the underlying event is
   * a normal part of selling.
   */
  refundBanner: {
    from: "#FDF3F3",
    to: "#FFFFFF",
    border: "#ECCFCF",
    heading: "#B12704",
    body: storeLight.text.primary,
    /** The shimmer that crosses the banner every few seconds. */
    shimmer: "rgba(177,39,4,0.06)"
  },
  /** Statement and tax-document tiles. Documents are paper: neutral, no hue. */
  document: {
    tileBg: storeLight.bg.strip,
    tileBorder: storeLight.border.hairline,
    icon: storeLight.text.muted,
    title: storeLight.text.primary,
    meta: storeLight.text.muted
  },
  ledger: {
    kind: LEDGER_KIND_COLOR,
    word: LEDGER_KIND_WORD,
    /** The sticky "Yesterday" / "12 March" header above each day group. */
    dayHeader: storeLight.text.muted,
    dayHeaderBg: storeLight.bg.page,
    /** Row meta line — reference and status word. */
    meta: storeLight.text.muted,
    /** A failed or reversed row keeps its amount and gains this status colour. */
    failedStatus: storeLight.status.error
  },
  cta: PAYMENTS_CTA,
  radius: {
    card: storeLight.radius.card,
    control: storeLight.radius.control,
    thumb: storeLight.radius.thumb,
    pill: storeLight.radius.pill,
    /** Ledger row icon circles. */
    iconCircle: 18
  },
  size: {
    thumb: storeLight.size.thumb,
    tapTarget: storeLight.size.tapTarget,
    /** Diameter of a ledger row's type circle. */
    iconCircle: 36
  },
  space: {
    gutter: storeLight.space.gutter,
    section: storeLight.space.section,
    card: storeLight.space.card
  },
  /**
   * Type for money figures.
   *
   * `fontVariant: ["tabular-nums"]` is not cosmetic here. Proportional digits
   * change width as a balance updates, so a figure that ticks from $1,199 to
   * $1,211 visibly jitters — on a number the seller is reading carefully, that
   * looks like instability in the amount rather than in the font. Tabular
   * figures also let a column of ledger amounts align on the decimal, which is
   * how anyone actually scans a list of transactions.
   */
  money: {
    hero: {
      fontSize: 30,
      fontWeight: "800" as const,
      fontVariant: ["tabular-nums"] as const,
      letterSpacing: -0.4
    },
    balance: {
      fontSize: 18,
      fontWeight: "700" as const,
      fontVariant: ["tabular-nums"] as const
    },
    row: {
      fontSize: 15,
      fontWeight: "600" as const,
      fontVariant: ["tabular-nums"] as const
    }
  }
} as const;

export type PaymentsLightTheme = typeof paymentsLight;
