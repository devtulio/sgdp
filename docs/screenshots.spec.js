// Capturas do README. Não é teste: monta um cenário de demonstração e fotografa.
// Roda fora do CI, por configuração própria:
//
//     npx playwright test -c docs/screenshots.config.js
//
// Todo dado aqui é fictício, por decisão: as imagens vão para um repositório
// público e nada que saia daqui pode ser de um documento, parte ou servidor
// real. O órgão é "Município de Exemplo/SP"; não há brasão (upload em
// Configurações, nunca embutido).
import { test, expect } from '@playwright/test';

const SHOTS = 'docs/screenshots';

const ORG = {
  orgao: 'Procuradoria-Geral do Município de Exemplo',
  municipio: 'Município de Exemplo',
  uf: 'SP',
  nome: 'Helena Ribeiro Campos',
  cargo: 'Procuradora do Município',
  matricula: '3041',
};

const emDias = n => {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

const DOCUMENTOS = [
  { tipo: 'lei',      numero: 2841, ano: 2026, data: emDias(-12), ementa: 'Dispõe sobre a criação do Conselho Municipal de Segurança Alimentar e Nutricional e dá outras providências', partes: '' },
  { tipo: 'lei',      numero: 2840, ano: 2026, data: emDias(-31), ementa: 'Autoriza o Poder Executivo a abrir crédito adicional suplementar ao orçamento vigente', partes: '' },
  { tipo: 'decreto',  numero: 1129, ano: 2026, data: emDias(-6),  ementa: 'Regulamenta o procedimento de contratação direta no âmbito da Administração Municipal', partes: '' },
  { tipo: 'decreto',  numero: 1128, ano: 2026, data: emDias(-19), ementa: 'Declara ponto facultativo nas repartições públicas municipais na data que especifica', partes: '' },
  { tipo: 'portaria', numero: 214,  ano: 2026, data: emDias(-3),  ementa: 'Designa servidores para compor a comissão de recebimento de obras e serviços de engenharia', partes: 'Secretaria de Obras' },
  { tipo: 'parecer',  numero: 87,   ano: 2026, data: emDias(-9),  ementa: 'Análise da minuta de convênio para cessão de uso de bem público municipal a entidade sem fins lucrativos', partes: 'Associação Comunitária Bairro Novo' },
  { tipo: 'parecer',  numero: 86,   ano: 2026, data: emDias(-22), ementa: 'Consulta sobre a possibilidade de prorrogação de contrato de prestação de serviço contínuo', partes: 'Secretaria de Administração' },
  { tipo: 'oficio',   numero: 356,  ano: 2026, data: emDias(-2),  ementa: 'Encaminha informações requisitadas em ação civil pública ao Ministério Público Estadual', partes: 'Ministério Público do Estado de São Paulo' },
  { tipo: 'parecer',  numero: 85,   ano: 2026, data: emDias(-27), ementa: 'Legalidade da cessão de servidor municipal a órgão estadual, com ônus para o cedente', partes: 'Secretaria de Educação' },
  { tipo: 'parecer',  numero: 84,   ano: 2026, data: emDias(-35), ementa: 'Manifestação sobre pedido administrativo de reequilíbrio econômico-financeiro contratual', partes: 'Secretaria de Obras' },
  { tipo: 'parecer',  numero: 83,   ano: 2026, data: emDias(-41), ementa: 'Análise de projeto de lei de iniciativa do Legislativo sobre denominação de logradouro público', partes: 'Câmara Municipal' },
  { tipo: 'parecer',  numero: 82,   ano: 2026, data: emDias(-48), ementa: 'Consulta sobre a incidência de contribuição previdenciária em verba de caráter indenizatório', partes: 'Secretaria de Finanças' },
  { tipo: 'parecer',  numero: 81,   ano: 2026, data: emDias(-56), ementa: 'Exame da minuta de edital de chamamento público para credenciamento de leiloeiros', partes: 'Secretaria de Administração' },
  { tipo: 'lei',      numero: 2839, ano: 2026, data: emDias(-44), ementa: 'Institui o Programa Municipal de Incentivo à Agricultura Familiar e dá outras providências', partes: '' },
  { tipo: 'lei',      numero: 2838, ano: 2026, data: emDias(-58), ementa: 'Altera a Lei nº 2.615/2023, que dispõe sobre o Plano de Cargos, Carreiras e Vencimentos', partes: '' },
  { tipo: 'portaria', numero: 213,  ano: 2026, data: emDias(-11), ementa: 'Institui comissão de sindicância para apuração de fatos noticiados em processo administrativo', partes: 'Secretaria de Administração' },
  { tipo: 'portaria', numero: 212,  ano: 2026, data: emDias(-25), ementa: 'Concede licença-prêmio a servidor efetivo, nos termos do Estatuto dos Servidores', partes: 'Secretaria de Saúde' },
  { tipo: 'oficio',   numero: 355,  ano: 2026, data: emDias(-8),  ementa: 'Solicita informações sobre regularidade fundiária de área objeto de doação ao Município', partes: 'Cartório de Registro de Imóveis' },
  { tipo: 'decreto',  numero: 1127, ano: 2026, data: emDias(-33), ementa: 'Dispõe sobre a organização do calendário de pagamentos a fornecedores municipais', partes: '' },
];

const LEMBRETES = [
  { titulo: 'Prazo para contestação — ação ordinária nº 1002345-67', data_prazo: emDias(2) },
  { titulo: 'Renovação do convênio de estágio com a instituição de ensino', data_prazo: emDias(9) },
  { titulo: 'Envio de informações ao Tribunal de Contas — prestação anual', data_prazo: emDias(23) },
];

test('capturas do README', async ({ page }) => {
  page.on('dialog', d => d.accept());

  await page.goto('/SGDP.html');
  await page.fill('#pin-username', 'admin');
  await page.fill('#pin-input', 'admin123');
  await page.click('#overlay-pin button[onclick="fazerLogin()"]');
  await expect(page.locator('#overlay-force-pwd')).toBeVisible();
  await page.fill('#fp-nova', 'demoSGDP2026');
  await page.fill('#fp-confirma', 'demoSGDP2026');
  await page.click('#overlay-force-pwd button');
  await expect(page.locator('#overlay-pin')).toBeHidden();

  await page.evaluate(async org => {
    const lista = await API.json(await API.get('/api/usuarios'));
    const eu = lista.find ? lista.find(u => u.username === 'admin') : lista.items.find(u => u.username === 'admin');
    await API.put(`/api/usuarios/${eu.id}`, { nome: org.nome, cargo: org.cargo, matricula: org.matricula });
    localStorage.setItem('sgdp-user', JSON.stringify(org));
  }, ORG);

  await page.evaluate(async ({ docs, lembretes }) => {
    for (const d of docs) await API.post('/api/documentos', d);
    for (const l of lembretes) await API.post('/api/lembretes', l);
  }, { docs: DOCUMENTOS, lembretes: LEMBRETES });

  await page.reload();
  await expect(page.locator('#overlay-pin')).toBeHidden();

  // ── 1. Painel ─────────────────────────────────────────────────────────────
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/painel.png` });

  // ── 2. Acervo por tipo, com busca e filtros ───────────────────────────────
  await page.click('.nav-item[data-view="parecer"]');
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${SHOTS}/documentos.png` });

  // ── 3. Agenda de prazos ───────────────────────────────────────────────────
  await page.click('.nav-item[data-view="agenda"]');
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${SHOTS}/agenda.png` });
});
