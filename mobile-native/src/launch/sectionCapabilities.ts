/**
 * What each Business OS section can actually do, in the operator's words.
 *
 * WHY THIS EXISTS
 *
 * `readiness.ts` locks whole sections. That answers "can I open Events?" but
 * not the more common case: a section that opens, works, and is still missing
 * pieces a user would reasonably look for. Store is real — listings, stock,
 * revenue — and has no view count. Insights is real and cannot measure reply
 * rate. Left unsaid, those gaps read as an empty state, which reads as "I have
 * no sales" rather than "PulseSoc does not count this yet".
 *
 * So each section names its own capabilities, and the launch gate says which of
 * them are live. The landing shows both lists: what works now, and what is
 * coming. Nothing is hidden and nothing is invented.
 *
 * WHERE THE STATE IS
 *
 * Not here. Every capability's state comes from `readinessOf`, off the one
 * table in `readiness.ts`, which is also where the evidence for each locked row
 * is written down. This file holds only labels — the same split the section
 * registry in `api/businessOs.ts` already has with that table, for the same
 * reason: the audit's verdict and the product's copy change on different days
 * and for different reasons.
 *
 * A capability with no row in that table is READY, exactly like a section with
 * no row. So the way to ship one is to delete its row, not to edit this file.
 *
 * WHAT GOES IN A LIST
 *
 * Capabilities, not features of the UI. "Search your listings" is one; "the
 * search box" is not. Every entry in an "Available now" list was verified
 * against the screen and the endpoint behind it, and every entry in a "Coming
 * next" list has its reason recorded next to its row in `readiness.ts`.
 */

import type { BusinessOsSectionKey } from "../api/businessOs";
import { capabilityModuleId, isLaunchGated, readinessOf, type LaunchModuleId, type ReadinessState } from "./readiness";

export type SectionCapability = {
  /** Unique inside its section. Forms `business:<section>.<key>`. */
  key: string;
  label: string;
  /** One line saying what the operator gets. Never how it is built. */
  blurb: string;
};

export type SectionOverview = {
  /**
   * Why this section exists, in two sentences at most. Longer than the grid
   * tile's blurb because the landing is where someone reads rather than scans.
   */
  purpose: string;
  capabilities: SectionCapability[];
};

/** A capability with the gate's verdict attached. */
export type ResolvedCapability = SectionCapability & {
  id: LaunchModuleId;
  state: ReadinessState;
};

/**
 * Ordered so that reading a section's list top to bottom describes the job,
 * not the screen layout. The split into available and upcoming happens at
 * render time from the gate, so a capability keeps its place in the story when
 * it ships.
 */
