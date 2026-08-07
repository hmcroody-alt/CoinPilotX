=====================================================================
PULSESOC ADVERTISING OS — SUPER MASTER ENGINEERING MISSION
=====================================================================
MISSION_NAME: PulseSoc Advertising OS — Marketplace Ads, Post Ads, Wallet, Campaigns, Audiences, Creative, Verification, Reporting, Policy, and Attribution
MISSION_GOAL: Transform the current Advertising screens into a complete native advertiser operating system where every visible control is wired, every deeper page exists, all financial and delivery states are server-authoritative, and no user reaches a placeholder, contradictory state, or dead end.
MISSION_SCOPE:

* Marketplace Ads
* Post Ads
* Advertising Account
* Ad Wallet
* Wallet & Billing
* Campaigns
* Ad Groups
* Ads
* Audiences
* Creative Library
* Business Verification
* Policy Center
* Reviews and Appeals
* Reports
* Conversion Attribution
* Notifications
* Staff and Permissions
* Support and Audit History

CORE_PRODUCT_LAW:

* Design and wire at the same time.
* Every visible item must lead to a complete native destination.
* Every destination must lead to its required subpages and workflows.
* Drafts must be allowed before verification.
* Campaign delivery must remain blocked until all required gates pass.
* Nothing is charged while a campaign is only a draft.
* Money, spend, verification, policy, and campaign states are server-owned.
* Unavailable systems must provide useful setup or preview pages.
* Never expose technical implementation language to advertisers.
* Never expose internal identifiers as primary customer-facing labels.
* Never show fake zero values after failed requests.
* Never leave clipped text, inert cards, or duplicate warnings in production.

=====================================================================
0. CURRENT SCREEN INSPECTION AND REQUIRED CORRECTIONS
=====================================================================
CURRENT_SCREEN: title: Advertising
visible_controls: - Back - Advertising title - Ad wallet summary - Wallet - Marketplace ads tab - Post ads tab - Advertising account row - Spend to date - Clicks to date - Cost per click - Spend last 7 days - Campaign area - Verify your business - Wallet & billing - Reports - Audiences - Creative Library - Create campaign
VISIBLE_DEFECTS:

* "Spend · to da..." is clipped.
* "Ad account 8" exposes an internal identifier too prominently.
* "Spend · last 7 days" does not actually show daily seven-day data.
* Cost per click uses a dash without a universal state standard.
* The campaign empty state provides verification but not a clear draft path.
* Audiences appears locked and unavailable.
* Creative Library appears locked and unavailable.
* Post Ads repeats the same unavailable explanation twice.
* Post Ads leaves most of the page empty.
* Verification state may contradict the separate Verification Center.
* Wallet and spend need one authoritative ledger.
* The page lacks Campaign, Ad Group, and Ad hierarchy.
* There is no visible Policy Center.
* There is no visible review or appeal history.
* Attribution is not represented.
* Staff and advertising permissions are not represented.

REQUIRED_COPY_CORRECTIONS: account_row: current: - "ROODY CHERIE Growth · Ad account 8" replace_with: - "ROODY CHERIE Growth" - "Advertising account · Active" internal_id_location: - Account details - Support information - Audit logs
spend_card: current: - "Spend · to da..." replace_with: - "Spend to date"
seven_day_card: current: - "Spend · last 7 days" - "Total spend to date. A day-by-day view isn’t available yet." rule: - Either implement real daily seven-day data - Or rename the card to "Account spend"
post_ads: current: - Repeated unavailable banners replace_with: - One useful Post Ads preview page - Early access - Eligibility preparation - Content-rights guidance
=====================================================================
1. GLOBAL ADVERTISING ARCHITECTURE
=====================================================================
ADVERTISING_OS: overview: - Account summary - Wallet summary - Campaign summary - Performance summary - Action center - Policy alerts - Verification alerts - Billing alerts
campaign_system: - Campaigns - Ad Groups - Ads - Products - Destinations - Objectives - Placements - Optimization - Budgets - Schedules
assets: - Audiences - Creative Library - Product Media - Post Media - Reel Media - Live Replay Media - Rights and Approvals
financial: - Ad Wallet - Deposits - Reserved Budget - Spend - Credits - Refunds - Invoices - Taxes - Payment Methods - Auto Refill - Spending Limits - Reconciliation
trust_and_policy: - Business Verification - Advertiser Eligibility - Product Eligibility - Creative Review - Account Health - Policy Center - Appeals - Suspensions - Audit Logs
measurement: - Impressions - Reach - Frequency - Clicks - Product Views - Saves - Messages - Add to Cart - Checkout - Purchases - Revenue - Refund-adjusted Revenue - Return on Ad Spend - Attribution
administration: - Staff - Roles - Permissions - Notifications - Account Settings - Support
=====================================================================
2. BACK BUTTON
=====================================================================
ON_TAP_BACK: behavior: - Return to previous Business OS destination. - Preserve selected ad product. - Preserve scroll position. - Preserve campaign filters. - Preserve wallet state. - Preserve unsaved drafts. - Do not remount the Advertising shell. - Do not discard in-progress campaign creation.
=====================================================================
3. ADVERTISING TITLE INTERACTION
=====================================================================
ADVERTISING_TITLE: single_tap_when_active: - Scroll to top.
double_tap_when_active: - Scroll to top. - Refresh Advertising account summary. - Refresh wallet summary. - Refresh campaign states. - Refresh performance metrics. - Refresh policy and verification alerts. - Prevent duplicate requests. - Preserve drafts and filters. - Provide subtle haptic feedback.
=====================================================================
4. AD WALLET HEADER CONTROL
=====================================================================
ON_TAP_AD_WALLET: destination: Ad Wallet Overview
AD_WALLET_OVERVIEW: summary: - Available balance - Pending funds - Reserved campaign budget - Promotional credits - Spend today - Spend this month - Automatic refill status - Wallet health
actions: - Add funds - Set up automatic refill - Change payment method - Set spending limits - View transactions - View invoices - View promotional credits - Resolve payment issue - Contact wallet support
WALLET_BALANCE_RULES:

