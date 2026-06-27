# Changelog — SGDP
## Sistema de Gestão de Documentos da Procuradoria
> Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/)  
> Versionamento semântico: [SemVer](https://semver.org/lang/pt-BR/)

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