const BUSINESS_OS_CAPABILITIES: Readonly<Record<BusinessOsSectionKey, SectionOverview>> = Object.freeze({
  dashboard: {
    purpose:
      "Everything happening across your business in one place, and the way in to every section below.",
    capabilities: []
  },

  profile: {
    purpose:
      "Your public face. This is what a buyer sees before they decide whether to buy from you, and what is still missing from it.",
    capabilities: [
      { key: "details", label: "Business details", blurb: "Name, handle, category, location and contact, saved as you edit." },
      { key: "hours", label: "Opening hours and links", blurb: "The hours and website shown on your public page." },
      { key: "hoursEditor", label: "Edit your hours", blurb: "Set opening hours day by day without leaving the profile." },
      { key: "completeness", label: "Profile completeness", blurb: "What is still missing, and what to fill in next." },
      { key: "verification", label: "Verification badge", blurb: "Your current verification state, from the same source buyers see." },
      { key: "preview", label: "View as a buyer", blurb: "Your business exactly as someone visiting it sees it." },
      { key: "counts", label: "Listings, orders and followers", blurb: "Live counts of what you have on sale and who follows you." },
      { key: "sync", label: "Live sync", blurb: "Every edit saved and confirmed, and queued when you are offline." },
      { key: "rating", label: "Seller rating", blurb: "Your star rating and how many buyers left one." },
      { key: "traffic", label: "Profile and store traffic", blurb: "Profile views, store clicks and new followers over time." },
      { key: "serviceStats", label: "Response and dispatch record", blurb: "How fast you reply and how often you ship on time." },
      { key: "linkedEvents", label: "Events on your profile", blurb: "The events you host, shown to buyers on your page." }
    ]
  },

  store: {
    purpose:
      "Your storefront and everything in it. Add and edit listings, keep stock right, and see what the store is earning.",
    capabilities: [
      { key: "inventory", label: "Your listings", blurb: "Every listing by state — active, pending, low stock, out of stock, hidden." },
      { key: "editor", label: "Edit a listing", blurb: "Open any listing to change price, stock, photos or description." },
      { key: "performance", label: "Store performance", blurb: "Revenue, total sales, average order value and active listings." },
      { key: "attention", label: "Stock warnings", blurb: "Listings that are low or out of stock, flagged before a buyer finds out." },
      { key: "search", label: "Find a listing", blurb: "Search your own catalogue by title, price or listing ID." },
      { key: "checklist", label: "Setup checklist", blurb: "The steps left before your store is ready to sell." },
      { key: "views", label: "Storefront views", blurb: "How many people looked at your store, day by day." },
      { key: "rating", label: "Seller rating", blurb: "Your star rating and review count, shown next to your store." },
      { key: "dispatch", label: "On-time dispatch", blurb: "The share of orders you sent out by their deadline." },
      { key: "shipToday", label: "Ship-today queue", blurb: "Orders that have to leave today, ordered by deadline." },
      { key: "stockTracking", label: "Stock tracking on or off", blurb: "Mark a listing as unlimited instead of counting units." },
      { key: "pauseStore", label: "Pause the whole store", blurb: "Take every listing off sale at once while you are away." }
    ]
  },

  marketplace: {
    purpose:
      "The marketplace from both sides. List an item and manage what you are selling, or browse and buy from everyone else.",
    capabilities: [
      { key: "selling", label: "What you are selling", blurb: "Active, reserved, sold, drafts, pending review, expired and archived, with counts." },
      { key: "offers", label: "Offers inbox", blurb: "Accept, decline or counter what buyers offer you." },
      { key: "listing", label: "List an item", blurb: "Put something new on the marketplace." },
      { key: "browse", label: "Browse and search", blurb: "The buying feed, by category or by search." },
      { key: "cart", label: "Cart", blurb: "Collect items from different sellers and check out." },
      { key: "saved", label: "Saved items", blurb: "Keep anything you are still thinking about." },
      { key: "city", label: "Your city", blurb: "Set which area the buying feed shows you." },
      { key: "boost", label: "Boost a listing", blurb: "Pay to put one of your listings in front of more buyers." },
      { key: "listingStats", label: "Views, saves and offers per listing", blurb: "How much attention each item is getting." },
      { key: "soldRevenue", label: "What you sold this month", blurb: "Your marketplace takings, totalled." },
      { key: "savedSearches", label: "Saved searches", blurb: "Save a search and hear about anything new that matches." },
      { key: "distance", label: "How far away an item is", blurb: "Distance from you, shown without exposing anyone's address." },
      { key: "ratings", label: "Buyer and seller ratings", blurb: "Rate someone after a sale, and see their rating before one." },
      { key: "meetupSpots", label: "Saved meetup spots", blurb: "Public places you are happy to hand items over." }
    ]
  },

  advertising: {
    purpose:
      "Reach beyond your followers. Set up an ad account, run campaigns against a budget, and watch what they spend.",
    capabilities: [
      { key: "accounts", label: "Ad accounts", blurb: "Create an ad account and switch between the ones you have." },
      { key: "campaigns", label: "Campaigns", blurb: "Every campaign with its current delivery state." },
      { key: "budgets", label: "Budgets", blurb: "What each campaign is allowed to spend." },
      { key: "controls", label: "Pause and resume", blurb: "Stop or restart a campaign as it runs." },
      { key: "spend", label: "Spend over time", blurb: "What your account has spent, charted." },
      { key: "wallet", label: "Ad wallet", blurb: "The balance campaigns draw from." },
      { key: "eligibility", label: "Delivery eligibility", blurb: "The verification an account needs before ads can run." },
      { key: "composer", label: "Create an ad here", blurb: "Build and submit a new ad without leaving the app." },
      { key: "attribution", label: "Sales from your ads", blurb: "Which orders came from which campaign." }
    ]
  },

  orders: {
    purpose:
      "Every order, from both sides. What buyers have placed with you, and what you have bought from other sellers.",
    capabilities: [
      { key: "queue", label: "Orders placed with you", blurb: "What sold, for how much, and where each order has got to." },
      { key: "attention", label: "Waiting on you", blurb: "The orders that need something from you before anything else happens." },
      { key: "timeline", label: "Order timeline", blurb: "Paid, shipped and delivered, per order." },
      { key: "buying", label: "What you have bought", blurb: "Your own orders, with tracking and a way to buy again." },
      { key: "payoutLink", label: "Payout for an order", blurb: "Jump from an order to the payout it belongs to." },
      { key: "fulfilment", label: "Mark packed, shipped or handed over", blurb: "Move an order along as you work through it." },
      { key: "shipBy", label: "Ship-by deadlines", blurb: "A real deadline per order, and a warning before it passes." },
      { key: "pickup", label: "Pickup scheduling", blurb: "Arrange a time and confirm the handover for collected orders." },
      { key: "escrow", label: "Escrow hold and release", blurb: "See a buyer's money held and released as the order completes." },
      { key: "perOrderPayout", label: "What you take home per order", blurb: "The amount left after fees, on the order itself." },
      { key: "returnWindow", label: "Return window", blurb: "How long a buyer still has to send something back." }
    ]
  },

  customers: {
    purpose:
      "The people who buy from you, kept together instead of scattered across orders and conversations.",
    capabilities: [
      { key: "records", label: "Customer records", blurb: "Everyone who has bought from you, in one list." },
      { key: "history", label: "What each one has bought", blurb: "A buyer's whole history with you at a glance." },
      { key: "segments", label: "Segments", blurb: "Group buyers — repeat, lapsed, high value — and act on a group." },
      { key: "notes", label: "Notes and tags", blurb: "Keep your own notes against a customer." }
    ]
  },

  messages: {
    purpose:
      "Your buyer conversations, separate from your personal inbox, with the listing or order each one is about attached.",
    capabilities: [
      { key: "inbox", label: "Buyer inbox", blurb: "Every conversation about your business, newest first." },
      { key: "threads", label: "Conversations", blurb: "Open a thread and reply." },
      { key: "live", label: "Live updates", blurb: "New messages arrive without a refresh." },
      { key: "filters", label: "Filters and search", blurb: "Narrow the inbox down, or search across it." },
      { key: "context", label: "Listing and order context", blurb: "What each conversation is about, on the conversation." },
      { key: "replyStats", label: "Your reply time", blurb: "How quickly you answer, and how much of your inbox you answer." },
      { key: "savedReplies", label: "Saved replies", blurb: "Answer the same question quickly, every time." },
      { key: "awayMode", label: "Away mode", blurb: "Tell buyers you are away, so nobody is left waiting." },
      { key: "typing", label: "Typing indicators", blurb: "See when the other person is writing." },
      { key: "offerExpiry", label: "Offer expiry", blurb: "A countdown on offers made in a conversation." }
    ]
  },

  insights: {
    purpose:
      "How the business is actually doing. Real numbers over a period you choose, and only the numbers PulseSoc can genuinely measure.",
    capabilities: [
      { key: "revenue", label: "Revenue and orders", blurb: "What you earned and sold, charted over time." },
      { key: "period", label: "Choose a period", blurb: "Compare a week, a month or a quarter." },
      { key: "sources", label: "Where sales come from", blurb: "Which parts of PulseSoc your buyers arrived through." },
      { key: "top", label: "Top performers", blurb: "The listings doing the most work." },
      { key: "followers", label: "New followers", blurb: "How your audience grew over the period." },
      { key: "export", label: "Export to CSV", blurb: "Take the numbers out for your own records." },
      { key: "tips", label: "Suggestions", blurb: "What the numbers suggest you do next." },
      { key: "storeViews", label: "Store views", blurb: "How many people saw your store, and how many bought." },
      { key: "dispatch", label: "On-time dispatch", blurb: "Your delivery record over the period." },
      { key: "replyRate", label: "Reply rate", blurb: "How much of your inbox you answered, and how fast." },
      { key: "offersAnswered", label: "Offers answered", blurb: "How many offers you responded to, and what came of them." },
      { key: "adAttribution", label: "Return on ad spend", blurb: "What your advertising earned back, per campaign." }
    ]
  },

  payments: {
    purpose:
      "The money. What you have earned, what is still clearing, and how it reaches your bank.",
    capabilities: [
      { key: "balance", label: "Your balance", blurb: "What is available now and what is still processing." },
      { key: "method", label: "Payout method", blurb: "Where your money goes, and whether it is ready to receive it." },
      { key: "withdraw", label: "Withdraw", blurb: "Send your available balance to your payout method." },
      { key: "history", label: "Payout history", blurb: "Every payout, when it left and what state it is in." },
      { key: "ledger", label: "Activity", blurb: "Every credit and debit that moved your balance." },
      { key: "wallet", label: "Ad wallet", blurb: "The separate balance your campaigns spend from." },
      { key: "rewards", label: "Rewards", blurb: "What you have earned outside sales." },
      { key: "instant", label: "Instant payout", blurb: "Take your balance now instead of waiting for the schedule." },
      { key: "escrow", label: "Escrow balance", blurb: "Money held for orders that have not completed yet." },
      { key: "statements", label: "Monthly statements", blurb: "A statement per month, ready to download." },
      { key: "taxDocuments", label: "Tax documents", blurb: "The end-of-year paperwork, generated for you." }
    ]
  },

  events: {
    purpose:
      "The events you run — workshops, live sales, pop-ups — and the people coming to them.",
    capabilities: [
      { key: "upcoming", label: "Upcoming events", blurb: "What you have scheduled and what is live right now." },
      { key: "past", label: "Past events", blurb: "What you have already run, and how it went." },
      { key: "drafts", label: "Drafts", blurb: "Events you have started and not published." },
      { key: "create", label: "Create an event", blurb: "Schedule a new event and publish it to your followers." },
      { key: "rsvp", label: "RSVPs and capacity", blurb: "Who is coming, and how many places are left." }
    ]
  },

  team: {
    purpose:
      "The people who help you run the business, and what each of them is allowed to do.",
    capabilities: [
      { key: "invites", label: "Invite people", blurb: "Bring someone into the business without sharing your login." },
      { key: "roles", label: "Roles and permissions", blurb: "Decide who can list, who can ship and who can see the money." },
      { key: "activity", label: "Who did what", blurb: "A record of the actions each person took." }
    ]
  },

  verification: {
    purpose:
      "Prove the business is real. Verification unlocks ad delivery and shows buyers a badge they can trust.",
    capabilities: [
      { key: "status", label: "Verification status", blurb: "Where your application has got to, across every track." },
      { key: "submit", label: "Apply for verification", blurb: "Start a business verification request." },
      { key: "documents", label: "Upload documents", blurb: "Send the paperwork that backs your application." },
      { key: "appeal", label: "Appeal a decision", blurb: "Respond if an application was rejected or needs more." },
      { key: "checklist", label: "What is still needed", blurb: "The steps left before you are verified." }
    ]
  },

  settings: {
    purpose:
      "Your account controls — sign-in, security, notifications, language, privacy and support.",
    capabilities: [
      { key: "account", label: "Account and security", blurb: "Profile, sign-in, active sessions and account health." },
      { key: "preferences", label: "Notifications and appearance", blurb: "What you are told about, how it looks, and in which language." },
      { key: "privacy", label: "Privacy and legal", blurb: "What is stored about you, and the terms you are on." },
      { key: "support", label: "Help and feedback", blurb: "Get help, or tell us what is wrong." },
      { key: "signOut", label: "Sign out", blurb: "On this device, or everywhere at once." },
      { key: "businessPreferences", label: "Business preferences", blurb: "Settings that belong to the business rather than to you." }
    ]
  }
});