* Balance comes from the advertising ledger.
* Never compute authoritative balance on the client.
* Never show a successful deposit before server confirmation.
* Never double-charge repeated taps.
* Use idempotency for every financial mutation.
* Preserve receipts and transaction references.
* Reconcile interrupted deposits and spend postings.
* Distinguish available, pending, reserved, held, and unavailable.

=====================================================================
5. ADD FUNDS
=====================================================================
ADD_FUNDS_FLOW: step_1_amount: options: - $10 - $25 - $50 - $100 - Custom
step_2_payment_method: - Saved card - New card - Business payment profile - Bank method when supported - Promotional credit where applicable
step_3_review: show: - Deposit amount - Fees - Tax - Payment source - New wallet balance estimate
step_4_authorize: requirements: - Idempotency key - Server confirmation - Duplicate-tap prevention - Retry-safe payment logic
step_5_result: states: - Completed - Pending - Failed - Requires verification - Payment method declined - Reconciliation required
success_actions: - View receipt - Return to wallet - Create campaign - Enable automatic refill
=====================================================================
6. AUTOMATIC REFILL
=====================================================================
AUTO_REFILL: fields: - Enable or disable - Balance threshold - Refill amount - Monthly maximum - Payment method - Failure notification preference - Pause campaigns on repeated refill failure
example: threshold: "$10" refill_amount: "$50" monthly_cap: "$200"
history: - Trigger date - Amount - Payment method - Result - Campaign impact - Failure reason
=====================================================================
7. SPENDING LIMITS
=====================================================================
SPENDING_CONTROLS: account_limits: - Daily maximum - Monthly maximum - Lifetime account cap
campaign_limits: - Campaign cap - Ad-group cap - Daily campaign cap
alerts: - Low wallet balance - High spend - Budget pacing issue - Refill failure - Limit reached
actions: - Pause campaigns at limit - Require approval for large budget changes - Notify owner - Notify billing manager
=====================================================================
8. WALLET TRANSACTION HISTORY
=====================================================================
WALLET_TRANSACTIONS: event_types: - deposit - authorization - campaign_reservation - campaign_spend - reservation_release - promotional_credit - refund - adjustment - chargeback - reversal - expired_credit
row_fields: - Type - Amount - Date - Status - Campaign - Balance after - Payment source - Reference
detail_page: - Full transaction timeline - Ledger postings - Related campaign - Invoice - Receipt - Support reference - Reconciliation state
=====================================================================
9. MARKETPLACE ADS TAB
=====================================================================
ON_TAP_MARKETPLACE_ADS: destination: Marketplace Ads Dashboard
MARKETPLACE_ADS_DASHBOARD: sections: - Overview - Campaigns - Products - Audiences - Creatives - Reports - Billing - Policy Center - Settings
ACTIVE_TAB_BEHAVIOR: single_tap: - Scroll to top.
double_tap: - Scroll to top. - Refresh Marketplace Ads data only. - Preserve selected date range and filters.
=====================================================================
10. POST ADS TAB
=====================================================================
POST_ADS_TEMPORARY_STATE: title: "Post Ads · Coming soon"
supported_future_content: - Feed posts - Reels - Live replays - Events - Business profile - Storefront - Creator collaboration content
useful_actions_now: - Join early access - Notify me when available - Verify business - Review advertising policies - Review eligible content - Prepare creative rights
current_capabilities: - Use the same Ad Wallet - Use the same Advertising Account - Prepare verification - Prepare approved media
prohibited: - Duplicate unavailable banners - Empty page with no actions - Active-looking production tab with no purpose
POST_ADS_ENABLED_STATE: dashboard: - Post Campaigns - Reel Campaigns - Live Replay Campaigns - Event Campaigns - Creator Collaboration Ads - Audiences - Creative Rights - Reports - Billing
content_eligibility_checks: - Ownership - Music advertising rights - Media rights - Collaborator approval - Active destination - Policy compliance - Age or regional restrictions
=====================================================================
11. ADVERTISING ACCOUNT ROW
=====================================================================
ON_TAP_ACCOUNT_ROW: destination: Advertising Account Settings
ADVERTISING_ACCOUNT_SETTINGS: sections: - Account name - Account status - Business owner - Connected Business Profile - Verification status - Currency - Time zone - Billing country - Payment methods - Spending limits - Staff access - Notifications - Policy history - Account ID - Support - Close account
ACCOUNT_STATES:

