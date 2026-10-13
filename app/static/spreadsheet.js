(() => {
  const editableSelector = [
    '[contenteditable="true"]',
    'input:not([type="file"]):not([type="checkbox"]):not([type="radio"]):not([type="hidden"])',
    'textarea',
    'select'
  ].join(',');
  const tables = [];
  const undoStack = [];
  const redoStack = [];
  const selected = new Set();
  let selectionAnchor = null;
  let activeCell = null;

  const controlFor = cell => {
    if (!cell) return null;
    if (cell.matches?.(editableSelector)) return cell;
    return cell.querySelector?.(editableSelector) || null
  };
  const cellFor = target => target?.closest?.('td,th');
  const visibleRow = row => Boolean(
    row &&
    row.parentElement?.tagName === 'TBODY' &&
    !row.hidden &&
    getComputedStyle(row).display !== 'none'
  );
  const valueOf = control => {
    if (!control) return '';
    return control.matches('input,textarea,select') ? control.value : control.innerText
  };
  const setValue = (control, value, commit = true) => {
    if (!control || control.disabled || control.readOnly) return;
    if (control.matches('input,textarea,select')) control.value = value;
    else control.innerText = value;
    control.dispatchEvent(new Event('input', {
      bubbles: true
    }));
    if (commit) {
      if (control.matches('select,input,textarea')) {
        control.dispatchEvent(new Event('change', {
          bubbles: true
        }));
      }
      if (!control.matches('select,input[type="date"],input[type="datetime-local"]')) {
        control.dispatchEvent(new FocusEvent('blur', {
          bubbles: true
        }))
      }
    }
  };
  const orderedSelected = () => [...selected]
    .filter(cell => document.contains(cell) && visibleRow(cell.parentElement))
    .sort((a, b) => {
      const rowDiff = a.parentElement.rowIndex - b.parentElement.rowIndex;
      return rowDiff || a.cellIndex - b.cellIndex
    });
  const clearSelection = () => {
    selected.forEach(cell => cell.classList.remove('sheet-selected'));
    selected.clear();
    selectionAnchor = null
  };
  const selectCell = (cell, append = false) => {
    if (!cell || !controlFor(cell) || !visibleRow(cell.parentElement)) return;
    if (!append) clearSelection();
    selected.add(cell);
    cell.classList.add('sheet-selected')
  };
  const selectRange = (from, to) => {
    if (!from || !to || from.closest('table') !== to.closest('table')) return;
    clearSelection();
    const table = from.closest('table');
    const rowStart = Math.min(from.parentElement.rowIndex, to.parentElement.rowIndex);
    const rowEnd = Math.max(from.parentElement.rowIndex, to.parentElement.rowIndex);
    const colStart = Math.min(from.cellIndex, to.cellIndex);
    const colEnd = Math.max(from.cellIndex, to.cellIndex);
    for (let rowIndex = rowStart; rowIndex <= rowEnd; rowIndex += 1) {
      const row = table.rows[rowIndex];
      if (!visibleRow(row)) continue;
      for (let colIndex = colStart; colIndex <= colEnd; colIndex += 1) {
        selectCell(row?.cells[colIndex], true)
      }
    }
  };
  const focusControl = (control, ensureVisible = false) => {
    if (!control) return;
    control.focus({
      preventScroll: true
    });
    if (ensureVisible) control.scrollIntoView({
      block: 'nearest',
      inline: 'nearest'
    });
    const cell = cellFor(control);
    if (activeCell && activeCell !== cell) activeCell.classList.remove('sheet-active');
    cell?.classList.add('sheet-active');
    activeCell = cell;
    selectionAnchor = cell
  };
  const nextControl = (control, rowOffset, colOffset = 0) => {
    const cell = cellFor(control);
    const table = cell?.closest('table');
    if (!cell || !table) return null;
    if (colOffset) {
      const direction = Math.sign(colOffset);
      const row = cell.parentElement;
      for (
        let colIndex = cell.cellIndex + direction; colIndex >= 0 && colIndex < row.cells.length; colIndex += direction
      ) {
        const candidate = controlFor(row.cells[colIndex]);
        if (candidate) return candidate
      }
      let rowIndex = row.rowIndex + direction;
      while (rowIndex >= 0 && rowIndex < table.rows.length) {
        const nextRow = table.rows[rowIndex];
        if (visibleRow(nextRow)) {
          const cells = direction > 0 ? [...nextRow.cells] : [...nextRow.cells].reverse();
          const candidate = cells.map(controlFor).find(Boolean);
          if (candidate) return candidate
        }
        rowIndex += direction
      }
      return null
    }
    let rowIndex = cell.parentElement.rowIndex + rowOffset;
    const colIndex = cell.cellIndex;
    while (rowIndex >= 0 && rowIndex < table.rows.length) {
      const row = table.rows[rowIndex];
      if (visibleRow(row)) {
        const direct = controlFor(row.cells[colIndex]);
        if (direct) return direct;
        const candidates = [...row.cells].map(controlFor).filter(Boolean);
        if (candidates.length) return candidates[Math.min(cell.cellIndex, candidates.length - 1)]
      }
      rowIndex += rowOffset || 1
    }
    return null
  };
  const commitAndMove = (control, rowOffset, colOffset = 0) => {
    const destination = nextControl(control, rowOffset, colOffset);
    control.blur();
    if (destination) requestAnimationFrame(() => focusControl(destination, true))
  };
  const remember = (control, before, after) => {
    if (before === after) return;
    undoStack.push({
      control,
      before,
      after
    });
    if (undoStack.length > 200) undoStack.shift();
    redoStack.length = 0
  };
  const applyHistory = (source, target, key) => {
    const entry = source.pop();
    if (!entry || !document.contains(entry.control)) return;
    target.push(entry);
    setValue(entry.control, entry[key]);
    focusControl(entry.control, true)
  };
  const applyMatrix = (startCell, matrix) => {
    const table = startCell.closest('table');
    const changes = [];
    const visibleRows = [...table.tBodies].flatMap(body => [...body.rows]).filter(visibleRow);
    const startVisibleIndex = visibleRows.indexOf(startCell.parentElement);
    if (startVisibleIndex < 0) return;
    matrix.forEach((values, rowOffset) => {
      const row = visibleRows[startVisibleIndex + rowOffset];
      values.forEach((value, colOffset) => {
        const control = controlFor(row?.cells[startCell.cellIndex + colOffset]);
        if (!control) return;
        const before = valueOf(control);
        setValue(control, value);
        changes.push({
          control,
          before,
          after: value
        })
      })
    });
    changes.forEach(change => remember(change.control, change.before, change.after))
  };
  const insertPlainText = (control, text) => {
    const selection = window.getSelection();
    if (!selection?.rangeCount || !control.contains(selection.anchorNode)) {
      control.innerText = text;
    } else {
      const range = selection.getRangeAt(0);
      range.deleteContents();
      const node = document.createTextNode(text);
      range.insertNode(node);
      range.setStartAfter(node);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range)
    }
    control.dispatchEvent(new Event('input', {
      bubbles: true
    }))
  };
  const tableKey = (table, index) => {
    const section = location.pathname.replace(/\d+/g, ':id');
    const headers = [...table.querySelectorAll('thead tr:first-child th')].map(th => th.innerText.trim()).join('|');
    return `sheet-widths:${section}:${index}:${headers}`
  };
  const installResizers = (table, tableIndex) => {
    const headers = [...table.querySelectorAll('thead tr:first-child th')];
    if (!headers.length) return;
    const stickyThrough = (table.dataset.stickyThrough || '').trim();
    const stickyThroughIndex = stickyThrough ?
      headers.findIndex(th => th.innerText.trim() === stickyThrough) : -1;
    const configuredStickyCount = Number.parseInt(table.dataset.stickyColumns || '', 10);
    const explicitStickyCount = stickyThroughIndex >= 0 ?
      stickyThroughIndex + 1 : configuredStickyCount;
    const key = tableKey(table, tableIndex);
    let saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(key) || '{}')
    } catch {}
    headers.forEach((th, index) => {
      th.dataset.sheetStickyX = Number.isFinite(explicitStickyCount) ?
        String(index < explicitStickyCount ? 1 : 0) :
        String(getComputedStyle(th).left !== 'auto' ? 1 : 0)
    });
    const syncStickyOffsets = () => {
      const stickyIndexes = headers
        .map((th, index) => th.dataset.sheetStickyX === '1' ? index : -1)
        .filter(index => index >= 0);
      const edgeIndex = stickyIndexes.at(-1);
      const columnOffsets = [];
      let offset = 0;
      headers.forEach((th, index) => {
        columnOffsets[index] = offset;
        offset += th.getBoundingClientRect().width
      });
      headers.forEach((th, index) => {
        [...table.rows].forEach(row => {
          const cell = row.cells[index];
          if (!cell) return;
          if (th.dataset.sheetStickyX !== '1') {
            cell.style.removeProperty('left');
            cell.classList.remove('sheet-sticky-x', 'sheet-sticky-edge');
            return
          }

          cell.style.left = `${columnOffsets[index]}px`;
          cell.classList.add('sheet-sticky-x');
          cell.classList.toggle('sheet-sticky-edge', index === edgeIndex)
        })
      })
    };
    const setColumnWidth = (index, width, persist = true, sync = true) => {
      const safeWidth = Math.max(58, Math.min(720, Math.round(width)));
      [...table.rows].forEach(row => {
        const cell = row.cells[index];
        if (!cell) return;
        const centered = getComputedStyle(cell).textAlign === 'center';
        cell.classList.toggle('sheet-align-center', centered);
        cell.classList.toggle('sheet-narrow', safeWidth < 100);
        cell.style.width = `${safeWidth}px`;
        cell.style.minWidth = `${safeWidth}px`;
        cell.style.maxWidth = `${safeWidth}px`
      });
      saved[index] = safeWidth;
      if (persist) localStorage.setItem(key, JSON.stringify(saved));
      if (sync) syncStickyOffsets()
    };
    headers.forEach((th, index) => {
      if (saved[index]) setColumnWidth(index, saved[index], false, false);
      th.classList.add('sheet-resizable');
      const handle = document.createElement('span');
      handle.className = 'sheet-column-resizer';
      handle.title = '드래그: 열 너비 조절 · 더블클릭: 내용에 맞춤';
      handle.addEventListener('pointerdown', event => {
        event.preventDefault();
        const startX = event.clientX;
        const startWidth = th.getBoundingClientRect().width;
        let resizeFrame = 0;
        let pendingWidth = startWidth;
        handle.setPointerCapture(event.pointerId);
        handle.classList.add('is-resizing');
        document.body.classList.add('sheet-resizing');
        const move = moveEvent => {
          pendingWidth = startWidth + moveEvent.clientX - startX;
          if (resizeFrame) return;
          resizeFrame = requestAnimationFrame(() => {
            resizeFrame = 0;
            setColumnWidth(index, pendingWidth, false)
          })
        };
        const up = () => {
          if (resizeFrame) {
            cancelAnimationFrame(resizeFrame);
            resizeFrame = 0;
            setColumnWidth(index, pendingWidth, false)
          }
          localStorage.setItem(key, JSON.stringify(saved));
          handle.classList.remove('is-resizing');
          document.body.classList.remove('sheet-resizing');
          handle.removeEventListener('pointermove', move);
          handle.removeEventListener('pointerup', up)
        };
        handle.addEventListener('pointermove', move);
        handle.addEventListener('pointerup', up)
      });
      handle.addEventListener('dblclick', event => {
        event.preventDefault();
        const widths = [...table.rows].slice(0, 80).map(row => {
          const cell = row.cells[index];
          return cell ? Math.min(cell.scrollWidth + 24, 420) : 58
        });
        setColumnWidth(index, Math.max(...widths, 58))
      });
      th.appendChild(handle)
    });
    syncStickyOffsets();
    if (document.fonts?.ready) document.fonts.ready.then(syncStickyOffsets);
    let resizeFrame = 0;
    window.addEventListener('resize', () => {
      if (resizeFrame) return;
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = 0;
        syncStickyOffsets()
      })
    }, {
      passive: true
    });
    table.addEventListener('spreadsheet:layout-changed', () => {
      requestAnimationFrame(() => {
        Object.entries(saved).forEach(([index, width]) => {
          setColumnWidth(Number(index), width, false, false)
        });
        syncStickyOffsets();
        requestAnimationFrame(syncStickyOffsets)
      })
    });
    if ('ResizeObserver' in window) {
      const stickyResizeObserver = new ResizeObserver(() => syncStickyOffsets());
      headers.forEach(th => stickyResizeObserver.observe(th))
    }
  };

  document.querySelectorAll('table').forEach((table, index) => {
    if (![...table.tBodies].some(body => body.querySelector(editableSelector))) return;
    table.classList.add('spreadsheet-enhanced');
    tables.push(table);
    installResizers(table, index)
  });
  if (!tables.length) return;

  document.addEventListener('focusin', event => {
    const control = event.target.closest?.(editableSelector);
    if (!control || !control.closest('.spreadsheet-enhanced')) return;
    control.dataset.sheetBefore = valueOf(control);
    focusControl(control)
  });
  document.addEventListener('focusout', event => {
    const control = event.target.closest?.(editableSelector);
    if (!control || !control.closest('.spreadsheet-enhanced')) return;
    remember(control, control.dataset.sheetBefore ?? valueOf(control), valueOf(control));
    delete control.dataset.sheetBefore
  });
  document.addEventListener('spreadsheet:filter-changed', clearSelection);
  document.addEventListener('mousedown', event => {
    const cell = cellFor(event.target);
    if (!cell?.closest('.spreadsheet-enhanced') || !controlFor(cell)) return;
    if (event.shiftKey && selectionAnchor) {
      event.preventDefault();
      selectRange(selectionAnchor, cell)
    } else if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      if (selected.has(cell)) {
        selected.delete(cell);
        cell.classList.remove('sheet-selected')
      } else selectCell(cell, true);
      selectionAnchor = cell
    } else if (!cell.classList.contains('sheet-selected')) {
      clearSelection();
      selectionAnchor = cell
    }
  });
  document.addEventListener('keydown', event => {
    const control = event.target.closest?.(editableSelector);
    const inSheet = control?.closest?.('.spreadsheet-enhanced');
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === 'z') {
      event.preventDefault();
      applyHistory(event.shiftKey ? redoStack : undoStack, event.shiftKey ? undoStack : redoStack, event.shiftKey ? 'after' : 'before');
      return
    }
    if ((event.ctrlKey || event.metaKey) && key === 'y') {
      event.preventDefault();
      applyHistory(redoStack, undoStack, 'after');
      return
    }
    if ((event.ctrlKey || event.metaKey) && key === 'd' && inSheet) {
      const cells = orderedSelected();
      event.preventDefault();
      if (cells.length <= 1) return;
      const source = valueOf(controlFor(cells[0]));
      cells.slice(1).forEach(cell => {
        const target = controlFor(cell);
        const before = valueOf(target);
        setValue(target, source);
        remember(target, before, source)
      });
      return
    }
    if (!inSheet) return;
    if (event.key === 'Enter') {
      if (event.shiftKey && (control.matches('textarea') || control.isContentEditable)) return;
      event.preventDefault();
      commitAndMove(control, 1)
    } else if (event.key === 'Tab') {
      const destination = nextControl(control, 0, event.shiftKey ? -1 : 1);
      if (destination) {
        event.preventDefault();
        control.blur();
        requestAnimationFrame(() => focusControl(destination, true))
      }
    } else if (!event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const movement = {
        ArrowUp: [-1, 0],
        ArrowDown: [1, 0],
        ArrowLeft: [0, -1],
        ArrowRight: [0, 1]
      } [event.key];
      if (movement) {
        if (control.matches('.compact-datetime')) return;
        const destination = nextControl(control, movement[0], movement[1]);
        if (destination) {
          event.preventDefault();
          commitAndMove(control, movement[0], movement[1])
        }
      }
    }
  });
  document.addEventListener('copy', event => {
    const cell = cellFor(event.target);
    if (!cell?.closest('.spreadsheet-enhanced') || !selected.size) return;
    const cells = orderedSelected();
    const rowIndexes = [...new Set(cells.map(item => item.parentElement.rowIndex))];
    const text = rowIndexes.map(rowIndex => cells
      .filter(item => item.parentElement.rowIndex === rowIndex)
      .map(item => valueOf(controlFor(item)))
      .join('\t')).join('\n');
    event.preventDefault();
    event.clipboardData.setData('text/plain', text)
  });
  document.addEventListener('paste', event => {
    const cell = cellFor(event.target);
    if (!cell?.closest('.spreadsheet-enhanced')) return;
    const text = event.clipboardData?.getData('text/plain') || '';
    const control = controlFor(cell);
    if (control?.isContentEditable && !text.includes('\t') && selected.size <= 1) {
      event.preventDefault();
      insertPlainText(control, text);
      return
    }
    if (!text.includes('\t') && !text.includes('\n') && selected.size <= 1) return;
    event.preventDefault();

    const normalizedText = text.replace(/\r/g, '').replace(/\n+$/, '');
    if (!normalizedText) return;
    const matrix = normalizedText.split('\n').map(row => row.split('\t'));
    applyMatrix(selected.size ? orderedSelected()[0] : cell, matrix)
  })
})();