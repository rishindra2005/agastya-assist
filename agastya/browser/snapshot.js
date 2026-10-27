(() => {
  /**
   * Determine whether an element is visible to the user.
   * Skips elements with display:none, visibility:hidden, aria-hidden, disabled, or zero size.
   */
  function isVisible(el) {
    if (!el) return false;
    if (el.disabled) return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    if (el.type === 'hidden') return false;

    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') {
      return false;
    }

    if (typeof el.checkVisibility === 'function') {
      if (!el.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) {
        return false;
      }
    } else {
      if (el.offsetParent === null && style.position !== 'fixed') {
        return false;
      }
    }

    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0 && el.type !== 'radio' && el.type !== 'checkbox') {
      return false;
    }

    return true;
  }

  function cleanText(t) {
    if (!t) return '';
    return t.replace(/\s+/g, ' ').trim();
  }

  /**
   * Resolve label text for an element via:
   * 1. <label for=id>
   * 2. wrapping <label>
   * 3. aria-label
   * 4. aria-labelledby
   * 5. placeholder
   * 6. nearest preceding text in table row / parent container
   */
  function getElementLabel(el) {
    // 1. <label for=id>
    if (el.id) {
      try {
        const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (lbl && cleanText(lbl.innerText)) {
          return cleanText(lbl.innerText);
        }
      } catch (e) {}
    }

    // 2. wrapping <label>
    const parentLabel = el.closest('label');
    if (parentLabel && cleanText(parentLabel.innerText)) {
      return cleanText(parentLabel.innerText);
    }

    // 3. aria-label
    const aria = el.getAttribute('aria-label');
    if (aria && cleanText(aria)) {
      return cleanText(aria);
    }

    // 4. aria-labelledby
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = labelledby.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean);
      const text = parts.map(p => cleanText(p.innerText || p.textContent)).filter(Boolean).join(' ');
      if (text) return text;
    }

    // 5. placeholder
    const ph = el.getAttribute('placeholder');
    if (ph && cleanText(ph)) {
      return cleanText(ph);
    }

    // 6. nearest preceding text in the same table row
    const tr = el.closest('tr');
    if (tr) {
      const td = el.closest('td, th');
      let prevCell = td ? td.previousElementSibling : null;
      while (prevCell) {
        const text = cleanText(prevCell.innerText || prevCell.textContent);
        if (text && text.length < 150) return text;
        prevCell = prevCell.previousElementSibling;
      }
    }

    // 7. nearest preceding text / label in parent
    let cur = el;
    while (cur && cur !== document.body) {
      let prev = cur.previousElementSibling;
      while (prev) {
        const t = cleanText(prev.innerText || prev.textContent);
        if (t && t.length < 150) return t;
        prev = prev.previousElementSibling;
      }
      cur = cur.parentElement;
    }

    return el.name || el.id || '';
  }

  /**
   * Resolve group label for radio/checkbox groups.
   */
  function getGroupLabel(inputs) {
    const first = inputs[0];

    // Check <fieldset><legend>
    const fieldset = first.closest('fieldset');
    if (fieldset) {
      const legend = fieldset.querySelector('legend');
      if (legend && cleanText(legend.innerText)) {
        return cleanText(legend.innerText);
      }
    }

    // Check preceding label or heading on container
    const container = first.closest('.radio-group, .checkbox-group') || first.parentElement;
    if (container) {
      let prev = container.previousElementSibling;
      while (prev) {
        const t = cleanText(prev.innerText || prev.textContent);
        if (t && t.length < 150) return t;
        prev = prev.previousElementSibling;
      }
      if (container.parentElement) {
        let pprev = container.parentElement.previousElementSibling;
        while (pprev) {
          const t = cleanText(pprev.innerText || pprev.textContent);
          if (t && t.length < 150) return t;
          pprev = pprev.previousElementSibling;
        }
      }
    }

    if (container && container.getAttribute('aria-label')) {
      return cleanText(container.getAttribute('aria-label'));
    }

    // Fallback to label of first element
    const fallback = getElementLabel(first);
    if (fallback) return fallback;

    return first.name || '';
  }

  // Scan existing assigned data-agastya-ref attributes to reuse refs and keep numbering stable
  let maxFieldRef = 0;
  let maxActionRef = 0;
  const existingElements = document.querySelectorAll('[data-agastya-ref]');
  existingElements.forEach(el => {
    const ref = el.getAttribute('data-agastya-ref') || '';
    const mField = ref.match(/^f(\d+)$/);
    if (mField) {
      maxFieldRef = Math.max(maxFieldRef, parseInt(mField[1], 10));
    }
    const mAction = ref.match(/^a(\d+)$/);
    if (mAction) {
      maxActionRef = Math.max(maxActionRef, parseInt(mAction[1], 10));
    }
  });

  function getOrCreateFieldRef(elements) {
    for (const el of elements) {
      const r = el.getAttribute('data-agastya-ref');
      if (r && r.startsWith('f')) return r;
    }
    maxFieldRef += 1;
    return 'f' + maxFieldRef;
  }

  function getOrCreateActionRef(el) {
    const r = el.getAttribute('data-agastya-ref');
    if (r && r.startsWith('a')) return r;
    maxActionRef += 1;
    return 'a' + maxActionRef;
  }

  // 1. Collect visible fields
  const rawInputs = Array.from(document.querySelectorAll('input, select, textarea'));
  const visibleInputs = rawInputs.filter(isVisible);

  const fields = [];
  const processedGroups = new Set();

  for (const el of visibleInputs) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || el.type || '').toLowerCase();

    // Skip button-like inputs; they are actions
    if (type === 'submit' || type === 'button' || type === 'reset' || type === 'image') {
      continue;
    }

    // Handle Radio button groups
    if (type === 'radio') {
      const name = el.name;
      const groupKey = 'radio:' + (name || el.id);
      if (processedGroups.has(groupKey)) continue;
      processedGroups.add(groupKey);

      const groupInputs = name
        ? visibleInputs.filter(i => i.tagName.toLowerCase() === 'input' && (i.getAttribute('type') || i.type || '').toLowerCase() === 'radio' && i.name === name)
        : [el];

      const ref = getOrCreateFieldRef(groupInputs);
      groupInputs.forEach(i => {
        i.setAttribute('data-agastya-ref', ref);
        if (i.value) i.setAttribute('data-agastya-option', i.value);
      });

      const label = getGroupLabel(groupInputs);
      const options = groupInputs.map(i => {
        const val = cleanText(i.value);
        const parentLbl = i.closest('label');
        const lbl = cleanText(parentLbl ? parentLbl.innerText : (i.getAttribute('aria-label') || ''));
        if (!lbl || lbl.toLowerCase() === val.toLowerCase()) return val || lbl;
        return val ? `${lbl} (${val})` : lbl;
      });

      const checked = groupInputs.find(i => i.checked);
      const isReq = groupInputs.some(i => i.required || i.getAttribute('aria-required') === 'true');

      fields.push({
        ref: ref,
        tag: 'input',
        type: 'radio',
        label: label,
        name: name || '',
        id: el.id || '',
        required: isReq,
        value: checked ? (checked.value || '') : '',
        options: options
      });
      continue;
    }

    // Handle Checkbox groups and standalone checkboxes
    if (type === 'checkbox') {
      const name = el.name;
      const groupInputs = name
        ? visibleInputs.filter(i => i.tagName.toLowerCase() === 'input' && (i.getAttribute('type') || i.type || '').toLowerCase() === 'checkbox' && i.name === name)
        : [el];

      if (groupInputs.length > 1) {
        const groupKey = 'checkbox:' + name;
        if (processedGroups.has(groupKey)) continue;
        processedGroups.add(groupKey);

        const ref = getOrCreateFieldRef(groupInputs);
        groupInputs.forEach(i => {
          i.setAttribute('data-agastya-ref', ref);
          if (i.value) i.setAttribute('data-agastya-option', i.value);
        });

        const label = getGroupLabel(groupInputs);
        const options = groupInputs.map(i => {
          const val = cleanText(i.value);
          const parentLbl = i.closest('label');
          const lbl = cleanText(parentLbl ? parentLbl.innerText : (i.getAttribute('aria-label') || ''));
          if (!lbl || lbl.toLowerCase() === val.toLowerCase()) return val || lbl;
          return val ? `${lbl} (${val})` : lbl;
        });

        const checkedVals = groupInputs.filter(i => i.checked).map(i => i.value);
        const isReq = groupInputs.some(i => i.required || i.getAttribute('aria-required') === 'true');

        fields.push({
          ref: ref,
          tag: 'input',
          type: 'checkbox',
          label: label,
          name: name,
          id: el.id || '',
          required: isReq,
          value: checkedVals.join(', '),
          options: options
        });
        continue;
      } else {
        const ref = getOrCreateFieldRef([el]);
        el.setAttribute('data-agastya-ref', ref);
        if (el.value) el.setAttribute('data-agastya-option', el.value);

        const label = getElementLabel(el);
        const isReq = el.required || el.getAttribute('aria-required') === 'true';

        fields.push({
          ref: ref,
          tag: 'input',
          type: 'checkbox',
          label: label,
          name: name || '',
          id: el.id || '',
          required: isReq,
          value: el.checked ? (el.value || 'true') : '',
          options: []
        });
        continue;
      }
    }

    // Handle <select>
    if (tag === 'select') {
      const ref = getOrCreateFieldRef([el]);
      el.setAttribute('data-agastya-ref', ref);

      const label = getElementLabel(el);
      const isReq = el.required || el.getAttribute('aria-required') === 'true';
      const options = Array.from(el.options)
        .filter(opt => !opt.disabled)
        .map(opt => {
          const t = cleanText(opt.text);
          const v = cleanText(opt.value);
          if (!v || t.toLowerCase() === v.toLowerCase()) return t;
          return `${t} (${v})`;
        });

      fields.push({
        ref: ref,
        tag: 'select',
        type: 'select',
        label: label,
        name: el.name || '',
        id: el.id || '',
        required: isReq,
        value: el.value || '',
        options: options
      });
      continue;
    }

    // Handle Text / Date / Email / Tel / Textarea / etc.
    const ref = getOrCreateFieldRef([el]);
    el.setAttribute('data-agastya-ref', ref);

    const label = getElementLabel(el);
    const isReq = el.required || el.getAttribute('aria-required') === 'true';

    fields.push({
      ref: ref,
      tag: tag,
      type: type || 'text',
      label: label,
      name: el.name || '',
      id: el.id || '',
      required: isReq,
      value: el.value || '',
      options: []
    });
  }

  // 2. Collect visible actions (button, a[href], [role=button], input[type=submit|button])
  const rawActions = Array.from(document.querySelectorAll(
    'button, input[type="submit"], input[type="button"], input[type="reset"], input[type="image"], a[href], [role="button"]'
  ));
  const visibleActions = [];
  const seenActionElements = new Set();

  for (const el of rawActions) {
    if (!isVisible(el)) continue;
    if (seenActionElements.has(el)) continue;
    seenActionElements.add(el);

    const ref = getOrCreateActionRef(el);
    el.setAttribute('data-agastya-ref', ref);

    const tag = el.tagName.toLowerCase();
    const href = el.getAttribute('href') || el.getAttribute('formaction') || '';
    let text = '';
    if (tag === 'input') {
      text = cleanText(el.value || el.getAttribute('aria-label') || 'Submit');
    } else {
      text = cleanText(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
    }

    visibleActions.push({
      ref: ref,
      tag: tag,
      text: text,
      href: href
    });
  }

  // 3. Visible page text truncated to 6000 chars
  const rawText = document.body ? (document.body.innerText || document.body.textContent || '') : '';
  const text = rawText
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s*\n/g, '\n')
    .trim()
    .slice(0, 6000);

  return {
    url: window.location.href,
    title: document.title || '',
    text: text,
    fields: fields,
    actions: visibleActions
  };
})()
