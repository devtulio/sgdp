# Changelog — SGDP
## Sistema de Gestão de Documentos da Procuradoria
> Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/)  
> Versionamento semântico: [SemVer](https://semver.org/lang/pt-BR/)

---

## [1.20.1] — 2026-07-09

### Corrigido
- **Backup/restore perdia as assinaturas digitais** — exportação/importação de backup não incluíam a tabela `signatures`. Corrigido para exportar e reimportar, igual ao SGCA
- **Factory reset não limpava assinaturas e lembretes** — `documento_id` em `signatures`/`lembretes` usa `ON DELETE SET NULL`, então excluir os documentos deixava esses registros órfãos em vez de apagados. `/api/factory-reset` agora limpa as duas tabelas explicitamente
- **Nome de arquivo não sanitizado no download** — `nome_original` ia direto para o cabeçalho `Content-Disposition` sem escapar aspas/quebras de linha, permitindo injeção de cabeçalhos HTTP a partir de um nome de arquivo malicioso. Mesma sanitização já usada no SGCD/SGCA

### Alterado
- **Toasts e skeletons de carregamento unificados com SGCD/SGCA** — toasts passam do estilo pastel (`#toast-box`/`.toast`) para o pill sólido com borda colorida (`#toast`/`.toast-msg`); skeletons de card ganham a animação shimmer (antes ficavam estáticos por causa da ordem das regras CSS) e a altura de 90px, igual aos outros dois sistemas

## [1.20.0] — 2026-07-09

### Adicionado
- **Cargo e Matrícula no cadastro de usuário** — campos que faltavam para paridade completa com SGCD/SGCA. Adicionados ao schema (`usuarios`), aos endpoints de criar/editar/listar usuário, à sessão/login e ao modal de Usuário (posicionados após E-mail, mesma ordem do SGCD)

## [1.19.3] — 2026-07-09

### Corrigido
- **Cor do cabeçalho dos modais** — a v1.19.2 usou `var(--accent)` para o banner colorido, copiado literalmente do SGCD. `--accent` só coincide com `--brand` (navy) no SGCD/SGCA — no SGDP `--accent` é dourado (`#b8962e`), uma cor de destaque diferente da marca. Trocado para `var(--brand)`, que é navy nos 3 sistemas

## [1.19.2] — 2026-07-09

### Alterado
- **Cabeçalho dos modais colorido** — o cabeçalho (`.modal-hd`, usado por todo modal do sistema) deixa de ser branco/neutro e passa a ter um banner na cor de destaque com título em branco, igual ao `.modal-header` do SGCD/SGCA. Muda uma regra CSS compartilhada — o efeito cascateia para todos os modais (Usuário, Documento, Lembrete, Confirmação, etc.), não só o de Usuário
- **Modal de Usuário em 1 coluna** — layout (antes 2 colunas) e rótulos (maiúsculos pequenos, sem dica de texto nem asterisco vermelho) alinhados ao padrão do SGCD/SGCA. Mesmos campos e textos, só a apresentação visual muda

## [1.19.1] — 2026-07-09

### Alterado
- **Ordem das abas de Configurações** — alinhada ao padrão do SGCD/SGCA: `Interface, Organização, Comunicação, Dados, Segurança, Diagnóstico, Usuários` (antes: Usuários era a 3ª aba e a de backup se chamava "Backup"). Sem mudança de conteúdo, só ordem e rótulo

## [1.19.0] — 2026-07-09

### Adicionado
- **CPF no cadastro de usuário** — novo campo no modal de Usuário, pareado com o E-mail já existente (logo após Nome). Sincronizado com SGCD e SGCA, onde também foi adicionado o campo E-mail

### Corrigido
- Badge de versão da capa do Manual Operacional estava desatualizado (mostrava v1.17.0)

## [1.18.0] — 2026-07-08

### Adicionado
- **Relatório de Cadeia Normativa** (`_cadeia_normativa`, `GET /api/documentos/<id>/cadeia`, `gerarRelatorioCadeiaNormativa`) — percorre `documento_vinculos` em largura (BFS, com proteção contra ciclos e deduplicação de arestas) a partir de um documento e monta a árvore de revogações/alterações/complementos/referências; acessível pelo botão "📜 Gerar Cadeia Normativa" no modal de Vínculos
- **Certidão Negativa de Pendências** (`gerarCertidaoNegativaPendencias`) — variante da Certidão de Documento que verifica lembretes pendentes vinculados e presença do PDF assinado; emite "Negativa" quando não há pendências ou "Certidão de Pendências" listando o que falta. `_blocoCertidao` foi extraído para reaproveitar o mesmo bloco de campos/vínculos/assinatura nas duas variantes
- **Relatório de Backup e Integridade** (`_relatorio_integridade`, `GET /api/relatorio/integridade`, restrito a administradores) — snapshot do estado do sistema: contagem e tamanho dos backups `.db`/`.json`, tamanho do banco e da pasta de uploads, contagens por tabela (documentos, arquivos, usuários, tags, vínculos, assinaturas, lembretes pendentes, itens na lixeira) e os últimos 15 eventos de backup/restauração na auditoria; botão em Configurações → Backup
- **Comparativo entre períodos no Relatório Gerencial** — botão "🔁 Comparar com período anterior" (preferência salva em `localStorage`) recalcula o mesmo intervalo de dias imediatamente anterior e mostra a variação (▲/▼, diferença absoluta e percentual) ao lado do total do período
- **Relatório de Etiquetas** (`_relatorio_etiquetas`, `GET /api/relatorio/etiquetas`, `gerarRelatorioEtiquetas`) — lista cada etiqueta com o total de documentos e a relação de quais, mais a contagem de documentos sem nenhuma etiqueta
- **Certidões em lote** (`gerarCertidoesEmLote`, botão "📜 Certidões em lote" na listagem) — gera as Certidões de todos os documentos que batem com o filtro/tipo atual (busca, ano, etiqueta) num único documento com quebra de página entre cada uma; pede confirmação acima de 100 documentos (`CERTIDAO_LOTE_LIMITE`)

### Corrigido
- **Barra de abas de Configurações mais larga que o conteúdo no modo Compacto** — as 7 abas administrativas (`display:flex`, sem quebra) não cabiam dentro de `.cfg-wrap` (`max-width:700px`), estourando visualmente a largura do card abaixo. Aumentado `max-width` para `900px` em vez de permitir quebra de linha nas abas, mantendo-as numa linha só
- **Tema escuro com vários pontos de baixo contraste** — auditoria completa do CSS: variável `--white`, usada em 6 cards do Relatório Gerencial, nunca havia sido definida (cards renderizavam com fundo transparente); botões de paginação, busca global, painel de notificações, cards de usuário (Configurações → Usuários) e skeletons de carregamento tinham fundo branco fixo com texto que herda a escala `--gray-*` (invertida no escuro), ficando ilegível. Adicionados `--white` (clara/escura) e os overrides de fundo faltantes. A tela de login também escurecia parcialmente (labels e rodapé usam a escala `--gray-*`, e a regra genérica de `input`/`select`/`textarea` do tema escuro tinha `!important`); isolada com `body.tema-escuro #tela-login` redefinindo a escala de cinza de volta aos valores claros, já que o login não deve mudar com o tema escolhido

---

## [1.17.0] — 2026-07-08

### Adicionado
- **Padrão documental oficial** (`SGDP_DOC_CSS`, `_gerarDocumentoOficial`, `_cabecalhoOficial`, `_blocoAssinaturaOficial`) — mesmo visual do SGCD (Times New Roman, margens 2cm/2.5cm, brasão + órgão no cabeçalho, bloco de assinatura), reutilizável por qualquer documento/relatório gerado em nova aba
- **Gerador de QR code portado do SGCD** (`_QR`, autocontido, sem dependência externa) — usado em `_footerVerificavel(cod)` para embutir um QR real apontando para `/verificar/<cod>` nas Certidões de documentos assinados; `_footerOficial()` (sem QR) para os demais relatórios, evitando um código de "autenticidade" que não corresponderia a nenhum registro real no servidor
- **Relatório de Pendências e Prazos** (`gerarRelatorioPendencias`) — lembretes vencidos/a vencer e documentos sem PDF anexado
- **Relatório de Produtividade por Usuário** (`gerarRelatorioProdutividade`, `GET /api/relatorio/produtividade`) — contagem de criar/editar/upload/assinar por usuário no período, agregado da tabela `auditoria`
- **Certidão de Documento** (`gerarCertidaoDocumento`, ícone 📜 na listagem) — ficha completa de um documento: campos, etiquetas, vínculos, histórico de revisões e status de assinatura, com QR de verificação real quando assinado digitalmente

---

## [1.16.0] — 2026-07-08

### Adicionado
- **Registro público de verificação de assinatura** — tabela `signatures` (imutável, independente do arquivo), `_gerar_cod_assinatura()` gera código curto único (ex: `65C9-CA87`) a cada assinatura ICP-Brasil; rota pública `/verificar/<cod>` (`_serve_verificar`, sem autenticação) renderiza página HTML com documento, signatário, certificado e data
- **`/api/public/last-backup`** — portado do SGCD/SGCA; exibido na tela de login (`login-last-backup`)
- **Autoridade Responsável** (`aut_nome`, `aut_cargo`, `diario_url` em `sys_settings`) — nova seção em Configurações → Organização, usada no cabeçalho de impressão do Relatório Gerencial
- **Sincronização de auditoria entre instâncias** — `_do_json_backup`/`_export_backup` agora incluem a tabela `auditoria`; `_diff_audit`/`_sync_apply` importam eventos novos (dedup por usuario_nome+acao+detalhes+em) preservando autor e data originais, com `documento_id`/`usuario_id` NULL para não atribuir a um registro local errado

### Corrigido
- **Backups automáticos do banco (`.db`) nunca eram rotacionados nem listados para restauração** — desde 29/06/2026 (commit `e6e6c28`), `_do_db_backup` gerava o nome com o prefixo `SIS_SGDP_BACKUP_` (igual ao JSON) em vez de `DB_SGDP_BACKUP_`, que é o que `_rotate_backups` e o endpoint `/api/backups/db` esperam. Resultado: backups `.db` acumulavam para sempre (33 arquivos órfãos encontrados em teste local) e a aba Configurações → Backup nunca mostrava nenhum snapshot. Revertido para `DB_SGDP_BACKUP_*`
- **`/api/public/org-info` sempre retornava `{}`** — consultava a chave `orgao`, mas o SGDP grava `orgao_nome`; corrigido o SELECT e mapeamento de chave

---

## [1.15.0] — 2026-07-08

### Adicionado
- **Busca full-text (FTS5)** — tabela virtual `documentos_fts` (content-linked, sincronizada por triggers de insert/update/delete) cobrindo ementa/partes/observações; fallback automático para `LIKE` se FTS5 não estiver disponível
- **Etiquetas (tags) livres** — tabelas `tags` e `documento_tags`, endpoints `GET /api/tags` e filtro `?tag=`, autocomplete via `<datalist>` e pílulas clicáveis na listagem
- **Vínculos entre documentos** — tabela `documento_vinculos` (revoga/altera/complementa/referencia), endpoints `GET/POST /api/documentos/<id>/vinculos` e `DELETE /api/vinculos/<id>`, exibidos nos dois sentidos com rótulos diferentes
- **Exportação do relatório** — `GET /api/relatorio/export.csv` (stdlib `csv`, BOM UTF-8) e impressão/PDF limpo do relatório via `@media print`
- **Assinatura digital ICP-Brasil** — `POST /api/documentos/<id>/assinar`, portado do SGCD (`_assinar_pdf_icp` via pyHanko, dependência opcional em `requirements.txt`); novas colunas `assinado_por`, `assinado_em`, `assinatura_cert` em `documentos`

### Corrigido
- **Assunto do Parecer duplicava o campo Modalidade** — removidos 8 itens da lista de Assunto (Dispensa de Licitação, Inexigibilidade de Licitação, Pregão Eletrônico, Concorrência Eletrônica, Contrato Administrativo, Aditivo Contratual, Registro de Preços, Tomada de Preços) que já existem como opções no campo Modalidade/Processo-filho
- **Modalidade mostrava só a sigla** — opções agora exibem "DL - Dispensa de Licitação" etc. por extenso
- **Modal do Parecer espremia o campo de referência** — modal alargado (580px → 720px) e o campo Modalidade+Referência passou a ocupar a linha inteira
- **Botão de busca global (Ctrl+K) colado na sidebar** — adicionada margem superior

---

## [1.14.3] — 2026-07-07

### Corrigido
- **Manual Operacional** — bloco `@media print` estava incompleto (só ocultava o botão de imprimir); adicionadas as quebras de página e o cabeçalho/rodapé de impressão (`@page`) que o SGCD/SGCA já tinham. Removidos números de página fixos (`.toc-num`) do sumário — ficavam desatualizados conforme o manual cresce; SGCA já não usa esse padrão

## [1.14.2] — 2026-07-06

### Adicionado
- **Rate limit de login** — bloqueia com HTTP 429 após 5 tentativas falhas em 5 minutos (janela deslizante, por usuário); login correto limpa o contador. Gap encontrado na auditoria de servidor: nenhum dos 3 sistemas tinha proteção contra força bruta

### Corrigido
- **Username case-sensitive no login** — `usuarios WHERE username=?` sem `COLLATE NOCASE`; SGCD/SGCA já eram case-insensitive, SGDP não

## [1.14.1] — 2026-07-06

### Corrigido
- **Servidor podia encerrar com o diálogo de pasta de backup aberto** — `/api/dialog/folder` abre um seletor de pasta nativo do Windows (pode ficar aberto até 2 minutos); durante esse tempo a aba do navegador fica em segundo plano, e o navegador pode atrasar o ping de sessão além do tempo de expiração, fazendo o watchdog encerrar o servidor com o diálogo ainda na tela. SGCD e SGCA já tinham a proteção (`_watchdog_paused`); replicada aqui. Achado ao auditar o comportamento de encerramento dos 3 sistemas

## [1.14.0] — 2026-07-06

### Adicionado
- **Resumo diário por e-mail** (`_send_daily_summary`, chamado a partir do watchdog) — envia uma vez por dia, para `smtp_to`, um resumo de lembretes vencidos e vencendo nos próximos 7 dias; controlado por `alert_email_last_sent` para não duplicar, igual ao SGCD

### Corrigido
- **Espaçamento apertado na aba Comunicação** — texto de ajuda abaixo de "E-mail interno" tinha `margin-top` negativo, colando no campo acima

---

## [1.13.2] — 2026-07-06

### Alterado
- **Aba "Comunicação"** — a configuração de SMTP foi movida da aba Segurança para uma aba própria "Comunicação", no mesmo padrão do SGCD
- **Teste de SMTP** — novo endpoint `POST /api/config/smtp/test` e botão "Testar conexão com servidor" que envia um e-mail de teste usando a configuração salva

---

## [1.13.1] — 2026-07-06

### Corrigido
- **Encerramento lento do servidor** — `_check_shutdown()` fazia um `PRAGMA wal_checkpoint(TRUNCATE)` manual antes de `os._exit(0)`, passo que não existe no SGCA/SGCD e atrasava o desligamento; removido

### Alterado
- **Configurações de SMTP alinhadas ao SGCD** — chaves renomeadas (`smtp_from` → `smtp_from_name`, `smtp_tls` → `smtp_require_tls`, `notificacao_email` → `smtp_to`) e adicionadas `smtp_secure` (SSL direto) e `smtp_ignore_ssl`, com migração automática das configurações já salvas

---

## [1.13.0] — 2026-07-06

### Adicionado
- **Histórico de revisões de documentos** — cada edição de um documento salva os campos anteriores em `documento_revisoes`; novo endpoint `GET /api/documentos/<id>/revisoes` e botão 🕘 na lista de documentos mostram autor e data/hora de cada alteração
- **Notificação de prazo por e-mail** — loop em background (`_lembrete_notify_loop`) verifica a cada hora os lembretes vencidos e não notificados, enviando e-mail para o responsável (novo campo `email` em usuários) ou para um e-mail padrão configurável em Configurações → Segurança (`notificacao_email`)

---

## [1.12.5] — 2026-07-06

### Corrigido
- **Mapa de rótulos de auditoria incompleto** — 8 ações reais não tinham rótulo cadastrado e apareciam cruas na tabela (ex: "excluir_permanente", "sincronizar_backup"): `restaurar`, `excluir_permanente`, `alterar_brasao`, `alterar_smtp`, `criar_lembrete`, `enviar_email`, `import_csv`, `sincronizar_backup`. Achado ao comparar visualmente a tela de Auditoria com SGCD/SGCA

## [1.12.4] — 2026-07-06

### Corrigido
- **Console crashava em ambiente não-UTF8** — prints com caracteres de caixa (╔═╗) e emojis quebravam com `UnicodeEncodeError` em consoles Windows usando cp1252/cp850; o servidor nem chegava a iniciar. Corrigido forçando UTF-8 em stdout/stderr na inicialização — SGCD/SGCA já tinham essa correção, aplicada aqui também
- **Seleção de modo travava em ambiente não-interativo** — `_selecionar_modo()` esperava input de teclado mesmo quando stdin não é um terminal (scripts, automação); adicionado o fallback `if not sys.stdin.isatty(): op = "2"` (assume modo servidor), já presente no SGCD/SGCA

## [1.12.3] — 2026-07-06

### Adicionado
- **Ferramentas de lint de desenvolvimento** (`package.json`, `eslint.config.js`, `scripts/lint.mjs`) — extrai os `<script>` do SGDP.html e roda ESLint (`no-undef`), no mesmo padrão do SGCD/SGCA; não afeta o runtime, que continua zero-dependência
- **Aviso ao fechar com formulário aberto** — `beforeunload` alerta antes de fechar/recarregar a página com o modal de documento aberto, evitando perda de dados digitados
- **`aria-label` nos botões de ícone** — botões de fechar modais (✕), excluir documento/lembrete e fechar notificações agora têm rótulo acessível para leitores de tela

### Corrigido
- **Cache do navegador após atualização de versão** — o servidor agora envia `Cache-Control: no-cache, must-revalidate` para `.html/.js/.css` (mesmo comportamento do SGCD/SGCA); sem isso o navegador podia continuar servindo uma versão antiga do SGDP.html após update
- **Ajuste de tamanho de fonte via `zoom` em vez de `font-size`** — a opção Pequena/Normal/Grande usava `main.style.zoom`, uma propriedade não padronizada (afeta apenas o layout, não a densidade de leitura de forma consistente); trocado para as mesmas classes `html.font-pequena`/`html.font-grande` do SGCD

---

## [1.12.2] — 2026-07-04

### Corrigido
- **Configurações não centralizada no modo Compacta** — a classe `.cfg-wrap`, introduzida na correção anterior (v1.12.1) para o modo Expandida, ficou sem `margin: 0 auto`, deixando o painel de Configurações alinhado à esquerda no modo Compacta (padrão) em vez de centralizado como as demais telas.

---

## [1.12.1] — 2026-07-04

### Corrigido
- **Configurações não expandia com Largura do Conteúdo = Expandida** — o painel de Configurações (e a aba Backup) tinha `max-width:700px` fixo, ignorando a opção escolhida. Corrigido usando a mesma classe/toggle das demais telas.

---

## [1.12.0] — 2026-07-04

### Adicionado
- **Largura do conteúdo** — Configurações → Interface: Compacta (padrão) ou Expandida
- **Cor de destaque** — Configurações → Interface: Institucional, Azul, Verde ou Roxo; altera botões, links e destaques em todo o sistema
- Mesma lógica e paleta de cores do SGCD, para paridade visual entre os dois sistemas

---

## [1.11.0] — 2026-07-04

### Adicionado
- **Suíte de testes automatizados** (`tests/test_server.py`) — testa login, CRUD de documentos, numeração automática, lembretes, auditoria, backup e sincronização usando `unittest` da stdlib
- **Fallback de Python portátil** — `Iniciar SGDP.bat` extrai automaticamente uma versão embarcável do Python (`python-3.12.9-embed-amd64.zip`) quando não há Python instalado no sistema, sem exigir instalação ou privilégio de administrador

### Corrigido
- **Numeração incorreta em documentos com tipo/ano inéditos** — `_create_doc` capturava o id via `SELECT last_insert_rowid()` depois de `bump_contador()`, que na primeira vez que um tipo/ano é usado também insere na tabela de contadores, sobrescrevendo o id capturado. Encontrado pela nova suíte de testes.
- **Vazamento de conexões SQLite** — `get_db()` nunca fechava a conexão ao sair do `with`; corrigido com subclasse de conexão que fecha automaticamente.

---

## [1.10.1] — 2026-07-04

### Corrigido
- **Manual desatualizado** — o corpo do `MANUAL.html` não descrevia Assunto/campos específicos, Importação CSV, Sincronização de backup, Personalização (brasão/sons), configuração de e-mail, diagnóstico de rede, Relatório Gerencial, Agenda e Lixeira, embora já lançados. Seções adicionadas e sumário/histórico renumerados.
- README: adicionada seção "Contribuição" apontando para `CONTRIBUTING.md`

---

## [1.10.0] — 2026-07-04

### Adicionado
- **Sincronização de backup entre agentes** — mescla o backup (JSON) de outra instalação com os dados atuais; documentos casam por (tipo, número, ano), não pelo id interno; revisão de conflitos um a um antes de aplicar; backup de segurança automático
- **Botão "Imprimir / Salvar PDF"** no Manual Operacional, igual ao SGCD

---

## [1.9.1] — 2026-07-04

### Corrigido
- **Botão "Fechar Sistema" não fechava a janela** — `navegar()` empilhava uma entrada de histórico do navegador a cada troca de tela (`location.hash = view`); o Chrome só permite `window.close()` via script em janelas sem histórico acumulado. Trocado para `history.replaceState()`, que preserva a tela após F5 sem impedir o fechamento da janela.

---

## [1.9.0] — 2026-07-04

### Corrigido
- **Alinhamento da sidebar** — logo, brasão e nome do órgão agora centralizados, paridade visual com o SGCD
- **Botão "Imprimir / Salvar PDF"** no modal de visualização de PDF, igual ao padrão de documentos do SGCD
- **Novo ícone do sistema** — `sgdp.ico` regenerado a partir de `SGDP_Icone_documento_juridico.png` (multi-resolução 16–256px) + favicon na aba do navegador

---

## [1.8.0] — 2026-07-04

### Alterado
- **Brasão armazenado no servidor** — antes salvo em `localStorage` (por navegador), agora persistido em `sys_settings` via `GET`/`PUT /api/settings/brasao`. Num sistema multiusuário em rede, todos os procuradores passam a ver o mesmo brasão, independente do computador; mesma lógica de armazenamento usada no SGCD.

---

## [1.7.0] — 2026-07-04

### Adicionado
- **Lixeira** — exclusão de documentos passa a ser reversível (soft-delete); itens ficam disponíveis para restauração por 30 dias antes da purga automática; nova tela "Lixeira" na sidebar com Restaurar e Excluir de vez
- **Agenda** — lembretes com título e prazo, vinculáveis opcionalmente a um documento; badge na sidebar com contagem de pendências vencidas; marcação de concluído via checkbox
- **Envio de documentos por e-mail** — botão ✉ na listagem de cada tipo de documento abre modal para enviar o PDF assinado anexado por e-mail; configuração de servidor SMTP (host, porta, usuário, senha, remetente) em Configurações → Segurança
- **Importação de documentos via CSV** — botão "Importar CSV" na listagem de cada tipo; aceita colunas `numero,ano,data,ementa,partes,observacoes,assunto`; numeração automática quando `numero` não informado; relatório de linhas importadas e erros

### Alterado
- Exclusão de documento agora envia para a lixeira em vez de apagar e remover o PDF imediatamente
- Dashboard, listas e Relatório Gerencial passam a ignorar documentos na lixeira

---

## [1.6.0] — 2026-06-29

### Adicionado
- **Canvas de nós animado na tela de login** — fundo ardósia (`#1a1f2e`) com partículas flutuantes conectadas por linhas que reagem ao movimento do cursor/toque; idêntico ao SGCD
- **Painel de configuração do efeito** — botão ⚙️ discreto no canto inferior direito da tela de login abre painel com sliders para ajustar quantidade de nós, distância de conexão e velocidade; configurações persistidas em `localStorage` (`sgdp_lc_config`)
- **`/api/public/org-info`** — endpoint público que retorna nome do órgão e município sem exigir autenticação, usado para exibir o card institucional em acessos via rede em novos navegadores

### Corrigido
- **Canvas não iniciava** — `_loginCanvasStart()` era chamado antes do layout terminar; corrigido com `requestAnimationFrame()` para garantir dimensões válidas do container
- **Canvas não voltava após logout** — `sair()` exibia a tela de login mas não reiniciava a animação; corrigido com chamada a `_loginCanvasStart()` via `requestAnimationFrame`

---

## [1.12.2] — 2026-07-04

### Corrigido
- **Configurações não centralizada no modo Compacta** — a classe `.cfg-wrap`, introduzida na correção anterior (v1.12.1) para o modo Expandida, ficou sem `margin: 0 auto`, deixando o painel de Configurações alinhado à esquerda no modo Compacta (padrão).

---

## [1.12.1] — 2026-07-04

### Corrigido
- **Configurações não expandia com Largura do Conteúdo = Expandida** — o painel de Configurações (e a aba Backup) tinha `max-width:700px` fixo, ignorando a opção escolhida. Corrigido usando a mesma classe/toggle das demais telas.

---

## [1.12.0] — 2026-07-04

### Adicionado
- **Largura do conteúdo** — Configurações → Interface: Compacta (padrão) ou Expandida
- **Cor de destaque** — Configurações → Interface: Institucional, Azul, Verde ou Roxo; altera botões, links e destaques em todo o sistema
- Mesma lógica e paleta de cores do SGCD, para paridade visual entre os dois sistemas

---

## [1.11.0] — 2026-07-04

### Adicionado
- **Suíte de testes automatizados** (`tests/test_server.py`) — testa login, CRUD de documentos, numeração automática, lembretes, auditoria, backup e sincronização usando `unittest` da stdlib
- **Fallback de Python portátil** — `Iniciar SGDP.bat` extrai automaticamente uma versão embarcável do Python (`python-3.12.9-embed-amd64.zip`) quando não há Python instalado no sistema, sem exigir instalação ou privilégio de administrador

### Corrigido
- **Numeração incorreta em documentos com tipo/ano inéditos** — `_create_doc` capturava o id via `SELECT last_insert_rowid()` depois de `bump_contador()`, que na primeira vez que um tipo/ano é usado também insere na tabela de contadores, sobrescrevendo o id capturado. Encontrado pela nova suíte de testes.
- **Vazamento de conexões SQLite** — `get_db()` nunca fechava a conexão ao sair do `with`; corrigido com subclasse de conexão que fecha automaticamente.

---

## [1.10.1] — 2026-07-04

### Corrigido
- **Manual desatualizado** — o corpo do `MANUAL.html` não descrevia Assunto/campos específicos, Importação CSV, Sincronização de backup, Personalização (brasão/sons), configuração de e-mail, diagnóstico de rede, Relatório Gerencial, Agenda e Lixeira, embora já lançados. Seções adicionadas e sumário/histórico renumerados.
- README: adicionada seção "Contribuição" apontando para `CONTRIBUTING.md`

---

## [1.10.0] — 2026-07-04

### Adicionado
- **Sincronização de backup entre agentes** — mescla o backup (JSON) de outra instalação com os dados atuais; documentos casam por (tipo, número, ano), não pelo id interno; revisão de conflitos um a um antes de aplicar; backup de segurança automático
- **Botão "Imprimir / Salvar PDF"** no Manual Operacional, igual ao SGCD

---

## [1.9.1] — 2026-07-04

### Corrigido
- **Botão "Fechar Sistema" não fechava a janela** — `navegar()` empilhava uma entrada de histórico do navegador a cada troca de tela (`location.hash = view`); o Chrome só permite `window.close()` via script em janelas sem histórico acumulado. Trocado para `history.replaceState()`, que preserva a tela após F5 sem impedir o fechamento da janela.

---

## [1.9.0] — 2026-07-04

### Corrigido
- **Alinhamento da sidebar** — logo, brasão e nome do órgão agora centralizados, paridade visual com o SGCD
- **Botão "Imprimir / Salvar PDF"** no modal de visualização de PDF, igual ao padrão de documentos do SGCD
- **Novo ícone do sistema** — `sgdp.ico` regenerado a partir de `SGDP_Icone_documento_juridico.png` (multi-resolução 16–256px) + favicon na aba do navegador

---

## [1.8.0] — 2026-07-04

### Alterado
- **Brasão armazenado no servidor** — antes salvo em `localStorage` (por navegador), agora persistido em `sys_settings` via `GET`/`PUT /api/settings/brasao`. Num sistema multiusuário em rede, todos os procuradores passam a ver o mesmo brasão, independente do computador; mesma lógica de armazenamento usada no SGCD.

---

## [1.7.0] — 2026-07-04

### Adicionado
- **Lixeira** — exclusão de documentos passa a ser reversível (soft-delete); itens ficam disponíveis para restauração por 30 dias antes da purga automática; nova tela "Lixeira" na sidebar com Restaurar e Excluir de vez
- **Agenda** — lembretes com título e prazo, vinculáveis opcionalmente a um documento; badge na sidebar com contagem de pendências vencidas; marcação de concluído via checkbox
- **Envio de documentos por e-mail** — botão ✉ na listagem de cada tipo de documento abre modal para enviar o PDF assinado anexado por e-mail; configuração de servidor SMTP (host, porta, usuário, senha, remetente) em Configurações → Segurança
- **Importação de documentos via CSV** — botão "Importar CSV" na listagem de cada tipo; aceita colunas `numero,ano,data,ementa,partes,observacoes,assunto`; numeração automática quando `numero` não informado; relatório de linhas importadas e erros

### Alterado
- Exclusão de documento agora envia para a lixeira em vez de apagar e remover o PDF imediatamente
- Dashboard, listas e Relatório Gerencial passam a ignorar documentos na lixeira

---

## [1.5.0] — 2026-06-29

### Adicionado
- **Diagnóstico de rede** (`diagnostico.py`) — verifica IP local, porta 3001, estado do firewall, regras de entrada e acessibilidade pela LAN; relatório com ✅/⚠️/❌ por item e instruções de correção em linguagem simples
- **`Diagnostico SGDP.bat`** — atalho clicável para rodar o diagnóstico sem abrir o servidor
- **`Liberar Porta SGDP.bat`** — cria regra de entrada no Windows Defender Firewall para a porta 3001 (requer execução como Administrador)
- **Opção [3] Diagnóstico no menu de inicialização** — acessível diretamente ao iniciar o `server.py`, sem necessidade de rodar o `.bat` separado

---

## [1.4.0] — 2026-06-29

### Adicionado
- **Sons de notificação** — Web Audio API com sons distintos para click, sucesso, erro e notificação; listener global em fase de captura cobre todos os botões, incluindo os gerados dinamicamente
- **Brasão do município na sidebar** — upload de imagem nas configurações, redimensionada para máx 256 px antes de salvar no localStorage (evita QuotaExceededError); exibida no topo da barra lateral
- **Campo Assunto** — categorias dinâmicas por tipo de documento: 14 categorias gerais para Lei, Decreto, Portaria e Ofício; 46 categorias jurídicas específicas para Parecer
- **Relatório Gerencial** — resumo de produção com contagem por tipo, por assunto e por mês; filtros de período (semana, mês, trimestre, ano, total); gráfico de barras SVG inline
- **Campos específicos de Parecer** — PA (Processo Administrativo) + Modalidade/processo-filho, ativados automaticamente quando o assunto selecionado é de licitação; legenda de siglas em largura total
- **Campos específicos de Portaria** — ato_tipo (Nomeação, Exoneração, etc.) e cargo, exibidos apenas para Portarias
- **Navegação persistente via hash** — `location.hash` atualizado a cada navegação; F5 ou recarga mantém o usuário na mesma tela
- **Tela de encerramento** — ao clicar em "Fechar sistema", a página exibe tela preta com mensagem de encerramento antes de tentar fechar a aba

### Corrigido
- **Bug estrutural no modal de Parecer** — `div#f-parecer-row` sem fechamento correto fazia upload de PDF e outros campos sumirem ao selecionar assunto fora do grupo de licitação; reestruturado como `f-processo-row` com fechamento adequado
- **Brasão não persistia** — QuotaExceededError silencioso ao salvar imagem grande; corrigido com canvas de redimensionamento e try/catch com toast de erro
- **Nomenclatura de backups** — padronizado para `SIS_SGDP_BACKUP_YYYY-MM-DD_HH-MM-SS` (.json e .db) em todos os pontos de geração

### Alterado
- **Versão do backup** — campo `sgdp_version` nos arquivos JSON atualizado para `1.4.0`
- **Siglas do processo licitatório** — adicionados RJ (Reajuste), RE (Reequilíbrio Econômico-Financeiro) e PR (Prorrogação)

---

## [1.2.0] — 2026-06-28

### Adicionado
- **Atalho de área de trabalho** — `Criar Atalho SGDP.bat` gera um atalho `.lnk` na área de trabalho com ícone personalizado
- **Ícone personalizado** (`sgdp.ico`) — Selo Soberano: anel duplo dourado com símbolo §, marcações e fundo circular transparente, multi-resolução (16–256 px)
- **Scripts de atalho** — `Criar Atalho SGDP.ps1` e `Criar Atalho SGDP.bat` para criação e atualização do atalho

### Melhorado
- **Login — restauração de sessão** — ao fazer logout, o campo usuário é pré-preenchido com o último usuário logado e o foco vai automaticamente para o campo senha
- **Ícones da barra lateral** — emojis substituídos por ícones SVG inline (`stroke="currentColor"`), adaptam-se ao tema claro/escuro
- **Ajuste de tamanho de fonte** — zoom aplicado exclusivamente ao painel `#main`, sem afetar a sidebar; corrige bug onde a sidebar sumia ao alterar o tamanho da fonte

### Corrigido
- **Janela do servidor — ícone correto** — removida flag `/min` do `Iniciar SGDP.bat`; Windows Terminal exibe seu próprio ícone em vez do ícone do Python
- **Links no servidor (CMD)** — removida função `_link()` com sequências OSC 8 não suportadas pelo CMD; URLs exibidas como texto simples
- **Alinhamento da caixa ASCII** — corrigida largura da borda `╔═╝` em `_selecionar_modo()` no `server.py`

---

## [1.1.0] — 2026-06-27

### Alterado
- **Sessões em SQLite** — sessões migradas de dict em memória para tabela `sessions` no banco, alinhando com o SGCD; sessões sobrevivem a reinicializações do servidor e são limpas automaticamente ao expirar
- **`get_db()` com context manager** — padrão `with get_db() as conn:` em todas as operações; auto-commit/rollback consistente com o SGCD
- **Handler base `SimpleHTTPRequestHandler`** — substituído `BaseHTTPRequestHandler` + serve manual por `SimpleHTTPRequestHandler` com `super().do_GET()`, igual ao SGCD
- **Roteamento separado por verbo** — `_route_get / _route_post / _route_put / _route_delete` no lugar de `_route()` único, alinhando com a estrutura do SGCD
- **`DB_PATH` e `UPLOADS_DIR` absolutos** — caminhos calculados com `os.path.dirname(os.path.abspath(__file__))` em vez de relativos ao `os.chdir`
- **`os.makedirs` no nível de módulo** — criação da pasta `uploads/` movida para o topo, igual ao SGCD
- **Mensagem de criação do admin** — `print()` ao criar usuário padrão, igual ao SGCD

---

## [1.0.0] — 2026-06-27

### Adicionado

- **Gestão de documentos jurídicos** — CRUD completo para cinco tipos: Lei, Decreto, Portaria, Parecer e Ofício
- **Numeração automática por tipo e ano** — contador independente por tipo, reinicia a cada ano; número editável manualmente no momento do cadastro
- **Upload de PDF assinado** — drag-and-drop ou seleção por clique; limite de 50 MB; PDFs armazenados em disco com nome aleatório (não previsível)
- **Visualização de PDF** embutida no navegador — carregado como blob autenticado sem expor o token na URL
- **Download de PDF** direto pelo navegador
- **Login multiusuário com sessões server-side** — token Bearer de 32 bytes gerado por `secrets.token_urlsafe`; sessões com TTL de 8 horas; logout invalida o token imediatamente
- **Perfil administrador** — gerencia usuários, acessa auditoria e backup; perfil padrão tem acesso apenas aos documentos
- **Gestão de usuários** — criar, editar nome/senha/perfil, ativar/desativar; restrição de auto-exclusão
- **Senhas com PBKDF2-HMAC-SHA256** — 100.000 iterações, salt aleatório por usuário de 16 bytes; comparação em tempo constante via `secrets.compare_digest`
- **Busca e filtros** — busca por número, ementa e partes; filtro por ano; paginação de 50 registros por página
- **Dashboard** — contadores totais e do ano corrente por tipo; lista dos 10 documentos mais recentes
- **Trilha de auditoria** — registra criar, editar, excluir, upload e remover_arquivo com usuário, data/hora e detalhes
- **Backup completo** — exporta JSON com documentos, PDFs (base64), usuários e contadores; restauração substitui todos os dados
- **Encerramento automático do servidor** — watchdog monitora heartbeat do frontend (enviado a cada 10 s); encerra o processo após 60 s sem nenhum cliente conectado
- **Banco SQLite com WAL** — `journal_mode=WAL` para suportar leituras concorrentes sem bloqueio; `foreign_keys=ON`
- **Pasta de uploads criada automaticamente** na primeira execução
- **Usuário administrador padrão** criado automaticamente se o banco estiver vazio (`admin` / `sgdp2024`)
- **Launcher `Iniciar SGDP.bat`** — verifica Python, exibe instruções e abre o navegador automaticamente; encerra sem aguardar tecla ao término do servidor
