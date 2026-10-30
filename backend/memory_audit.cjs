// Reuse ECC's parser, diagnostic and secret detector. No model or network.
const fs = require('node:fs');
const path = require('node:path');
const vault = require(path.join(process.argv[2], 'scripts/lib/memory-vault.js'));
const format = require(path.join(process.argv[2], 'scripts/lib/memory-vault-format.js'));
const root = process.argv[3];
let count = 0;
try {
  for (const key of fs.readdirSync(path.join(root, 'projects'))) {
    const roots = vault.resolveVaultRoots({cwd: root, env: {...process.env,
      ECC_MEMORY_PROJECT_ROOT: path.join(root, 'projects', key)}});
    const options = {roots, scopes: ['project', 'team']};
    const doctor = vault.doctorMemoryVault(options);
    if (!doctor.ok) throw new Error('ECC rechazó la integridad de un proyecto.');
    const loaded = vault.readMemoryFiles(options);
    for (const entry of loaded.entries) {
      if (format.findPotentialSecrets(JSON.stringify(entry.memory)).length)
        throw new Error('ECC detectó un posible secreto; no se exportará ni restaurará.');
    }
    count += doctor.memoryCount;
  }
  process.stdout.write(JSON.stringify({memoryCount: count}));
} catch (error) {
  process.stderr.write('La auditoría oficial de ECC no aprobó los documentos.\n');
  process.exitCode = 1;
}