/** The section's purpose line and full capability list, or undefined. */
export function businessOsSectionOverview(key: BusinessOsSectionKey): SectionOverview | undefined {
  return BUSINESS_OS_CAPABILITIES[key];
}

/** Every capability of a section, each carrying the gate's verdict. */
export function businessOsSectionCapabilities(key: BusinessOsSectionKey): ResolvedCapability[] {
  const overview = BUSINESS_OS_CAPABILITIES[key];
  if (!overview) return [];
  return overview.capabilities.map((capability) => {
    const id = capabilityModuleId(key, capability.key);
    return { ...capability, id, state: readinessOf(id) };
  });
}

/**
 * The two lists the landing renders, in registry order within each.
 *
 * Split here rather than in the screen so the ordering rule — story order, not
 * state order — has one owner, and so a capability shipping is a deletion in
 * `readiness.ts` and nothing else.
 */
export function businessOsSectionLists(key: BusinessOsSectionKey) {
  const capabilities = businessOsSectionCapabilities(key);
  return {
    available: capabilities.filter((capability) => capability.state === "READY"),
    upcoming: capabilities.filter((capability) => capability.state !== "READY")
  };
}

/**
 * Whether tapping this section's card opens the landing rather than the section
 * itself.
 *
 * The rule is "only where something is locked": a section every part of which
 * works keeps opening its real screen directly, because a landing in front of
 * a finished feature is a page of text between the user and their work.
 *
 * Deliberately keyed on the capability list rather than on the section's own
 * gate row. A section that is gated but whose capabilities nobody has written
 * down yet has nothing to put on a landing, so it falls through to the Coming
 * Soon message — the behaviour before this existed — instead of opening an
 * empty page.
 */
export function businessOsSectionHasLanding(key: BusinessOsSectionKey): boolean {
  return businessOsSectionCapabilities(key).some((capability) => isLaunchGated(capability.id));
}
