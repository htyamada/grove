// jsdom-driven runtime checks for llemon_image/image.html's multi-image
// edit UI (Task 13 Phase 2). Invoked as `node edit_images_dom_test.js
// <path-to-rendered-html>` by ../test_llemon_image_edit_dom.py, which
// renders the page against a fixture edit_meta covering every scenario
// this file exercises (see that file's `_build_edit_meta()` for the
// exact model ids/shapes referenced below).
//
// This exists because the committed Python suite otherwise only checks
// JS *syntax* (`node --check`) and string-matches the rendered source
// (tests/test_image_creator_render.py) -- neither can catch a runtime
// `ReferenceError`, a stale closure variable, or an event handler that
// silently never fires. Every scenario below reproduces a defect this
// series of reviews actually found by executing the page for real; see
// prj/llemon/upgrade.md's Task 13 (in the hty7 repo) for the history.
//
// Exits 0 with one "OK <name>" line per passing step, or exits 1 with at
// least one "FAIL <name> -> ..." line naming what broke.
//
// Two properties matter for this harness's own correctness, both found
// by review rather than by using it:
//
// 1. A runtime exception thrown inside an event listener during
//    dispatchEvent() is not propagated to the caller -- it is reported
//    via window.onerror (or, for an async listener's rejected promise,
//    'unhandledrejection') and dispatchEvent() returns normally. A step
//    whose own explicit assertions happen to pass regardless would
//    therefore print OK even though the interaction it just performed
//    threw. So every step checks for newly accumulated window errors
//    after it runs, not only once at the very start.
// 2. The page's submit handlers are `async function`s invoked from a
//    synchronous dispatchEvent() call, which does not await them --
//    their internal `await fetch(...)`/`await resp.json()` continuations
//    are still pending microtasks when dispatchEvent() returns. Exiting
//    (or even just asserting) immediately after firing 'submit' would
//    therefore never actually exercise the response-handling code that
//    runs after the mocked fetch/json promises resolve. submitEdit()
//    awaits one macrotask tick to let those microtasks drain before the
//    caller inspects state, and the harness's own top level is async and
//    sets process.exitCode rather than calling process.exit() before
//    those continuations (or Node's unhandledRejection reporting) would
//    have a chance to run.

const fs = require('fs');
const { JSDOM } = require('jsdom');

const htmlPath = process.argv[2];
if (!htmlPath) {
  console.error('usage: node edit_images_dom_test.js <path-to-rendered-html>');
  process.exit(2);
}
const html = fs.readFileSync(htmlPath, 'utf-8');

const windowErrors = [];
let lastFetch = null;
let fetchImpl = defaultFetchMock;

function defaultFetchMock() {
  return Promise.resolve({
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve({ file: 'out.png', files: ['out.png'] }),
    status: 200,
  });
}

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  resources: 'usable',
  url: 'http://localhost/llemon/media/image-creator/',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = function (url, opts) {
      lastFetch = { url, opts };
      return fetchImpl(url, opts);
    };
    window.onerror = function (msg, src, line, col) {
      windowErrors.push('onerror: ' + String(msg) + (line ? ' @' + line + ':' + (col || '?') : ''));
    };
    window.addEventListener('unhandledrejection', function (event) {
      const reason = event && event.reason;
      const text = reason && (reason.stack || reason.message) ? (reason.stack || reason.message) : reason;
      windowErrors.push('unhandledrejection: ' + text);
    });
  },
});
const { window } = dom;
const doc = window.document;

function fire(el, type) {
  el.dispatchEvent(new window.Event(type, { bubbles: true }));
}

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

let failures = 0;
let checkedErrorsUpTo = 0;

async function step(name, fn) {
  let stepError = null;
  try {
    await fn();
  } catch (e) {
    stepError = e;
  }
  // Checked after every step (including this one's own interactions),
  // not only once before the first -- see the file header.
  const newErrors = windowErrors.slice(checkedErrorsUpTo);
  checkedErrorsUpTo = windowErrors.length;
  if (stepError) {
    failures += 1;
    console.log('FAIL', name, '->', (stepError && stepError.stack) || stepError);
    return;
  }
  if (newErrors.length) {
    failures += 1;
    console.log('FAIL', name, '-> runtime error(s) surfaced during this step:', newErrors.join(' | '));
    return;
  }
  console.log('OK  ', name);
}

function selectEditModel(modelId) {
  const emSel = doc.getElementById('edit-model-sel');
  emSel.value = modelId;
  fire(emSel, 'change');
}

function pickerThumbs() {
  return Array.from(doc.querySelectorAll('#image-picker-grid .image-thumb-wrap'));
}

