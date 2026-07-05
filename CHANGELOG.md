# Changelog — SGDP
## Sistema de Gestão de Documentos da Procuradoria
> Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/)  
> Versionamento semântico: [SemVer](https://semver.org/lang/pt-BR/)

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
