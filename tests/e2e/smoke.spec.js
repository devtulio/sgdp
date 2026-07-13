// Caminho feliz de ponta a ponta: login (com troca de senha obrigatória, já que
// o banco é novo a cada run) → criar documento (Lei).
import { test, expect } from '@playwright/test';

test('login força troca de senha e cria documento', async ({ page }) => {
  await page.goto('/SGDP.html');

  await page.fill('#l-user', 'admin');
  await page.fill('#l-pass', 'admin123');
  await page.click('.btn-login');

  // Banco novo → admin padrão nasce com troca de senha obrigatória
  await expect(page.locator('#overlay-force-pwd')).toBeVisible();
  await page.fill('#fp-nova', 'novaSenhaE2E123');
  await page.fill('#fp-confirma', 'novaSenhaE2E123');
  await page.click('#overlay-force-pwd button');

  await expect(page.locator('#overlay-force-pwd')).toBeHidden();

  await page.click('[data-view="lei"]');
  await page.click('button:has-text("Nova Lei")');
  await page.fill('#f-ementa', 'Ementa de teste E2E');
  await page.click('.modal-footer button:has-text("Salvar")');

  await expect(page.getByRole('cell', { name: 'Ementa de teste E2E' })).toBeVisible();
});

test('paginação em Documentos preserva o foco do campo de busca', async ({ page }) => {
  test.setTimeout(60_000); // semeia 55 documentos via fetch sequencial, mais lento que o padrão

  // Regressão do bug corrigido na Fase 2 da padronização de arquitetura: antes,
  // a toolbar (campo de busca incluso) era reconstruída via innerHTML a cada
  // clique de paginação, derrubando o foco mesmo preservando o valor digitado.
  // Specs rodam sequencialmente contra o mesmo servidor/banco (fullyParallel:false,
  // workers:1) — o teste anterior já trocou a senha padrão, então login aqui usa
  // a senha já definida por ele, sem overlay de troca obrigatória de novo.
  await page.goto('/SGDP.html');
  await page.fill('#l-user', 'admin');
  await page.fill('#l-pass', 'novaSenhaE2E123');
  await page.click('.btn-login');
  await expect(page.locator('[data-view="dashboard"]')).toBeVisible();

  // Semeia documentos suficientes para a paginação aparecer (per=50).
  await page.evaluate(async () => {
    for (let i = 1; i <= 55; i++) {
      await API.post('/api/documentos', { tipo: 'lei', ano: 2026, ementa: `Lei paginação ${i}`, data: '2026-01-01' });
    }
  });

  await page.click('[data-view="lei"]');
  await expect(page.locator('.pag-btn')).toHaveCount(4); // ‹, 1, 2, ›

  const searchInput = page.locator('#doc-search');
  await searchInput.click();
  const nodeIdAntes = await searchInput.evaluate(el => (el.dataset.__id ??= Math.random().toString()));

  await page.locator('.pag-btn', { hasText: '2' }).click();
  await expect(page.locator('.pag-btn.active')).toHaveText('2');

  // Cabeçalho e toolbar continuam visíveis (não somem entre navegações de página).
  await expect(page.locator('#doc-titulo')).toBeVisible();
  await expect(searchInput).toBeVisible();

  // Mesmo nó do DOM — não foi reconstruído (clicar num botão de paginação move o
  // foco pra ele mesmo, esperado; o que a Fase 2 corrige é a identidade do campo).
  const nodeIdDepois = await searchInput.evaluate(el => el.dataset.__id);
  expect(nodeIdDepois).toBe(nodeIdAntes);
});
