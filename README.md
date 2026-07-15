# SGDP — Sistema de Gestão de Documentos da Procuradoria

![Versão](https://img.shields.io/badge/versão-v1.33.2-blue) ![Tecnologia](https://img.shields.io/badge/tecnologia-Python%20%2B%20SQLite-orange) ![Licença](https://img.shields.io/badge/licença-MIT-green) ![Multiusuário](https://img.shields.io/badge/acesso-multiusuário-blueviolet) [![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21314680.svg)](https://doi.org/10.5281/zenodo.21314680) [![CI](https://github.com/devtulio/sgdp/actions/workflows/ci.yml/badge.svg)](https://github.com/devtulio/sgdp/actions/workflows/ci.yml)

## Descrição

O **SGDP** é uma aplicação web multiusuário para a **Procuradoria-Geral municipal** gerenciar seus documentos jurídicos — Leis, Decretos, Portarias, Pareceres e Ofícios. O sistema controla numeração automática, armazena os PDFs assinados digitalmente e oferece busca, auditoria e backup completo.

Funciona em rede local: um único computador executa o servidor e todos os procuradores acessam pelo navegador via IP ou `localhost`.

---

## Funcionalidades Principais

- **Gestão de 5 tipos de documento:** Lei, Decreto, Portaria, Parecer e Ofício
- **Numeração automática** por tipo e ano, com possibilidade de ajuste manual
- **Últimos instrumentos no Dashboard** — cada card de tipo mostra a numeração mais recente cadastrada (ex: Última Lei: 012/2026)
- **Campo Assunto** com categorias dinâmicas: 14 gerais e 46 jurídicas específicas para Parecer
- **Campos específicos por tipo** — Parecer: PA + Modalidade licitatória (ativados por assunto); Portaria: tipo de ato e cargo
- **Relatório Gerencial** — produção por tipo, assunto e mês com filtros de período e gráfico SVG
- **Sons de notificação** — feedback sonoro para cliques, sucesso e erro via Web Audio API
- **Brasão do município** na sidebar, configurável e persistido no servidor (visível para todos os procuradores)
- **Upload e visualização de PDF** assinado diretamente no navegador
- **Login multiusuário** com sessões de 8 horas — até N procuradores simultâneos
- **Busca e filtros** por número, ementa, partes envolvidas e ano
- **Trilha de auditoria** completa de todas as ações (criar, editar, excluir, upload)
- **Gestão de usuários** com perfil administrador e perfil padrão, cada um com departamento fixo (Procuradoria-Geral ou Gabinete)
- **Documentos sigilosos** — marcação opcional que restringe a visibilidade e a edição a quem criou e a administradores; documentos não-sigilosos podem ser editados por qualquer colega do mesmo departamento de quem criou. Coluna "Origem" no Dashboard e nas listagens mostra o departamento de cada documento
- **Backup e restauração** — exporta JSON com todos os documentos e PDFs; backup automático do banco de dados
- **Sincronização de backup entre agentes** — mescla dados de outra instalação (soma o que é novo, revisa o que conflita) sem substituir o banco inteiro
- **Navegação persistente** — F5 mantém o usuário na tela atual via `location.hash`
- **Encerramento do sistema** com tela de confirmação e desligamento do servidor
- **Diagnóstico de rede** — verifica IP, porta 3001, regras de firewall e acessibilidade pela LAN; disponível como script independente e no menu de inicialização
- **Lixeira** — exclusão reversível com restauração em até 30 dias
- **Agenda** — lembretes com prazo, vinculáveis a documentos
- **Envio por e-mail** — anexa o PDF assinado e envia via SMTP configurável
- **Importação em lote via CSV** — cadastro de múltiplos documentos de uma vez
- **Personalização visual** — largura do conteúdo (compacta/expandida) e cor de destaque (institucional/azul/verde/roxo)
- **Histórico de revisões** — cada edição de documento guarda os campos anteriores, com autor e data/hora
- **Notificação de prazo por e-mail** — avisa automaticamente o responsável (ou um e-mail padrão) quando um lembrete vence
- **Resumo diário por e-mail** — envia diariamente um resumo de lembretes vencidos e vencendo em breve, mesmo sem ninguém logado
- **Busca full-text (FTS5)** — pesquisa em ementa, partes e observações por palavras (não só substring), com índice sincronizado automaticamente
- **Etiquetas (tags) livres** — categorização adicional além do Assunto fixo, com autocomplete e filtro por etiqueta
- **Vínculos entre documentos** — relação tipada (revoga, altera, complementa, referencia) navegável nos dois sentidos
- **Exportação do relatório** — CSV com os dados brutos do período, e impressão/PDF limpo (sem menu/sidebar)
- **Exportação da Trilha de Auditoria em CSV**, respeitando os filtros ativos na tela
- **Assinatura digital ICP-Brasil** — assina o PDF anexado com certificado A1 (`.pfx`), nível qualificado (dependência opcional)
- **Verificação pública de assinatura** — cada assinatura gera um código de verificação consultável sem login em `/verificar/<código>`
- **Sincronização de auditoria entre instâncias** — a sincronização de backup também mescla o histórico de auditoria, preservando autor e data originais
- **Relatórios em papel timbrado** — Pendências e Prazos, Produtividade por Usuário, Certidão de Documento, Certidão Negativa de Pendências, Certidões em lote, Cadeia Normativa, Relatório de Etiquetas e Backup e Integridade (admin), no mesmo padrão documental do SGCD (brasão, Times New Roman, bloco de assinatura, QR de verificação quando o documento tem assinatura digital)
- **Comparativo entre períodos** no Relatório Gerencial — variação (▲/▼, diferença e percentual) contra o intervalo de mesma duração imediatamente anterior

---

## Requisitos

- **Python 3.8+** (apenas biblioteca padrão — sem dependências externas)
- **Google Chrome** ou **Microsoft Edge** (recomendado)
- Windows 10/11
- Opcional: `pip install -r requirements.txt` — só necessário para o módulo de assinatura com certificado ICP-Brasil (`pyhanko`)

> **Servidor sem Python instalado (ex.: Windows Server bloqueado por política de TI):**
> o `Iniciar SGDP.bat` detecta automaticamente a ausência do Python e extrai uma versão portátil (embarcável, sem instalador) incluída no próprio projeto (`python-3.12.9-embed-amd64.zip`) para `C:\Python312-embed\` — não exige instalação nem privilégio de administrador.
>
> Essa versão portátil não vem com `pip` pronto (limitação do próprio pacote embarcável do Python). Se esse servidor precisar do módulo de assinatura ICP-Brasil, rode **`Instalar Assinatura ICP-Brasil.bat`** depois — ele habilita o pip e instala o `pyhanko` (requer acesso à internet só nesse momento, para baixar do PyPI).

---

## Instalação e uso

1. Copie a pasta `SGDP/` para o computador que atuará como servidor
2. Clique duas vezes em **`Iniciar SGDP.bat`**
3. Selecione o modo de operação no menu que aparecer
4. Faça login com as credenciais iniciais abaixo e **altere a senha imediatamente**

> ⚠️ **Importante:** abrir o `SGDP.html` diretamente pelo navegador (sem o servidor) impede o funcionamento do sistema. Use sempre o `Iniciar SGDP.bat`.

### Login inicial

| Campo   | Valor       |
|---------|-------------|
| Usuário | `admin`     |
| Senha   | `admin123`  |

### Modo de operação

| Opção | Descrição |
|-------|-----------|
| **[1] Pessoal** | Uso individual — abre o navegador automaticamente e encerra ao sair |
| **[2] Servidor** | Máquina central em rede — fica rodando continuamente (Ctrl+C para parar) |
| **[3] Diagnóstico** | Verifica rede, porta e firewall |

### Acesso em rede local

O sistema foi projetado para uso multiusuário em rede local (LAN): **uma única máquina executa o servidor** (e guarda o banco de dados) e os demais procuradores acessam pelo navegador, sem instalar nada.

**Na máquina servidora (uma vez só):**

1. Execute **`Liberar Porta SGDP.bat`** como Administrador (botão direito → *Executar como administrador*) — cria a regra no Firewall do Windows liberando a porta 3001 para conexões de entrada
2. Inicie o sistema pelo `Iniciar SGDP.bat` e deixe a máquina ligada — ao iniciar, o console mostra o endereço de rede pronto para distribuir (`Rede: http://<IP>:3001/SGDP.html`)

**Nas outras máquinas:** basta abrir o navegador (Chrome ou Edge) no endereço do servidor:

```
http://192.168.x.x:3001/SGDP.html
```

Cada procurador faz login com sua própria conta — o servidor atende acessos simultâneos e todos enxergam os mesmos dados.

Se a conexão não funcionar, execute **`Diagnostico SGDP.bat`** (ou a opção **[3]** do `Iniciar SGDP.bat`) na máquina servidora: ele descobre o IP e verifica a acessibilidade pela rede.

> ⚠️ **Uso restrito à rede interna.** A comunicação é HTTP simples (sem criptografia de transporte) — adequado para uma LAN interna confiável, mas **nunca exponha a porta do sistema à internet** (redirecionamento de porta no roteador, DMZ etc.). Para acesso remoto, use a VPN institucional.

---

## Estrutura de arquivos

```
SGDP/
├── SGDP.html               # Frontend — aplicação web completa
├── server.py               # Servidor Python (API REST + SQLite + uploads)
├── tests/                  # Suíte de testes automatizados do backend
│   ├── test_server.py
│   └── e2e/                # Testes E2E (Playwright) — navegador real de ponta a ponta
├── Iniciar SGDP.bat        # Inicializa o servidor
├── python-3.12.9-embed-amd64.zip  # Python portátil (fallback se não houver Python instalado)
├── Instalar Assinatura ICP-Brasil.bat  # Opcional — instala pip + pyhanko no Python embarcável
├── get-pip.py              # Usado só pelo script acima (Python embarcável não vem com pip)
├── Criar Atalho SGDP.bat   # Cria atalho na área de trabalho com ícone
├── Criar Atalho SGDP.ps1   # Script PowerShell de criação do atalho
├── Diagnostico SGDP.bat    # Roda o diagnóstico de rede (clique duplo)
├── Liberar Porta SGDP.bat  # Cria regra de firewall para porta 3001 (Admin)
├── diagnostico.py          # Script de diagnóstico de rede e firewall
├── sgdp.ico                # Ícone personalizado do sistema
├── sgdp.db                 # Banco de dados SQLite (criado automaticamente)
├── uploads/                # PDFs armazenados (criado automaticamente)
├── backups/                # Backups automáticos (criado automaticamente)
├── requirements.txt        # Dependência opcional (pyhanko — só p/ assinatura ICP-Brasil)
├── README.md
├── CHANGELOG.md
└── MANUAL.html
```

---

## Segurança

- Senhas armazenadas com **PBKDF2-HMAC-SHA256** e salt aleatório por usuário
- Sessões invalidadas automaticamente após 8 horas de inatividade
- Acesso à API exige token de sessão em todas as rotas (exceto login e verificação)
- Upload restrito a PDF, com limite de 50 MB
- Trilha de auditoria imutável registra todas as ações com usuário e timestamp
- Verificação de integridade do banco de dados (SQLite `PRAGMA integrity_check`) na inicialização
- Recomenda-se uso em rede interna (LAN) apenas

---

## Tecnologias

| Tecnologia | Uso |
|-----------|-----|
| **HTML5 + CSS3** | Interface da aplicação, temas claro/escuro, layout responsivo |
| **JavaScript puro (ES6+)** | Toda a lógica de negócio, sem frameworks externos |
| **Python 3 (stdlib)** | Servidor local: REST API, SQLite, auth, SMTP |
| **SQLite** | Armazenamento persistente dos dados (`sgdp.db`), com índice FTS5 para busca full-text |

---

## Desenvolvimento

O sistema em si continua zero-dependência (Python stdlib + HTML puro). Para quem for alterar o código, há um lint opcional que verifica variáveis indefinidas no JavaScript de `SGDP.html`:

```bash
npm install   # uma vez, instala apenas o ESLint (ferramenta de dev, não é usada em produção)
npm run lint
```

Há também uma suíte de testes automatizados do backend (`server.py`), usando só `unittest` da stdlib — sobe o servidor real contra um banco e uploads temporários e testa os endpoints REST (login, documentos, departamento/sigiloso, usuários, vínculos, tags, revisões, importação CSV, arquivos, relatórios, lembretes, auditoria, config, backup/restore e sincronização):

```bash
python -m unittest discover -s tests -v
```

Há também uma suíte de testes E2E (`tests/e2e/`), usando Playwright — sobe o servidor real e dirige um Chromium de verdade pelo fluxo completo (login com troca de senha obrigatória, criar documento):

```bash
npm install
npx playwright install chromium   # uma vez, baixa o navegador de teste
npm run test:e2e
```

Roda contra um banco/uploads/backups temporários (nunca o `sgdp.db` real), criados e descartados automaticamente a cada execução.

---

## Versionamento

Consulte o [CHANGELOG.md](CHANGELOG.md) para o histórico completo de versões e alterações.

---

## Contribuição

Contribuições são bem-vindas! Veja o [CONTRIBUTING.md](CONTRIBUTING.md) para orientações sobre como reportar bugs, sugerir funcionalidades e enviar Pull Requests.

---

## Licença

Distribuído sob a licença **MIT**. Veja [LICENSE](LICENSE) para o texto completo.

> **Aviso:** Os dados ficam armazenados no arquivo `sgdp.db` na pasta do sistema. Faça backups regulares em **Configurações → Dados** e mantenha cópia do `sgdp.db` em local seguro.
