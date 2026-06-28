(() => {
  const modal = document.getElementById('pulsePromotionModal');
  const form = document.getElementById('pulsePromotionForm');
  if (!modal || !form || modal.dataset.bound === '1') return;
  modal.dataset.bound = '1';

  const state = {
    step: 1,
    contentType: '',
    contentId: '',
    contentLabel: '',
    eligibility: null,
    draftId: 0,
  };
  const status = modal.querySelector('[data-promotion-state]');
  const goals = modal.querySelector('[data-promotion-goals]');
  const contentTitle = modal.querySelector('[data-promotion-content-title]');
  const billing = modal.querySelector('[data-promotion-billing]');
  const review = modal.querySelector('[data-promotion-review]');
  const backButton = modal.querySelector('[data-promotion-back]');
  const nextButton = modal.querySelector('[data-promotion-next]');
  const saveButton = modal.querySelector('[data-promotion-save]');
  const launchButton = modal.querySelector('[data-promotion-launch]');

  const esc = value => String(value || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  const money = cents => `$${(Number(cents || 0) / 100).toFixed(2)}`;
  const setStatus = (message, type = 'info') => {
    status.textContent = message || '';
    status.dataset.state = type;
    status.hidden = !message;
  };
  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...(options.headers || {})},
      ...options,
    });
    const data = await response.json().catch(() => ({ok: false, message: 'Promotion service returned an unreadable response.'}));
    if (!response.ok || data.ok === false) {
      const error = new Error(data.message || 'Promotion request failed.');
      error.payload = data;
      throw error;
    }
    return data;
  };

  const showStep = step => {
    state.step = Math.max(1, Math.min(4, Number(step || 1)));
    modal.querySelectorAll('[data-promotion-step]').forEach(section => section.classList.toggle('active', Number(section.dataset.promotionStep) === state.step));
    modal.querySelectorAll('[data-promotion-progress]').forEach(bar => bar.classList.toggle('active', Number(bar.dataset.promotionProgress) <= state.step));
    backButton.hidden = state.step === 1;
    nextButton.hidden = state.step === 4;
    saveButton.hidden = state.step !== 4;
    launchButton.hidden = state.step !== 4;
    if (state.step === 4) renderReview();
  };

  const selectedGoal = () => form.querySelector('input[name="promotion_goal"]:checked')?.value || '';
  const payload = launch => {
    const amount = Math.round(Number(form.elements.budget_amount.value || 0) * 100);
    return {
      content_type: state.contentType,
      content_id: state.contentId,
      goal: selectedGoal(),
      audience: {type: 'automatic'},
      budget: {type: form.elements.budget_type.value, amount_cents: amount},
      duration: {days: Number(form.elements.duration_days.value || 0), start_date: form.elements.start_date.value},
      placement: 'auto',
      launch: !!launch,
    };
  };

  const renderReview = () => {
    const data = payload(false);
    const goalLabel = form.querySelector('input[name="promotion_goal"]:checked')?.dataset.label || 'Choose a goal';
    review.innerHTML = `<dl>
      <dt>Content</dt><dd>${esc(state.contentLabel)}</dd>
      <dt>Goal</dt><dd>${esc(goalLabel)}</dd>
      <dt>Audience</dt><dd>Automatic Audience</dd>
      <dt>Budget</dt><dd>${esc(money(data.budget.amount_cents))} ${esc(data.budget.type)}</dd>
      <dt>Duration</dt><dd>${esc(data.duration.days)} day${data.duration.days === 1 ? '' : 's'}</dd>
      <dt>Billing</dt><dd>${esc(state.eligibility?.billing?.message || 'Checking billing readiness.')}</dd>
      <dt>Policy</dt><dd>${esc(state.eligibility?.reason || 'Policy check pending.')}</dd>
      <dt>Estimated reach</dt><dd>Unavailable. No approved forecasting provider is configured.</dd>
      <dt>Total cost</dt><dd>${esc(money(data.budget.type === 'daily' ? data.budget.amount_cents * data.duration.days : data.budget.amount_cents))}</dd>
    </dl>`;
  };

  const renderEligibility = eligibility => {
    state.eligibility = eligibility;
    state.contentLabel = eligibility.content?.title || state.contentLabel || 'PulseSoc content';
    contentTitle.textContent = state.contentLabel;
    goals.innerHTML = (eligibility.goals || []).map(goal => `<label class="pulse-promotion-option ${goal.enabled ? '' : 'disabled'}" title="${esc(goal.reason || '')}">
      <input type="radio" name="promotion_goal" value="${esc(goal.key)}" data-label="${esc(goal.label)}" ${goal.enabled ? '' : 'disabled'}>
      <span><strong>${esc(goal.label)}</strong><small>${esc(goal.enabled ? 'Available for this content.' : goal.reason)}</small></span>
    </label>`).join('');
    const firstGoal = goals.querySelector('input:not(:disabled)');
    if (firstGoal) firstGoal.checked = true;
    billing.textContent = eligibility.billing?.message || 'Promotion billing status is unavailable.';
    const canLaunch = !!eligibility.eligible && !!eligibility.billing?.ready;
    launchButton.disabled = !canLaunch;
    launchButton.title = canLaunch ? 'Submit this promotion for policy review.' : (eligibility.billing?.message || eligibility.reason || 'Promotion launch is unavailable.');
    saveButton.disabled = !eligibility.eligible;
    nextButton.disabled = !eligibility.eligible;
    setStatus(eligibility.eligible ? 'Promotion settings loaded from the backend.' : eligibility.reason, eligibility.eligible ? 'success' : 'error');
  };

  const openPromotion = async trigger => {
    state.contentType = trigger.dataset.promoteContent || '';
    state.contentId = trigger.dataset.contentId || '';
    state.contentLabel = trigger.dataset.contentLabel || 'PulseSoc content';
    state.eligibility = null;
    state.draftId = 0;
    form.reset();
    form.elements.budget_amount.value = '10.00';
    form.elements.duration_days.value = '3';
    form.elements.start_date.value = new Date().toISOString().slice(0, 10);
    contentTitle.textContent = state.contentLabel;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    showStep(1);
    goals.innerHTML = '<p class="pulse-promotion-muted">Checking content ownership and promotion eligibility...</p>';
    setStatus('Checking promotion eligibility...');
    try {
      const data = await api(`/api/promotions/eligibility?content_type=${encodeURIComponent(state.contentType)}&content_id=${encodeURIComponent(state.contentId)}`);
      renderEligibility(data);
    } catch (error) {
      goals.innerHTML = `<p class="pulse-promotion-muted">${esc(error.message)}</p>`;
      nextButton.disabled = true;
      saveButton.disabled = true;
      launchButton.disabled = true;
      setStatus(error.message, 'error');
    }
  };

  const closePromotion = () => {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    setStatus('');
  };

  const validateStep = () => {
    if (state.step === 1 && !selectedGoal()) {
      setStatus('Choose an available promotion goal.', 'error');
      return false;
    }
    if (state.step === 3) {
      const data = payload(false);
      if (data.budget.amount_cents < 500 || data.budget.amount_cents > 500000) {
        setStatus('Promotion budget must be between $5.00 and $5,000.00.', 'error');
        return false;
      }
      if (data.duration.days < 1 || data.duration.days > 30) {
        setStatus('Promotion duration must be between 1 and 30 days.', 'error');
        return false;
      }
    }
    setStatus('');
    return true;
  };

  const submitPromotion = async launch => {
    if (!validateStep()) return;
    const button = launch ? launchButton : saveButton;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = launch ? 'Submitting...' : 'Saving...';
    setStatus(launch ? 'Submitting promotion for review...' : 'Saving promotion draft...');
    try {
      const data = await api('/api/promotions', {method: 'POST', body: JSON.stringify(payload(launch))});
      state.draftId = Number(data.promotion?.promotion_id || 0);
      setStatus(data.message || (launch ? 'Promotion submitted for review.' : 'Promotion draft saved.'), 'success');
      if (launch) launchButton.disabled = true;
    } catch (error) {
      if (error.payload?.promotion?.promotion_id) state.draftId = Number(error.payload.promotion.promotion_id);
      setStatus(`${error.payload?.draft_saved ? 'Draft saved. ' : ''}${error.message}`, 'error');
    } finally {
      button.textContent = original;
      button.disabled = launch ? !state.eligibility?.billing?.ready : !state.eligibility?.eligible;
    }
  };

  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-promote-content]');
    if (trigger) {
      event.preventDefault();
      openPromotion(trigger);
      return;
    }
    if (event.target.closest('[data-close-promotion]') || event.target === modal) closePromotion();
  });
  backButton.addEventListener('click', () => showStep(state.step - 1));
  nextButton.addEventListener('click', () => { if (validateStep()) showStep(state.step + 1); });
  saveButton.addEventListener('click', () => submitPromotion(false));
  launchButton.addEventListener('click', () => submitPromotion(true));
  form.addEventListener('input', () => { if (state.step === 4) renderReview(); });
  form.addEventListener('change', () => { if (state.step === 4) renderReview(); });
})();
