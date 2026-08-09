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
      const data = Object.prototype.hasOwnProperty.call(cache, key)
        ? cache[key]
        : await options.load(target);
      if (sequence !== requestSequence) return {applied: false, stale: true};
      cache[key] = data;
      currentTarget = target;
      options.apply(data, target, previousTarget);
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
