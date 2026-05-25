(function () {
  const inputSelector = 'input[type="file"]';

  function hideNativeInput(input) {
    input.classList.add('pulse-native-file-input');
    if (!input.hasAttribute('aria-hidden')) input.setAttribute('aria-hidden', 'true');
    input.tabIndex = -1;
  }

  function trigger(input, accept) {
    if (!input) return false;
    if (accept) input.setAttribute('accept', accept);
    input.click();
    return true;
  }

  function bindTrigger(button) {
    if (button.dataset.pulsePickerBound === '1') return;
    button.dataset.pulsePickerBound = '1';
    button.addEventListener('click', () => {
      const target = button.getAttribute('data-media-input') || button.getAttribute('data-target-input') || 'postMedia';
      const input = document.getElementById(target) || document.querySelector(button.getAttribute('data-media-selector') || '');
      trigger(input, button.getAttribute('data-media-accept') || '');
    });
  }

  function hydrate(root) {
    const scope = root || document;
    scope.querySelectorAll(inputSelector).forEach(hideNativeInput);
    scope.querySelectorAll('[data-pulse-media-trigger],[data-open-media]').forEach(bindTrigger);
  }

  window.PulseMediaPicker = { hydrate, trigger };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => hydrate(document));
  } else {
    hydrate(document);
  }
})();