* Active
* Setup incomplete
* Verification required
* Under review
* Limited
* Payment issue
* Policy restricted
* Suspended
* Closed

STATUS_INDICATOR_RULE:

* Green means active and eligible.
* Amber means action required.
* Purple means under review.
* Red means restricted or suspended.
* Gray means inactive or closed.
* Status must include text, not color only.

=====================================================================
12. STAFF AND PERMISSIONS
=====================================================================
ADVERTISING_ROLES:

* Owner
* Administrator
* Campaign Manager
* Creative Manager
* Analyst
* Billing Manager
* Read Only

PERMISSIONS:

* campaigns.create
* campaigns.edit
* campaigns.submit
* campaigns.pause
* campaigns.resume
* campaigns.end
* campaigns.archive
* wallet.fund
* billing.manage
* audiences.manage
* creatives.manage
* reports.view
* reports.export
* policies.appeal
* staff.manage
* account.close

RULES:

* Campaign Manager cannot fund wallet unless separately permitted.
* Analyst cannot edit or submit campaigns.
* Billing Manager cannot edit creative or audience.
* Every permission change creates an audit event.

=====================================================================
13. SPEND TO DATE
=====================================================================
ON_TAP_SPEND_TO_DATE: destination: Lifetime Spend Report
LIFETIME_SPEND_REPORT: show: - Total spend - Marketplace Ads spend - Post Ads spend - Campaign breakdown - Product breakdown - Placement breakdown - Daily spend - Fees - Credits used - Refunds - Adjustments - Billing records
filters: - Date range - Campaign - Ad group - Product - Placement - Objective - Status
=====================================================================
14. CLICKS TO DATE
=====================================================================
ON_TAP_CLICKS_TO_DATE: destination: Click Performance Report
CLICK_REPORT: metrics: - Total clicks - Unique clicks - Invalid clicks removed - Click-through rate - Clicks by campaign - Clicks by product - Clicks by placement - Clicks by audience - Click trend
invalid_traffic_exclusions: - Internal preview traffic - Owner testing - Known bots - Repeated invalid clicks - Failed-delivery events - Confirmed fraudulent traffic
=====================================================================
15. COST PER CLICK
=====================================================================
COST_PER_CLICK_DISPLAY: confirmed_value: - "$0.00" or calculated amount
no_clicks: - "No clicks yet"
loading: - "Loading…"
unavailable: - "Unavailable"
not_applicable: - "Not applicable"
ON_TAP_COST_PER_CLICK: destination: Cost Efficiency Report
COST_EFFICIENCY_REPORT: metrics: - Average cost per click - Cost per product view - Cost per save - Cost per message - Cost per add to cart - Cost per purchase - Cost per storefront visit - Cost by campaign - Cost by placement - Trend over time
OBJECTIVE_AWARE_RULE:

* Message campaigns emphasize cost per conversation.
* Sales campaigns emphasize cost per purchase.
* Traffic campaigns emphasize cost per click or visit.
* Reach campaigns emphasize cost per thousand impressions.