function addImagesByIndex(indices) {
  fire(doc.getElementById('edit-images-btn'), 'click');
  const thumbs = pickerThumbs();
  indices.forEach(function (i) { fire(thumbs[i], 'click'); });
  fire(doc.getElementById('image-picker-close'), 'click');
}

function editListItems() {
  return doc.getElementById('edit-images-list').querySelectorAll('.ref-thumb-item');
}

async function submitEdit(prompt) {
  lastFetch = null;
  doc.getElementById('prompt').value = prompt || 'edit it';
  fire(doc.getElementById('imagegen-form'), 'submit');
  // See the file header, point 2: let the still-pending fetch/json
  // microtask chain drain before returning control to the caller.
  await sleep(0);
}

function errorText() {
  return doc.getElementById('error-msg').textContent;
}

function submitButtonDisabled() {
  return doc.getElementById('generate-btn').disabled;
}

async function main() {
  await sleep(50); // let the page's own <script> blocks finish setting up

  await step('page loads and runs with no window errors', function () {});

  await step('switch Type to edit shows the multi-image section, hides Upscale\'s', function () {
    doc.getElementById('image-type').value = 'edit';
    fire(doc.getElementById('image-type'), 'change');
    if (doc.getElementById('edit-images-section').style.display === 'none') {
      throw new Error('edit-images-section still hidden');
    }
    if (doc.getElementById('source-image-section').style.display !== 'none') {
      throw new Error('source-image-section should be hidden for edit type');
    }
  });

  // -- Ordered multi-image (no roles): add/reorder/remove, submitted order --
  await step('ordered schema: add up to cap, position tags, no role selects', function () {
    selectEditModel('ordered-multi'); // effective_max_count 3, no roles
    addImagesByIndex([0, 1, 2]); // cat, dog, bird
    const items = editListItems();
    if (items.length !== 3) throw new Error('expected 3 items, got ' + items.length);
    if (doc.querySelector('#edit-images-list select.edit-thumb-role')) {
      throw new Error('an ordered schema must not show role selects');
    }
    const tags = Array.from(items).map(function (it) {
      return it.querySelector('.edit-thumb-position').textContent;
    });
    if (tags.join(',') !== '#1,#2,#3') throw new Error('position tags wrong: ' + tags.join(','));
  });

  await step('ordered schema: reorder controls are real buttons with correct boundary state', function () {
    const items = editListItems();
    const buttons = items[0].querySelectorAll('.edit-thumb-move');
    const moveLeft0 = buttons[0];
    const moveRight0 = buttons[1];
    if (moveLeft0.tagName !== 'BUTTON' || moveRight0.tagName !== 'BUTTON') {
      throw new Error('move controls must be <button> elements, not <span>');
    }
    if (!moveLeft0.hasAttribute('aria-label') || !moveRight0.hasAttribute('aria-label')) {
      throw new Error('move controls must carry an aria-label');
    }
    if (!moveLeft0.disabled) throw new Error('first item\'s move-earlier should be disabled');
    const lastButtons = items[items.length - 1].querySelectorAll('.edit-thumb-move');
    if (!lastButtons[1].disabled) throw new Error('last item\'s move-later should be disabled');

    // A disabled <button>'s native .click() must not fire the handler.
    const before = editListItems()[0].title;
    moveLeft0.click();
    if (editListItems()[0].title !== before) {
      throw new Error('clicking a disabled move button changed order');
    }

    const removeBtn = items[0].querySelector('.edit-thumb-remove');
    if (removeBtn.tagName !== 'BUTTON' || !removeBtn.hasAttribute('aria-label')) {
      throw new Error('remove control must be a labeled <button>');
    }
  });

  await step('ordered schema: move-right on the first item swaps it with the second', function () {
    const items = editListItems();
    const order0 = Array.from(items).map(function (it) { return it.title; });
    items[0].querySelectorAll('.edit-thumb-move')[1].click(); // move-right
    const order1 = Array.from(editListItems()).map(function (it) { return it.title; });
    if (order1[0] !== order0[1] || order1[1] !== order0[0]) {
      throw new Error('move-right did not swap positions 0 and 1: ' + order1.join(','));
    }
  });

  await step('ordered schema: submitted body reflects order, carries no role key', async function () {
    const order = Array.from(editListItems()).map(function (it) { return it.title; });
    await submitEdit('edit all three');
    if (!lastFetch) throw new Error('fetch was not called');
    const body = JSON.parse(lastFetch.opts.body);
    const fnames = body.images.map(function (i) { return i.filename; });
    if (fnames.join(',') !== order.join(',')) {
      throw new Error('submitted order ' + fnames + ' != UI order ' + order);
    }
    if (body.images.some(function (i) { return 'role' in i; })) {
      throw new Error('ordered images must not carry a role key');
    }
  });

  await step(
    'a successful submission\'s async response handling completes without error',
    function () {
      // Only meaningful now that submitEdit() actually awaits the pending
      // fetch/json microtasks (see the file header, point 2) -- before
      // that fix, this step would trivially "pass" no matter what
      // applyUpscaleResult() did, because it would never have run yet.
      const err = errorText();
      if (err) throw new Error('expected no error text after a successful submission, got: ' + err);
      if (submitButtonDisabled()) {
        throw new Error('submit button should be re-enabled once the finally block has run');
      }
    },
  );

  await step(
    'a rejected fetch is caught and surfaces "Request failed" via the async catch path',
    async function () {
      const original = fetchImpl;
      fetchImpl = function () { return Promise.reject(new Error('network down')); };
      try {
        if (editListItems().length === 0) addImagesByIndex([0]);
        await submitEdit('will fail');
      } finally {
        fetchImpl = original;
      }
      if (!/Request failed: network down/.test(errorText())) {
        throw new Error('expected a caught "Request failed" message, got: ' + errorText());
      }
    },
  );

  await step('ordered schema: removing an item drops it from the list', function () {
    const before = editListItems().length;
    editListItems()[0].querySelector('.edit-thumb-remove').click();
    if (editListItems().length !== before - 1) throw new Error('remove did not shrink the list');
  });

  // -- min_count enforcement (P2, ordered schema with min_count > 1) --
  await step('ordered schema with min_count 2: one image is rejected client-side', async function () {
    selectEditModel('ordered-min2'); // effective_max_count 2, min_count 2, no roles
    addImagesByIndex([0]);
    await submitEdit('too few');
    if (lastFetch) throw new Error('fetch should not have been called with only 1 of 2 required images');
    if (!/at least 2/.test(errorText())) {
      throw new Error('unexpected error message: ' + errorText());
    }
  });

  await step('ordered schema with min_count 2: two images submit successfully', async function () {
    addImagesByIndex([1]);
    if (editListItems().length !== 2) throw new Error('expected 2 selected images');
    await submitEdit('enough now');
    if (!lastFetch) throw new Error('fetch should have been called with 2 images');
    const body = JSON.parse(lastFetch.opts.body);
    if (body.images.length !== 2) throw new Error('wrong image count: ' + body.images.length);
  });

  // -- Named roles: assignment, duplicate/missing-role rejection --
  await step('named schema: role selects appear, assigning roles and submitting works', async function () {
    selectEditModel('named-roles'); // effective_max_count 2, roles: first, second (both required)
    addImagesByIndex([0, 1]);
    const selects = doc.querySelectorAll('#edit-images-list select.edit-thumb-role');
    if (selects.length !== 2) throw new Error('expected 2 role selects, got ' + selects.length);
    selects[0].value = 'first';
    fire(selects[0], 'change');
    selects[1].value = 'second';
    fire(selects[1], 'change');
    await submitEdit('combine them');
    if (!lastFetch) throw new Error('fetch was not called');
    const body = JSON.parse(lastFetch.opts.body);
    const roles = body.images.map(function (i) { return i.role; });
    if (roles.join(',') !== 'first,second') throw new Error('roles not carried through: ' + roles);
  });

  await step('named schema: duplicate role assignment is rejected client-side', async function () {
    const selects = doc.querySelectorAll('#edit-images-list select.edit-thumb-role');
    selects[1].value = 'first'; // duplicate of selects[0]
    fire(selects[1], 'change');
    await submitEdit('dup');
    if (lastFetch) throw new Error('fetch should not have been called with a duplicate role');
    if (!/more than one image/.test(errorText())) {
      throw new Error('unexpected error message: ' + errorText());
    }
  });

  await step('named schema: missing required role is rejected client-side', async function () {
    // For an all-required schema, min_count equals the required-role
    // count, so meeting min_count with no duplicates always covers every
    // required role by pigeonhole -- that case is already exercised by
    // the min_count check above. A genuinely distinct "missing required
    // role" (count satisfied, no duplicates, but a required role still
    // unfilled) needs a mix of required and optional roles: assign the
    // optional role instead of the second required one.
    selectEditModel('mixed-required-optional'); // required: req1, req2; optional: opt1; min_count 2
    addImagesByIndex([0, 1]);
    const selects = doc.querySelectorAll('#edit-images-list select.edit-thumb-role');
    selects[0].value = 'req1';
    fire(selects[0], 'change');
    selects[1].value = 'opt1';
    fire(selects[1], 'change');
    await submitEdit('missing req2');
    if (lastFetch) throw new Error('fetch should not have been called with a missing required role');
    if (!/Missing required role.*req2/.test(errorText())) {
      throw new Error('unexpected error message: ' + errorText());
    }
  });

  // -- Role-scoped eligibility (top-level intersection can be empty) --
  await step('a model whose roles accept data_url via disjoint paths is still selectable', function () {
    const emSel = doc.getElementById('edit-model-sel');
    const opt = Array.from(emSel.options).find(function (o) { return o.value === 'disjoint-named-roles'; });
    if (!opt) throw new Error('option missing');
    if (opt.disabled) throw new Error('wrongly disabled: ' + opt.title);
  });

  // -- All-optional warning semantics (OR, not AND, across usable roles) --
  await step('all-optional schema stays enabled if one usable role needs no warning', function () {
    const emSel = doc.getElementById('edit-model-sel');
    const opt = Array.from(emSel.options).find(function (o) { return o.value === 'mixed-optional-roles'; });
    if (opt.disabled) throw new Error('wrongly disabled: ' + opt.title);
  });

  await step('role dropdown disables unusable options with an explanatory label', function () {
    selectEditModel('mixed-optional-roles'); // roles: warned, clean, unreachable; effective_max_count 2
    addImagesByIndex([0]);
    const options = Array.from(doc.querySelector('#edit-images-list select.edit-thumb-role').options);
    const warned = options.find(function (o) { return o.value === 'warned'; });
    const clean = options.find(function (o) { return o.value === 'clean'; });
    const unreachable = options.find(function (o) { return o.value === 'unreachable'; });
    if (!warned.disabled || !/data-handling warning/.test(warned.textContent)) {
      throw new Error('warned role option not disabled+labeled: ' + warned.textContent);
    }
    if (clean.disabled) throw new Error('clean role option should stay enabled');
    if (!unreachable.disabled || !/data URL unsupported/.test(unreachable.textContent)) {
      throw new Error('unreachable role option not disabled+labeled: ' + unreachable.textContent);
    }
  });

  await step('selection cap is reduced to the usable-role count, not the declared max', function () {
    // Only 'clean' is usable, so the cap must be 1 even though
    // effective_max_count is 2.
    const thumbs = pickerThumbs();
    fire(doc.getElementById('edit-images-btn'), 'click');
    fire(thumbs[1], 'click'); // attempt a second image
    if (editListItems().length !== 1) {
      throw new Error('cap should hold at 1, got ' + editListItems().length);
    }
    if (!doc.getElementById('image-picker-grid').classList.contains('at-cap')) {
      throw new Error('picker should show at-cap once the single usable-role slot fills');
    }
    if (!doc.getElementById('edit-images-btn').disabled) {
      throw new Error('Add image button should be disabled at the reduced cap');
    }
    fire(doc.getElementById('image-picker-close'), 'click');
  });

  // -- Compatibility signature: same names, different transport/warning facts --
  await step(
    'switching between same-shaped schemas with different role facts clears a stale selection',
    function () {
      selectEditModel('compat-a'); // role 'x' (optional, clean), role 'y' (optional, clean)
      addImagesByIndex([0]);
      const selectA = doc.querySelector('#edit-images-list select.edit-thumb-role');
      selectA.value = 'x';
      fire(selectA, 'change');
      if (editListItems().length !== 1) throw new Error('setup: expected 1 selected image under compat-a');

      // compat-b has identical shape/effective_max_count/min_count/role
      // names, but role 'x' now requires warning consent -- the old
      // signature (names only) would treat this as "the same schema" and
      // keep the stale selection/role assignment around.
      selectEditModel('compat-b');
      if (editListItems().length !== 0) {
        throw new Error('stale selection should have been cleared on the role-fact change, got '
          + editListItems().length + ' items');
      }
    },
  );

  // -- Regression: Upscale's untouched single-image picker --
  await step('Upscale keeps single-select-and-close picker behavior', function () {
    doc.getElementById('image-type').value = 'upscale';
    fire(doc.getElementById('image-type'), 'change');
    fire(doc.getElementById('source-image-btn'), 'click');
    const title = doc.getElementById('image-picker-title').textContent;
    if (title !== 'Select source image') throw new Error('unexpected picker title: ' + title);
    fire(pickerThumbs()[0], 'click');
    if (doc.getElementById('image-picker').style.display !== 'none') {
      throw new Error('single-select picker should auto-close');
    }
    const fname = doc.getElementById('source-image-section').dataset.selectedFname;
    if (!fname) throw new Error('source image not recorded');
  });

  console.log(failures ? 'DONE (' + failures + ' failure(s))' : 'DONE');
  process.exitCode = failures ? 1 : 0;
}

main().catch(function (err) {
  console.error('harness crashed:', (err && err.stack) || err);
  process.exitCode = 1;
});
