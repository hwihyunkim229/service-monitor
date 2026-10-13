(() => {
  const enhanced = new WeakSet();
  let openControl = null;
  let tableSelect = null;
  let tableMenu = null;

  const close = control => {
    if (!control) return;
    control.wrapper.classList.remove('open');
    control.button.setAttribute('aria-expanded', 'false');
    if (openControl === control) openControl = null
  };

  const shouldEnhance = select => {
    if (!(select instanceof HTMLSelectElement) || enhanced.has(select)) return false;
    if (select.multiple || Number(select.size) > 1 || select.dataset.customSelect === 'off') return false;
    if (select.closest('table,.product-select-control,.product-dropdown')) return false;
    return true
  };

  const enhance = select => {
    if (!shouldEnhance(select)) return;
    enhanced.add(select);
    const measuredWidth = select.getBoundingClientRect().width;
    const wrapper = document.createElement('div');
    wrapper.className = 'app-select';
    if (measuredWidth) wrapper.style.width = `${Math.max(84, measuredWidth)}px`;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'app-select__trigger';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    const menu = document.createElement('div');
    menu.className = 'app-select__menu';
    menu.setAttribute('role', 'listbox');
    select.parentNode.insertBefore(wrapper, select);
    wrapper.append(select, button, menu);
    const control = {
      select,
      wrapper,
      button,
      menu
    };

    const render = () => {
      const selected = select.selectedOptions[0];
      const label = selected?.textContent?.trim() || select.getAttribute('aria-label') || '선택';
      button.innerHTML = '<span class="app-select__mark" aria-hidden="true"></span><span class="app-select__value"></span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4"/></svg>';
      button.querySelector('.app-select__value').textContent = label;
      button.disabled = select.disabled;
      wrapper.classList.toggle('disabled', select.disabled);
      menu.replaceChildren();
      [...select.options].forEach((option, index) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'app-select__option';
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.selected));
        item.disabled = option.disabled;
        item.innerHTML = '<span class="app-select__option-dot" aria-hidden="true"></span><span></span><i aria-hidden="true">✓</i>';
        item.children[1].textContent = option.textContent || option.value;
        item.addEventListener('click', event => {
          event.preventDefault();
          if (select.selectedIndex !== index) {
            select.selectedIndex = index;
            select.dispatchEvent(new Event('change', {
              bubbles: true
            }))
          }
          render();
          close(control);
          button.focus()
        });
        menu.appendChild(item)
      })
    };

    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      if (select.disabled) return;
      if (openControl && openControl !== control) close(openControl);
      const opening = !wrapper.classList.contains('open');
      wrapper.classList.toggle('open', opening);
      button.setAttribute('aria-expanded', String(opening));
      openControl = opening ? control : null;
      if (opening) menu.querySelector('[aria-selected="true"]')?.scrollIntoView({
        block: 'nearest'
      })
    });
    menu.addEventListener('click', event => event.stopPropagation());
    select.addEventListener('change', render);
    new MutationObserver(render).observe(select, {
      attributes: true,
      attributeFilter: ['disabled'],
      childList: true,
      subtree: true
    });
    render()
  };

  const scan = root => {
    if (root instanceof HTMLSelectElement) enhance(root);
    root.querySelectorAll?.('select').forEach(enhance)
  };

  const closeTableMenu = () => {
    tableMenu?.classList.remove('open');
    tableSelect?.classList.remove('table-select-open');
    tableSelect = null
  };

  const positionTableMenu = select => {
    const rect = select.getBoundingClientRect();
    const width = Math.min(Math.max(rect.width, 170), 360);
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    tableMenu.style.width = `${width}px`;
    tableMenu.style.left = `${left}px`;
    tableMenu.style.top = `${rect.bottom + 6}px`;
    const menuRect = tableMenu.getBoundingClientRect();
    if (menuRect.bottom > window.innerHeight - 8 && rect.top > menuRect.height + 8) {
      tableMenu.style.top = `${rect.top - menuRect.height - 6}px`
    }
  };

  const openTableMenu = select => {
    if (select.disabled || getComputedStyle(select).display === 'none') return;
    close(openControl);
    closeTableMenu();
    tableSelect = select;
    select.classList.add('table-select-open');
    tableMenu.replaceChildren();
    tableMenu.classList.toggle('multi', select.multiple);
    const items = [];
    const refreshItems = () => items.forEach(({ item, option }) => {
      item.setAttribute('aria-selected', String(option.selected))
    });
    [...select.options].forEach((option, index) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'table-select-option';
      item.disabled = option.disabled;
      item.setAttribute('aria-selected', String(option.selected));
      item.innerHTML = '<span></span><i aria-hidden="true">✓</i>';
      item.firstElementChild.textContent = option.textContent?.trim() || '빈칸';
      item.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        if (select.multiple) {
          if (!option.value) {
            [...select.options].forEach(candidate => candidate.selected = candidate === option)
          } else {
            option.selected = !option.selected;
            const allOption = [...select.options].find(candidate => !candidate.value);
            if (allOption) allOption.selected = false;
            if (![...select.options].some(candidate => candidate.value && candidate.selected) && allOption) {
              allOption.selected = true
            }
          }
          refreshItems();
          return
        }
        if (select.selectedIndex !== index) {
          select.selectedIndex = index;
          select.dispatchEvent(new Event('input', {
            bubbles: true
          }));
          select.dispatchEvent(new Event('change', {
            bubbles: true
          }))
        }
        closeTableMenu();
        select.focus({
          preventScroll: true
        })
      });
      items.push({ item, option });
      tableMenu.appendChild(item)
    });
    if (select.multiple) {
      const actions = document.createElement('div');
      actions.className = 'table-select-actions';
      const reset = document.createElement('button');
      reset.type = 'button';
      reset.className = 'table-select-reset';
      reset.textContent = '전체 해제';
      reset.addEventListener('click', () => {
        [...select.options].forEach(option => option.selected = !option.value);
        refreshItems()
      });
      const apply = document.createElement('button');
      apply.type = 'button';
      apply.className = 'table-select-apply';
      apply.textContent = '적용';
      apply.addEventListener('click', () => {
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
        closeTableMenu()
      });
      actions.append(reset, apply);
      tableMenu.appendChild(actions)
    }
    tableMenu.classList.add('open');
    positionTableMenu(select);
    tableMenu.querySelector('[aria-selected="true"]')?.scrollIntoView({
      block: 'nearest'
    })
  };

  document.addEventListener('click', event => {
    if (event.target.closest?.('table select')) return;
    close(openControl);
    closeTableMenu()
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      close(openControl);
      closeTableMenu()
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    tableMenu = document.createElement('div');
    tableMenu.className = 'table-select-menu';
    tableMenu.setAttribute('role', 'listbox');
    tableMenu.addEventListener('click', event => event.stopPropagation());
    document.body.appendChild(tableMenu);
    scan(document);
    new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(scan)))
      .observe(document.body, {
        childList: true,
        subtree: true
      });
    document.addEventListener('pointerdown', event => {
      const select = event.target.closest?.('table select');
      if (!select || select.dataset.customSelect === 'off') return;
      event.preventDefault();
      select.focus({
        preventScroll: true
      });
      requestAnimationFrame(() => openTableMenu(select))
    }, true);
    window.addEventListener('resize', closeTableMenu);
    document.addEventListener('scroll', event => {
      if (tableMenu.contains(event.target)) return;
      closeTableMenu()
    }, true)
  })
})();