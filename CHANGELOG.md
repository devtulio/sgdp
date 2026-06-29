# Changelog — SGDP
## Sistema de Gestão de Documentos da Procuradoria
> Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/)  
> Versionamento semântico: [SemVer](https://semver.org/lang/pt-BR/)

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
