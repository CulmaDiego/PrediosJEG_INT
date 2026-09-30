document.querySelectorAll('[data-confirm]').forEach((element) => {
  element.addEventListener('click', (event) => {
    if (!window.confirm(element.dataset.confirm)) event.preventDefault();
  });
});

document.querySelectorAll('[data-copy-target]').forEach((button) => {
  button.addEventListener('click', async () => {
    const field = document.querySelector(button.dataset.copyTarget);
    if (!field) return;
    const value = field.value || field.textContent;
    try {
      await navigator.clipboard.writeText(value);
    } catch (_) {
      field.select();
      document.execCommand('copy');
    }
    const original = button.textContent;
    button.textContent = 'Copiado';
    window.setTimeout(() => { button.textContent = original; }, 1800);
  });
});
