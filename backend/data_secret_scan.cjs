// ECC's existing detector, used only for local maintenance snapshots.
const fs = require('node:fs');
const path = require('node:path');
try {
  const format = require(path.join(process.argv[2], 'scripts/lib/memory-vault-format.js'));
  const data = fs.readFileSync(0, 'utf8');
  if (Buffer.byteLength(data) > 64 * 1024 * 1024 || format.findPotentialSecrets(data).length)
    throw new Error('sensitive');
  process.stdout.write('safe\n');
} catch (_) {
  process.stderr.write('ECC no aprobó el contenido del respaldo.\n');
  process.exitCode = 1;
}