=====================================================================
16. SPEND LAST 7 DAYS
=====================================================================
ON_TAP_SPEND_LAST_7_DAYS: destination: Spend Report default_range: Last 7 Days
SPEND_REPORT: date_ranges: - Today - 7 days - 30 days - 90 days - Custom - Compare previous period
show: - Daily spend chart - Total spend - Average daily spend - Remaining wallet balance - Spend by campaign - Spend by product - Spend by placement - Budget pacing - Promotional credits used - Refunds and adjustments
data_rules: - Show data freshness timestamp. - Distinguish finalized from estimated spend. - Preserve prior data during refresh. - Do not use lifetime totals under a seven-day heading.
=====================================================================
17. CAMPAIGN EMPTY STATE
=====================================================================
NO_CAMPAIGNS_STATE: title: "No campaigns yet"
body: - Campaigns begin as drafts. - Nothing is charged while a campaign remains a draft. - Nothing delivers until submission, approval, eligibility, and funding pass.
actions: primary: - Create campaign draft secondary: - Verify your business
rule: - Verification may block delivery. - Verification must not block draft creation.
=====================================================================
18. CAMPAIGN LIST
=====================================================================
CAMPAIGN_LIST: tabs: - All - Drafts - Under review - Scheduled - Active - Paused - Limited - Needs changes - Rejected - Completed - Archived
campaign_row: - Campaign name - Objective - Promoted item - Status - Budget - Spend - Results - Cost per result - Start date - End date - Review issue - Action required
quick_actions: - View - Edit - Pause - Resume - Duplicate - Increase budget - Extend schedule - End - Archive - Appeal
=====================================================================
19. BUSINESS VERIFICATION
=====================================================================
ON_TAP_VERIFY_YOUR_BUSINESS: destination: Advertising Verification
AUTHORITATIVE_VERIFICATION_STATES:

* Not started
* Draft
* Submitted
* Needs information
* Under review
* Approved
* Rejected
* Suspended
* Expired
* Revoked

VERIFICATION_DISPLAY_RULES: approved: replace_button_with: - "Business verified" - "View verification"
under_review: show: - Current status - Expected next step - Submitted date - Requested information prohibit: - Another Start Verification button
needs_information: show: - Exact missing item - Deadline - Replace document - Resubmit
rejected: show: - Exact reason - Corrective action - Appeal - Review history - Support reference
ADVERTISING_VERIFICATION_FLOW: step_1: - Confirm business identity
step_2: - Select advertiser type
advertiser_types: - Individual seller - Registered business - Organization - Creator business - Agency - Nonprofit
step_3: - Confirm business details
step_4: - Add billing information
step_5: - Submit required documents
possible_evidence: - Government ID - Business registration - Tax information - Business address - Website ownership - Brand authorization - Payment-method confirmation
step_6: - Confirm advertising contact
step_7: - Review declarations
step_8: - Submit
step_9: - Track status
CUSTOMER_COPY_RULE:

* Do not expose API paths.
* Do not expose backend implementation notes.
* Explain what is required, why, and what happens next.

=====================================================================
20. WALLET & BILLING CARD
=====================================================================
ON_TAP_WALLET_AND_BILLING: destination: Wallet & Billing
WALLET_AND_BILLING: sections: - Wallet overview - Add funds - Automatic refill - Payment methods - Spending limits - Transactions - Invoices - Tax information - Promotional credits - Billing contacts - Payment issues
PAYMENT_METHODS:

* Saved cards
* New card
* Bank account when supported
* Business payment profile
* Default payment source
* Backup payment source
* Expired method recovery
* Failed method recovery

INVOICES: row_fields: - Invoice number - Billing period - Spend - Credits - Taxes - Adjustments - Total charged - Payment status
actions: - View - Download - Email - Report issue
=====================================================================
21. REPORTS CARD
=====================================================================
ON_TAP_REPORTS: destination: Advertising Reports
ADVERTISING_REPORTS: sections: - Overview - Campaigns - Ad Groups - Ads - Products - Audiences - Placements - Search Terms - Conversions - Billing - Export History
metrics: - Spend - Impressions - Reach - Frequency - Clicks - Click-through rate - Cost per click - Product views - Storefront visits - Messages started - Saves - Add-to-cart events - Checkouts - Purchases - Revenue attributed - Refund-adjusted revenue - Return on ad spend
filters: - Date range - Campaign - Ad group - Ad - Product - Placement - Audience - Objective - Delivery status
exports: - CSV - PDF - Scheduled email - Saved report - Export history
reporting_requirements: - Data freshness timestamp - Attribution window - Metric definitions - Estimated or finalized label - Refund-adjusted revenue - Permission-aware access
=====================================================================
22. AUDIENCES CARD
=====================================================================
ON_TAP_AUDIENCES_BEFORE_FULL_LAUNCH: destination: Audiences Setup Preview
AUDIENCES_SETUP_PREVIEW: available_now: - Automatic audience - Location targeting - Radius - Age range - Language - Marketplace interests - Basic exclusions
coming_later: - Saved audiences - Retargeting - Customer lists - Similar audiences
actions: - Learn audience types - Create basic audience - Join early access - Review privacy rules
FULL_AUDIENCE_LIBRARY: tabs: - Automatic - Saved - Custom - Retargeting - Customer Lists - Similar - Exclusions - Archived
CREATE_AUDIENCE: fields: - Audience name - Location - Radius - Age range - Language - Interests - Marketplace behavior - Store visitors - Product viewers - Cart abandoners - Past customers - Engaged followers - Exclusions - Estimated audience size
TARGETING_SAFETY:

