# SGDP — Sistema de Gestão de Documentos da Procuradoria

![Versão](https://img.shields.io/badge/versão-v1.20.2-blue) ![Tecnologia](https://img.shields.io/badge/tecnologia-Python%20%2B%20HTML5-navy) ![Licença](https://img.shields.io/badge/licença-MIT-green) ![Multiusuário](https://img.shields.io/badge/acesso-multiusuário-blueviolet)

## Descrição

O **SGDP** é uma aplicação web multiusuário para a **Procuradoria-Geral municipal** gerenciar seus documentos jurídicos — Leis, Decretos, Portarias, Pareceres e Ofícios. O sistema controla numeração automática, armazena os PDFs assinados digitalmente e oferece busca, auditoria e backup completo.

Funciona em rede local: um único computador executa o servidor e todos os procuradores acessam pelo navegador via IP ou `localhost`.

---

## Funcionalidades

- **Gestão de 5 tipos de documento:** Lei, Decreto, Portaria, Parecer e Ofício
- **Numeração automática** por tipo e ano, com possibilidade de ajuste manual
- **Campo Assunto** com categorias dinâmicas: 14 gerais e 46 jurídicas específicas para Parecer
- **Campos específicos por tipo** — Parecer: PA + Modalidade licitatória (ativados por assunto); Portaria: tipo de ato e cargo
- **Relatório Gerencial** — produção por tipo, assunto e mês com filtros de período e gráfico SVG
- **Sons de notificação** — feedback sonoro para cliques, sucesso e erro via Web Audio API
- **Brasão do município** na sidebar, configurável e persistido no servidor (visível para todos os procuradores)
- **Upload e visualização de PDF** assinado diretamente no navegador
- **Login multiusuário** com sessões de 8 horas — até N procuradores simultâneos
- **Busca e filtros** por número, ementa, partes envolvidas e ano
- **Trilha de auditoria** completa de todas as ações (criar, editar, excluir, upload)
- **Gestão de usuários** com perfil administrador e perfil padrão
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

---

## Instalação e uso

1. Copie a pasta `SGDP/` para o computador que atuará como servidor
2. Clique duas vezes em **`Iniciar SGDP.bat`**
3. O navegador abrirá automaticamente em `http://localhost:3001`
4. Faça login com as credenciais iniciais abaixo e **altere a senha imediatamente**

### Login inicial

| Campo   | Valor      |
|---------|------------|
| Usuário | `admin`    |
| Senha   | `sgdp2024` |

### Acesso em rede local

Os outros procuradores acessam pelo IP do computador servidor:

```
http://192.168.x.x:3001
```

---

## Estrutura de arquivos

```
SGDP/
├── SGDP.html               # Frontend — aplicação web completa
├── server.py               # Servidor Python (API REST + SQLite + uploads)
├── tests/                  # Suíte de testes automatizados do backend
│   └── test_server.py
├── Iniciar SGDP.bat        # Inicializa o servidor
├── python-3.12.9-embed-amd64.zip  # Python portátil (fallback se não houver Python instalado)
├── Criar Atalho SGDP.bat   # Cria atalho na área de trabalho com ícone
├── Criar Atalho SGDP.ps1   # Script PowerShell de criação do atalho
├── Diagnostico SGDP.bat    # Roda o diagnóstico de rede (clique duplo)
├── Liberar Porta SGDP.bat  # Cria regra de firewall para porta 3001 (Admin)
├── diagnostico.py          # Script de diagnóstico de rede e firewall
├── sgdp.ico                # Ícone personalizado do sistema
├── sgdp.db                 # Banco de dados SQLite (criado automaticamente)
├── uploads/                # PDFs armazenados (criado automaticamente)
├── requirements.txt        # Dependência opcional (pyhanko — só p/ assinatura ICP-Brasil)
├── README.md
├── CHANGELOG.md
└── MANUAL.html
```

---

## Testes

O sistema em si continua zero-dependência (Python stdlib + HTML puro). Há uma suíte de testes automatizados do backend (`server.py`), usando só `unittest` da stdlib — sobe o servidor real contra um banco e uploads temporários e testa os endpoints REST (login, documentos, lembretes, auditoria, backup e sincronização):

```bash
python -m unittest discover -s tests -v
```

Para quem for alterar o código, há também um lint opcional que verifica variáveis indefinidas no JavaScript de `SGDP.html`:

```bash
npm install   # uma vez, instala apenas o ESLint (ferramenta de dev, não é usada em produção)
npm run lint
```

---

## Segurança

- Senhas armazenadas com **PBKDF2-HMAC-SHA256** e salt aleatório por usuário
- Sessões invalidadas automaticamente após 8 horas de inatividade
- Acesso à API exige token de sessão em todas as rotas (exceto login)
- Recomenda-se uso em rede interna (LAN) apenas

---

## Contribuição

Contribuições são bem-vindas! Veja o [CONTRIBUTING.md](CONTRIBUTING.md) para orientações sobre como reportar bugs, sugerir funcionalidades e enviar Pull Requests.

---

## Licença

MIT © Município — uso interno da Procuradoria-Geral.
