// node comfy-node/tools/write-service-json.mjs [--staging]
// Writes comfy-node/weekend_studio/service.json — where the node finds Weekend Studio — from the
// app's own env (apps/weekend/.env), so the values have one home. The publishable key is the same
// public key every app bundle ships; nothing secret goes in this file.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const env = Object.fromEntries(readFileSync(`${root}apps/weekend/.env`, 'utf8').split('\n')
  .map((l) => l.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/)).filter(Boolean).map((m) => [m[1], m[2].replace(/^["']|["']$/g, '')]));
const url = env.VITE_SUPABASE_URL, key = env.VITE_SUPABASE_ANON_KEY;
if (!url || !key) throw new Error('VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY missing from apps/weekend/.env');
if (!/^sb_publishable_/.test(key)) throw new Error('expected a publishable key (sb_publishable_…), never a secret one');
const staging = process.argv.includes('--staging');
const out = {
  functionsUrl: `${url.replace(/\/$/, '')}/functions/v1`,
  apiKey: key,
  appUrl: staging ? 'https://studio.weekendindustries.dev' : 'https://weekendindustries.app',
};
writeFileSync(fileURLToPath(new URL('../weekend_studio/service.json', import.meta.url)), JSON.stringify(out, null, 2) + '\n');
console.log('wrote weekend_studio/service.json →', out.functionsUrl);
