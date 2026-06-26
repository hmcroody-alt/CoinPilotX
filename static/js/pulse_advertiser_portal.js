(function () {
  const root = document.querySelector("[data-portal-root]");
  if (!root) return;

  const state = {
    portal: null,
    selectedAccountId: null,
  };

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function jsonFetch(url, options) {
    const init = Object.assign({ credentials: "same-origin" }, options || {});
    init.headers = Object.assign(
      { "Accept": "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
      init.headers || {}
    );
    return fetch(url, init).then(async (response) => {
      const data = await response.json().catch(() => ({ ok: false, error: "Invalid server response." }));
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || "Request failed.");
      }
      return data;
    });
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function pill(text, kind) {
    return el("span", `pill ${kind || ""}`.trim(), text);
  }

  function setMetric(key, value) {
    const node = document.querySelector(`[data-metric="${key}"]`);
    if (node) node.textContent = value === undefined || value === null ? "0" : String(value);
  }

  function selectedAccount() {
    const accounts = (state.portal && state.portal.accounts) || [];
    return accounts.find((account) => String(account.id) === String(state.selectedAccountId)) || accounts[0] || null;
  }

  function serializeForm(form) {
    const data = {};
    const checkboxes = {};
    Array.from(new FormData(form).entries()).forEach(([key, value]) => {
      if (key === "placements") {
        if (!checkboxes[key]) checkboxes[key] = [];
        checkboxes[key].push(value);
      } else {
        data[key] = value;
      }
    });
    Object.keys(checkboxes).forEach((key) => {
      data[key] = checkboxes[key];
    });
    return data;
  }

  function renderMetrics() {
    const metrics = (state.portal && state.portal.metrics) || {};
    setMetric("account_count", metrics.account_count || 0);
    setMetric("campaign_count", metrics.campaign_count || 0);
    setMetric("active_campaigns", metrics.active_campaigns || 0);
    setMetric("pending_reviews", metrics.pending_reviews || 0);
    setMetric("total_spend", metrics.total_spend || "$0.00");
    setMetric("wallet_balance", metrics.wallet_balance || "$0.00");
    setMetric("reserved_budget", metrics.reserved_budget || "$0.00");
    const role = document.querySelector("[data-role-pill]");
    if (role) role.textContent = ((state.portal && state.portal.roles && state.portal.roles.current) || "advertiser").replace(/_/g, " ");
  }

  function renderAccountLists() {
    const list = document.querySelector("[data-account-list]");
    const accountSelect = document.querySelector("[data-account-select]");
    clear(list);
    clear(accountSelect);
    const accounts = (state.portal && state.portal.accounts) || [];
    if (!accounts.length) {
      list.appendChild(el("div", "empty", "Create your first advertiser account using the existing Ads Accounts API, then build campaigns here."));
      const option = el("option", "", "Create an ad account first");
      option.value = "";
      accountSelect.appendChild(option);
      return;
    }
    accounts.forEach((account) => {
      if (!state.selectedAccountId) state.selectedAccountId = account.id;
      const card = el("article", "portal-card");
      card.appendChild(el("h3", "", account.business_name || "Advertiser account"));
      card.appendChild(el("p", "", `${account.business_type || "Business"} · ${account.total_spend || "$0.00"} spend`));
      const row = el("div", "row");
      row.appendChild(pill(account.status || "pending", account.status === "active" ? "ok" : "warn"));
      row.appendChild(pill(account.verification_status || "unverified", account.verification_status === "verified" ? "ok" : "warn"));
      row.appendChild(pill(`${account.health_score || 0}% health`, (account.health_score || 0) > 75 ? "ok" : "warn"));
      card.appendChild(row);
      const selectButton = el("button", "secondary", "Edit Profile");
      selectButton.type = "button";
      selectButton.addEventListener("click", () => loadAccountProfile(account.id));
      card.appendChild(selectButton);
      list.appendChild(card);

      const option = el("option", "", account.business_name || `Account ${account.id}`);
      option.value = account.id;
      accountSelect.appendChild(option);
    });
    renderWalletAccountSelect(accounts);
  }

  function renderWalletAccountSelect(accounts) {
    const select = document.querySelector("[data-wallet-account-select]");
    clear(select);
    if (!accounts.length) {
      const option = el("option", "", "Create an ad account first");
      option.value = "";
      select.appendChild(option);
      return;
    }
    accounts.forEach((account) => {
      const option = el("option", "", account.business_name || `Account ${account.id}`);
      option.value = account.id;
      select.appendChild(option);
    });
  }

  function renderCampaignSelect() {
    const select = document.querySelector("[data-campaign-select]");
    clear(select);
    const campaigns = (state.portal && state.portal.campaigns) || [];
    if (!campaigns.length) {
      const option = el("option", "", "Create a campaign first");
      option.value = "";
      select.appendChild(option);
      return;
    }
    campaigns.forEach((campaign) => {
      const option = el("option", "", `${campaign.campaign_name} · ${campaign.status}`);
      option.value = campaign.id;
      select.appendChild(option);
    });
  }

  function renderCreatives() {
    const list = document.querySelector("[data-creative-list]");
    clear(list);
    const creatives = (state.portal && state.portal.creatives) || [];
    if (!creatives.length) {
      list.appendChild(el("div", "empty", "No creatives yet. Create a draft, preview it, then submit it for review."));
      return;
    }
    creatives.forEach((creative) => {
      const card = el("article", "portal-card");
      card.appendChild(el("h3", "", creative.title || "Creative"));
      card.appendChild(el("p", "", `${creative.creative_type || "text"} · ${creative.campaign_name || "Campaign"}`));
      const row = el("div", "row");
      row.appendChild(pill(creative.moderation_status || "draft", creative.moderation_status === "approved" ? "ok" : creative.moderation_status === "rejected" ? "bad" : "warn"));
      row.appendChild(pill(creative.media_ready ? "media ready" : "text only", creative.media_ready ? "ok" : "warn"));
      card.appendChild(row);
      const actions = el("div", "row");
      [["submit", "Submit"], ["duplicate", "Duplicate"], ["archive", "Archive"], ["delete_draft", "Delete Draft"]].forEach(([action, label]) => {
        const button = el("button", action === "archive" || action === "delete_draft" ? "danger" : "secondary", label);
        button.type = "button";
        button.addEventListener("click", () => creativeAction(creative.id, action));
        actions.appendChild(button);
      });
      card.appendChild(actions);
      list.appendChild(card);
    });
  }

  function renderBudget() {
    const board = document.querySelector("[data-budget-board]");
    clear(board);
    const campaigns = (state.portal && state.portal.campaigns) || [];
    if (!campaigns.length) {
      board.appendChild(el("div", "empty", "Budget projections appear after you save a campaign draft."));
      return;
    }
    campaigns.slice(0, 6).forEach((campaign) => {
      const card = el("article", "portal-card");
      card.appendChild(el("h3", "", campaign.campaign_name));
      card.appendChild(el("p", "", `${campaign.budget_type || "daily"} budget ${campaign.budget_display || "$0.00"} · remaining ${campaign.remaining_budget || "$0.00"}`));
      const row = el("div", "row");
      row.appendChild(pill(campaign.status || "draft", campaign.status === "active" ? "ok" : "warn"));
      row.appendChild(pill((campaign.placements || []).join(", ") || "feed_inline"));
      card.appendChild(row);
      const actions = el("div", "row");
      [["pause", "Pause"], ["resume", "Resume"], ["duplicate", "Duplicate"], ["archive", "Archive"]].forEach(([action, label]) => {
        const button = el("button", action === "archive" ? "danger" : "secondary", label);
        button.type = "button";
        button.addEventListener("click", () => campaignAction(campaign.id, action));
        actions.appendChild(button);
      });
      card.appendChild(actions);
      board.appendChild(card);
    });
  }

  function renderAnalytics() {
    const node = document.querySelector("[data-analytics]");
    clear(node);
    const totals = (state.portal && state.portal.analytics && state.portal.analytics.totals) || {};
    [
      ["Impressions", totals.impressions || 0],
      ["Viewable", totals.viewable_impressions || 0],
      ["Clicks", totals.clicks || 0],
      ["CTR", `${totals.ctr || 0}%`],
      ["Spend", totals.spend || "$0.00"],
      ["eCPM", totals.estimated_cpm || 0],
      ["Reports", totals.reports || 0],
      ["Hides", totals.hides || 0],
    ].forEach(([label, value]) => {
      const card = el("article", "portal-card");
      card.appendChild(el("h3", "", value));
      card.appendChild(el("p", "", label));
      node.appendChild(card);
    });
  }

  function renderPlacements() {
    const node = document.querySelector("[data-placement-board]");
    clear(node);
    const placements = state.portal && state.portal.placements ? Object.values(state.portal.placements) : [];
    if (!placements.length) {
      node.appendChild(el("div", "empty", "No placements are configured yet."));
      return;
    }
    placements.forEach((placement) => {
      const card = el("article", "portal-card");
      card.appendChild(el("h3", "", placement.display_name || placement.placement_key));
      card.appendChild(el("p", "", `${placement.placement_key} · ${placement.device_type || "all"} · max frequency ${placement.max_frequency || 0}`));
      const row = el("div", "row");
      row.appendChild(pill(placement.card_style || "signal-card"));
      row.appendChild(pill((placement.supported_creative_types || []).join(", ") || "all creatives"));
      card.appendChild(row);
      node.appendChild(card);
    });
  }

  function renderWallet() {
    const board = document.querySelector("[data-wallet-board]");
    const billing = document.querySelector("[data-billing-list]");
    clear(board);
    clear(billing);
    const wallets = (state.portal && state.portal.wallets) || [];
    if (!wallets.length) {
      board.appendChild(el("div", "empty", "Create an advertiser account to activate wallet controls."));
      billing.appendChild(el("div", "empty", "Receipts appear after successful wallet funding."));
      return;
    }
    wallets.forEach((wallet) => {
      const card = el("article", "portal-card wallet-card");
      card.appendChild(el("h3", "", wallet.spendable_balance || "$0.00"));
      card.appendChild(el("p", "", `Spendable balance · reserved ${wallet.reserved_budget || "$0.00"}`));
      const row = el("div", "row");
      row.appendChild(pill(wallet.billing_enabled ? "billing enabled" : "funding prepared", wallet.billing_enabled ? "ok" : "warn"));
      row.appendChild(pill(wallet.stripe_ready ? "stripe ready" : "stripe not configured", wallet.stripe_ready ? "ok" : "warn"));
      card.appendChild(row);
      board.appendChild(card);

      (wallet.transactions || []).slice(0, 8).forEach((tx) => {
        const item = el("article", "portal-card compact");
        item.appendChild(el("h3", "", `${tx.transaction_type || "transaction"} · ${tx.amount || "$0.00"}`));
        item.appendChild(el("p", "", `${tx.status || "posted"} · ${tx.description || ""} · ${tx.created_at || ""}`));
        billing.appendChild(item);
      });
      (wallet.receipts || []).slice(0, 6).forEach((receipt) => {
        const item = el("article", "portal-card compact");
        item.appendChild(el("h3", "", `${receipt.receipt_number || "Receipt"} · ${receipt.amount || "$0.00"}`));
        item.appendChild(el("p", "", `${receipt.status || "paid"} · ${receipt.created_at || ""}`));
        billing.appendChild(item);
      });
    });
    if (!billing.children.length) {
      billing.appendChild(el("div", "empty", "Funding, spend, receipts, and future refunds will appear here."));
    }
  }

  function renderReview() {
    const list = document.querySelector("[data-review-list]");
    clear(list);
    const rows = (state.portal && state.portal.review_board) || [];
    if (!rows.length) {
      list.appendChild(el("div", "empty", "No creative review records yet. Submit a creative to enter the moderation queue."));
      return;
    }
    rows.forEach((row) => {
      const card = el("article", "portal-card");
      card.appendChild(el("h3", "", row.title || "Creative review"));
      card.appendChild(el("p", "", `${row.campaign_name || "Campaign"} · ${row.review_reason || row.rejection_reason || "Review history will appear here."}`));
      const statusRow = el("div", "row");
      statusRow.appendChild(pill(row.review_status || "pending", row.review_status === "approved" ? "ok" : row.review_status === "rejected" ? "bad" : "warn"));
      statusRow.appendChild(pill(`risk ${row.risk_score || 0}`));
      card.appendChild(statusRow);
      list.appendChild(card);
    });
  }

  function renderNotifications() {
    const list = document.querySelector("[data-ad-notification-list]");
    clear(list);
    const rows = (state.portal && state.portal.notifications) || [];
    if (!rows.length) {
      list.appendChild(el("div", "empty", "No advertiser notifications yet."));
      return;
    }
    rows.slice(0, 20).forEach((row) => {
      const card = el("article", "portal-card compact");
      card.appendChild(el("h3", "", row.title || "Notification"));
      card.appendChild(el("p", "", `${row.body || ""} · ${row.created_at || ""}`));
      card.appendChild(pill(row.status || "unread", row.status === "read" ? "" : "warn"));
      list.appendChild(card);
    });
  }

  function renderAll() {
    renderMetrics();
    renderAccountLists();
    renderCampaignSelect();
    renderCreatives();
    renderPlacements();
    renderBudget();
    renderAnalytics();
    renderWallet();
    renderReview();
    renderNotifications();
  }

  async function loadPortal() {
    const data = await jsonFetch("/api/pulse/ads/portal");
    state.portal = data.portal;
    renderAll();
  }

  async function loadAccountProfile(accountId) {
    state.selectedAccountId = accountId;
    const data = await jsonFetch(`/api/pulse/ads/accounts/${accountId}/profile`);
    const form = document.querySelector("[data-account-form]");
    const profile = data.profile || {};
    form.account_id.value = accountId;
    ["legal_name", "contact_email", "website", "industry", "tax_country"].forEach((key) => {
      if (form[key]) form[key].value = profile[key] || "";
    });
    if (form.tax_identifier) form.tax_identifier.value = "";
  }

  async function saveAccountProfile(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const accountId = form.account_id.value || (selectedAccount() && selectedAccount().id);
    if (!accountId) return;
    await jsonFetch(`/api/pulse/ads/accounts/${accountId}/profile`, {
      method: "POST",
      body: JSON.stringify(serializeForm(form)),
    });
    await loadPortal();
  }

  async function createAccount(event) {
    event.preventDefault();
    const payload = serializeForm(event.currentTarget);
    await jsonFetch("/api/pulse/ads/accounts", { method: "POST", body: JSON.stringify(payload) });
    event.currentTarget.reset();
    state.selectedAccountId = null;
    await loadPortal();
  }

  async function createCampaign(event) {
    event.preventDefault();
    const payload = serializeForm(event.currentTarget);
    if (!payload.ad_account_id) return;
    await jsonFetch("/api/pulse/ads/campaigns", { method: "POST", body: JSON.stringify(payload) });
    event.currentTarget.reset();
    await loadPortal();
  }

  async function createCreative(event) {
    event.preventDefault();
    const payload = serializeForm(event.currentTarget);
    if (!payload.campaign_id) return;
    await jsonFetch("/api/pulse/ads/creatives", { method: "POST", body: JSON.stringify(payload) });
    event.currentTarget.reset();
    await loadPortal();
  }

  async function createFundingSession(event) {
    event.preventDefault();
    const payload = serializeForm(event.currentTarget);
    if (!payload.account_id) return;
    const data = await jsonFetch(`/api/pulse/ads/accounts/${payload.account_id}/wallet/funding-session`, {
      method: "POST",
      body: JSON.stringify({
        amount_cents: payload.amount_cents,
        currency: "usd",
        idempotency_key: `wallet-${payload.account_id}-${Date.now()}`,
      }),
    });
    const checkoutUrl = data.funding_session && data.funding_session.checkout_url;
    if (checkoutUrl) {
      window.location.assign(checkoutUrl);
      return;
    }
    await loadPortal();
  }

  async function campaignAction(campaignId, action) {
    await jsonFetch(`/api/pulse/ads/campaigns/${campaignId}/action`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    await loadPortal();
  }

  async function creativeAction(creativeId, action) {
    await jsonFetch(`/api/pulse/ads/creatives/${creativeId}/action`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    await loadPortal();
  }

  document.querySelector("[data-account-form]")?.addEventListener("submit", saveAccountProfile);
  document.querySelector("[data-create-account-form]")?.addEventListener("submit", createAccount);
  document.querySelector("[data-campaign-form]")?.addEventListener("submit", createCampaign);
  document.querySelector("[data-creative-form]")?.addEventListener("submit", createCreative);
  document.querySelector("[data-wallet-funding-form]")?.addEventListener("submit", createFundingSession);
  document.querySelector("[data-refresh]")?.addEventListener("click", loadPortal);

  loadPortal().catch((error) => {
    const main = document.querySelector(".ad-main");
    if (main) {
      const box = el("div", "empty", error.message || "Advertiser portal failed to load.");
      main.insertBefore(box, main.firstChild);
    }
  });
})();
