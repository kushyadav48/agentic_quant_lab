import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
const file = 'src/api/generated.ts';
const before = readFileSync(file, 'utf8');
const result = spawnSync(process.execPath, ['node_modules/openapi-typescript/bin/cli.js', 'contracts/openapi.json', '-o', file], { stdio: 'inherit' });
if (result.status !== 0) process.exit(result.status ?? 1);
if (readFileSync(file, 'utf8') !== before) {
  console.error('Generated types differ. Review the exported schema and regenerate contracts.');
  process.exit(1);
}
console.log('Generated TypeScript contracts match the checked-in OpenAPI schema.');
