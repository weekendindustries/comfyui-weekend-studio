// The ComfyUI tab's change signature (web/weekend_studio.js runSignature, 0.2.2). Run:
//   node --test tests/web_signature.test.mjs
// The tab can't be imported outside ComfyUI (it imports ComfyUI's app.js), so the function is lifted
// from the source and run against a stand-in `app` shaped like ComfyUI's graph and widgets.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../web/weekend_studio.js', import.meta.url), 'utf8');
const from = src.indexOf('const CONTROL =');
const to = src.indexOf('\n}\n', src.indexOf('function runSignature(')) + 3;
assert.ok(from > 0 && to > from, 'runSignature found in the tab source');
const make = (app) => new Function('app', `${src.slice(from, to)}\nreturn runSignature;`)(app);

function setup({ control = 'randomize', seed = 1, text = 'a {cat|dog}', rolled = 'a cat', steps = 20 } = {}) {
  const seedW = { name: 'seed', value: seed };
  const ctl = { name: 'control_after_generate', value: control };
  seedW.linkedWidgets = [ctl];
  const nodes = [
    { id: 3, widgets: [seedW, ctl, { name: 'steps', value: steps }] },
    { id: 6, widgets: [{ name: 'text', value: text, options: { dynamicPrompts: true } }] },
  ];
  const prompt = {
    3: { class_type: 'KSampler', inputs: { seed, steps, model: ['4', 0] } },
    6: { class_type: 'CLIPTextEncode', inputs: { text: rolled, clip: ['4', 1] } },
  };
  return { sig: make({ graph: { nodes } }), prompt };
}

test('a re-rolled seed and a re-rolled dynamic prompt are not changes', () => {
  const a = setup({ seed: 1, rolled: 'a cat' });
  const b = setup({ seed: 987654, rolled: 'a dog' });
  assert.equal(a.sig(a.prompt), b.sig(b.prompt));
});

test('real edits are changes', () => {
  const base = setup();
  const sig = base.sig(base.prompt);
  const steps = setup({ steps: 30 });
  assert.notEqual(steps.sig(steps.prompt), sig, 'a setting changed');
  const text = setup({ text: 'a {fox|owl}', rolled: 'a fox' });
  assert.notEqual(text.sig(text.prompt), sig, 'the prompt template changed');
});

test('a fixed seed is a value like any other', () => {
  const a = setup({ control: 'fixed', seed: 1 });
  const b = setup({ control: 'fixed', seed: 2 });
  assert.notEqual(a.sig(a.prompt), b.sig(b.prompt));
});

test('wires are left alone and the prompt object is not touched', () => {
  const { sig, prompt } = setup();
  const before = JSON.stringify(prompt);
  const out = JSON.parse(sig(prompt));
  assert.deepEqual(out[3].inputs.model, ['4', 0]);
  assert.equal(JSON.stringify(prompt), before);
});

test('the control widget is found by position too (frontends without linkedWidgets)', () => {
  const sig = make({ graph: { nodes: [{ id: 3, widgets: [{ name: 'seed', value: 0 }, { name: 'control_after_generate', value: 'increment' }] }] } });
  const prompt = (seed) => ({ 3: { class_type: 'KSampler', inputs: { seed } } });
  assert.equal(sig(prompt(1)), sig(prompt(2)));
});
