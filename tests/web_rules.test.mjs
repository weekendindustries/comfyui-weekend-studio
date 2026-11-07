// The ComfyUI tab's rules (web/weekend_studio.js, 0.3.0): whose choices a workflow carries, and which
// settings the picker refuses. Run: node --test tests/web_rules.test.mjs
// Lifted from the source like web_signature.test.mjs (the tab imports ComfyUI's app.js).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../web/weekend_studio.js', import.meta.url), 'utf8');
const from = src.indexOf('// ── Rules (pure');
const to = src.indexOf('// ── end of rules');
assert.ok(from > 0 && to > from, 'the rules block is in the tab source');
const { recordState, widgetRefusal } = new Function(`${src.slice(from, to)}\nreturn { recordState, widgetRefusal };`)();

test('a workflow set up on another ComfyUI (or before 0.3.0) is foreign until "Use here"', () => {
  assert.equal(recordState({ id: null, marks: [] }, 'abc'), 'none');
  assert.equal(recordState({ id: 'txt2img', marks: [], install: 'abc' }, 'abc'), 'ours');
  assert.equal(recordState({ id: 'txt2img', marks: [], install: 'other' }, 'abc'), 'foreign');
  assert.equal(recordState({ id: 'txt2img', marks: [] }, 'abc'), 'foreign', 'no install id: from an older file');
  assert.equal(recordState({ id: null, marks: [{ node: '1', input: 'x' }] }, 'abc'), 'foreign', 'marks alone count');
  assert.equal(recordState({ id: 'a', marks: [], install: 'abc' }, null), 'foreign', 'not ours while this ComfyUI hasn\'t answered');
});

const node = (name, display, inputs, output = []) => ({ name, display_name: display, output, input: { required: inputs } });

test('the picker refuses paths, addresses and code, and keeps prompts', () => {
  const save = node('SaveTextFile', 'Save Text File', { path: ['STRING', {}], filename_prefix: ['STRING', {}], text: ['STRING', { multiline: true }] });
  assert.ok(widgetRefusal(save, 'path'));
  assert.ok(widgetRefusal(save, 'filename_prefix'));
  assert.equal(widgetRefusal(save, 'text'), null);
  const py = node('RunPythonCode', 'Run Python', { source: ['STRING', { multiline: true }], n: ['INT', {}] });
  assert.ok(widgetRefusal(py, 'source'));
  assert.equal(widgetRefusal(py, 'n'), null, 'numbers stay allowed');
  const enc = node('CLIPTextEncode', 'CLIP Text Encode (Prompt)', { text: ['STRING', { multiline: true }] }, ['CONDITIONING']);
  assert.equal(widgetRefusal(enc, 'text'), null);
  const ckpt = node('CheckpointLoaderSimple', 'Load Checkpoint', { ckpt_name: [['a.safetensors']] });
  assert.equal(widgetRefusal(ckpt, 'ckpt_name'), null, 'dropdowns are checked against ComfyUI\'s own list');
  assert.ok(widgetRefusal(node('X', 'X', { image_url: ['STRING', {}] }), 'image_url'));
});
