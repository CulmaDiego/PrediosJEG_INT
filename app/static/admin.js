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

document.querySelectorAll('[data-selection-form]').forEach((form) => {
  const selections = [...form.querySelectorAll('[data-person-select]')];
  const selectAll = form.querySelector('[data-select-all]');
  const count = form.querySelector('[data-selected-count]');
  const submit = form.querySelector('[data-confirm-selected]');
  const refresh = () => {
    const selected = selections.filter(checkbox => checkbox.checked).length;
    if (count) count.textContent = String(selected);
    if (submit) submit.disabled = selected === 0;
    if (selectAll) {
      selectAll.checked = selections.length > 0 && selected === selections.length;
      selectAll.indeterminate = selected > 0 && selected < selections.length;
    }
  };
  selectAll?.addEventListener('change', () => {
    selections.forEach(checkbox => { checkbox.checked = selectAll.checked; });
    refresh();
  });
  selections.forEach(checkbox => checkbox.addEventListener('change', refresh));
  submit?.addEventListener('click', event => {
    const selected = selections.filter(checkbox => checkbox.checked).length;
    if (!selected || !window.confirm(`Esta acción es permanente. Se eliminarán completamente ${selected} persona(s), sus enlaces, dibujos, teselas y archivos derivados. ¿Desea continuar?`)) {
      event.preventDefault();
    }
  });
  refresh();
});
