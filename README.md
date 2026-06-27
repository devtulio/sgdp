# SGDP — Sistema de Gestão de Documentos da Procuradoria

![Versão](https://img.shields.io/badge/versão-v1.0.0-blue) ![Tecnologia](https://img.shields.io/badge/tecnologia-Python%20%2B%20HTML5-navy) ![Licença](https://img.shields.io/badge/licença-MIT-green) ![Multiusuário](https://img.shields.io/badge/acesso-multiusuário-blueviolet)

## Descrição

O **SGDP** é uma aplicação web multiusuário para a **Procuradoria-Geral municipal** gerenciar seus documentos jurídicos — Leis, Decretos, Portarias, Pareceres e Ofícios. O sistema controla numeração automática, armazena os PDFs assinados digitalmente e oferece busca, auditoria e backup completo.

Funciona em rede local: um único computador executa o servidor e todos os procuradores acessam pelo navegador via IP ou `localhost`.

---

## Funcionalidades

- **Gestão de 5 tipos de documento:** Lei, Decreto, Portaria, Parecer e Ofício
- **Numeração automática** por tipo e ano, com possibilidade de ajuste manual
- **Upload e visualização de PDF** assinado diretamente no navegador
- **Login multiusuário** com sessões de 8 horas — até N procuradores simultâneos
- **Busca e filtros** por número, ementa, partes envolvidas e ano
- **Trilha de auditoria** completa de todas as ações (criar, editar, excluir, upload)
- **Gestão de usuários** com perfil administrador e perfil padrão
- **Backup e restauração** — exporta um arquivo JSON com todos os documentos e PDFs incluídos
- **Encerramento automático** do servidor ao fechar todas as janelas do navegador

---

## Requisitos

- **Python 3.8+** (apenas biblioteca padrão — sem dependências externas)
- **Google Chrome** ou **Microsoft Edge** (recomendado)
- Windows 10/11

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
├── SGDP.html          # Frontend — aplicação web completa
├── server.py          # Servidor Python (API REST + SQLite + uploads)
├── Iniciar SGDP.bat   # Atalho de inicialização
├── sgdp.db            # Banco de dados SQLite (criado automaticamente)
├── uploads/           # PDFs armazenados (criado automaticamente)
├── README.md
├── CHANGELOG.md
└── MANUAL.html
```

---

## Segurança

- Senhas armazenadas com **PBKDF2-HMAC-SHA256** e salt aleatório por usuário
- Sessões invalidadas automaticamente após 8 horas de inatividade
- Acesso à API exige token de sessão em todas as rotas (exceto login)
- Recomenda-se uso em rede interna (LAN) apenas

---

## Licença

MIT © Município — uso interno da Procuradoria-Geral.