* Block targeting based on protected traits.
* Block highly sensitive personal data.
* Validate targeting server-side.
* Explain why an option is unavailable.
* Preserve user privacy and consent rules.

=====================================================================
23. CREATIVE LIBRARY CARD
=====================================================================
ON_TAP_CREATIVE_LIBRARY_BEFORE_FULL_LAUNCH: destination: Creative Library Preview
CREATIVE_LIBRARY_PREVIEW: available_now: - Product media - Uploaded ad media - Videos - Draft creatives - Rights status
actions: - Upload creative - Use product media - Review creative requirements - Join early access
FULL_CREATIVE_LIBRARY: tabs: - All - Images - Videos - Product Media - Post Media - Reel Media - Live Replay Media - Drafts - Approved - Rejected - Archived
creative_row: - Thumbnail - Name - Format - Dimensions - Duration - Rights status - Policy state - Campaign usage - Last updated
actions: - Upload - Create - Edit - Duplicate - Preview - Use in campaign - Archive - Delete
validation: - File format - Resolution - Aspect ratio - Duration - Text density - Misleading claims - Copyright - Music advertising rights - Sensitive content - Destination compatibility
=====================================================================
24. CREATE CAMPAIGN
=====================================================================
ON_TAP_CREATE_CAMPAIGN: destination: Campaign Creation
CAMPAIGN_CREATION: step_1_choose_promotion_source: marketplace_ads: - Marketplace product - Marketplace listing - Store product - Collection - Storefront - Event
post_ads_when_enabled:
  - Post
  - Reel
  - Live replay
  - Event
  - Business profile

rules:
  - Only active and eligible content may be selected.
  - Draft or restricted destinations cannot deliver.
  - Save draft remains available.
step_2_objective: options: - Increase product sales - Get Marketplace messages - Drive storefront visits - Promote an event - Increase listing views - Get more saves - Reach more buyers - Increase post engagement - Get video views
objective_controls:
  - Optimization
  - Reporting
  - Placements
  - CTA choices
  - Result metric
step_3_campaign_identity: fields: - Campaign name - Internal description - Campaign tags - Special category declaration
step_4_campaign_structure: model: Campaign: contains: - One or more Ad Groups
  Ad_Group:
    contains:
      - Audience
      - Placements
      - Schedule
      - Optimization
      - Budget allocation
      - One or more Ads

  Ad:
    contains:
      - Creative
      - Product or destination
      - Headline
      - Primary text
      - Call to action

first_campaign_ux:
  - Create one default Ad Group automatically.
  - Allow advanced users to add more.
step_5_audience: options: - Automatic audience - Saved audience - Create audience - Retargeting audience
basic_controls:
  - Location
  - Radius
  - Age range
  - Language
  - Interests
  - Marketplace behavior
  - Past store engagement
  - Exclusions

output:
  - Estimated audience range
  - Clear estimate disclaimer
step_6_placements: marketplace: - Marketplace home - Search results - Category pages - Product recommendations - Listing detail recommendations - Storefront discovery
social_when_enabled:
  - Homefeed
  - Reels
  - Live discovery
  - Event discovery

modes:
  - Automatic placements
  - Manual placements

rule:
  - Unavailable placements cannot be selected.
step_7_creative: product_ads: - Use product media - Upload custom image - Upload video - Headline - Primary text - Call to action - Destination
cta_options:
  - Shop now
  - View item
  - Make offer
  - Message seller
  - Visit store
  - Learn more
  - Reserve

preview:
  - Preview every selected placement.
  - Show mobile-native rendering.
  - Validate text and media fit.
step_8_budget: options: - Daily budget - Lifetime budget - Campaign spending cap - Ad-group allocation
display:
  - Wallet balance
  - Minimum budget
  - Estimated daily results
  - Estimated campaign duration
  - Budget warnings
  - Refill recommendation

