# Changelog — SGDP
## Sistema de Gestão de Documentos da Procuradoria
> Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/)  
> Versionamento semântico: [SemVer](https://semver.org/lang/pt-BR/)

---

## [1.33.3] — 2026-07-15

### Corrigido
- **Revisão geral do Manual Operacional — vários pontos desatualizados desde a remoção do modo "Pessoal" e outras mudanças recentes**:
  - Versão da capa presa em v1.32.5 (duas versões atrás do título/rodapé)
  - Descrevia o servidor encerrando sozinho 60s após fechar o navegador — esse comportamento foi removido há algumas versões (`_check_shutdown` só dispara backup, nunca mais `os._exit`); o servidor só para com Ctrl+C
  - Referenciava uma opção "[3] Diagnóstico" no menu de inicialização; o menu atual (`_selecionar_modo()`) só tem duas opções: `[1] Diagnóstico` / `[2] Iniciar Servidor` — sem "modo Pessoal"
  - Tabela de ações de auditoria descrevia `excluir` como remoção permanente; na verdade `excluir` é o envio à lixeira (reversível) — a remoção definitiva é uma ação separada (`excluir_permanente`), agora listada corretamente junto com as ~15 outras ações que a tabela não cobria (login/logout, usuários, vínculos, CSV, e-mail, sincronização, restaurar banco, factory reset)
  - Nome de arquivo de backup errado (`sgdp_backup_AAAAMMDD.json`); o nome real é `SIS_SGDP_BACKUP_AAAA-MM-DD_HH-MM-SS.json`
  - README.md tinha a mesma tabela desatualizada do menu de inicialização — corrigida junto

### Adicionado
- **Seções novas no Manual cobrindo funcionalidades que só existiam no Histórico de Versões, nunca no corpo instrutivo**: 4.7 (assinatura digital ICP-Brasil feita dentro do próprio sistema, verificação pública de assinatura, histórico de revisões) e 4.8 (etiquetas e vínculos entre documentos, incl. Cadeia Normativa). Também passaram a ser descritas: busca full-text por palavra e busca global (Ctrl+K) na Seção 5; departamento/CPF/e-mail/cargo/matrícula no cadastro de usuário (6.1); backup automático e manual em dois formatos, JSON e `.db` bruto, lado a lado (6.3, reescrita); cor de destaque e largura do conteúdo (6.5); notificação de lembrete por e-mail e resumo diário (Seção 8)

## [1.33.2] — 2026-07-15

### Adicionado
- **Cobertura de testes de departamento/sigiloso e áreas sem nenhum teste automatizado** — suíte foi de 15 para 62 testes (`tests/test_server.py`), sem mudança de comportamento do sistema:
  - `pode_ver_doc()`/`pode_editar_doc()`: sigiloso invisível/bloqueado pra quem não pode ver (inclusive download de PDF, que usa a mesma regra), editável por colega de departamento só quando não-sigiloso, e a regra mais fina de que só o criador ou admin pode alterar a própria marcação de sigilo
  - CRUD de usuário/departamento, incluindo o teste de regressão do bug corrigido na v1.33.1 (excluir usuário com histórico → 409, não 500)
  - Vínculos entre documentos e cadeia normativa (sem duplicar aresta), histórico de revisões, tags, importação CSV, upload/download de PDF, relatórios (incl. filtro de sigiloso), config, backup manual do banco
  - Restore de backup JSON, sync-apply e factory-reset — cada teste monta e confere só o próprio payload, sem depender de estado de outra classe, já que esses endpoints substituem/zeram tabelas inteiras
  - Deliberadamente fora da suíte automatizada: assinatura ICP-Brasil (precisa de certificado real), envio de e-mail (precisa de SMTP real) e restore de `.db` bruto (troca o arquivo físico do banco inteiro — risco alto demais pra rodar dentro do banco compartilhado da suíte). Essas três, mais o restante, foram validadas manualmente contra o servidor e banco reais nesta mesma sessão (incluindo um certificado autoassinado de verdade pro fluxo de assinatura, e um ciclo completo de backup→restore de `.db` bruto)

## [1.33.1] — 2026-07-15

### Corrigido
- **Excluir usuário com histórico causava erro 500** — `documentos.criado_por/atualizado_por/assinado_por`, `auditoria`, `lembretes` e `signatures` referenciam `usuarios(id)` sem `ON DELETE CASCADE`/`SET NULL`; o esqueleto compartilhado liga `PRAGMA foreign_keys=ON` em toda conexão, então excluir qualquer usuário já referenciado em algum desses lugares (ou seja, com praticamente qualquer histórico de uso) violava a constraint. Não é regressão da feature de departamento/sigiloso — sempre foi assim, só que antes o SQLite não aplicava a regra, e a exclusão "funcionava" deixando registros com `criado_por` órfão (corrupção silenciosa). `_delete_usuario` agora captura a falha e devolve 409 com mensagem explicando que o usuário tem histórico e sugerindo "Desativar" em vez de excluir (mecanismo que já existia, via `ativo=0`), em vez do 500 genérico

## [1.33.0] — 2026-07-14

### Adicionado
- **Departamento de usuário** — cada usuário pertence a um departamento fixo (`Procuradoria-Geral` ou `Gabinete`; tupla `DEPARTAMENTOS` em `server.py`, endpoint `GET /api/departamentos`), definido pelo administrador no cadastro
- **Documentos sigilosos** — coluna `sigiloso` em `documentos`; `pode_ver_doc()`/`pode_editar_doc()` centralizam a regra: sigiloso só é visível para quem criou ou admin (filtrado em listagem, busca por id, download de PDF, dashboard, relatórios — incl. export CSV — e sincronização entre instâncias); documento não-sigiloso pode ser editado/excluído/assinado por qualquer usuário do mesmo departamento de quem criou (ou admin); só o criador ou um admin pode alterar a própria marcação de sigilo. Backups (manual, automático e sync) preservam `departamento` e `sigiloso` no round-trip. *(Implementado via Claude web / PR #1, `31f14ac` — documentado agora)*
- **Coluna "Origem" no Dashboard e nas listagens de documentos** — mostra o departamento de quem criou cada documento, ao lado da Ementa

## [1.32.5] — 2026-07-14

### Removido
- **6 das 8 funções do motor de partículas da tela de login (`_lcLoadConfig`/`_lcSaveConfig`/`_lcToggleConfig`/`_lcParam`/`_lcResetConfig`/`_lcSpeedVal`) reimplementadas localmente**, byte-idênticas às do esqueleto compartilhado (só a chave de `localStorage` estava hardcoded em vez de usar `_lcConfigKey()` — mesmo valor na prática). `_loginCanvasStart`/`_loginCanvasStop` continuam locais (reação a resize da janela durante o login, que o base.js não faz)

## [1.32.4] — 2026-07-14

### Removido
- **`create_session`/`delete_session`/`renew_session`/`active_sessions` reimplementadas localmente**, mecanicamente idênticas ao esqueleto compartilhado (`_esqueleto/sgx_base.py`) — agora delegam pro `sgx_base`, mantendo a mesma assinatura local. `get_session()` permanece local (SELECT de colunas explícito por segurança, colunas divergem por sistema)

## [1.32.3] — 2026-07-14

### Corrigido
- **Classe `.dash-top` (cabeçalho de título + ações de cada tela) não estava definida no esqueleto compartilhado (`_esqueleto/base.css`)** — só a classe morta `.view-top` (nunca usada em nenhum dos 4 sistemas) existia lá. Sem efeito visível aqui porque SGCD/SGCA/SGDP definiam `.dash-top` localmente (byte-idêntico entre os 3), mas deixava o SGEA sem nenhum estilo no cabeçalho de cada tela. Corrigido movendo `.dash-top` para `base.css` e removendo a cópia local agora redundante

## [1.32.2] — 2026-07-14

### Corrigido
- **`customConfirm()` do esqueleto compartilhado (`_esqueleto/base.js`) travava para sempre ao fechar por Esc ou clique fora do overlay** — corrigido na fonte compartilhada e propagado aos 4 sistemas via `sync.py`. Sem efeito prático no SGDP, que usa sua própria caixa de confirmação (não baseada em `customConfirm`); atualização apenas para manter o arquivo vendorizado idêntico ao dos demais sistemas

## [1.32.1] — 2026-07-13

### Alterado
- **Migração para o esqueleto compartilhado da família** (`_esqueleto/base.css`/`base.js`/`sgx_base.py`, vendorizados via `sync.py`) — remove duplicação de CSS/JS/backend entre SGCD/SGCA/SGDP/SGEA (tokens, tema de cor, modal, busca global, notificações, `get_db`, hashing, sessões, watchdog). Tela de login convertida de página cheia (`#tela-login`) pro padrão overlay (`#overlay-pin`/`.pin-*`) dos outros 3 sistemas, com a identidade visual do SGDP (cabeçalho e ícone/badge dourados) preservada. Os 10 modais mantiveram os mesmos nomes de função (`abrirModal`/`fecharModal`/`confirmar`) — só o mecanismo interno mudou.

### Corrigido
- **Senha incorreta na tela de login não mostrava mensagem de erro** — disparava o mesmo fluxo de "sessão expirada" (limpava o campo, sem avisar o motivo). Bug pré-existente, exposto ao comparar com o padrão dos outros sistemas durante a migração acima.

## [1.32.0] — 2026-07-13

### Corrigido — Padronização arquitetural no padrão SGCD (Fase 2)
- **Perda de foco/cursor ao paginar a lista de documentos** — o campo de busca, o filtro de ano e os botões da barra de ferramentas eram reconstruídos via `innerHTML` a cada clique de paginação (mesmo com o valor digitado preservado, o campo virava um elemento novo, perdendo posição do cursor e qualquer estado de digitação em andamento). A barra de ferramentas de Leis/Decretos/Portarias/Pareceres/Ofícios agora é fixa — só a lista de documentos e a paginação são atualizadas a cada navegação de página, filtro ou busca
- Adicionado teste E2E que semeia 55 documentos, pagina e confirma que o campo de busca continua sendo o mesmo elemento do DOM (não reconstruído) — rede de segurança automatizada contra reintroduzir essa classe de bug

## [1.31.0] — 2026-07-13

### Alterado — Padronização arquitetural no padrão SGCD (Fase 1)
- **Motor de navegação reescrito**: as 9 rotas (Dashboard, 5 tipos de documento, Relatório, Agenda, Lixeira, Auditoria, Configurações) deixam de reconstruir a página inteira (cabeçalho incluso) a cada navegação. Agora são 7 blocos de tela estáticos (`view-dashboard`, `view-documentos` — compartilhado pelos 5 tipos —, `view-relatorio`, `view-agenda`, `view-lixeira`, `view-auditoria`, `view-configuracoes`), cada um com seu próprio cabeçalho fixo (título + botões de ação), mostrados/escondidos via `_showView()` — mesmo padrão já usado no SGCD/SGCA/SGDP (dashboard) e agora replicado nas telas restantes
- **Removida a barra de cabeçalho compartilhada** (`.page-header`/`#page-title`/`#header-actions`) — a barra compartilhada era a causa raiz do bug do fundo branco no tema escuro corrigido na v1.30.3 (um elemento genérico, reconstruído a cada navegação, sem paralelo nos sistemas irmãos)
- **Skeleton de carregamento do Dashboard** — agora é HTML estático (visível só no primeiro carregamento da sessão), igual ao padrão do SGCD, em vez de reinjetado via JavaScript a cada vez que se volta para o Dashboard

### Corrigido
- **Bug de roteamento por link direto/atualização de página** — abrir a URL com `#agenda` ou `#lixeira` (ou atualizar a página nessas telas) caía incorretamente no Dashboard; a lista de rotas válidas usada nesse caminho estava desatualizada e faltavam essas duas entradas. Corrigido na raiz: a lista agora é derivada automaticamente do mapa de rotas usado pela navegação normal, então não pode mais dessincronizar

## [1.30.3] — 2026-07-13

### Corrigido
- **Barra de cabeçalho ("Configurações", "Voltar", "Salvar" etc.) ficava branca no tema escuro** — o `.page-header` tinha fundo fixo `#fff` sem nenhuma sobrescrita para `body.dark`, deixando uma faixa branca no topo de toda página e o título quase invisível (a cor do título já era clara para o modo escuro, mas ficava sobre fundo branco). Corrigido com `body.dark .page-header { background: #1e2436 }`, a mesma cor de superfície elevada já usada em tabelas, modais e cards escuros

## [1.30.2] — 2026-07-13

### Corrigido
- **Legend "Backup de Dados" (aba Dados) usava `var(--brand)` em vez de `var(--accent-light)`** — mesmo desvio de token corrigido na v1.30.1 para a aba Interface, agora estendido a este legend

## [1.30.1] — 2026-07-13

### Corrigido — Auditoria de consistência visual (comparação lado a lado com SGCD/SGCA)
- **Largura "Compacta" media 1100px, contra 960px no SGCD/SGCA** — mesmo rótulo, larguras diferentes; corrigido para 960px
- **Ordem das seções na aba Interface trocada** — "Cor de destaque" vinha antes de "Tamanho da fonte"; invertido para bater com o padrão SGCD/SGCA
- **"Brasão do Município" estava na aba Interface** — nos irmãos essa configuração fica na aba Organização, junto da Autoridade Responsável; movido para lá
- **Cor do rótulo das seções (legend) usava `var(--brand)` em vez de `var(--accent-light)`** — mesmo token usado no SGCD/SGCA para essas 4 seções da aba Interface

## [1.30.0] — 2026-07-13

### Alterado — Auditoria de consistência visual (P3: dívida estrutural)
- **Nomenclatura de classes alinhada ao padrão SGCD/SGCA** — `tema-escuro` → `dark`, `.modal-hd`/`.modal-ft` → `.modal-header`/`.modal-footer` (CSS, JS e testes E2E). Sem efeito visual: apenas os nomes internos usados pelos 3 sistemas passam a ser os mesmos, o que facilita portar correções entre eles

## [1.29.0] — 2026-07-13

### Alterado — Auditoria de consistência visual (P2: convergências ao padrão SGCD)
- **Tokens de status unificados** — `--green` #16a34a→#15803d e fundos `--green-bg/--red-bg/--yellow-bg` alinhados aos valores do SGCD/SGCA; adicionada a **camada semântica** (`--danger/--success/--warning` + variantes `-light`) com os mesmos nomes dos irmãos, para intercambialidade de código
- **Botões** — `.btn` .85rem, `.btn-sm` 5px 12px/.78rem, `.btn-outline` borda `--gray-200` e hover na cor da marca, `.btn-danger:hover` #991b1b (valores do SGCD)
- **Modais** — largura máxima 580→560px e padding do corpo 22px→20px 24px
- **Escala de z-index normalizada** para a convenção do SGCD — overlay 200, toast 300, busca global 1100, notificações 9999 (antes: 1000/9999/1100/1200, mesma semântica com números próprios)
- **Miudezas** — foco da busca global (inset −2px), texto da upload-zone `--gray-500`, abas de Configurações (.78rem, margem 24px), cor-base do texto `--gray-900`

## [1.28.0] — 2026-07-13

### Alterado — Auditoria de consistência visual (P1)
- **Cor de destaque (accent) agora segue o tema de cor** — o dourado fixo (`--accent: #b8962e`) ficava inalterado ao trocar para tema azul/verde/roxo, enquanto nos irmãos SGCD/SGCA o accent acompanha o tema. Valores e sobrescritas de tema alinhados ao padrão SGCD (#1a3a6b no institucional). Afeta: borda do item ativo do menu, fundo dos toasts, tela de troca de senha obrigatória
- **Anéis de foco/pulso derivam do brand via `color-mix`** — pulso do botão Salvar e borda do card do órgão no login deixam de usar navy fixo e passam a acompanhar o tema (mesmo padrão aplicado ao SGCD/SGCA nesta rodada)

## [1.27.0] — 2026-07-12

### Adicionado
- **Últimos instrumentos no Dashboard** — cada card de tipo agora mostra uma linha "Último: 012/2026" com a numeração mais alta cadastrada daquele tipo (ex: a última Lei, o último Parecer), em destaque na cor do tipo; "Último: —" quando ainda não há documento. Backend: `/api/dashboard` ganhou o campo `ultimos` (maior `ano`/`numero` por tipo, ignorando lixeira)

### Corrigido
- **Título da aba do navegador** — mostrava "SGDP — Procuradoria-Geral"; padronizado com SGCD/SGCA para "SGDP v1.27.0" via nova constante `SGDP_VERSION` + `document.title`. A versão agora tem fonte única: o badge da tela de login e o rodapé do app leem da mesma constante (classe `.sgdp-version-badge`, como o `.sgcd-version-badge` do SGCD) em vez de números hardcoded em três lugares
- **Card do Dashboard mostrava "Parecers"** — o rótulo era montado como singular + "s", plural errado para Parecer; novo mapa `TIPOS_PLURAL` com os plurais corretos

---

## [1.26.0] — 2026-07-10

### Adicionado — Acessibilidade (WCAG 2.1 AA)
Correções de uma auditoria de acessibilidade dedicada (leitura de código + cálculo de contraste, 8 frentes: contraste de cor, texto alternativo, associação de rótulos, teclado, foco, alvo de toque, modais, landmarks).

- **Navegação por teclado** — menu lateral (12 itens) e demais elementos clicáveis que usavam `<div onclick>`/`<span onclick>` (cards de dashboard, chips de etiqueta, itens de busca global e notificações) agora têm `role="button"` + `tabindex="0"`, ativados por Enter/Espaço via um único listener delegado
- **Rótulos de formulário associados** — `<label for>`/`id` ou `aria-label` adicionados em todos os campos que dependiam só de proximidade visual: modais de documento, usuário, lembrete, e-mail, vínculos, assinatura, troca de senha forçada, e todas as abas de Configurações (Organização, Segurança, Interface, Backup, SMTP)
- **Contraste de texto corrigido** — `--gray-400` (usado como cor de texto em ~30 pontos: dicas de formulário, estados vazios, "Carregando…") tinha 2,54:1 de contraste sobre branco; unificado com `--gray-500` (4,83:1). Texto secundário da barra lateral (data, seções do menu) também ajustado
- **Indicador de foco visível** — adicionado `:focus`/`:focus-visible` nos campos que removiam o contorno padrão sem substituto (filtros de auditoria, pasta de backup, seletor de tema/largura/fonte, campo de confirmação de exclusão, busca global)
- **Modais com semântica de diálogo** — `role="dialog"` + `aria-modal="true"` + `aria-labelledby` nos 10 modais do sistema; foco automático no primeiro campo ao abrir; Tab preso dentro do modal enquanto aberto; foco devolvido a quem acionou o modal ao fechar
- **Alt text e área de toque** — imagens de brasão (sidebar e preview de upload) com texto alternativo correto; botões de fechar (✕) com área clicável de 44×44px sem alterar o tamanho visual do ícone

## [1.25.2] — 2026-07-10

### Corrigido
- **Nome do órgão na barra lateral combinava com o município** ("Prefeitura Municipal de Orindiúva — Orindiúva/SP") — agora mostra só o nome do órgão, mesmo padrão do SGCD e do SGCA

## [1.25.1] — 2026-07-10

### Adicionado
- **Data de hoje na barra lateral**, abaixo do campo de busca — mesmo padrão visual já usado no SGCD e no SGCA

## [1.25.0] — 2026-07-10

### Corrigido
- **Servidor podia encerrar sozinho no meio do uso (Modo Pessoal)** — `_check_shutdown()` chamava `os._exit(0)` quando a última sessão ativa expirava; uma aba em segundo plano (ex. ao gerar um documento, que abre popup e tira o foco da aba principal) sofria throttling do navegador no `setInterval` do ping, a sessão expirava sem ninguém ter saído de propósito, e o servidor se autodestruía no meio do uso. Corrigido removendo esse caminho inteiramente — o servidor agora só encerra via Ctrl+C no terminal.
- **`SESSION_TTL` aumentado de 15s para 60s** — 15s era propositalmente curto para o antigo modo "Pessoal" detectar rápido que o navegador tinha fechado; sem esse motivo, virou uma margem perigosamente curta para o uso normal (chamadas de API concorrentes no login, aba perdendo foco ao abrir popup de documento).
- **Menu inicial simplificado** — em vez de escolher entre "Pessoal" e "Servidor", agora são só 2 opções: Diagnóstico ou Iniciar Servidor. Iniciar sempre abre o navegador automaticamente e o sistema fica sempre disponível. Removido o botão "Fechar Sistema", que prometia um encerramento que não existe mais.
- **3 pontos de vazamento de conexão SQLite** nos caminhos de backup/restore — `sqlite3.connect()` chamado sem a factory que fecha a conexão automaticamente (mesma classe de bug já corrigida em outros pontos do sistema).
- **Watchdog podia morrer para sempre com valor não-numérico em `auto_backup_keep`** — `_get_backup_cfg()` agora ignora o valor inválido em vez de derrubar a thread.
- **`handle_error` nunca era chamado de verdade** (é método de `socketserver.BaseServer`, não do request handler — exceções não tratadas em qualquer `do_GET/POST/PUT/DELETE` derrubavam a conexão sem log nem resposta ao cliente). Substituído por um `_safe_dispatch` que envolve os 4 handlers, loga o erro e responde 500 em vez de deixar a conexão cair silenciosamente.

## [1.24.0] — 2026-07-10

### Adicionado
- **Suíte de testes E2E (`tests/e2e/`)**, usando Playwright — sobe o servidor real (`SGDP_PORT`/`SGDP_DATA_DIR` isolam porta e banco/uploads/backups dos de produção) e dirige um Chromium de verdade pelo login com troca de senha obrigatória e criação de documento. Mesma implementação do SGCD/SGCA

## [1.23.0] — 2026-07-10

### Adicionado
- **`Instalar Assinatura ICP-Brasil.bat`** (opcional) — habilita o pip via `get-pip.py` (incluído) e instala o `pyhanko` quando o servidor usa o Python embarcável, que não vem com pip. `Iniciar SGDP.bat` agora também habilita o módulo `site` na extração do Python embarcável (pré-requisito para o script funcionar depois). Mesma implementação do SGCD/SGCA

## [1.22.0] — 2026-07-10

### Adicionado
- **Exportar CSV na Trilha de Auditoria** — botão "Exportar CSV" no cabeçalho da tela, respeitando a busca/filtros ativos. Mesmo padrão do SGCD/SGCA (SGDP não tem tela de Fornecedores)

## [1.21.0] — 2026-07-09

### Adicionado
- **Troca de senha obrigatória no primeiro acesso** — o admin padrão (criado com `admin`/`admin123`) é obrigado a definir uma nova senha antes de acessar o sistema, em vez de depender só do aviso impresso no terminal. Nova coluna `usuarios.must_change_password` (migração automática, instalações existentes não são afetadas). Mesma implementação do SGCD/SGCA

### Corrigido
- **Ping de renovação de sessão só começava depois do login completo** — o `setInterval` que renova a sessão a cada 5s (`SESSION_TTL=15s`) ficava dentro de `mostrarApp()`, chamada só após o usuário passar pela tela de troca de senha obrigatória. Se o usuário demorasse mais de 15s nessa tela, a sessão expirava e a troca de senha falhava com 401. Movido para escopo global, como já era no SGCD/SGCA

## [1.20.5] — 2026-07-09

### Corrigido
- **Clicar fora de um modal fechava a janela e descartava os dados digitados** — a função compartilhada `abrirModal()` anexava um fechamento por clique no fundo a todo modal aberto por ela (Documento, Usuário, Lembrete, E-mail, Histórico, Vínculos, Assinatura, CSV, Confirmação), além da Busca Global. Removido; agora só fecham pelo botão Cancelar/✕ ou pela tecla Esc

## [1.20.4] — 2026-07-09

### Corrigido
- **Título de página desalinhado do conteúdo em modo Compacto** — `.page-header` não respeitava o mesmo `max-width`/centralização que `.page-content`, então em telas largas o título (fixo à esquerda) ficava visualmente desconectado do card de conteúdo (centralizado) abaixo dele — mais perceptível em Configurações. Criado `.page-header-inner`, que replica o mesmo box-model de `.page-content` (mesmo padding, mesmo `max-width:1100px` centralizado em modo Compacto), alinhando os dois em todas as páginas. Removido também o `max-width:900px` específico do `.cfg-wrap` (remendo da v1.18.0 só para caber as abas) — agora usa a largura padrão da página, como o SGCD
- **Configuração de "Tamanho da fonte" sem nenhum efeito visível** — `body` tinha `font-size` fixo em `px` (não herdava do `<html>`) e praticamente todo o restante da folha de estilo também usava `px` em vez de `rem`, então as classes `html.font-pequena`/`.font-grande` (aplicadas ao trocar a preferência) não alteravam nada visível na tela. Convertidas cerca de 90 declarações de `font-size` — a folha de estilo principal e as telas de Configurações/Backup, onde o problema foi reportado — de `px` para `rem`, mesmo padrão já usado pelo SGCD. Os documentos em papel timbrado continuam fixos em `px` de propósito (impressão não deve variar com a preferência de acessibilidade da tela)

## [1.20.3] — 2026-07-09

### Alterado
- **Senha padrão do usuário admin** — instalações novas passam a criar `admin` / `admin123`, igual ao SGCD/SGCA (era `admin` / `sgdp2024`). Instalações já existentes não são alteradas — a senha continua sendo o que já foi definido no banco

## [1.20.2] — 2026-07-09

### Alterado
- **Tela de Configurações unificada com SGCD/SGCA** — lista de usuários passa de cards para tabela (Usuário/Nome/Cargo-Matrícula/Admin/Ativo/Ações), com heading "Gerenciar Usuários" e subtítulo; título "Configurações" ganha botões "← Voltar" e "Salvar" no mesmo cabeçalho, igual ao padrão dos outros dois sistemas
- **Botão Salvar único e consolidado** — Interface, Organização, Comunicação (SMTP) e o backup automático (aba Dados) deixam de ter botões de salvar independentes por seção e passam a ser salvos juntos por um único botão "Salvar" no cabeçalho, com indicador visual (pulso) quando há alterações não salvas. Seleção de pasta de backup, exportar/restaurar/sincronizar backup, Factory Reset e troca de senha continuam sendo ações imediatas e independentes, como já eram

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
