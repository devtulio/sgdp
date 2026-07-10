// Testes E2E do fluxo real no navegador (login, criar documento).
// Complementa tests/test_server.py (unittest, só backend) — aqui é HTML+JS+backend
// juntos, exatamente como um usuário usaria. Roda contra um banco/uploads/backups
// isolados (SGDP_DATA_DIR), nunca o sgdp.db real.
import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dataDir = mkdtempSync(join(tmpdir(), 'sgdp-e2e-'));
const port = 3052;

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  fullyParallel: false, // um único servidor/banco compartilhado entre os specs
  workers: 1,
  use: {
    baseURL: `http://localhost:${port}`,
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'python server.py',
    url: `http://localhost:${port}/health`,
    reuseExistingServer: false,
    timeout: 15_000,
    env: { SGDP_DATA_DIR: dataDir, SGDP_PORT: String(port) },
  },
});