rule:
  - Estimates are not guarantees.
step_9_schedule: options: - Start now - Schedule start - End date - Run continuously - Daypart schedule - Time zone
step_10_optimization: initial_options: - Automatic optimization - Maximize clicks - Maximize product views - Maximize messages - Maximize purchases - Maximize reach
future_advanced:
  - Cost cap
  - Bid cap
  - Target cost
  - Placement adjustment

rule:
  - Do not expose advanced bidding before the delivery engine supports it.
step_11_billing_source: options: - Use Ad Wallet - Add funds - Enable automatic refill - Apply promotional credit - Set campaign spending limit
insufficient_funds:
  - Allow Save Draft.
  - Block activation.
  - Explain required amount.
step_12_policy_precheck: checks: - Advertiser verification - Product eligibility - Listing availability - Creative rights - Claims - Destination - Restricted categories - Audience restrictions - Payment readiness - Regional eligibility
results:
  - Ready
  - Needs correction
  - Verification required
  - Payment required
  - Manual review required
  - Not eligible

behavior:
  - Every issue deep-links to the exact field.
step_13_review: show: - Campaign - Objective - Promoted item - Ad groups - Audience - Placements - Creative - Budget - Schedule - Wallet - Verification - Policy status - Estimated results
actions:
  - Save draft
  - Preview ads
  - Submit for review
  - Schedule after approval
step_14_submission_result: states: - Draft saved - Submitted - Under review - Approved - Scheduled - Needs changes - Rejected - Payment required - Verification required
=====================================================================
25. CAMPAIGN STATES
=====================================================================
CAMPAIGN_STATES:

* draft
* incomplete
* submitted
* under_review
* approved
* scheduled
* active
* paused
* limited
* needs_changes
* rejected
* completed
* cancelled
* archived

STATE_TRANSITION_RULE:

* Every transition is server-authoritative.
* Every transition is auditable.
* Every transition includes actor, timestamp, reason, and previous state.
* Financial delivery cannot begin before approval and funding.
* Client cannot mark campaign active independently.

=====================================================================
26. CAMPAIGN REJECTION AND APPEAL
=====================================================================
CAMPAIGN_REJECTION_PAGE: show: - Rejected component - Policy reason - Suggested correction - Review date - Review history - Support reference
actions: - Edit campaign - Replace creative - Change destination - Resubmit - Appeal - Contact support
APPEAL_FLOW:

* Review decision
* Add explanation
* Add evidence
* Submit appeal
* Track status
* View final decision

=====================================================================
27. CAMPAIGN DETAIL
=====================================================================
CAMPAIGN_DETAIL: tabs: - Overview - Performance - Ad Groups - Ads - Audience - Placements - Budget - Schedule - Billing - Review Status - Change History - Activity Log
overview: - Status - Objective - Budget - Spend - Results - Cost per result - Remaining schedule - Wallet health - Review warnings
actions: - Pause - Resume - Edit - Duplicate - Increase budget - Extend schedule - Replace creative - End campaign - Archive - Appeal
EDITING_RULES: safe_edits: - Campaign name - Internal notes - Labels
optimization_reset_edits: - Audience - Budget - Optimization - Placement - Creative
review_required_edits: - Product - Destination - Media - Claims - Primary text - Call to action
immutable_history: - Past spend - Past impressions - Posted ledger entries - Historical review decisions - Previous conversion events
=====================================================================
28. POLICY CENTER
=====================================================================
POLICY_CENTER: sections: - Account status - Campaign issues - Creative issues - Product eligibility - Restricted categories - Review history - Appeals - Advertising policies - Support cases
issue_detail: - Affected account, campaign, ad group, or ad - Reason - Severity - Delivery impact - Required correction - Deadline - Appeal eligibility - Support reference
rules: - Explain restrictions clearly. - Never hide whether funds are safe. - Never show only generic failure language. - Preserve immutable decision history.
=====================================================================
29. ATTRIBUTION
=====================================================================
ATTRIBUTION_FUNNEL:

* Ad impression
* Click
* Product view
* Save
* Message
* Add to cart
* Checkout
* Purchase
* Refund adjustment

ATTRIBUTION_SETTINGS:

* Click-through window
* View-through window
* Conversion source
* Cross-device rules
* Organic versus paid separation
* Refund adjustment
* Multi-touch reporting when supported

ATTRIBUTION_RULES:

* Refunded orders must reduce attributed revenue.
* Duplicate conversion events must be deduplicated.
* Internal previews do not count.
* Failed payments do not count as purchases.
* Attribution logic must be documented in reports.

