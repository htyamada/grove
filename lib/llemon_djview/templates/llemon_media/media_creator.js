function mediaPresentationTargetKey(target) {
  if (typeof target === 'string') return target;
  const value = target || {};
  return JSON.stringify([
    value.provider || '',
    value.api || '',
    value.operation || '',
    value.model || '',
  ]);
}

function mediaOverlayControls(fallback, target, keys) {
  const controls = target || {};
  if (Object.keys(controls).length === 0) return null;
  const result = {};
  for (const key of keys) {
    result[key] = Object.prototype.hasOwnProperty.call(controls, key)
      ? controls[key] : fallback[key];
  }
  return result;
}

function mediaChoiceControlState(values, defaultValue) {
  const choices = Array.isArray(values) ? values : [];
  const selected = choices.includes(defaultValue) ? defaultValue : choices[0];
  return {
    mode: choices.length ? 'select' : 'open',
    choices: choices.slice(),
    value: choices.length ? selected : '',
  };
}

function mediaApplyChoiceControl(doc, name, values, defaultValue) {
  const select = doc.getElementById(name);
  const open = doc.getElementById(name + '_open');
  const state = mediaChoiceControlState(values, defaultValue);
  select.innerHTML = '';
  for (const value of state.choices) {
    const option = doc.createElement('option');
    option.value = value;
    option.textContent = value;
    option.selected = value === state.value;
    select.appendChild(option);
  }
  select.style.display = state.mode === 'select' ? '' : 'none';
  open.style.display = state.mode === 'open' ? '' : 'none';
  open.value = state.mode === 'open' ? state.value : '';
  return state;
}

function mediaSetVisibleChoiceControl(doc, name, value) {
  const select = doc.getElementById(name);
  const open = doc.getElementById(name + '_open');
  if (select && select.style.display !== 'none') select.value = value;
  else if (open) open.value = value;
}

function mediaVisibleChoiceControlValue(doc, name) {
  const select = doc.getElementById(name);
  const open = doc.getElementById(name + '_open');
  return select && select.style.display !== 'none'
    ? select.value : (open ? open.value.trim() : '');
}

function mediaAfterTargetReady(targetReady, apply) {
  return Promise.resolve(targetReady).then(apply);
}

function mediaSameFieldDescriptor(left, right) {
  return JSON.stringify(left || null) === JSON.stringify(right || null);
}

function mediaUnresolvedGenerationState(model, reason) {
  return {
    selected_model: model,
    selected_target: null,
    availability: {
      enabled: false,
      reason: reason || 'model controls could not be loaded',
    },
  };
}

function createMediaRefreshController(options) {
  const cache = options.cache || Object.create(null);
  const keyFor = options.key || mediaPresentationTargetKey;
  const initialTarget = options.initialTarget;
  if (options.initialData !== undefined) {
    cache[keyFor(initialTarget)] = options.initialData;
  }
  let currentTarget = initialTarget;
  let requestSequence = 0;

  async function select(target) {
    const previousTarget = currentTarget;
    const sequence = ++requestSequence;
    if (options.commitOnBegin) currentTarget = target;
    if (options.begin) options.begin(target, previousTarget);

    try {
      const key = keyFor(target);
      const cached = Object.prototype.hasOwnProperty.call(cache, key);
      const loaded = cached ? cache[key] : await options.load(target);
      if (sequence !== requestSequence) return {applied: false, stale: true};
      const data = cached || !options.value ? loaded : options.value(loaded);
      if (!cached && options.transient) options.transient(loaded, data);
      options.apply(data, target, previousTarget);
      cache[key] = data;
      currentTarget = target;
      return {applied: true, stale: false, data};
    } catch (error) {
      if (sequence !== requestSequence) return {applied: false, stale: true};
      if (!options.commitOnBegin) currentTarget = previousTarget;
      if (options.fail) options.fail(error, target, previousTarget);
      throw error;
    }
  }

  return {
    cache,
    current: function () { return currentTarget; },
    currentKey: function () { return keyFor(currentTarget); },
    select,
  };
}
