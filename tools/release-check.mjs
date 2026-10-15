// node comfy-node/tools/release-check.mjs — run before `comfy node publish`. Says what's missing.
//   · pyproject.toml: no OWNER-TO-CHOOSE left (repository, publisher id), an icon URL
//   · weekend_studio/service.json: production (weekendindustries.app) with a publishable key
//   · the version in pyproject.toml = routes.py VERSION (the tab shows it; the relay is told it)
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const here = (p) => fileURLToPath(new URL(`../${p}`, import.meta.url));
const problems = [];
const py = readFileSync(here('pyproject.toml'), 'utf8');
if (py.includes('OWNER-TO-CHOOSE')) problems.push('pyproject.toml still has OWNER-TO-CHOOSE (Repository or PublisherId)');
if (!existsSync(here('LICENSE'))) problems.push('LICENSE is missing');
if (/^Icon = ""/m.test(py)) problems.push('pyproject.toml has no Icon URL (a square image, at most 400 × 400)');
const version = py.match(/^version = "([^"]+)"/m)?.[1];
const routesVersion = readFileSync(here('weekend_studio/routes.py'), 'utf8').match(/^VERSION = "([^"]+)"/m)?.[1];
if (version !== routesVersion) problems.push(`version mismatch: pyproject ${version}, routes.py ${routesVersion}`);
if (!existsSync(here('weekend_studio/service.json'))) problems.push('weekend_studio/service.json is missing: node comfy-node/tools/write-service-json.mjs');
else {
  const s = JSON.parse(readFileSync(here('weekend_studio/service.json'), 'utf8'));
  if (s.appUrl !== 'https://weekendindustries.app') problems.push(`service.json points at ${s.appUrl}, not production (rerun write-service-json.mjs without --staging)`);
  if (!/^sb_publishable_/.test(s.apiKey || '')) problems.push('service.json has no publishable key');
  if (!/^https:\/\/[a-z0-9]+\.supabase\.co\/functions\/v1$/.test(s.functionsUrl || '')) problems.push(`service.json functionsUrl looks wrong: ${s.functionsUrl}`);
}
// The node pack's copy of the shared test cases must match the source (tools/sync-fixtures.mjs).
{
  const { readdirSync } = await import('node:fs');
  const src = here('../packages/comfy/fixtures'), dst = here('tests/fixtures');
  if (existsSync(src)) {
    const names = (d) => (existsSync(d) ? readdirSync(d).sort() : []);
    const same = JSON.stringify(names(src)) === JSON.stringify(names(dst))
      && names(src).every((n) => readFileSync(`${src}/${n}`, 'utf8') === readFileSync(`${dst}/${n}`, 'utf8'));
    if (!same) problems.push('tests/fixtures is out of date: node comfy-node/tools/sync-fixtures.mjs');
  }
}
if (problems.length) { console.log('Not ready to publish:\n' + problems.map((p) => `  · ${p}`).join('\n')); process.exit(1); }
console.log(`Ready: comfyui-weekend-studio ${version}. Next: run the tests, then \`comfy node publish\`.`);