=====================================================================
30. ADVERTISING NOTIFICATIONS
=====================================================================
ADVERTISING_NOTIFICATIONS: types: - Campaign approved - Campaign rejected - Campaign limited - Campaign budget exhausted - Wallet balance low - Automatic refill failed - Creative rejected - Verification needed - Campaign completed - Unusual spend detected - Invoice available - Policy appeal updated
deep_links: - Exact campaign - Exact creative - Exact billing issue - Exact verification request - Exact policy decision
badge_rule: - Use Advertising-specific unread count. - Do not reuse generic global 99+ without context.
=====================================================================
31. ERROR, EMPTY, LOADING, AND RESTRICTED STATES
=====================================================================
SCREEN_STATES:

* loading
* refreshing
* loaded_with_data
* loaded_empty
* partially_loaded
* verification_required
* payment_required
* permission_required
* offline
* retryable_error
* non_retryable_error
* restricted
* suspended

DISPLAY_STANDARD: "$0.00": meaning: - Confirmed real zero
"0": meaning: - Confirmed real zero count
"Loading…": meaning: - Request in progress
"No activity yet": meaning: - Valid empty result
"Unavailable": meaning: - Request failed
"Not configured": meaning: - Setup missing
"Restricted": meaning: - Access intentionally blocked
PROHIBITED_DISPLAY:

* Universal dash for unknown states
* Fake zero after service failure
* Full-screen blanking when cached data exists
* Generic error with no recovery action
* Active-looking unavailable controls

=====================================================================
32. BACKEND AND FINANCIAL INTEGRITY
=====================================================================
SERVER_AUTHORITY: server_owned: - Wallet balance - Campaign state - Spend - Budget reservation - Ledger entries - Verification state - Policy state - Delivery state - Conversion attribution - Credits - Refunds - Invoices
FINANCIAL_REQUIREMENTS:

* Double-entry or equivalent auditable ledger
* Idempotent deposits
* Idempotent campaign spend posting
* Idempotent refunds
* Reservation and release accounting
* Reconciliation jobs
* Immutable posted entries
* Compensating entries for corrections
* Support references
* Audit trail

ASYNC_JOBS:

* Spend reconciliation
* Wallet deposit reconciliation
* Credit expiration
* Invoice generation
* Campaign state reconciliation
* Policy review ingestion
* Attribution backfill
* Notification retry
* Dead-letter handling

=====================================================================
33. SHARED NATIVE COMPONENTS
=====================================================================
COMPONENTS:

* AdvertisingShell
* AdvertisingHeader
* AdvertisingProductTabs
* AdAccountStatusRow
* AdWalletPill
* AdvertisingMetricCard
* SpendChart
* CampaignList
* CampaignCard
* CampaignStatusBadge
* AdGroupCard
* AdCard
* AudienceCard
* CreativeCard
* PolicyIssueCard
* VerificationStatusCard
* BillingTransactionRow
* AdvertisingEmptyState
* AdvertisingErrorState
* AdvertisingSkeleton
* AdvertisingBottomAction
* AdvertisingActivityTimeline

VISUAL_STANDARD:

* Preserve current premium dark header and light content body.
* Fix all clipped text.
* Support Dynamic Type.
* Use consistent card height and spacing.
* Use status text and icon, not color alone.
* Keep CTA hierarchy clear.
* Avoid duplicate messages.
* Avoid empty dead-space pages.

=====================================================================
34. ACCESSIBILITY AND LOCALIZATION
=====================================================================
ACCESSIBILITY:

* VoiceOver labels
* Dynamic Type
* Minimum touch targets
* High contrast
* Reduced motion
* Color-independent status
* Keyboard support where relevant
* RTL support
* Long translated text
* Safe-area correctness

LOCALIZATION:

* Currency
* Number formatting
* Date formatting
* Time zone
* Billing country
* Language
* Regional advertising availability
* Tax display
* Legal policy availability

