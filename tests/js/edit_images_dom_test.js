// jsdom-driven runtime checks for llemon_image/image.html's multi-image
// edit UI (Task 13 Phase 2). Invoked as `node edit_images_dom_test.js
// <path-to-rendered-html>` by ../test_llemon_image_edit_dom.py, which
// renders the page against a fixture edit_meta covering every scenario
// this file exercises (see that file's `_EDIT_META` for the exact model
// ids/shapes referenced below).
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

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const htmlPath = process.argv[2];
if (!htmlPath) {
  console.error('usage: node edit_images_dom_test.js <path-to-rendered-html>');
  process.exit(2);
}
const html = fs.readFileSync(htmlPath, 'utf-8');

const windowErrors = [];
let lastFetch = null;

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  resources: 'usable',
  url: 'http://localhost/llemon/media/image-creator/',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = function (url, opts) {
      lastFetch = { url, opts };
      return Promise.resolve({
        headers: { get: () => 'application/json' },
        json: () => Promise.resolve({ file: 'out.png', files: ['out.png'] }),
        status: 200,
      });
    };
    window.onerror = function (msg, src, line, col) {
      windowErrors.push(String(msg) + (line ? ' @' + line + ':' + (col || '?') : ''));
    };
  },
});
const { window } = dom;
const doc = window.document;

function fire(el, type) {
  el.dispatchEvent(new window.Event(type, { bubbles: true }));
}

let failures = 0;
function step(name, fn) {
  try {
    fn();
    console.log('OK  ', name);
  } catch (e) {
    failures += 1;
    console.log('FAIL', name, '->', (e && e.stack) || e);
  }
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

function submitEdit(prompt) {
  lastFetch = null;
  doc.getElementById('prompt').value = prompt || 'edit it';
  fire(doc.getElementById('imagegen-form'), 'submit');
}

function errorText() {
  return doc.getElementById('error-msg').textContent;
}

setTimeout(function () {
  step('page loads and runs with no window errors', function () {
    if (windowErrors.length) throw new Error(windowErrors.join(' | '));
  });

  step('switch Type to edit shows the multi-image section, hides Upscale\'s', function () {
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
  step('ordered schema: add up to cap, position tags, no role selects', function () {
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

  step('ordered schema: reorder controls are real buttons with correct boundary state', function () {
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

  step('ordered schema: move-right on the first item swaps it with the second', function () {
    const items = editListItems();
    const order0 = Array.from(items).map(function (it) { return it.title; });
    items[0].querySelectorAll('.edit-thumb-move')[1].click(); // move-right
    const order1 = Array.from(editListItems()).map(function (it) { return it.title; });
    if (order1[0] !== order0[1] || order1[1] !== order0[0]) {
      throw new Error('move-right did not swap positions 0 and 1: ' + order1.join(','));
    }
  });

  step('ordered schema: submitted body reflects order, carries no role key', function () {
    const order = Array.from(editListItems()).map(function (it) { return it.title; });
    submitEdit('edit all three');
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

  step('ordered schema: removing an item drops it from the list', function () {
    const before = editListItems().length;
    editListItems()[0].querySelector('.edit-thumb-remove').click();
    if (editListItems().length !== before - 1) throw new Error('remove did not shrink the list');
  });

  // -- min_count enforcement (P2, ordered schema with min_count > 1) --
  step('ordered schema with min_count 2: one image is rejected client-side', function () {
    selectEditModel('ordered-min2'); // effective_max_count 2, min_count 2, no roles
    addImagesByIndex([0]);
    submitEdit('too few');
    if (lastFetch) throw new Error('fetch should not have been called with only 1 of 2 required images');
    if (!/at least 2/.test(errorText())) {
      throw new Error('unexpected error message: ' + errorText());
    }
  });

  step('ordered schema with min_count 2: two images submit successfully', function () {
    addImagesByIndex([1]);
    if (editListItems().length !== 2) throw new Error('expected 2 selected images');
    submitEdit('enough now');
    if (!lastFetch) throw new Error('fetch should have been called with 2 images');
    const body = JSON.parse(lastFetch.opts.body);
    if (body.images.length !== 2) throw new Error('wrong image count: ' + body.images.length);
  });

  // -- Named roles: assignment, duplicate/missing-role rejection --
  step('named schema: role selects appear, assigning roles and submitting works', function () {
    selectEditModel('named-roles'); // effective_max_count 2, roles: first, second (both required)
    addImagesByIndex([0, 1]);
    const selects = doc.querySelectorAll('#edit-images-list select.edit-thumb-role');
    if (selects.length !== 2) throw new Error('expected 2 role selects, got ' + selects.length);
    selects[0].value = 'first';
    fire(selects[0], 'change');
    selects[1].value = 'second';
    fire(selects[1], 'change');
    submitEdit('combine them');
    if (!lastFetch) throw new Error('fetch was not called');
    const body = JSON.parse(lastFetch.opts.body);
    const roles = body.images.map(function (i) { return i.role; });
    if (roles.join(',') !== 'first,second') throw new Error('roles not carried through: ' + roles);
  });

  step('named schema: duplicate role assignment is rejected client-side', function () {
    const selects = doc.querySelectorAll('#edit-images-list select.edit-thumb-role');
    selects[1].value = 'first'; // duplicate of selects[0]
    fire(selects[1], 'change');
    submitEdit('dup');
    if (lastFetch) throw new Error('fetch should not have been called with a duplicate role');
    if (!/more than one image/.test(errorText())) {
      throw new Error('unexpected error message: ' + errorText());
    }
  });

  step('named schema: missing required role is rejected client-side', function () {
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
    submitEdit('missing req2');
    if (lastFetch) throw new Error('fetch should not have been called with a missing required role');
    if (!/Missing required role.*req2/.test(errorText())) {
      throw new Error('unexpected error message: ' + errorText());
    }
  });

  // -- Role-scoped eligibility (top-level intersection can be empty) --
  step('a model whose roles accept data_url via disjoint paths is still selectable', function () {
    const emSel = doc.getElementById('edit-model-sel');
    const opt = Array.from(emSel.options).find(function (o) { return o.value === 'disjoint-named-roles'; });
    if (!opt) throw new Error('option missing');
    if (opt.disabled) throw new Error('wrongly disabled: ' + opt.title);
  });

  // -- All-optional warning semantics (OR, not AND, across usable roles) --
  step('all-optional schema stays enabled if one usable role needs no warning', function () {
    const emSel = doc.getElementById('edit-model-sel');
    const opt = Array.from(emSel.options).find(function (o) { return o.value === 'mixed-optional-roles'; });
    if (opt.disabled) throw new Error('wrongly disabled: ' + opt.title);
  });

  step('role dropdown disables unusable options with an explanatory label', function () {
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

  step('selection cap is reduced to the usable-role count, not the declared max', function () {
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
  step('switching between same-shaped schemas with different role facts clears a stale selection', function () {
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
  });

  // -- Regression: Upscale's untouched single-image picker --
  step('Upscale keeps single-select-and-close picker behavior', function () {
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
  process.exit(failures ? 1 : 0);
}, 50);
