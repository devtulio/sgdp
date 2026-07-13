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
