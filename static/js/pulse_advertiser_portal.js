(function () {
  const root = document.querySelector("[data-portal-root]");
  if (!root) return;

  const state = {
    portal: null,
    selectedAccountId: null,
    creativeMediaAsset: null,
    creativeThumbnailAsset: null,
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

  function selectedCampaign() {
    const form = document.querySelector("[data-creative-form]");
    const campaignId = form && form.campaign_id ? form.campaign_id.value : "";
    const campaigns = (state.portal && state.portal.campaigns) || [];
    return campaigns.find((campaign) => String(campaign.id) === String(campaignId)) || null;
  }

  function accountIdForCreativeUpload() {
    const campaign = selectedCampaign();
    return campaign ? campaign.ad_account_id || campaign.account_id || "" : "";
  }

  function resetCreativeUploadState() {
    state.creativeMediaAsset = null;
    state.creativeThumbnailAsset = null;
    const form = document.querySelector("[data-creative-form]");
    const mediaInput = form && form.querySelector("[data-media-asset-id]");
    const thumbInput = form && form.querySelector("[data-thumbnail-asset-id]");
    if (mediaInput) mediaInput.value = "";
    if (thumbInput) thumbInput.value = "";
    const status = document.querySelector("[data-creative-upload-status]");
    const preview = document.querySelector("[data-creative-upload-preview]");
    const remove = document.querySelector("[data-remove-creative-media]");
    const progress = document.querySelector("[data-creative-upload-progress]");
    if (status) status.textContent = "Waiting for upload.";
    if (remove) remove.disabled = true;
    if (progress) {
      progress.hidden = true;
      progress.value = 0;
    }
    if (preview) {
      clear(preview);
      preview.appendChild(el("span", "muted", "Preview appears after upload. Raw storage URLs are never requested from advertisers."));
    }
  }

  function renderUploadedAsset(asset, kind) {
    const preview = document.querySelector("[data-creative-upload-preview]");
    const status = document.querySelector("[data-creative-upload-status]");
    const remove = document.querySelector("[data-remove-creative-media]");
    if (!preview || !asset) return;
    clear(preview);
    const mediaType = String(asset.media_type || "").toLowerCase();
    const url = asset.public_url || asset.playback_url || "";
    const thumb = asset.thumbnail_url || asset.poster_url || url;
    if (mediaType === "video") {
      const video = el("video", "ad-upload-media");
      video.src = url;
      video.controls = true;
      video.muted = true;
      video.playsInline = true;
      video.preload = "metadata";
      if (thumb) video.poster = thumb;
      preview.appendChild(video);
    } else if (mediaType === "audio") {
      const audio = el("audio", "ad-upload-audio");
      audio.src = url;
      audio.controls = true;
      audio.preload = "metadata";
      preview.appendChild(audio);
    } else {
      const img = el("img", "ad-upload-media");
      img.src = thumb || url;
      img.alt = "Uploaded ad creative preview";
      img.loading = "lazy";
      preview.appendChild(img);
    }
    const details = el("div", "ad-upload-details");
    details.appendChild(el("strong", "", kind === "thumbnail" ? "Thumbnail uploaded" : "Media uploaded"));
    details.appendChild(el("span", "", `${asset.media_type || "media"} · ${asset.mime_type || "verified"} · ${asset.file_size || 0} bytes`));
    details.appendChild(pill(asset.moderation_status || "pending review", "warn"));
    preview.appendChild(details);
    if (status) status.textContent = kind === "thumbnail" ? "Custom thumbnail attached." : "Media uploaded. Preview and submit when ready.";
    if (remove) remove.disabled = !state.creativeMediaAsset;
  }

  function uploadAdMedia(file, assetKind) {
    const accountId = accountIdForCreativeUpload();
    if (!accountId) {
      return Promise.reject(new Error("Select a campaign before uploading media."));
    }
    const progress = document.querySelector("[data-creative-upload-progress]");
    const status = document.querySelector("[data-creative-upload-status]");
    if (status) status.textContent = "Uploading through PulseSoc secure media pipeline...";
    if (progress) {
      progress.hidden = false;
      progress.value = 0;
    }
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/pulse/ads/accounts/${accountId}/media/upload`);
      xhr.withCredentials = true;
      xhr.setRequestHeader("X-CSRF-Token", csrfToken());
      xhr.upload.onprogress = (event) => {
        if (progress && event.lengthComputable) {
          progress.value = Math.round((event.loaded / event.total) * 100);
        }
      };
      xhr.onload = () => {
        let data = {};
        try {
          data = JSON.parse(xhr.responseText || "{}");
        } catch (_) {
          data = { ok: false, error: "Invalid server response." };
        }
        if (progress) {
          progress.value = 100;
          window.setTimeout(() => { progress.hidden = true; }, 400);
        }
        if (xhr.status < 200 || xhr.status >= 300 || data.ok === false) {
          reject(new Error(data.error || "Upload failed."));
          return;
        }
        resolve(data.asset);
      };
      xhr.onerror = () => reject(new Error("Upload failed. Check your connection and retry."));
      const body = new FormData();
      body.append("file", file);
      body.append("asset_kind", assetKind || "creative_media");
      xhr.send(body);
    });
  }

  async function handleMediaInput(event, assetKind) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      const asset = await uploadAdMedia(file, assetKind);
      const form = document.querySelector("[data-creative-form]");
      if (assetKind === "thumbnail") {
        state.creativeThumbnailAsset = asset;
        const input = form && form.querySelector("[data-thumbnail-asset-id]");
        if (input) input.value = asset.id || "";
      } else {
        state.creativeMediaAsset = asset;
        const input = form && form.querySelector("[data-media-asset-id]");
        if (input) input.value = asset.id || "";
      }
      renderUploadedAsset(asset, assetKind);
    } catch (error) {
      const status = document.querySelector("[data-creative-upload-status]");
      if (status) status.textContent = error.message || "Upload failed.";
      alert(error.message || "Upload failed.");
    } finally {
      event.target.value = "";
    }
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
      const media = creative.media_asset || {};
      if (media.public_url || media.thumbnail_url) {
        const preview = el("div", "ad-card-preview");
        const type = String(media.media_type || creative.creative_type || "").toLowerCase();
        if (type === "audio") {
          const audio = el("audio", "");
          audio.controls = true;
          audio.preload = "metadata";
          audio.src = media.public_url || media.playback_url || "";
          preview.appendChild(audio);
        } else if (type === "video") {
          const video = el("video", "");
          video.controls = true;
          video.muted = true;
          video.playsInline = true;
          video.preload = "metadata";
          video.src = media.public_url || media.playback_url || "";
          if (media.thumbnail_url) video.poster = media.thumbnail_url;
          preview.appendChild(video);
        } else {
          const img = el("img", "");
          img.src = media.thumbnail_url || media.public_url || "";
          img.alt = "Creative preview";
          img.loading = "lazy";
          preview.appendChild(img);
        }
        card.appendChild(preview);
      }
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
    delete payload.file;
    delete payload.creative_media_file;
    delete payload.creative_thumbnail_file;
    const creativeType = String(payload.creative_type || "text").toLowerCase();
    if (["image", "video", "audio"].includes(creativeType) && !payload.media_asset_id) {
      alert(`Upload a ${creativeType} file before creating this creative.`);
      return;
    }
    await jsonFetch("/api/pulse/ads/creatives", { method: "POST", body: JSON.stringify(payload) });
    event.currentTarget.reset();
    resetCreativeUploadState();
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
  document.querySelector("[data-creative-media-input]")?.addEventListener("change", (event) => handleMediaInput(event, "creative_media"));
  document.querySelector("[data-creative-thumbnail-input]")?.addEventListener("change", (event) => handleMediaInput(event, "thumbnail"));
  document.querySelector("[data-campaign-select]")?.addEventListener("change", resetCreativeUploadState);
  document.querySelector("[data-remove-creative-media]")?.addEventListener("click", async () => {
    const accountId = accountIdForCreativeUpload();
    const asset = state.creativeMediaAsset;
    if (!accountId || !asset) return;
    try {
      await jsonFetch(`/api/pulse/ads/accounts/${accountId}/media/${asset.id}/delete`, { method: "POST", body: JSON.stringify({}) });
      resetCreativeUploadState();
    } catch (error) {
      alert(error.message || "Could not delete draft media.");
    }
  });

  loadPortal().catch((error) => {
    const main = document.querySelector(".ad-main");
    if (main) {
      const box = el("div", "empty", error.message || "Advertiser portal failed to load.");
      main.insertBefore(box, main.firstChild);
    }
  });
})();
