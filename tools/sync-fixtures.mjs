// node comfy-node/tools/sync-fixtures.mjs — copy the shared test cases into the node pack.
// packages/comfy/fixtures/ is the one source (the JS side runs the same cases); comfy-node/tests/fixtures/
// is the copy that lets the node pack's tests run in its own public repo. release-check.mjs refuses
// to pass while the two differ.
import { cpSync, rmSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const src = fileURLToPath(new URL('../../packages/comfy/fixtures/', import.meta.url));
const dst = fileURLToPath(new URL('../tests/fixtures/', import.meta.url));
if (!existsSync(src)) { console.log('No packages/comfy/fixtures here (outside the monorepo): nothing to sync.'); process.exit(0); }
rmSync(dst, { recursive: true, force: true });
cpSync(src, dst, { recursive: true });
console.log('synced packages/comfy/fixtures → comfy-node/tests/fixtures');