=====================================================================
35. TEST REQUIREMENTS
=====================================================================
AUTOMATED_TESTS: navigation: - Every visible control opens the correct route. - Back preserves state. - No control reaches a placeholder-only page.
wallet: - Add funds is idempotent. - Duplicate taps do not duplicate charges. - Failed payment does not change balance. - Pending payment remains pending. - Reconciliation resolves uncertain state safely.
campaigns: - Draft creation works before verification. - Verification blocks delivery, not drafting. - Insufficient funds blocks activation. - Campaign state transitions are server-owned. - Editing restricted fields triggers re-review. - Pause and resume preserve spend history.
audiences: - Sensitive targeting is rejected. - Saved audience loads correctly. - Estimate is labeled as an estimate.
creatives: - Rights validation works. - Unsupported media is blocked. - Music without ad rights is rejected. - Rejected creative provides actionable reason.
reporting: - Seven-day card uses seven-day data. - Refunds reduce attributed revenue. - Invalid clicks are excluded. - Data freshness is visible. - Export respects permissions.
verification: - Approved state hides Verify button. - Under-review state does not restart application. - Needs-information deep-links to exact requirement. - Rejected state supports appeal.
permissions: - Campaign Manager cannot fund wallet without permission. - Analyst cannot mutate campaigns. - Billing Manager cannot edit creative. - Permission changes are audited.
accessibility: - No clipped metric titles. - Large text remains usable. - Every icon has a label. - Status does not rely on color alone.
=====================================================================
36. IMPLEMENTATION ORDER
=====================================================================
PHASE_0_FOUNDATION:

* Canonical Advertising Account
* Campaign
* Ad Group
* Ad
* Audience
* Creative
* Wallet
* Ledger
* Policy
* Verification
* Attribution IDs
* Shared state model
* Audit events
* Permissions
* Feature flags

PHASE_1_SCREEN_CORRECTIONS:

* Fix clipped spend title
* Correct account identity row
* Correct seven-day data card
* Remove duplicate Post Ads notices
* Add useful locked-feature destinations
* Correct zero/loading/unavailable states
* Wire all visible controls

PHASE_2_WALLET_AND_BILLING:

* Wallet overview
* Add funds
* Auto refill
* Spending limits
* Payment methods
* Transactions
* Invoices
* Tax
* Reconciliation

PHASE_3_CAMPAIGN_CREATION:

* Promotion source
* Objective
* Campaign identity
* Ad group
* Audience
* Placement
* Creative
* Budget
* Schedule
* Billing
* Policy pre-check
* Review
* Submit

PHASE_4_CAMPAIGN_MANAGEMENT:

* Campaign list
* Campaign detail
* Edit
* Pause
* Resume
* Duplicate
* End
* Archive
* Review status
* Activity history

PHASE_5_VERIFICATION_AND_POLICY:

* Advertising verification
* Status-aware UI
* Policy Center
* Reviews
* Appeals
* Account restrictions
* Support references

PHASE_6_AUDIENCES_AND_CREATIVE:

* Basic targeting
* Saved audiences
* Retargeting
* Creative Library
* Rights checks
* Product media
* Post/Reel/Live readiness

PHASE_7_REPORTING_AND_ATTRIBUTION:

* Spend
* Clicks
* CPC
* Results
* Conversions
* Revenue
* Refund adjustments
* Attribution windows
* Exports

PHASE_8_POST_ADS:

* Feed post promotion
* Reel promotion
* Live replay promotion
* Event promotion
* Creator collaboration rights
* Social placements
* Social campaign reporting

PHASE_9_RELEASE_HARDENING:

* Accessibility
* Localization
* Poor-network recovery
* Financial reconciliation
* Real-device verification
* Multi-role testing
* Policy review testing
* Production evidence

=====================================================================
37. DEFINITION OF DONE
=====================================================================
MISSION_COMPLETE_ONLY_WHEN: advertiser_journey: - User opens Advertising. - User sees accurate account and wallet states. - User can create a campaign draft before verification. - User chooses an eligible product or content source. - User selects objective. - User creates an Ad Group. - User configures audience and placement. - User selects or creates creative. - User configures budget and schedule. - User chooses billing source. - User resolves policy and verification issues. - User submits campaign. - Campaign is reviewed. - Campaign becomes active only after all gates pass. - Spend posts correctly to the wallet ledger. - Results and attribution update correctly. - Refunds adjust attributed revenue. - User can pause, resume, edit, duplicate, end, and archive. - User can review invoices and transaction history. - User can appeal a rejected campaign. - Staff permissions are enforced. - Every action is auditable. - No visible control is a dead end.
HARD_COMPLETION_RULES:

* No clipped text.
* No duplicate unavailable notices.
* No fake seven-day report.
* No prominent internal account ID.
* No client-authoritative wallet balance.
* No campaign activation without verification, policy, eligibility, and funding.
* No blocked draft creation solely because verification is pending.
* No sensitive audience targeting.
* No creative delivery without rights checks.
* No attributed revenue left unadjusted after refunds.
* No inaccessible policy reason or appeal path.
* No empty locked card without a useful destination.
* No release claim without end-to-end real-device and financial verification.
