# SGDP v1.33.6 — Servidor local: SQLite, autenticação, REST API, uploads de PDF
import http.server
import socketserver
import socket
import sys
import os
import json
import sqlite3
import hashlib
import secrets
import threading
import time
import subprocess
import re
import html as html_mod
import logging
import mimetypes
from urllib.parse import urlparse, parse_qs

import sgx_base

# Windows: console pode usar cp1252/cp850 em vez de UTF-8, quebrando prints
# com caracteres especiais (╔═╗, emojis). Força UTF-8 para evitar UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

PORT              = int(os.environ.get('SGDP_PORT', 3001))
_BASE             = os.path.dirname(os.path.abspath(__file__))
# SGDP_DATA_DIR: usado pelos testes E2E para isolar banco/uploads/backups do
# sgdp.db real sem precisar rodar o servidor a partir de outra pasta (os
# arquivos estáticos como SGDP.html continuam servidos a partir de _BASE).
_DATA_DIR         = os.environ.get('SGDP_DATA_DIR', _BASE)
DB_PATH           = os.path.join(_DATA_DIR, 'sgdp.db')
UPLOADS_DIR       = os.path.join(_DATA_DIR, 'uploads')
BACKUP_DIR        = os.path.join(_DATA_DIR, 'backups')
LOG_PATH          = os.path.join(_DATA_DIR, 'sgdp_errors.log')
BACKUP_KEEP       = 7
SESSION_TTL       = 60   # renovado pelo ping a cada 5s (ver comentário em _watchdog mais abaixo)
MAX_UPLOAD_SIZE   = 50 * 1024 * 1024

os.makedirs(_DATA_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH, level=logging.ERROR,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)
_log = logging.getLogger('sgdp')

os.chdir(_BASE)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR,  exist_ok=True)

_had_session      = False   # True após primeiro login; controla quando o backup pós-sessão pode disparar
_backup_pos_sess  = False   # True = backup pós-sessão já executado; aguarda nova sessão para resetar
FTS_AVAILABLE     = False   # True se o SQLite tem FTS5 compilado (setado em init_db)
_watchdog_paused  = False   # pausa o watchdog durante diálogos bloqueantes (ex: FolderBrowser)

TIPOS = ('lei', 'decreto', 'portaria', 'parecer', 'oficio')
DEPARTAMENTOS = ('Procuradoria-Geral', 'Gabinete')
TIPOS_LABELS_CSV = {'lei': 'Lei', 'decreto': 'Decreto', 'portaria': 'Portaria', 'parecer': 'Parecer', 'oficio': 'Ofício'}

# tipo de vínculo -> (label no sentido origem->destino, label no sentido inverso)
TIPOS_VINCULO = {
    'revoga':      ('Revoga',        'Revogado por'),
    'altera':      ('Altera',        'Alterado por'),
    'complementa': ('Complementa',   'Complementado por'),
    'referencia':  ('Referencia',    'Referenciado por'),
}

# ── Banco de dados ────────────────────────────────────────────────────────────

# Alias de compatibilidade: _ConnAutoClose é referenciada diretamente em vários
# pontos além de get_db() (restore, backup) — manter o nome em vez de caçar
# cada call site.
_ConnAutoClose = sgx_base.ConnAutoClose

def get_db():
    return sgx_base.connect_db(DB_PATH)

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
                nome       TEXT NOT NULL,
                senha_hash TEXT NOT NULL,
                admin      INTEGER DEFAULT 0,
                ativo      INTEGER DEFAULT 1,
                criado_em  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token    TEXT PRIMARY KEY,
                user_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                expires  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS arquivos (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_original TEXT NOT NULL,
                nome_disco    TEXT NOT NULL,
                tamanho       INTEGER,
                enviado_por   INTEGER REFERENCES usuarios(id),
                enviado_em    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS documentos (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo           TEXT NOT NULL CHECK(tipo IN ('lei','decreto','portaria','parecer','oficio')),
                numero         INTEGER NOT NULL,
                ano            INTEGER NOT NULL,
                data           TEXT NOT NULL,
                ementa         TEXT NOT NULL,
                partes         TEXT,
                observacoes    TEXT,
                arquivo_id     INTEGER REFERENCES arquivos(id) ON DELETE SET NULL,
                criado_por     INTEGER REFERENCES usuarios(id),
                atualizado_por INTEGER REFERENCES usuarios(id),
                criado_em      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                atualizado_em  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                UNIQUE(tipo, numero, ano)
            );
            CREATE TABLE IF NOT EXISTS contadores (
                tipo   TEXT NOT NULL,
                ano    INTEGER NOT NULL,
                ultimo INTEGER DEFAULT 0,
                PRIMARY KEY (tipo, ano)
            );
            CREATE TABLE IF NOT EXISTS auditoria (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id   INTEGER,
                usuario_nome TEXT,
                acao         TEXT NOT NULL,
                documento_id INTEGER,
                detalhes     TEXT,
                em           TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS sys_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS lembretes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo       TEXT NOT NULL,
                data_prazo   TEXT NOT NULL,
                documento_id INTEGER REFERENCES documentos(id) ON DELETE SET NULL,
                concluido    INTEGER DEFAULT 0,
                criado_por   INTEGER REFERENCES usuarios(id),
                criado_em    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                notificado_em TEXT
            );
            CREATE TABLE IF NOT EXISTS documento_revisoes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                documento_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
                dados_json   TEXT NOT NULL,
                editado_por  INTEGER REFERENCES usuarios(id),
                editado_em   TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS tags (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE COLLATE NOCASE
            );
            CREATE TABLE IF NOT EXISTS documento_tags (
                documento_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
                tag_id       INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (documento_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS documento_vinculos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                origem_id  INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
                destino_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
                tipo       TEXT NOT NULL CHECK(tipo IN ('revoga','altera','complementa','referencia')),
                criado_por INTEGER REFERENCES usuarios(id),
                criado_em  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                UNIQUE(origem_id, destino_id, tipo)
            );
            CREATE TABLE IF NOT EXISTS signatures (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                cod            TEXT NOT NULL UNIQUE,
                documento_id   INTEGER REFERENCES documentos(id) ON DELETE SET NULL,
                doc_tipo       TEXT, doc_numero INTEGER, doc_ano INTEGER, doc_ementa TEXT,
                signer_user_id INTEGER REFERENCES usuarios(id),
                signer_name    TEXT,
                method         TEXT DEFAULT 'icp-brasil',
                cert_subject   TEXT,
                hash_sha256    TEXT,
                signed_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_docs_tipo ON documentos(tipo);
            CREATE INDEX IF NOT EXISTS idx_docs_ano  ON documentos(ano);
            CREATE INDEX IF NOT EXISTS idx_audit_em  ON auditoria(em);
            CREATE INDEX IF NOT EXISTS idx_lembretes_prazo ON lembretes(data_prazo);
            CREATE INDEX IF NOT EXISTS idx_revisoes_doc ON documento_revisoes(documento_id);
            CREATE INDEX IF NOT EXISTS idx_doc_tags_tag ON documento_tags(tag_id);
            CREATE INDEX IF NOT EXISTS idx_vinculos_origem  ON documento_vinculos(origem_id);
            CREATE INDEX IF NOT EXISTS idx_vinculos_destino ON documento_vinculos(destino_id);
            CREATE INDEX IF NOT EXISTS idx_signatures_doc ON signatures(documento_id);
        ''')
        global FTS_AVAILABLE
        try:
            conn.executescript('''
                CREATE VIRTUAL TABLE IF NOT EXISTS documentos_fts USING fts5(
                    ementa, partes, observacoes, content='documentos', content_rowid='id'
                );
                CREATE TRIGGER IF NOT EXISTS documentos_fts_ai AFTER INSERT ON documentos BEGIN
                    INSERT INTO documentos_fts(rowid, ementa, partes, observacoes)
                    VALUES (new.id, new.ementa, new.partes, new.observacoes);
                END;
                CREATE TRIGGER IF NOT EXISTS documentos_fts_ad AFTER DELETE ON documentos BEGIN
                    INSERT INTO documentos_fts(documentos_fts, rowid, ementa, partes, observacoes)
                    VALUES ('delete', old.id, old.ementa, old.partes, old.observacoes);
                END;
                CREATE TRIGGER IF NOT EXISTS documentos_fts_au AFTER UPDATE ON documentos BEGIN
                    INSERT INTO documentos_fts(documentos_fts, rowid, ementa, partes, observacoes)
                    VALUES ('delete', old.id, old.ementa, old.partes, old.observacoes);
                    INSERT INTO documentos_fts(rowid, ementa, partes, observacoes)
                    VALUES (new.id, new.ementa, new.partes, new.observacoes);
                END;
            ''')
            if conn.execute('SELECT COUNT(*) FROM documentos_fts').fetchone()[0] == 0:
                conn.execute('''INSERT INTO documentos_fts(rowid, ementa, partes, observacoes)
                                 SELECT id, ementa, partes, observacoes FROM documentos''')
            FTS_AVAILABLE = True
        except sqlite3.OperationalError as e:
            # ponytail: builds do SQLite sem FTS5 (raro) caem para busca com LIKE
            _log.error('FTS5 indisponível, busca usará LIKE: %s', e)
            FTS_AVAILABLE = False
        conn.executemany('INSERT OR IGNORE INTO sys_settings VALUES (?,?)', [
            ('orgao_nome',           'Procuradoria-Geral'),
            ('municipio',            ''),
            ('aut_nome', ''), ('aut_cargo', ''), ('diario_url', ''),
            ('backup_path',          BACKUP_DIR),
            ('auto_backup_enabled',  '1'),
            ('auto_backup_keep',     str(BACKUP_KEEP)),
            ('smtp_host', ''), ('smtp_port', '587'), ('smtp_user', ''), ('smtp_pass', ''),
            ('smtp_secure', '0'), ('smtp_require_tls', '1'), ('smtp_ignore_ssl', '0'),
            ('smtp_from_name', ''), ('smtp_to', ''),
        ])
        # Migrações de colunas
        cols = [r[1] for r in conn.execute('PRAGMA table_info(documentos)').fetchall()]
        if 'assunto'        not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN assunto        TEXT DEFAULT 'Outros'")
        if 'processo_pa'    not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN processo_pa    TEXT DEFAULT ''")
        if 'processo_tipo'  not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN processo_tipo  TEXT DEFAULT ''")
        if 'processo_ref'   not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN processo_ref   TEXT DEFAULT ''")
        if 'ato_tipo'       not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN ato_tipo       TEXT DEFAULT ''")
        if 'cargo'          not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN cargo          TEXT DEFAULT ''")
        if 'excluido_em'    not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN excluido_em    TEXT DEFAULT NULL")
        if 'assinado_por'   not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN assinado_por   INTEGER REFERENCES usuarios(id)")
        if 'assinado_em'    not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN assinado_em    TEXT DEFAULT NULL")
        if 'assinatura_cert' not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN assinatura_cert TEXT DEFAULT ''")
        if 'sigiloso'       not in cols: conn.execute("ALTER TABLE documentos ADD COLUMN sigiloso       INTEGER NOT NULL DEFAULT 0")
        cols_usu = [r[1] for r in conn.execute('PRAGMA table_info(usuarios)').fetchall()]
        if 'email' not in cols_usu: conn.execute("ALTER TABLE usuarios ADD COLUMN email TEXT DEFAULT ''")
        if 'cpf'   not in cols_usu: conn.execute("ALTER TABLE usuarios ADD COLUMN cpf   TEXT DEFAULT ''")
        if 'cargo' not in cols_usu: conn.execute("ALTER TABLE usuarios ADD COLUMN cargo TEXT DEFAULT ''")
        if 'matricula' not in cols_usu: conn.execute("ALTER TABLE usuarios ADD COLUMN matricula TEXT DEFAULT ''")
        if 'must_change_password' not in cols_usu: conn.execute("ALTER TABLE usuarios ADD COLUMN must_change_password INTEGER DEFAULT 0")
        if 'departamento' not in cols_usu: conn.execute(f"ALTER TABLE usuarios ADD COLUMN departamento TEXT NOT NULL DEFAULT '{DEPARTAMENTOS[0]}'")
        cols_lem = [r[1] for r in conn.execute('PRAGMA table_info(lembretes)').fetchall()]
        if 'notificado_em' not in cols_lem: conn.execute("ALTER TABLE lembretes ADD COLUMN notificado_em TEXT DEFAULT NULL")
        # Migração: alinha as chaves de SMTP com o padrão do SGCD (smtp_from -> smtp_from_name,
        # smtp_tls -> smtp_require_tls, notificacao_email -> smtp_to)
        old = {r['key']: r['value'] for r in conn.execute(
            "SELECT key,value FROM sys_settings WHERE key IN ('smtp_from','smtp_tls','notificacao_email')")}
        if old:
            if 'smtp_from' in old:
                conn.execute("INSERT OR REPLACE INTO sys_settings VALUES ('smtp_from_name',?)", (old['smtp_from'],))
                conn.execute("DELETE FROM sys_settings WHERE key='smtp_from'")
            if 'smtp_tls' in old:
                conn.execute("INSERT OR REPLACE INTO sys_settings VALUES ('smtp_require_tls',?)", (old['smtp_tls'],))
                conn.execute("DELETE FROM sys_settings WHERE key='smtp_tls'")
            if 'notificacao_email' in old:
                conn.execute("INSERT OR REPLACE INTO sys_settings VALUES ('smtp_to',?)", (old['notificacao_email'],))
                conn.execute("DELETE FROM sys_settings WHERE key='notificacao_email'")
        # Sessões são descartadas a cada início do servidor (evita sessões órfãs)
        conn.execute('DELETE FROM sessions')
        conn.commit()
        if conn.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0] == 0:
            conn.execute(
                'INSERT INTO usuarios (username,nome,senha_hash,admin,must_change_password) VALUES (?,?,?,1,1)',
                ('admin', 'Administrador', _hash_password('admin123'))
            )
            conn.commit()
            print('Usuário padrão criado: admin / admin123 — troque a senha no primeiro acesso.')

def _fts_match_query(text):
    """Converte texto livre em uma query FTS5 (AND de prefixos por palavra)."""
    tokens = re.findall(r'\w+', text, re.UNICODE)
    if not tokens: return None
    return ' '.join(f'"{t}"*' for t in tokens)

def _parse_multipart_all(body, boundary):
    """Extrai todos os campos de um multipart/form-data em um dict:
    {field_name: {'text': str, 'data': bytes, 'filename': str}} (portado do SGCD)."""
    parts = {}
    for part in body.split(b'--' + boundary):
        if b'Content-Disposition' not in part: continue
        sep = part.find(b'\r\n\r\n')
        if sep < 0: continue
        header  = part[:sep].decode('utf-8', errors='replace')
        content = part[sep+4:]
        if content.endswith(b'\r\n'): content = content[:-2]
        m_name = re.search(r'name="([^"]*)"', header)
        if not m_name: continue
        name = m_name.group(1)
        m_file = re.search(r'filename="([^"]*)"', header)
        if m_file:
            parts[name] = {'data': content, 'filename': m_file.group(1), 'text': None}
        else:
            parts[name] = {'data': content, 'filename': None, 'text': content.decode('utf-8', errors='replace').strip()}
    return parts

def _assinar_pdf_icp(pdf_bytes, cert_bytes, senha):
    """Assina um PDF com certificado ICP-Brasil A1 (.pfx), nível qualificado.
    Import tardio de pyHanko: o servidor sobe normalmente mesmo sem a lib
    instalada — só este módulo fica indisponível, com erro claro. Portado do SGCD.
    Retorna (pdf_assinado_bytes, subject_do_certificado)."""
    import tempfile, io
    from pyhanko.sign import signers
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    with tempfile.NamedTemporaryFile(suffix='.pfx', delete=False) as tf:
        tf.write(cert_bytes)
        pfx_path = tf.name
    try:
        signer = signers.SimpleSigner.load_pkcs12(pfx_path, passphrase=senha.encode('utf-8'))
        if signer is None:
            raise ValueError('Senha do certificado incorreta ou arquivo .pfx inválido/corrompido')
        cert_subject = str(signer.signing_cert.subject.human_friendly)
        writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))
        out = io.BytesIO()
        signers.sign_pdf(writer, signers.PdfSignatureMetadata(field_name='Signature1'), signer=signer, output=out)
        return out.getvalue(), cert_subject
    finally:
        os.remove(pfx_path)

# ── Segurança ─────────────────────────────────────────────────────────────────

def get_config():
    with get_db() as conn:
        return {r['key']: r['value'] for r in conn.execute('SELECT key,value FROM sys_settings').fetchall()}

_hash_password   = sgx_base.hash_password
_verify_password = sgx_base.verify_password

# ── Rate limit de login ─────────────────────────────────────────────────────
LOGIN_MAX_ATTEMPTS   = 5
LOGIN_LOCKOUT_WINDOW = 300   # 5 min — janela deslizante de tentativas falhas
_rate_limiter = sgx_base.LoginRateLimiter(LOGIN_MAX_ATTEMPTS, LOGIN_LOCKOUT_WINDOW)
_login_rate_limited    = _rate_limiter.is_locked
_record_login_failure  = _rate_limiter.record_failure
_clear_login_failures   = _rate_limiter.clear

# create_session/delete_session/renew_session/active_sessions delegam pro
# sgx_base (mecânica idêntica nos 4 sistemas). get_session() fica local: faz
# um SELECT de colunas explícito (não u.*) por segurança — nunca deve devolver
# a coluna de hash de senha junto com os dados da sessão — e as colunas
# selecionadas divergem por sistema (schema de usuarios não é idêntico).
def create_session(user_id):
    return sgx_base.create_session(get_db, user_id, SESSION_TTL)

def get_session(token):
    if not token:
        return None
    with get_db() as conn:
        row = conn.execute(
            '''SELECT s.token, s.user_id, s.expires,
                      u.nome, u.username, u.cpf, u.email, u.cargo, u.matricula, u.admin, u.ativo, u.departamento
               FROM sessions s JOIN usuarios u ON u.id=s.user_id
               WHERE s.token=? AND s.expires>? AND u.ativo=1''',
            (token, time.time())
        ).fetchone()
    return dict(row) if row else None

def delete_session(token):
    sgx_base.delete_session(get_db, token)

def renew_session(token):
    sgx_base.renew_session(get_db, token, SESSION_TTL)

def active_sessions():
    return sgx_base.active_sessions(get_db)

def _check_shutdown():
    """O servidor nunca encerra sozinho por contagem de sessões — só via Ctrl+C
    no terminal (ver bloco principal). Aqui só dispara um backup automático,
    uma única vez, depois que a última sessão ativa termina.

    ponytail: existia um modo "Pessoal" que fazia os._exit(0) nesta função
    quando a última sessão caía — a ideia era encerrar sozinho ao fechar a
    janela do navegador. Removido — se o encerramento automático por
    inatividade real for necessário de novo, a forma correta é um timeout bem
    mais longo (minutos, não segundos), não a contagem de sessões do ping."""
    global _backup_pos_sess
    if _had_session and active_sessions() == 0 and not _backup_pos_sess:
        _backup_pos_sess = True
        cfg = _get_backup_cfg()
        if cfg['enabled']:
            print('\nÚltima sessão encerrada. Executando backup automático...')
            _do_json_backup(cfg)
            _do_db_backup(cfg)

# ── Helpers de domínio ────────────────────────────────────────────────────────

def proximo_numero(conn, tipo, ano):
    row = conn.execute('SELECT ultimo FROM contadores WHERE tipo=? AND ano=?', (tipo, ano)).fetchone()
    return (row['ultimo'] + 1) if row else 1

def bump_contador(conn, tipo, ano, numero):
    conn.execute(
        'INSERT INTO contadores (tipo,ano,ultimo) VALUES (?,?,?) '
        'ON CONFLICT(tipo,ano) DO UPDATE SET ultimo=MAX(ultimo,excluded.ultimo)',
        (tipo, ano, numero)
    )

def audit(conn, uid, nome, acao, doc_id=None, detalhes=None):
    conn.execute(
        'INSERT INTO auditoria (usuario_id,usuario_nome,acao,documento_id,detalhes) VALUES (?,?,?,?,?)',
        (uid, nome, acao, doc_id, detalhes)
    )

def pode_ver_doc(doc, s):
    """Documentos sigilosos só são visíveis para o criador ou admin."""
    return not doc['sigiloso'] or s['admin'] or doc['criado_por'] == s['user_id']

def pode_editar_doc(doc, s):
    """Sigiloso: só criador ou admin. Não-sigiloso: mesmo departamento do criador ou admin."""
    if s['admin']:
        return True
    if doc['sigiloso']:
        return doc['criado_por'] == s['user_id']
    return doc['criado_por_departamento'] == s['departamento']

def _sync_tags(conn, did, tag_names):
    """Substitui as tags do documento pela lista informada (cria as que não existem)."""
    nomes = sorted({t.strip() for t in (tag_names or []) if t.strip()})
    conn.execute('DELETE FROM documento_tags WHERE documento_id=?', (did,))
    for nome in nomes:
        conn.execute('INSERT OR IGNORE INTO tags (nome) VALUES (?)', (nome,))
        tag_id = conn.execute('SELECT id FROM tags WHERE nome=? COLLATE NOCASE', (nome,)).fetchone()['id']
        conn.execute('INSERT OR IGNORE INTO documento_tags (documento_id,tag_id) VALUES (?,?)', (did, tag_id))

def _tags_map(conn, doc_ids):
    """Retorna {documento_id: [nomes de tag]} para os ids informados."""
    if not doc_ids: return {}
    qs = ','.join('?' * len(doc_ids))
    rows = conn.execute(
        f'''SELECT dt.documento_id, t.nome FROM documento_tags dt
            JOIN tags t ON dt.tag_id=t.id WHERE dt.documento_id IN ({qs})
            ORDER BY t.nome''', doc_ids
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r['documento_id'], []).append(r['nome'])
    return out

def _sig_cod_map(conn, doc_ids):
    """Retorna {documento_id: cod} do registro de assinatura mais recente de cada id."""
    if not doc_ids: return {}
    qs = ','.join('?' * len(doc_ids))
    rows = conn.execute(
        f'''SELECT documento_id, cod FROM signatures WHERE id IN (
                SELECT MAX(id) FROM signatures WHERE documento_id IN ({qs}) GROUP BY documento_id
            )''', doc_ids
    ).fetchall()
    return {r['documento_id']: r['cod'] for r in rows}

def _gerar_cod_assinatura(conn):
    """Código curto de verificação (ex: A1B2-C3D4), único na tabela signatures."""
    for _ in range(10):
        raw = secrets.token_hex(4).upper()
        cod = raw[:4] + '-' + raw[4:]
        if not conn.execute('SELECT 1 FROM signatures WHERE cod=?', (cod,)).fetchone():
            return cod
    raise RuntimeError('Não foi possível gerar código de verificação único')

# ── HTTP Handler ──────────────────────────────────────────────────────────────

class SGDPHandler(http.server.SimpleHTTPRequestHandler):

    def end_headers(self):
        # SGDP.html/JS mudam com frequência entre versões; sem isso o navegador
        # pode servir do cache sem revalidar com o servidor (heurística por Last-Modified).
        if self.command == 'GET' and urlparse(self.path).path.rstrip('/').endswith(('.html', '.js', '.css')):
            self.send_header('Cache-Control', 'no-cache, must-revalidate')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _safe_dispatch(self, inner):
        # handle_error (mais abaixo) nunca era chamado de verdade — é método de
        # socketserver.BaseServer, não do request handler, então exceções não
        # tratadas em qualquer do_GET/POST/PUT/DELETE só apareciam no console
        # (nada no log, cliente só via a conexão cair). Isso escondia bugs reais.
        try:
            inner()
        except Exception as e:
            _log.error('Erro não tratado em %s %s: %s', self.command, self.path, e)
            try:
                self._json(500, {'error': 'Erro interno no servidor.'})
            except Exception:
                pass  # resposta já pode ter começado a ser enviada

    def do_GET(self):
        self._safe_dispatch(self._do_GET)

    def _do_GET(self):
        parsed = urlparse(self.path)
        p  = parsed.path.rstrip('/')
        qs = parse_qs(parsed.query)

        if p == '/health':
            self._json(200, {'ok': True})
        elif p == '/api/auth/logout':
            # Aceita token via query string para suportar sendBeacon
            tok = qs.get('token', [None])[0] or self._token()
            delete_session(tok)
            self._json(200, {'ok': True})
            threading.Thread(target=_check_shutdown, daemon=True).start()
        elif p == '/api/config/public':
            cfg = get_config()
            self._json(200, {'orgao_nome': cfg.get('orgao_nome',''), 'municipio': cfg.get('municipio','')})
        elif p == '/api/public/org-info':
            try:
                with get_db() as conn:
                    rows = conn.execute(
                        "SELECT key,value FROM sys_settings WHERE key IN ('orgao_nome','municipio')"
                    ).fetchall()
                info = {r['key']: r['value'] for r in rows}
                if 'orgao_nome' in info: info['orgao'] = info.pop('orgao_nome')
                self._json(200, info)
            except Exception:
                self._json(200, {})
        elif p == '/api/public/last-backup':
            try:
                with get_db() as conn:
                    row = conn.execute("SELECT value FROM sys_settings WHERE key='auto_backup_last'").fetchone()
                self._json(200, {'ts': row['value'] if row else None})
            except Exception:
                self._json(200, {'ts': None})
        elif p.startswith('/verificar/'):
            self._serve_verificar(p[len('/verificar/'):].strip('/').upper())
        elif p.startswith('/api/'):
            s = self._auth()
            if s: self._route_get(p, qs, s)
        else:
            if p in ('', '/'):
                self.path = '/SGDP.html'
            super().do_GET()

    def do_POST(self):
        self._safe_dispatch(self._do_POST)

    def _do_POST(self):
        parsed = urlparse(self.path)
        p = parsed.path.rstrip('/')

        if p == '/api/auth/login':
            self._login(self._body())
            return

        # Logout via beacon (sem Authorization header — lê token do query string)
        if p == '/api/auth/logout':
            qs_tok = parse_qs(parsed.query).get('token', [None])[0]
            delete_session(qs_tok or self._token())
            self._json(200, {'ok': True})
            threading.Thread(target=_check_shutdown, daemon=True).start()
            return

        s = self._auth()
        if not s: return
        self._route_post(p, s)

    def do_PUT(self):
        self._safe_dispatch(self._do_PUT)

    def _do_PUT(self):
        p = urlparse(self.path).path.rstrip('/')
        s = self._auth()
        if not s: return
        self._route_put(p, self._body(), s)

    def do_DELETE(self):
        self._safe_dispatch(self._do_DELETE)

    def _do_DELETE(self):
        p = urlparse(self.path).path.rstrip('/')
        s = self._auth()
        if not s: return
        self._route_delete(p, s)

    # ── Roteamento ────────────────────────────────────────────────────────────

    def _route_get(self, p, qs, s):
        def qp(k, d=None): v = qs.get(k); return v[0] if v else d

        if p == '/api/auth/logout':
            tok = qs.get('token', [None])[0] or self._token()
            delete_session(tok)
            self._json(200, {'ok': True})
            threading.Thread(target=_check_shutdown, daemon=True).start()

        elif p == '/api/auth/ping':
            renew_session(self._token())
            self._json(200, {'ok': True})

        elif p == '/api/auth/me':
            self._json(200, {'id': s['user_id'], 'username': s['username'], 'nome': s['nome'],
                              'cpf': s.get('cpf'), 'email': s.get('email'),
                              'cargo': s.get('cargo'), 'matricula': s.get('matricula'), 'admin': bool(s['admin']),
                              'departamento': s.get('departamento')})

        elif p == '/api/departamentos':
            self._json(200, list(DEPARTAMENTOS))

        elif p == '/api/documentos':
            self._list_docs(qs, s)
        elif re.fullmatch(r'/api/documentos/\d+', p):
            self._get_doc(int(p.split('/')[-1]), s)
        elif re.fullmatch(r'/api/documentos/\d+/revisoes', p):
            self._list_revisoes(int(p.split('/')[3]))
        elif re.fullmatch(r'/api/documentos/\d+/vinculos', p):
            self._list_vinculos(int(p.split('/')[3]))
        elif re.fullmatch(r'/api/documentos/\d+/cadeia', p):
            self._cadeia_normativa(int(p.split('/')[3]))

        elif p == '/api/lixeira':
            self._list_lixeira(qs, s)

        elif p == '/api/lembretes':
            self._list_lembretes(qs, s)

        elif p == '/api/config/smtp':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            cfg = get_config()
            self._json(200, {k: cfg.get(k, '') for k in
                             ('smtp_host','smtp_port','smtp_user','smtp_secure','smtp_require_tls',
                              'smtp_ignore_ssl','smtp_from_name','smtp_to')})

        elif re.fullmatch(r'/api/arquivos/\d+', p):
            self._download_arquivo(int(p.split('/')[-1]), qs, s)

        elif p == '/api/contadores':
            self._get_contadores(qs)

        elif p == '/api/tags':
            self._list_tags()

        elif p == '/api/dashboard':
            self._dashboard(s)

        elif p == '/api/relatorio':
            self._relatorio(qs, s)
        elif p == '/api/relatorio/export.csv':
            self._relatorio_export_csv(qs, s)
        elif p == '/api/relatorio/produtividade':
            self._relatorio_produtividade(qs)
        elif p == '/api/relatorio/integridade':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._relatorio_integridade()
        elif p == '/api/relatorio/etiquetas':
            self._relatorio_etiquetas()

        elif p == '/api/usuarios':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            with get_db() as conn:
                rows = conn.execute('SELECT id,username,nome,cpf,email,cargo,matricula,admin,ativo,departamento,criado_em FROM usuarios ORDER BY nome').fetchall()
            self._json(200, [dict(r) for r in rows])

        elif p == '/api/diagnostico':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            with get_db() as conn:
                total_docs     = conn.execute('SELECT COUNT(*) FROM documentos').fetchone()[0]
                por_tipo       = [dict(r) for r in conn.execute(
                    'SELECT tipo, COUNT(*) n FROM documentos GROUP BY tipo').fetchall()]
                docs_sem_pdf   = conn.execute(
                    'SELECT COUNT(*) FROM documentos WHERE arquivo_id IS NULL').fetchone()[0]
                arquivos_db    = conn.execute('SELECT COUNT(*) FROM arquivos').fetchone()[0]
                total_usuarios = conn.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0]
                usuarios_ativos = conn.execute('SELECT COUNT(*) FROM usuarios WHERE ativo=1').fetchone()[0]
                total_audit    = conn.execute('SELECT COUNT(*) FROM auditoria').fetchone()[0]
                ultimo_backup  = conn.execute(
                    "SELECT value FROM sys_settings WHERE key='auto_backup_last'").fetchone()
                # contadores vs max real
                contadores_ok = True
                for tipo in TIPOS:
                    max_real = conn.execute(
                        'SELECT MAX(numero) FROM documentos WHERE tipo=?', (tipo,)).fetchone()[0] or 0
                    cont = conn.execute(
                        'SELECT ultimo FROM contadores WHERE tipo=?', (tipo,)).fetchone()
                    cont_val = cont['ultimo'] if cont else 0
                    if cont_val < max_real:
                        contadores_ok = False; break
                # arquivos no banco sem arquivo no disco
                arqs_banco = conn.execute('SELECT nome_disco FROM arquivos').fetchall()
            orfaos_banco = sum(1 for a in arqs_banco if not os.path.isfile(os.path.join(UPLOADS_DIR, a['nome_disco'])))
            # arquivos no disco sem registro no banco
            discos = set(os.listdir(UPLOADS_DIR)) if os.path.isdir(UPLOADS_DIR) else set()
            nomes_banco = {a['nome_disco'] for a in arqs_banco}
            orfaos_disco = len(discos - nomes_banco)
            db_size_kb = os.path.getsize(DB_PATH) // 1024 if os.path.isfile(DB_PATH) else 0
            self._json(200, {
                'total_docs': total_docs, 'por_tipo': por_tipo,
                'docs_sem_pdf': docs_sem_pdf,
                'arquivos_db': arquivos_db, 'arquivos_disco': len(discos),
                'orfaos_banco': orfaos_banco, 'orfaos_disco': orfaos_disco,
                'total_usuarios': total_usuarios, 'usuarios_ativos': usuarios_ativos,
                'contadores_ok': contadores_ok,
                'total_auditoria': total_audit,
                'ultimo_backup': ultimo_backup['value'] if ultimo_backup else None,
                'db_size_kb': db_size_kb,
            })

        elif p == '/api/auditoria':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            page  = int(qp('page', 1)); per = int(qp('per', 50))
            q     = (qp('q') or '').strip()
            acao  = qp('acao') or ''
            de    = qp('de')   or ''
            ate   = qp('ate')  or ''
            where, params = [], []
            if q:    where.append('(usuario_nome LIKE ? OR detalhes LIKE ?)'); params += [f'%{q}%', f'%{q}%']
            if acao: where.append('acao=?'); params.append(acao)
            if de:   where.append('em >= ?'); params.append(de)
            if ate:  where.append('em <= ?'); params.append(ate + 'T23:59:59')
            w = ('WHERE ' + ' AND '.join(where)) if where else ''
            with get_db() as conn:
                total = conn.execute(f'SELECT COUNT(*) FROM auditoria {w}', params).fetchone()[0]
                rows  = conn.execute(f'SELECT * FROM auditoria {w} ORDER BY id DESC LIMIT ? OFFSET ?',
                                     params + [per, (page-1)*per]).fetchall()
            self._json(200, {'total': total, 'page': page, 'per': per, 'items': [dict(r) for r in rows]})

        elif p == '/api/backup':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._export_backup()

        elif p == '/api/backups/db':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            cfg = _get_backup_cfg()
            bdir = cfg['path']
            files = sorted(
                (f for f in os.listdir(bdir) if f.startswith('DB_SGDP_BACKUP_') and f.endswith('.db')),
                reverse=True
            ) if os.path.isdir(bdir) else []
            def _parse_ts(f):
                d = f[15:25]; t = f[26:34].replace('-', ':')
                return f'{d}T{t}'
            items = [{'name': f, 'size': os.path.getsize(os.path.join(bdir, f)), 'ts': _parse_ts(f)} for f in files]
            with get_db() as conn:
                last_row = conn.execute("SELECT value FROM sys_settings WHERE key='auto_backup_last'").fetchone()
            self._json(200, {'items': items, 'path': bdir, 'cfg': cfg,
                             'last_backup': last_row['value'] if last_row else None})

        elif p.startswith('/api/backups/db/download'):
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            name = qs.get('name', [None])[0]
            if not name or not name.startswith('DB_SGDP_BACKUP_') or not name.endswith('.db') or '/' in name or '\\' in name:
                self._json(400, {'error': 'Nome inválido'}); return
            fp = os.path.join(_get_backup_cfg()['path'], name)
            if not os.path.exists(fp): self._json(404, {'error': 'Não encontrado'}); return
            with open(fp, 'rb') as f: data_bytes = f.read()
            self.send_response(200); self._cors()
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{name}"')
            self.send_header('Content-Length', str(len(data_bytes)))
            self.end_headers(); self.wfile.write(data_bytes)

        elif p == '/api/dialog/folder':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            global _watchdog_paused
            _watchdog_paused = True
            try:
                ps_cmd = (
                    'Add-Type -AssemblyName System.Windows.Forms;'
                    '$d=New-Object System.Windows.Forms.FolderBrowserDialog;'
                    '$d.Description="Selecione a pasta de backup do SGDP";'
                    '$d.ShowNewFolderButton=$true;'
                    'if($d.ShowDialog()-eq"OK"){Write-Output $d.SelectedPath}'
                )
                r = subprocess.run(['powershell', '-Sta', '-WindowStyle', 'Hidden', '-Command', ps_cmd],
                                   capture_output=True, text=True, timeout=120)
                self._json(200, {'path': r.stdout.strip() or None})
            except Exception as e:
                self._json(500, {'error': str(e)})
            finally:
                _watchdog_paused = False

        elif p == '/api/config':
            cfg = get_config()
            self._json(200, {'orgao_nome': cfg.get('orgao_nome',''), 'municipio': cfg.get('municipio',''),
                             'auto_backup_enabled': cfg.get('auto_backup_enabled','1'),
                             'auto_backup_keep': cfg.get('auto_backup_keep', str(BACKUP_KEEP)),
                             'backup_path': cfg.get('backup_path', BACKUP_DIR),
                             'aut_nome': cfg.get('aut_nome',''), 'aut_cargo': cfg.get('aut_cargo',''),
                             'diario_url': cfg.get('diario_url','')})

        elif p in ('/api/settings/brasao', '/api/settings/brasao/'):
            cfg = get_config()
            self._json(200, {'brasao_dataurl': cfg.get('brasao_dataurl', '')})

        else:
            self._json(404, {'error': 'Rota não encontrada'})

    def _route_post(self, p, s):
        if p == '/api/auth/logout':
            delete_session(self._token())
            with get_db() as conn:
                audit(conn, s['user_id'], s['nome'], 'logout')
                conn.commit()
            self._json(200, {'ok': True})
            threading.Thread(target=_check_shutdown, daemon=True).start()

        elif p == '/api/documentos':
            self._create_doc(self._body(), s)

        elif re.fullmatch(r'/api/documentos/\d+/arquivo', p):
            self._upload_arquivo(int(p.split('/')[3]), s)

        elif re.fullmatch(r'/api/documentos/\d+/assinar', p):
            self._assinar_doc(int(p.split('/')[3]), s)

        elif re.fullmatch(r'/api/documentos/\d+/email', p):
            self._enviar_email(int(p.split('/')[3]), self._body(), s)

        elif re.fullmatch(r'/api/documentos/\d+/vinculos', p):
            self._create_vinculo(int(p.split('/')[3]), self._body(), s)

        elif re.fullmatch(r'/api/lixeira/\d+/restaurar', p):
            self._restaurar_doc(int(p.split('/')[3]), s)

        elif p == '/api/lembretes':
            self._create_lembrete(self._body(), s)

        elif p == '/api/import/csv':
            self._import_csv(self._body(), s)

        elif p == '/api/usuarios':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._create_usuario(self._body(), s)

        elif p == '/api/config/smtp/test':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._test_smtp(s)

        elif p == '/api/backup/restore':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._import_backup(s)

        elif p == '/api/backup/sync-preview':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._sync_preview()

        elif p == '/api/backup/sync-apply':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._sync_apply(s)

        elif p == '/api/backups/db/now':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            name = _do_db_backup()
            _rotate_backups()
            self._json(200, {'ok': bool(name), 'name': name})

        elif p == '/api/backups/db/restore':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._restore_db_backup(s)

        elif p == '/api/factory-reset':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._factory_reset(s)

        else:
            self._json(404, {'error': 'Rota não encontrada'})

    def _route_put(self, p, body, s):
        if re.fullmatch(r'/api/documentos/\d+', p):
            self._update_doc(int(p.split('/')[-1]), body, s)
        elif re.fullmatch(r'/api/usuarios/\d+', p):
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._update_usuario(int(p.split('/')[-1]), body, s)
        elif p == '/api/auth/senha':
            self._change_senha(body, s)
        elif p == '/api/config':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._update_config(body, s)
        elif p == '/api/config/smtp':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._update_smtp(body, s)
        elif p in ('/api/settings/brasao', '/api/settings/brasao/'):
            self._update_brasao(body, s)
        elif re.fullmatch(r'/api/lembretes/\d+', p):
            self._update_lembrete(int(p.split('/')[-1]), body, s)
        else:
            self._json(404, {'error': 'Rota não encontrada'})

    def _route_delete(self, p, s):
        if re.fullmatch(r'/api/documentos/\d+', p):
            self._delete_doc(int(p.split('/')[-1]), s)
        elif re.fullmatch(r'/api/documentos/\d+/arquivo', p):
            self._remove_arquivo(int(p.split('/')[3]), s)
        elif re.fullmatch(r'/api/usuarios/\d+', p):
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._delete_usuario(int(p.split('/')[-1]), s)
        elif re.fullmatch(r'/api/lixeira/\d+', p):
            self._purgar_doc_endpoint(int(p.split('/')[-1]), s)
        elif re.fullmatch(r'/api/lembretes/\d+', p):
            self._delete_lembrete(int(p.split('/')[-1]), s)
        elif re.fullmatch(r'/api/vinculos/\d+', p):
            self._delete_vinculo(int(p.split('/')[-1]), s)
        else:
            self._json(404, {'error': 'Rota não encontrada'})

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _login(self, body):
        data = json.loads(body) if body else {}
        username = data.get('username', '').strip()
        password = data.get('password', '')
        if not username or not password:
            self._json(400, {'error': 'Usuário e senha são obrigatórios'}); return
        if _login_rate_limited(username):
            self._json(429, {'error': 'Muitas tentativas de login. Aguarde alguns minutos e tente novamente.'}); return
        with get_db() as conn:
            row = conn.execute('SELECT * FROM usuarios WHERE username=? COLLATE NOCASE AND ativo=1', (username,)).fetchone()
        if not row or not _verify_password(password, row['senha_hash']):
            _record_login_failure(username)
            self._json(401, {'error': 'Usuário ou senha incorretos'}); return
        _clear_login_failures(username)
        global _had_session, _backup_pos_sess
        _had_session = True
        _backup_pos_sess = False  # nova sessão — permite backup ao próximo logout
        token = create_session(row['id'])
        with get_db() as conn:
            audit(conn, row['id'], row['nome'], 'login', detalhes=f"Acesso de {self.client_address[0]}")
            conn.commit()
        self._json(200, {
            'token': token,
            'user': {'id': row['id'], 'username': row['username'], 'nome': row['nome'],
                      'cpf': row['cpf'], 'email': row['email'],
                      'cargo': row['cargo'], 'matricula': row['matricula'], 'admin': bool(row['admin']),
                      'departamento': row['departamento'],
                      'mustChangePassword': bool(row['must_change_password'])}
        })

    # ── Documentos ────────────────────────────────────────────────────────────

    def _list_docs(self, qs, s):
        def qp(k, d=None): v = qs.get(k); return v[0] if v else d
        tipo   = qp('tipo')
        search = (qp('q') or '').strip()
        ano    = qp('ano')
        page   = int(qp('page', 1))
        per    = int(qp('per', 50))

        where, params = ['d.excluido_em IS NULL'], []
        if tipo:   where.append('d.tipo=?');   params.append(tipo)
        if search:
            fts_q = _fts_match_query(search) if FTS_AVAILABLE else None
            if fts_q:
                where.append('''(d.id IN (SELECT rowid FROM documentos_fts WHERE documentos_fts MATCH ?)
                                  OR d.partes LIKE ? OR CAST(d.numero AS TEXT) LIKE ?)''')
                params += [fts_q, f'%{search}%', f'%{search}%']
            else:
                where.append('(d.ementa LIKE ? OR d.partes LIKE ? OR CAST(d.numero AS TEXT) LIKE ?)')
                params += [f'%{search}%'] * 3
        if ano:    where.append('d.ano=?');    params.append(int(ano))
        if qp('sem_pdf'): where.append('d.arquivo_id IS NULL')
        tag = qp('tag')
        if tag:
            where.append('d.id IN (SELECT dt.documento_id FROM documento_tags dt JOIN tags t ON dt.tag_id=t.id WHERE t.nome=? COLLATE NOCASE)')
            params.append(tag)
        if not s['admin']:
            where.append('(d.sigiloso=0 OR d.criado_por=?)'); params.append(s['user_id'])
        w = 'WHERE ' + ' AND '.join(where)

        with get_db() as conn:
            total = conn.execute(f'SELECT COUNT(*) FROM documentos d {w}', params).fetchone()[0]
            rows  = conn.execute(
                f'''SELECT d.*, u1.nome criado_por_nome, u1.departamento criado_por_departamento, u2.nome atualizado_por_nome,
                           a.nome_original arquivo_nome, a.tamanho arquivo_tamanho
                    FROM documentos d
                    LEFT JOIN usuarios u1 ON d.criado_por=u1.id
                    LEFT JOIN usuarios u2 ON d.atualizado_por=u2.id
                    LEFT JOIN arquivos a ON d.arquivo_id=a.id
                    {w} ORDER BY d.ano DESC, d.numero DESC LIMIT ? OFFSET ?''',
                params + [per, (page-1)*per]
            ).fetchall()
            ids = [r['id'] for r in rows]
            tags_map = _tags_map(conn, ids)
            cod_map = _sig_cod_map(conn, ids)
        items = []
        for r in rows:
            item = dict(r)
            item['tags'] = tags_map.get(r['id'], [])
            item['cod_verificacao'] = cod_map.get(r['id'])
            items.append(item)
        self._json(200, {'total': total, 'page': page, 'per': per, 'items': items})

    def _get_doc(self, did, s):
        with get_db() as conn:
            row = conn.execute(
                '''SELECT d.*, u1.nome criado_por_nome, u1.departamento criado_por_departamento, u2.nome atualizado_por_nome, u3.nome assinado_por_nome,
                          a.nome_original arquivo_nome, a.tamanho arquivo_tamanho
                   FROM documentos d
                   LEFT JOIN usuarios u1 ON d.criado_por=u1.id
                   LEFT JOIN usuarios u2 ON d.atualizado_por=u2.id
                   LEFT JOIN usuarios u3 ON d.assinado_por=u3.id
                   LEFT JOIN arquivos a ON d.arquivo_id=a.id
                   WHERE d.id=?''', (did,)
            ).fetchone()
            if not row: self._json(404, {'error': 'Não encontrado'}); return
            if not pode_ver_doc(row, s): self._json(404, {'error': 'Não encontrado'}); return
            tags = _tags_map(conn, [did]).get(did, [])
            sig = conn.execute(
                'SELECT cod FROM signatures WHERE documento_id=? ORDER BY id DESC LIMIT 1', (did,)
            ).fetchone()
        item = dict(row); item['tags'] = tags
        item['cod_verificacao'] = sig['cod'] if sig else None
        self._json(200, item)

    def _list_tags(self):
        with get_db() as conn:
            rows = conn.execute('SELECT nome FROM tags ORDER BY nome').fetchall()
        self._json(200, {'items': [r['nome'] for r in rows]})

    def _create_doc(self, body, s):
        data = json.loads(body) if body else {}
        tipo   = data.get('tipo', '').lower()
        ementa = (data.get('ementa') or '').strip()
        data_d = (data.get('data') or '').strip()
        if tipo not in TIPOS:   self._json(400, {'error': 'Tipo inválido'}); return
        if not ementa:          self._json(400, {'error': 'Ementa obrigatória'}); return
        if not data_d:          self._json(400, {'error': 'Data obrigatória'}); return
        ano = int(data.get('ano') or data_d[:4])
        with get_db() as conn:
            numero = int(data['numero']) if data.get('numero') not in (None, '') else proximo_numero(conn, tipo, ano)
            try:
                cur = conn.execute(
                    'INSERT INTO documentos (tipo,numero,ano,data,ementa,partes,observacoes,assunto,processo_pa,processo_tipo,processo_ref,ato_tipo,cargo,sigiloso,criado_por,atualizado_por)'
                    ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (tipo, numero, ano, data_d, ementa,
                     data.get('partes') or '', data.get('observacoes') or '',
                     data.get('assunto') or 'Outros',
                     data.get('processo_pa') or '', data.get('processo_tipo') or '', data.get('processo_ref') or '',
                     data.get('ato_tipo') or '', data.get('cargo') or '',
                     int(bool(data.get('sigiloso'))),
                     s['user_id'], s['user_id'])
                )
                # captura o rowid ANTES de bump_contador — que pode fazer seu próprio
                # INSERT em contadores na primeira vez que o tipo/ano é usado, o que
                # sobrescreveria um last_insert_rowid() consultado depois dele
                did = cur.lastrowid
                bump_contador(conn, tipo, ano, numero)
                if 'tags' in data: _sync_tags(conn, did, data['tags'])
                audit(conn, s['user_id'], s['nome'], 'criar', did, f"{tipo} nº {numero}/{ano}")
                conn.commit()
            except sqlite3.IntegrityError:
                self._json(409, {'error': f'Já existe {tipo} nº {numero}/{ano}'}); return
            row = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            tags = _tags_map(conn, [did]).get(did, [])
        item = dict(row); item['tags'] = tags
        self._json(201, item)

    def _update_doc(self, did, body, s):
        data = json.loads(body) if body else {}
        with get_db() as conn:
            row = conn.execute(
                '''SELECT d.*, u.departamento criado_por_departamento
                   FROM documentos d LEFT JOIN usuarios u ON d.criado_por=u.id
                   WHERE d.id=?''', (did,)
            ).fetchone()
            if not row: self._json(404, {'error': 'Não encontrado'}); return
            if not pode_editar_doc(row, s): self._json(403, {'error': 'Sem permissão para editar este documento'}); return
            fields = {'atualizado_por': s['user_id'], 'atualizado_em': time.strftime('%Y-%m-%dT%H:%M:%S')}
            for f in ('ementa', 'partes', 'observacoes', 'data', 'assunto', 'processo_pa', 'processo_tipo', 'processo_ref', 'ato_tipo', 'cargo'):
                if f in data: fields[f] = data[f]
            # sigiloso é sensível: mesmo quem só tem permissão de edição por
            # departamento não pode alterar a confidencialidade de um documento
            # que não criou — só o criador ou admin.
            if 'sigiloso' in data and (s['admin'] or row['criado_por'] == s['user_id']):
                fields['sigiloso'] = int(bool(data['sigiloso']))
            if 'numero' in data: fields['numero'] = int(data['numero'])
            if 'ano'    in data: fields['ano']    = int(data['ano'])
            try:
                snapshot = dict(row); snapshot.pop('criado_por_departamento', None)
                conn.execute(
                    'INSERT INTO documento_revisoes (documento_id, dados_json, editado_por) VALUES (?,?,?)',
                    (did, json.dumps(snapshot), s['user_id'])
                )
                conn.execute(f"UPDATE documentos SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                             list(fields.values()) + [did])
                if 'numero' in fields:
                    bump_contador(conn, row['tipo'], fields.get('ano', row['ano']), fields['numero'])
                if 'tags' in data: _sync_tags(conn, did, data['tags'])
                audit(conn, s['user_id'], s['nome'], 'editar', did)
                conn.commit()
            except sqlite3.IntegrityError:
                self._json(409, {'error': 'Número/ano já existe para este tipo'}); return
            updated = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            tags = _tags_map(conn, [did]).get(did, [])
        item = dict(updated); item['tags'] = tags
        self._json(200, item)

    def _list_revisoes(self, did):
        with get_db() as conn:
            rows = conn.execute(
                '''SELECT r.*, u.nome editado_por_nome FROM documento_revisoes r
                   LEFT JOIN usuarios u ON r.editado_por=u.id
                   WHERE r.documento_id=? ORDER BY r.editado_em DESC''', (did,)
            ).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            item['dados'] = json.loads(item.pop('dados_json'))
            items.append(item)
        self._json(200, {'items': items})

    def _list_vinculos(self, did):
        with get_db() as conn:
            diretos = conn.execute(
                '''SELECT v.id, v.tipo, d.id doc_id, d.tipo doc_tipo, d.numero doc_numero, d.ano doc_ano, d.ementa doc_ementa
                   FROM documento_vinculos v JOIN documentos d ON v.destino_id=d.id
                   WHERE v.origem_id=?''', (did,)).fetchall()
            inversos = conn.execute(
                '''SELECT v.id, v.tipo, d.id doc_id, d.tipo doc_tipo, d.numero doc_numero, d.ano doc_ano, d.ementa doc_ementa
                   FROM documento_vinculos v JOIN documentos d ON v.origem_id=d.id
                   WHERE v.destino_id=?''', (did,)).fetchall()
        items = [
            {**dict(r), 'label': TIPOS_VINCULO[r['tipo']][0], 'direcao': 'direto'} for r in diretos
        ] + [
            {**dict(r), 'label': TIPOS_VINCULO[r['tipo']][1], 'direcao': 'inverso'} for r in inversos
        ]
        self._json(200, {'items': items})

    def _cadeia_normativa(self, did, max_prof=6):
        """Percorre a cadeia de vínculos (nos dois sentidos) a partir de um documento,
        em largura, até max_prof níveis — protegido contra ciclos por visitados."""
        with get_db() as conn:
            raiz = conn.execute(
                'SELECT id,tipo,numero,ano,ementa FROM documentos WHERE id=?', (did,)).fetchone()
            if not raiz: self._json(404, {'error': 'Documento não encontrado'}); return
            docs_info = {did: dict(raiz)}
            arestas = []
            arestas_vistas = set()
            visitados = {did}
            fila = [(did, 0)]
            while fila:
                atual_id, prof = fila.pop(0)
                if prof >= max_prof: continue
                diretos = conn.execute(
                    '''SELECT v.tipo, d.id doc_id, d.tipo doc_tipo, d.numero doc_numero, d.ano doc_ano, d.ementa doc_ementa
                       FROM documento_vinculos v JOIN documentos d ON v.destino_id=d.id
                       WHERE v.origem_id=?''', (atual_id,)).fetchall()
                inversos = conn.execute(
                    '''SELECT v.tipo, d.id doc_id, d.tipo doc_tipo, d.numero doc_numero, d.ano doc_ano, d.ementa doc_ementa
                       FROM documento_vinculos v JOIN documentos d ON v.origem_id=d.id
                       WHERE v.destino_id=?''', (atual_id,)).fetchall()
                for r in diretos:
                    chave = (atual_id, r['doc_id'], r['tipo'])
                    docs_info[r['doc_id']] = {'id': r['doc_id'], 'tipo': r['doc_tipo'], 'numero': r['doc_numero'],
                                               'ano': r['doc_ano'], 'ementa': r['doc_ementa']}
                    if chave not in arestas_vistas:
                        arestas_vistas.add(chave)
                        arestas.append({'de': atual_id, 'para': r['doc_id'], 'tipo': r['tipo'],
                                         'label': TIPOS_VINCULO[r['tipo']][0]})
                    if r['doc_id'] not in visitados:
                        visitados.add(r['doc_id']); fila.append((r['doc_id'], prof + 1))
                for r in inversos:
                    chave = (r['doc_id'], atual_id, r['tipo'])
                    docs_info[r['doc_id']] = {'id': r['doc_id'], 'tipo': r['doc_tipo'], 'numero': r['doc_numero'],
                                               'ano': r['doc_ano'], 'ementa': r['doc_ementa']}
                    if chave not in arestas_vistas:
                        arestas_vistas.add(chave)
                        arestas.append({'de': r['doc_id'], 'para': atual_id, 'tipo': r['tipo'],
                                         'label': TIPOS_VINCULO[r['tipo']][0]})
                    if r['doc_id'] not in visitados:
                        visitados.add(r['doc_id']); fila.append((r['doc_id'], prof + 1))
        self._json(200, {'raiz': dict(raiz), 'arestas': arestas, 'docs': list(docs_info.values())})

    def _create_vinculo(self, did, body, s):
        data = json.loads(body) if body else {}
        tipo = data.get('tipo')
        destino_id = data.get('destino_id')
        if tipo not in TIPOS_VINCULO: self._json(400, {'error': 'Tipo de vínculo inválido'}); return
        if not destino_id or int(destino_id) == did: self._json(400, {'error': 'Documento de destino inválido'}); return
        with get_db() as conn:
            destino = conn.execute('SELECT id FROM documentos WHERE id=?', (destino_id,)).fetchone()
            if not destino: self._json(404, {'error': 'Documento de destino não encontrado'}); return
            try:
                conn.execute('INSERT INTO documento_vinculos (origem_id,destino_id,tipo,criado_por) VALUES (?,?,?,?)',
                             (did, destino_id, tipo, s['user_id']))
                audit(conn, s['user_id'], s['nome'], 'criar_vinculo', did, f'{tipo} -> #{destino_id}')
                conn.commit()
            except sqlite3.IntegrityError:
                self._json(409, {'error': 'Esse vínculo já existe'}); return
        self._list_vinculos(did)

    def _delete_vinculo(self, vid, s):
        with get_db() as conn:
            row = conn.execute('SELECT * FROM documento_vinculos WHERE id=?', (vid,)).fetchone()
            if not row: self._json(404, {'error': 'Não encontrado'}); return
            conn.execute('DELETE FROM documento_vinculos WHERE id=?', (vid,))
            audit(conn, s['user_id'], s['nome'], 'excluir_vinculo', row['origem_id'])
            conn.commit()
        self._json(200, {'ok': True})

    def _delete_doc(self, did, s):
        with get_db() as conn:
            row = conn.execute(
                '''SELECT d.*, u.departamento criado_por_departamento
                   FROM documentos d LEFT JOIN usuarios u ON d.criado_por=u.id
                   WHERE d.id=?''', (did,)
            ).fetchone()
            if not row: self._json(404, {'error': 'Não encontrado'}); return
            if not pode_editar_doc(row, s): self._json(403, {'error': 'Sem permissão para excluir este documento'}); return
            audit(conn, s['user_id'], s['nome'], 'excluir', did, f"{row['tipo']} nº {row['numero']}/{row['ano']} (enviado à lixeira)")
            conn.execute("UPDATE documentos SET excluido_em=? WHERE id=?",
                         (time.strftime('%Y-%m-%dT%H:%M:%S'), did))
            conn.commit()
        self._json(200, {'ok': True})

    def _list_lixeira(self, qs, s):
        # purga automática após 30 dias na lixeira
        limite = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() - 30*86400))
        with get_db() as conn:
            expirados = conn.execute(
                'SELECT id, arquivo_id FROM documentos WHERE excluido_em IS NOT NULL AND excluido_em < ?',
                (limite,)).fetchall()
            for row in expirados:
                self._purgar_doc(conn, row['id'], row['arquivo_id'])
            conn.commit()
            rows = conn.execute(
                '''SELECT d.*, u.nome criado_por_nome FROM documentos d
                   LEFT JOIN usuarios u ON d.criado_por=u.id
                   WHERE d.excluido_em IS NOT NULL ORDER BY d.excluido_em DESC'''
            ).fetchall()
        self._json(200, {'items': [dict(r) for r in rows]})

    def _purgar_doc(self, conn, did, arquivo_id):
        if arquivo_id:
            arq = conn.execute('SELECT * FROM arquivos WHERE id=?', (arquivo_id,)).fetchone()
            if arq:
                p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if os.path.isfile(p): os.remove(p)
                conn.execute('DELETE FROM arquivos WHERE id=?', (arquivo_id,))
        conn.execute('DELETE FROM documentos WHERE id=?', (did,))

    def _restaurar_doc(self, did, s):
        with get_db() as conn:
            row = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            if not row or not row['excluido_em']: self._json(404, {'error': 'Não encontrado na lixeira'}); return
            conn.execute('UPDATE documentos SET excluido_em=NULL WHERE id=?', (did,))
            audit(conn, s['user_id'], s['nome'], 'restaurar', did, f"{row['tipo']} nº {row['numero']}/{row['ano']}")
            conn.commit()
        self._json(200, {'ok': True})

    def _purgar_doc_endpoint(self, did, s):
        with get_db() as conn:
            row = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            if not row or not row['excluido_em']: self._json(404, {'error': 'Não encontrado na lixeira'}); return
            audit(conn, s['user_id'], s['nome'], 'excluir_permanente', did, f"{row['tipo']} nº {row['numero']}/{row['ano']}")
            self._purgar_doc(conn, did, row['arquivo_id'])
            conn.commit()
        self._json(200, {'ok': True})

    # ── Relatório ─────────────────────────────────────────────────────────────

    def _relatorio(self, qs, s):
        def qp(k, d=None): v = qs.get(k); return v[0] if v else d
        de  = qp('de',  '1900-01-01')
        ate = qp('ate', '2999-12-31')
        vis = '' if s['admin'] else 'AND (sigiloso=0 OR criado_por=?)'
        vp  = [] if s['admin'] else [s['user_id']]
        with get_db() as conn:
            total = conn.execute(
                f'SELECT COUNT(*) FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL {vis}', (de, ate, *vp)).fetchone()[0]
            por_tipo = [dict(r) for r in conn.execute(
                f'SELECT tipo, COUNT(*) n FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL {vis} GROUP BY tipo ORDER BY n DESC',
                (de, ate, *vp)).fetchall()]
            por_assunto = [dict(r) for r in conn.execute(
                f'SELECT assunto, COUNT(*) n FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL {vis} GROUP BY assunto ORDER BY n DESC',
                (de, ate, *vp)).fetchall()]
            por_mes = [dict(r) for r in conn.execute(
                f"SELECT strftime('%Y-%m', data) mes, COUNT(*) n FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL {vis} GROUP BY mes ORDER BY mes",
                (de, ate, *vp)).fetchall()]
            docs = [dict(r) for r in conn.execute(
                f'SELECT id,tipo,numero,ano,data,ementa,assunto FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL {vis} ORDER BY data DESC, id DESC LIMIT 200',
                (de, ate, *vp)).fetchall()]
        self._json(200, {'total': total, 'por_tipo': por_tipo, 'por_assunto': por_assunto, 'por_mes': por_mes, 'documentos': docs})

    def _relatorio_export_csv(self, qs, s):
        import csv, io
        def qp(k, d=None): v = qs.get(k); return v[0] if v else d
        de  = qp('de',  '1900-01-01')
        ate = qp('ate', '2999-12-31')
        vis = '' if s['admin'] else 'AND (sigiloso=0 OR criado_por=?)'
        vp  = [] if s['admin'] else [s['user_id']]
        with get_db() as conn:
            docs = conn.execute(
                f'''SELECT tipo,numero,ano,data,ementa,partes,observacoes,assunto FROM documentos
                   WHERE data BETWEEN ? AND ? AND excluido_em IS NULL {vis} ORDER BY data DESC, id DESC''',
                (de, ate, *vp)).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['Tipo', 'Número', 'Ano', 'Data', 'Ementa', 'Partes', 'Observações', 'Assunto'])
        for d in docs:
            w.writerow([TIPOS_LABELS_CSV.get(d['tipo'], d['tipo']), d['numero'], d['ano'], d['data'],
                        d['ementa'], d['partes'], d['observacoes'], d['assunto']])
        payload = ('﻿' + buf.getvalue()).encode('utf-8')  # BOM: acentos corretos ao abrir no Excel
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="relatorio_sgdp_{de}_a_{ate}.csv"')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _relatorio_produtividade(self, qs):
        def qp(k, d=None): v = qs.get(k); return v[0] if v else d
        de  = qp('de',  '1900-01-01')
        ate = qp('ate', '2999-12-31') + 'T23:59:59'
        with get_db() as conn:
            rows = conn.execute(
                '''SELECT usuario_nome, acao, COUNT(*) n FROM auditoria
                   WHERE em BETWEEN ? AND ? AND acao IN ('criar','editar','upload','assinar_icp')
                   AND usuario_nome IS NOT NULL GROUP BY usuario_nome, acao''',
                (de, ate)).fetchall()
        por_usuario = {}
        for r in rows:
            u = por_usuario.setdefault(r['usuario_nome'], {'criar': 0, 'editar': 0, 'upload': 0, 'assinar_icp': 0})
            u[r['acao']] = r['n']
        items = [{'usuario_nome': nome, **contagens} for nome, contagens in sorted(por_usuario.items())]
        self._json(200, {'items': items})

    def _relatorio_integridade(self):
        def _dir_size(path):
            total = 0
            if os.path.isdir(path):
                for f in os.listdir(path):
                    fp = os.path.join(path, f)
                    if os.path.isfile(fp): total += os.path.getsize(fp)
            return total

        cfg = _get_backup_cfg()
        bdir = cfg['path']
        backups_db = sorted(
            (f for f in os.listdir(bdir) if f.startswith('DB_SGDP_BACKUP_') and f.endswith('.db')),
            reverse=True
        ) if os.path.isdir(bdir) else []
        backups_json = sorted(
            (f for f in os.listdir(bdir) if f.startswith('SIS_SGDP_BACKUP_') and f.endswith('.json')),
            reverse=True
        ) if os.path.isdir(bdir) else []

        with get_db() as conn:
            contagens = {
                'documentos': conn.execute('SELECT COUNT(*) FROM documentos WHERE excluido_em IS NULL').fetchone()[0],
                'arquivos': conn.execute('SELECT COUNT(*) FROM arquivos').fetchone()[0],
                'usuarios': conn.execute('SELECT COUNT(*) FROM usuarios WHERE ativo=1').fetchone()[0],
                'tags': conn.execute('SELECT COUNT(*) FROM tags').fetchone()[0],
                'vinculos': conn.execute('SELECT COUNT(*) FROM documento_vinculos').fetchone()[0],
                'assinaturas': conn.execute('SELECT COUNT(*) FROM signatures').fetchone()[0],
                'lembretes_pendentes': conn.execute('SELECT COUNT(*) FROM lembretes WHERE concluido=0').fetchone()[0],
                'na_lixeira': conn.execute('SELECT COUNT(*) FROM documentos WHERE excluido_em IS NOT NULL').fetchone()[0],
            }
            eventos = [dict(r) for r in conn.execute(
                '''SELECT * FROM auditoria WHERE acao IN
                   ('sincronizar_backup','restaurar_backup','restaurar_db','factory_reset')
                   ORDER BY id DESC LIMIT 15''').fetchall()]
            last_row = conn.execute("SELECT value FROM sys_settings WHERE key='auto_backup_last'").fetchone()

        self._json(200, {
            'auto_backup_enabled': cfg['enabled'], 'auto_backup_keep': cfg['keep'], 'backup_path': bdir,
            'last_backup': last_row['value'] if last_row else None,
            'db_size_bytes': os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0,
            'uploads_size_bytes': _dir_size(UPLOADS_DIR),
            'uploads_count': len([f for f in os.listdir(UPLOADS_DIR)]) if os.path.isdir(UPLOADS_DIR) else 0,
            'backups_db_count': len(backups_db), 'backups_json_count': len(backups_json),
            'backups_db_size_bytes': sum(os.path.getsize(os.path.join(bdir, f)) for f in backups_db),
            'contagens': contagens, 'eventos_recentes': eventos,
        })

    def _relatorio_etiquetas(self):
        with get_db() as conn:
            tags = conn.execute('SELECT id, nome FROM tags ORDER BY nome').fetchall()
            items = []
            for t in tags:
                docs = conn.execute(
                    '''SELECT d.id,d.tipo,d.numero,d.ano,d.ementa FROM documento_tags dt
                       JOIN documentos d ON dt.documento_id=d.id
                       WHERE dt.tag_id=? AND d.excluido_em IS NULL ORDER BY d.ano DESC, d.numero DESC''',
                    (t['id'],)).fetchall()
                items.append({'nome': t['nome'], 'total': len(docs), 'documentos': [dict(d) for d in docs]})
            sem_tag = conn.execute(
                '''SELECT COUNT(*) FROM documentos d WHERE d.excluido_em IS NULL
                   AND d.id NOT IN (SELECT documento_id FROM documento_tags)'''
            ).fetchone()[0]
        items.sort(key=lambda x: -x['total'])
        self._json(200, {'items': items, 'sem_etiqueta': sem_tag})

    # ── Agenda / Lembretes ────────────────────────────────────────────────────

    def _list_lembretes(self, qs, s):
        so_pendentes = qs.get('pendentes', [None])[0]
        doc_id = qs.get('documento_id', [None])[0]
        where, params = [], []
        if so_pendentes: where.append('l.concluido=0')
        if doc_id: where.append('l.documento_id=?'); params.append(int(doc_id))
        w = ('WHERE ' + ' AND '.join(where)) if where else ''
        with get_db() as conn:
            rows = conn.execute(
                f'''SELECT l.*, d.tipo doc_tipo, d.numero doc_numero, d.ano doc_ano
                    FROM lembretes l LEFT JOIN documentos d ON l.documento_id=d.id
                    {w} ORDER BY l.data_prazo ASC''', params
            ).fetchall()
        self._json(200, {'items': [dict(r) for r in rows]})

    def _create_lembrete(self, body, s):
        data = json.loads(body) if body else {}
        titulo = (data.get('titulo') or '').strip()
        prazo  = (data.get('data_prazo') or '').strip()
        if not titulo or not prazo: self._json(400, {'error': 'Título e prazo são obrigatórios'}); return
        with get_db() as conn:
            conn.execute(
                'INSERT INTO lembretes (titulo,data_prazo,documento_id,criado_por) VALUES (?,?,?,?)',
                (titulo, prazo, data.get('documento_id') or None, s['user_id'])
            )
            lid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            audit(conn, s['user_id'], s['nome'], 'criar_lembrete', detalhes=titulo)
            conn.commit()
            row = conn.execute('SELECT * FROM lembretes WHERE id=?', (lid,)).fetchone()
        self._json(201, dict(row))

    def _update_lembrete(self, lid, body, s):
        data = json.loads(body) if body else {}
        with get_db() as conn:
            row = conn.execute('SELECT * FROM lembretes WHERE id=?', (lid,)).fetchone()
            if not row: self._json(404, {'error': 'Não encontrado'}); return
            fields = {}
            for f in ('titulo', 'data_prazo', 'concluido', 'documento_id'):
                if f in data: fields[f] = data[f]
            if fields:
                conn.execute(f"UPDATE lembretes SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                             list(fields.values()) + [lid])
                conn.commit()
            updated = conn.execute('SELECT * FROM lembretes WHERE id=?', (lid,)).fetchone()
        self._json(200, dict(updated))

    def _delete_lembrete(self, lid, s):
        with get_db() as conn:
            conn.execute('DELETE FROM lembretes WHERE id=?', (lid,))
            conn.commit()
        self._json(200, {'ok': True})

    # ── E-mail ────────────────────────────────────────────────────────────────

    def _update_brasao(self, body, s):
        data = json.loads(body) if body else {}
        dataurl = data.get('brasao_dataurl', '')
        with get_db() as conn:
            if dataurl:
                conn.execute('INSERT OR REPLACE INTO sys_settings (key,value) VALUES (?,?)', ('brasao_dataurl', dataurl))
            else:
                conn.execute("DELETE FROM sys_settings WHERE key='brasao_dataurl'")
            audit(conn, s['user_id'], s['nome'], 'alterar_brasao', detalhes='removido' if not dataurl else f'{len(dataurl)} bytes')
            conn.commit()
        self._json(200, {'ok': True})

    def _update_smtp(self, body, s):
        data = json.loads(body) if body else {}
        allowed = ('smtp_host','smtp_port','smtp_user','smtp_pass','smtp_secure','smtp_require_tls',
                   'smtp_ignore_ssl','smtp_from_name','smtp_to')
        with get_db() as conn:
            for k in allowed:
                if k in data:
                    conn.execute('INSERT INTO sys_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                 (k, str(data[k])))
            audit(conn, s['user_id'], s['nome'], 'alterar_smtp')
            conn.commit()
        self._json(200, {'ok': True})

    def _test_smtp(self, s):
        cfg = get_config()
        if not cfg.get('smtp_host') or not cfg.get('smtp_user'):
            self._json(400, {'error': 'Preencha host e usuário antes de testar.'}); return
        destino = cfg.get('smtp_to') or cfg.get('smtp_user')
        try:
            ok = _send_plain_email(cfg, destino, 'SGDP — Teste de configuração SMTP',
                                    'Este é um e-mail de teste da configuração SMTP do SGDP.')
            if not ok: raise Exception('Destinatário ou host ausente')
            self._json(200, {'ok': True})
        except Exception as e:
            _log.error('Falha no teste de SMTP: %s', e)
            self._json(500, {'error': str(e)})

    def _enviar_email(self, did, body, s):
        data = json.loads(body) if body else {}
        destinatario = (data.get('to') or '').strip()
        assunto      = (data.get('subject') or '').strip()
        corpo        = data.get('body') or ''
        if not destinatario: self._json(400, {'error': 'Destinatário obrigatório'}); return
        cfg = get_config()
        if not cfg.get('smtp_host'): self._json(400, {'error': 'SMTP não configurado. Configure em Configurações → Segurança.'}); return
        with get_db() as conn:
            doc = conn.execute(
                'SELECT d.*, a.nome_original, a.nome_disco FROM documentos d LEFT JOIN arquivos a ON d.arquivo_id=a.id WHERE d.id=?',
                (did,)).fetchone()
        if not doc: self._json(404, {'error': 'Documento não encontrado'}); return
        try:
            from email.message import EmailMessage
            msg = EmailMessage()
            msg['Subject'] = assunto or f"{doc['tipo'].capitalize()} nº {doc['numero']}/{doc['ano']}"
            msg['From'] = f"{cfg.get('smtp_from_name') or 'SGDP'} <{cfg.get('smtp_user')}>"
            msg['To'] = destinatario
            msg.set_content(corpo or doc['ementa'])
            if doc['nome_disco']:
                fp = os.path.join(UPLOADS_DIR, doc['nome_disco'])
                if os.path.isfile(fp):
                    with open(fp, 'rb') as f:
                        msg.add_attachment(f.read(), maintype='application', subtype='pdf',
                                           filename=doc['nome_original'] or 'documento.pdf')
            _smtp_send(cfg, msg)
            with get_db() as conn:
                audit(conn, s['user_id'], s['nome'], 'enviar_email', did, f"Para {destinatario}")
                conn.commit()
            self._json(200, {'ok': True})
        except Exception as e:
            _log.error(f'Erro ao enviar e-mail: {e}')
            self._json(500, {'error': f'Falha ao enviar e-mail: {e}'})

    # ── Importação CSV ────────────────────────────────────────────────────────

    def _import_csv(self, body, s):
        data = json.loads(body) if body else {}
        rows = data.get('rows') or []
        if not rows: self._json(400, {'error': 'Nenhuma linha para importar'}); return
        importados, erros = 0, []
        with get_db() as conn:
            for i, r in enumerate(rows):
                tipo = (r.get('tipo') or '').strip().lower()
                ementa = (r.get('ementa') or '').strip()
                data_d = (r.get('data') or '').strip()
                if tipo not in TIPOS:
                    erros.append(f'Linha {i+1}: tipo inválido "{tipo}"'); continue
                if not ementa or not data_d:
                    erros.append(f'Linha {i+1}: ementa e data são obrigatórias'); continue
                ano = int(r.get('ano') or data_d[:4])
                numero = int(r['numero']) if r.get('numero') else proximo_numero(conn, tipo, ano)
                try:
                    conn.execute(
                        'INSERT INTO documentos (tipo,numero,ano,data,ementa,partes,observacoes,assunto,criado_por,atualizado_por)'
                        ' VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (tipo, numero, ano, data_d, ementa, r.get('partes') or '', r.get('observacoes') or '',
                         r.get('assunto') or 'Outros', s['user_id'], s['user_id'])
                    )
                    bump_contador(conn, tipo, ano, numero)
                    importados += 1
                except sqlite3.IntegrityError:
                    erros.append(f'Linha {i+1}: já existe {tipo} nº {numero}/{ano}')
            audit(conn, s['user_id'], s['nome'], 'import_csv', detalhes=f'{importados} documentos importados')
            conn.commit()
        self._json(200, {'importados': importados, 'erros': erros})

    # ── Arquivos ──────────────────────────────────────────────────────────────

    def _upload_arquivo(self, did, s):
        ct = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in ct:
            self._json(400, {'error': 'Envie como multipart/form-data'}); return
        length = int(self.headers.get('Content-Length', 0))
        if length > MAX_UPLOAD_SIZE:
            self._json(413, {'error': 'Arquivo muito grande (máx. 50 MB)'}); return
        boundary = next((p.strip()[9:].strip('"') for p in ct.split(';') if p.strip().startswith('boundary=')), None)
        if not boundary: self._json(400, {'error': 'Boundary não encontrado'}); return
        filename, filedata = self._parse_multipart(self.rfile.read(length), boundary)
        if not filename or filedata is None: self._json(400, {'error': 'Arquivo não encontrado'}); return
        if not filename.lower().endswith('.pdf'): self._json(400, {'error': 'Apenas PDFs aceitos'}); return
        with get_db() as conn:
            doc = conn.execute(
                '''SELECT d.*, u.departamento criado_por_departamento
                   FROM documentos d LEFT JOIN usuarios u ON d.criado_por=u.id
                   WHERE d.id=?''', (did,)
            ).fetchone()
            if not doc: self._json(404, {'error': 'Documento não encontrado'}); return
            if not pode_editar_doc(doc, s): self._json(403, {'error': 'Sem permissão para este documento'}); return
            if doc['arquivo_id']:
                old = conn.execute('SELECT * FROM arquivos WHERE id=?', (doc['arquivo_id'],)).fetchone()
                if old:
                    p = os.path.join(UPLOADS_DIR, old['nome_disco'])
                    if os.path.isfile(p): os.remove(p)
                    conn.execute('DELETE FROM arquivos WHERE id=?', (old['id'],))
            nome_disco = f"{secrets.token_hex(16)}.pdf"
            with open(os.path.join(UPLOADS_DIR, nome_disco), 'wb') as f:
                f.write(filedata)
            conn.execute('INSERT INTO arquivos (nome_original,nome_disco,tamanho,enviado_por) VALUES (?,?,?,?)',
                         (filename, nome_disco, len(filedata), s['user_id']))
            aid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.execute('UPDATE documentos SET arquivo_id=?,atualizado_por=?,atualizado_em=? WHERE id=?',
                         (aid, s['user_id'], time.strftime('%Y-%m-%dT%H:%M:%S'), did))
            audit(conn, s['user_id'], s['nome'], 'upload', did, filename)
            conn.commit()
        self._json(200, {'ok': True, 'arquivo_id': aid, 'nome_original': filename, 'tamanho': len(filedata)})

    def _assinar_doc(self, did, s):
        """Assina digitalmente o PDF do documento com certificado ICP-Brasil (.pfx A1).
        Se um novo PDF for enviado no campo 'pdf', assina e anexa esse; senão assina
        o PDF já anexado ao documento."""
        ct = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in ct:
            self._json(400, {'error': 'Envie como multipart/form-data'}); return
        length = int(self.headers.get('Content-Length', 0))
        if length > MAX_UPLOAD_SIZE:
            self._json(413, {'error': 'Arquivo muito grande (máx. 50 MB)'}); return
        boundary = next((p.strip()[9:].strip('"') for p in ct.split(';') if p.strip().startswith('boundary=')), None)
        if not boundary: self._json(400, {'error': 'Boundary não encontrado'}); return
        parts = _parse_multipart_all(self.rfile.read(length), boundary.encode())

        cert_bytes = parts.get('cert', {}).get('data')
        senha = parts.get('senha', {}).get('text', '')
        pdf_novo = parts.get('pdf', {}).get('data')
        if not cert_bytes or not senha:
            self._json(400, {'error': 'Certificado (.pfx) e senha são obrigatórios'}); return

        with get_db() as conn:
            doc = conn.execute(
                '''SELECT d.*, u.departamento criado_por_departamento
                   FROM documentos d LEFT JOIN usuarios u ON d.criado_por=u.id
                   WHERE d.id=?''', (did,)
            ).fetchone()
            if not doc: self._json(404, {'error': 'Documento não encontrado'}); return
            if not pode_editar_doc(doc, s): self._json(403, {'error': 'Sem permissão para este documento'}); return
            nome_original = 'documento.pdf'
            if pdf_novo:
                pdf_bytes = pdf_novo
            elif doc['arquivo_id']:
                arq = conn.execute('SELECT * FROM arquivos WHERE id=?', (doc['arquivo_id'],)).fetchone()
                if not arq: self._json(404, {'error': 'PDF anexado não encontrado no disco'}); return
                fp = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if not os.path.isfile(fp): self._json(404, {'error': 'PDF anexado não encontrado no disco'}); return
                with open(fp, 'rb') as f: pdf_bytes = f.read()
                nome_original = arq['nome_original']
            else:
                self._json(400, {'error': 'Este documento não tem PDF anexado. Envie um PDF para assinar.'}); return

        try:
            pdf_assinado, cert_subject = _assinar_pdf_icp(pdf_bytes, cert_bytes, senha)
        except ImportError:
            self._json(400, {'error': 'Módulo de assinatura ICP-Brasil indisponível — instale com "pip install -r requirements.txt"'}); return
        except Exception as e:
            self._json(400, {'error': f'Falha ao assinar com o certificado: {e}'}); return
        finally:
            cert_bytes = None; senha = None  # descarta referências assim que possível

        with get_db() as conn:
            doc = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            if doc['arquivo_id']:
                old = conn.execute('SELECT * FROM arquivos WHERE id=?', (doc['arquivo_id'],)).fetchone()
                if old:
                    p = os.path.join(UPLOADS_DIR, old['nome_disco'])
                    if os.path.isfile(p): os.remove(p)
                    conn.execute('DELETE FROM arquivos WHERE id=?', (old['id'],))
            nome_disco = f"{secrets.token_hex(16)}.pdf"
            with open(os.path.join(UPLOADS_DIR, nome_disco), 'wb') as f:
                f.write(pdf_assinado)
            conn.execute('INSERT INTO arquivos (nome_original,nome_disco,tamanho,enviado_por) VALUES (?,?,?,?)',
                         (nome_original, nome_disco, len(pdf_assinado), s['user_id']))
            aid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            agora = time.strftime('%Y-%m-%dT%H:%M:%S')
            conn.execute('''UPDATE documentos SET arquivo_id=?,atualizado_por=?,atualizado_em=?,
                             assinado_por=?,assinado_em=?,assinatura_cert=? WHERE id=?''',
                         (aid, s['user_id'], agora, s['user_id'], agora, cert_subject, did))
            # Registro imutável e independente do arquivo — sobrevive mesmo se o PDF for
            # trocado/apagado depois, permitindo verificação pública por código.
            cod = _gerar_cod_assinatura(conn)
            hash_sha256 = hashlib.sha256(pdf_assinado).hexdigest()
            conn.execute(
                '''INSERT INTO signatures (cod,documento_id,doc_tipo,doc_numero,doc_ano,doc_ementa,
                   signer_user_id,signer_name,method,cert_subject,hash_sha256,signed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (cod, did, doc['tipo'], doc['numero'], doc['ano'], doc['ementa'],
                 s['user_id'], s['nome'], 'icp-brasil', cert_subject, hash_sha256, agora)
            )
            audit(conn, s['user_id'], s['nome'], 'assinar_icp', did, f'{cert_subject} — cód. {cod}')
            conn.commit()
        self._json(200, {'ok': True, 'arquivo_id': aid, 'cert_subject': cert_subject, 'cod_verificacao': cod})

    def _remove_arquivo(self, did, s):
        with get_db() as conn:
            doc = conn.execute(
                '''SELECT d.*, u.departamento criado_por_departamento
                   FROM documentos d LEFT JOIN usuarios u ON d.criado_por=u.id
                   WHERE d.id=?''', (did,)
            ).fetchone()
            if not doc or not doc['arquivo_id']:
                self._json(404, {'error': 'Sem arquivo para remover'}); return
            if not pode_editar_doc(doc, s): self._json(403, {'error': 'Sem permissão para este documento'}); return
            arq = conn.execute('SELECT * FROM arquivos WHERE id=?', (doc['arquivo_id'],)).fetchone()
            if arq:
                p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if os.path.isfile(p): os.remove(p)
                conn.execute('DELETE FROM arquivos WHERE id=?', (arq['id'],))
            conn.execute('UPDATE documentos SET arquivo_id=NULL WHERE id=?', (did,))
            audit(conn, s['user_id'], s['nome'], 'remover_arquivo', did)
            conn.commit()
        self._json(200, {'ok': True})

    def _serve_verificar(self, cod):
        with get_db() as conn:
            row = conn.execute('SELECT * FROM signatures WHERE cod=?', (cod,)).fetchone()
        TIPOS_LABEL = {'lei': 'Lei', 'decreto': 'Decreto', 'portaria': 'Portaria', 'parecer': 'Parecer', 'oficio': 'Ofício'}
        if row:
            doc_label = f"{TIPOS_LABEL.get(row['doc_tipo'], row['doc_tipo'] or '')} nº {row['doc_numero']}/{row['doc_ano']}"
            status_html = f'''<h2>✓ Assinatura Encontrada</h2>
    <div class="field"><strong>Documento:</strong> {html_mod.escape(doc_label)}</div>
    <div class="field"><strong>Ementa:</strong> {html_mod.escape(row['doc_ementa'] or '—')}</div>
    <div class="field"><strong>Assinado por:</strong> {html_mod.escape(row['signer_name'] or '—')}</div>
    <div class="field"><strong>Certificado:</strong> {html_mod.escape(row['cert_subject'] or '—')}</div>
    <div class="field"><strong>Data:</strong> {html_mod.escape(row['signed_at'] or '—')}</div>'''
            status_class = 'ok'
            extra_note = '<p style="font-size:12px;color:#6b7280;margin-top:10px">Para validar a cadeia de certificação, confira também o <a href="https://verificador.iti.gov.br/" target="_blank" rel="noopener">verificador oficial do ITI</a>.</p>'
        else:
            status_html = '<h2>✗ Não encontrado</h2><p style="font-size:13px;margin-top:6px">O código não corresponde a nenhuma assinatura registrada nesta instalação.</p>'
            status_class = 'err'
            extra_note = ''

        html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verificação de Autenticidade — SGDP</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,sans-serif;background:#f3f4f6;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
  .card{{background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.10);max-width:520px;width:100%;padding:32px 36px}}
  .logo{{font-size:13px;font-weight:700;letter-spacing:.5px;color:#6b7280;text-transform:uppercase;margin-bottom:20px}}
  h1{{font-size:18px;font-weight:700;margin-bottom:6px}}
  .cod{{font-family:monospace;font-size:15px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:8px 14px;display:inline-block;margin-bottom:20px;letter-spacing:2px}}
  #status{{border-radius:8px;padding:16px 20px;margin-bottom:20px}}
  #status.ok{{background:#f0fdf4;border:1px solid #86efac}}
  #status.err{{background:#fef2f2;border:1px solid #fca5a5}}
  #status h2{{font-size:15px;font-weight:700;margin-bottom:4px}}
  #status.ok h2{{color:#166534}} #status.err h2{{color:#b91c1c}}
  .field{{margin-bottom:8px;font-size:13px}} .field strong{{color:#374151}}
  .footer{{font-size:11px;color:#9ca3af;margin-top:20px;text-align:center}}
</style>
</head>
<body>
  <div class="card">
    <div class="logo">SGDP — Sistema de Gestão de Documentos da Procuradoria</div>
    <h1>Verificação de Autenticidade</h1>
    <div class="cod">{html_mod.escape(cod)}</div>
    <div id="status" class="{status_class}">{status_html}</div>
    {extra_note}
    <div class="footer">Consulta realizada em {time.strftime('%d/%m/%Y %H:%M')} nesta instalação do SGDP.</div>
  </div>
</body>
</html>"""
        payload = html.encode('utf-8')
        self.send_response(200 if row else 404)
        self._cors()
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _download_arquivo(self, aid, qs, s):
        with get_db() as conn:
            arq = conn.execute('SELECT * FROM arquivos WHERE id=?', (aid,)).fetchone()
            doc = conn.execute('SELECT * FROM documentos WHERE arquivo_id=?', (aid,)).fetchone()
        if not arq: self._json(404, {'error': 'Não encontrado'}); return
        if doc and not pode_ver_doc(doc, s): self._json(404, {'error': 'Não encontrado'}); return
        filepath = os.path.join(UPLOADS_DIR, arq['nome_disco'])
        if not os.path.isfile(filepath): self._json(404, {'error': 'Arquivo não encontrado no disco'}); return
        with open(filepath, 'rb') as f:
            data = f.read()
        inline = (qs.get('inline') or ['0'])[0] == '1'
        safe_fn = arq['nome_original'].replace('"', '_').replace('\n', '_').replace('\r', '_')
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/pdf')
        self.send_header('Content-Disposition', 'inline' if inline else f'attachment; filename="{safe_fn}"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _parse_multipart(self, body, boundary):
        for part in body.split(f'--{boundary}'.encode())[1:]:
            if part.startswith(b'--'): break
            sep = b'\r\n\r\n' if b'\r\n\r\n' in part else b'\n\n'
            if sep not in part: continue
            hdrs, content = part.split(sep, 1)
            hdrs_str = hdrs.decode('utf-8', errors='replace')
            if 'filename=' not in hdrs_str: continue
            m = re.search(r'filename="([^"]*)"', hdrs_str)
            if not m: continue
            content = content[:-2] if content.endswith(b'\r\n') else content[:-1] if content.endswith(b'\n') else content
            return m.group(1), content
        return None, None

    # ── Contadores / Dashboard ────────────────────────────────────────────────

    def _get_contadores(self, qs):
        tipo = (qs.get('tipo') or [None])[0]
        ano  = int((qs.get('ano') or [str(time.localtime().tm_year)])[0])
        with get_db() as conn:
            if tipo:
                self._json(200, {'tipo': tipo, 'ano': ano, 'proximo': proximo_numero(conn, tipo, ano)}); return
            self._json(200, {t: proximo_numero(conn, t, ano) for t in TIPOS})

    def _dashboard(self, s):
        ano = time.localtime().tm_year
        vis = '' if s['admin'] else 'AND (sigiloso=0 OR criado_por=?)'
        vp  = [] if s['admin'] else [s['user_id']]
        with get_db() as conn:
            totais   = {t: conn.execute(f'SELECT COUNT(*) FROM documentos WHERE tipo=? AND excluido_em IS NULL {vis}', (t, *vp)).fetchone()[0] for t in TIPOS}
            ano_atual = {t: conn.execute(f'SELECT COUNT(*) FROM documentos WHERE tipo=? AND ano=? AND excluido_em IS NULL {vis}', (t, ano, *vp)).fetchone()[0] for t in TIPOS}
            recentes = conn.execute(
                f'''SELECT d.id, d.tipo, d.numero, d.ano, d.data, d.ementa, d.arquivo_id,
                          u.nome criado_por_nome, u.departamento criado_por_departamento
                   FROM documentos d LEFT JOIN usuarios u ON d.criado_por=u.id
                   WHERE d.excluido_em IS NULL {vis}
                   ORDER BY d.criado_em DESC LIMIT 10''', vp
            ).fetchall()
            ultimos = {}
            for t in TIPOS:
                row = conn.execute(
                    f'''SELECT id, numero, ano FROM documentos
                       WHERE tipo=? AND excluido_em IS NULL {vis}
                       ORDER BY ano DESC, numero DESC LIMIT 1''', (t, *vp)).fetchone()
                ultimos[t] = dict(row) if row else None
        self._json(200, {'totais': totais, 'ano_atual': ano_atual, 'recentes': [dict(r) for r in recentes], 'ultimos': ultimos})

    # ── Usuários ──────────────────────────────────────────────────────────────

    def _create_usuario(self, body, s):
        data = json.loads(body) if body else {}
        username = data.get('username', '').strip()
        nome     = data.get('nome', '').strip()
        senha    = data.get('senha', '')
        if not username or not nome or not senha:
            self._json(400, {'error': 'username, nome e senha são obrigatórios'}); return
        if len(senha) < 6:
            self._json(400, {'error': 'Senha mínima: 6 caracteres'}); return
        departamento = data.get('departamento') or DEPARTAMENTOS[0]
        if departamento not in DEPARTAMENTOS:
            self._json(400, {'error': 'Departamento inválido'}); return
        try:
            with get_db() as conn:
                conn.execute('INSERT INTO usuarios (username,nome,senha_hash,admin,email,cpf,cargo,matricula,departamento) VALUES (?,?,?,?,?,?,?,?,?)',
                             (username, nome, _hash_password(senha), int(bool(data.get('admin'))),
                              (data.get('email') or '').strip(), (data.get('cpf') or '').strip(),
                              (data.get('cargo') or '').strip(), (data.get('matricula') or '').strip(),
                              departamento))
                uid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                audit(conn, s['user_id'], s['nome'], 'criar_usuario', detalhes=f"@{username} ({nome})")
                conn.commit()
            self._json(201, {'id': uid, 'username': username, 'nome': nome, 'admin': bool(data.get('admin')), 'departamento': departamento})
        except sqlite3.IntegrityError:
            self._json(409, {'error': f'Usuário "{username}" já existe'})

    def _update_usuario(self, uid, body, s):
        data = json.loads(body) if body else {}
        fields = {}
        if 'nome'  in data: fields['nome']  = data['nome'].strip()
        if 'email' in data: fields['email'] = data['email'].strip()
        if 'cpf'   in data: fields['cpf']   = data['cpf'].strip()
        if 'cargo' in data: fields['cargo'] = data['cargo'].strip()
        if 'matricula' in data: fields['matricula'] = data['matricula'].strip()
        if 'departamento' in data:
            if data['departamento'] not in DEPARTAMENTOS:
                self._json(400, {'error': 'Departamento inválido'}); return
            fields['departamento'] = data['departamento']
        if 'admin' in data: fields['admin'] = int(bool(data['admin']))
        if 'ativo' in data: fields['ativo'] = int(bool(data['ativo']))
        if data.get('senha'):
            if len(data['senha']) < 6: self._json(400, {'error': 'Senha mínima: 6 caracteres'}); return
            fields['senha_hash'] = _hash_password(data['senha'])
            fields['must_change_password'] = 0
        if not fields: self._json(400, {'error': 'Nada para atualizar'}); return
        with get_db() as conn:
            row = conn.execute('SELECT nome, username FROM usuarios WHERE id=?', (uid,)).fetchone()
            if not row:
                self._json(404, {'error': 'Usuário não encontrado'}); return
            conn.execute(f"UPDATE usuarios SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                         list(fields.values()) + [uid])
            audit(conn, s['user_id'], s['nome'], 'editar_usuario', detalhes=f"@{row['username']} ({row['nome']}): {', '.join(k for k in fields if k != 'senha_hash')}")
            conn.commit()
        self._json(200, {'ok': True})

    def _delete_usuario(self, uid, s):
        if uid == s['user_id']:
            self._json(400, {'error': 'Não pode excluir seu próprio usuário'}); return
        with get_db() as conn:
            row = conn.execute('SELECT nome, username FROM usuarios WHERE id=?', (uid,)).fetchone()
            try:
                conn.execute('DELETE FROM usuarios WHERE id=?', (uid,))
            except sqlite3.IntegrityError:
                self._json(409, {'error': 'Não é possível excluir: este usuário já criou, editou, assinou ou tem alguma outra ação registrada no sistema (documentos, auditoria, lembretes). Use "Desativar" em vez de excluir, para preservar o histórico.'})
                return
            if row:
                audit(conn, s['user_id'], s['nome'], 'excluir_usuario', detalhes=f"@{row['username']} ({row['nome']})")
            conn.commit()
        self._json(200, {'ok': True})

    # ── Configurações ─────────────────────────────────────────────────────────

    def _change_senha(self, body, s):
        data = json.loads(body) if body else {}
        atual    = data.get('atual', '')
        nova     = data.get('nova', '')
        confirma = data.get('confirma', '')
        if not atual:    self._json(400, {'error': 'Digite a senha atual'}); return
        if not nova:     self._json(400, {'error': 'Digite a nova senha'}); return
        if len(nova) < 6: self._json(400, {'error': 'Senha mínima: 6 caracteres'}); return
        if nova != confirma: self._json(400, {'error': 'As senhas não coincidem'}); return
        with get_db() as conn:
            row = conn.execute('SELECT senha_hash FROM usuarios WHERE id=?', (s['user_id'],)).fetchone()
        if not row or not _verify_password(atual, row['senha_hash']):
            self._json(401, {'error': 'Senha atual incorreta'}); return
        with get_db() as conn:
            conn.execute('UPDATE usuarios SET senha_hash=? WHERE id=?', (_hash_password(nova), s['user_id']))
            audit(conn, s['user_id'], s['nome'], 'alterar_senha')
            conn.commit()
        self._json(200, {'ok': True})

    def _update_config(self, body, s):
        data = json.loads(body) if body else {}
        allowed = ('orgao_nome', 'municipio', 'backup_path', 'auto_backup_enabled', 'auto_backup_keep',
                   'aut_nome', 'aut_cargo', 'diario_url')
        with get_db() as conn:
            for k in allowed:
                if k in data:
                    conn.execute('INSERT OR REPLACE INTO sys_settings VALUES (?,?)', (k, str(data[k])))
            audit(conn, s['user_id'], s['nome'], 'alterar_config', detalhes=', '.join(k for k in allowed if k in data))
            conn.commit()
        if 'auto_backup_keep' in data or 'backup_path' in data:
            _rotate_backups()
        self._json(200, {'ok': True})

    def _restore_db_backup(self, s):
        import tempfile
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
        if len(raw) < 16 or raw[:16] != b'SQLite format 3\x00':
            self._json(400, {'error': 'Arquivo não é um banco SQLite válido'}); return
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        try:
            tmp.write(raw); tmp.close()
            with sqlite3.connect(tmp.name, factory=_ConnAutoClose) as tc:
                tables = {r[0] for r in tc.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if not {'documentos', 'arquivos', 'usuarios'}.issubset(tables):
                self._json(400, {'error': 'Banco inválido: tabelas obrigatórias ausentes'}); return
            _do_db_backup()  # backup do atual antes de restaurar
            with sqlite3.connect(tmp.name, factory=_ConnAutoClose) as src, sqlite3.connect(DB_PATH, factory=_ConnAutoClose) as dst:
                src.backup(dst)
            with get_db() as conn:
                audit(conn, s['user_id'], s['nome'], 'restaurar_db', detalhes='Banco restaurado a partir de arquivo .db')
                conn.commit()
            self._json(200, {'ok': True})
        except Exception as e:
            self._json(500, {'error': str(e)})
        finally:
            try: os.remove(tmp.name)
            except: pass

    def _factory_reset(self, s):
        _do_db_backup()
        with get_db() as conn:
            audit(conn, s['user_id'], s['nome'], 'factory_reset', detalhes='Todos os dados apagados')
            conn.execute('DELETE FROM documentos')
            conn.execute('DELETE FROM arquivos')
            conn.execute('DELETE FROM contadores')
            conn.execute('DELETE FROM auditoria')
            conn.execute('DELETE FROM signatures')
            conn.execute('DELETE FROM lembretes')
            conn.commit()
        import shutil
        shutil.rmtree(UPLOADS_DIR, ignore_errors=True)
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        self._json(200, {'ok': True})

    # ── Backup / Restore ──────────────────────────────────────────────────────

    def _export_backup(self):
        import base64
        with get_db() as conn:
            docs  = [dict(r) for r in conn.execute('SELECT * FROM documentos').fetchall()]
            users = [dict(r) for r in conn.execute(
                'SELECT id,username,nome,senha_hash,admin,ativo,departamento,criado_em FROM usuarios').fetchall()]
            conts = [dict(r) for r in conn.execute('SELECT * FROM contadores').fetchall()]
            auditoria = [dict(r) for r in conn.execute('SELECT * FROM auditoria').fetchall()]
            signatures = [dict(r) for r in conn.execute('SELECT * FROM signatures').fetchall()]
            arqs  = []
            for arq in conn.execute('SELECT * FROM arquivos').fetchall():
                p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if os.path.isfile(p):
                    with open(p, 'rb') as f:
                        arqs.append({**dict(arq), 'data_b64': base64.b64encode(f.read()).decode()})
        backup = {'sgdp_version': '1.17.0', 'exported_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                  'documentos': docs, 'usuarios': users, 'contadores': conts, 'arquivos': arqs,
                  'auditoria': auditoria, 'signatures': signatures}
        body = json.dumps(backup, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="SIS_SGDP_BACKUP_{time.strftime("%Y-%m-%d_%H-%M-%S")}.json"')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _import_backup(self, s):
        import base64
        length = int(self.headers.get('Content-Length', 0))
        if length > 500 * 1024 * 1024: self._json(413, {'error': 'Backup muito grande'}); return
        try:
            backup = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            self._json(400, {'error': 'Arquivo inválido'}); return
        if 'sgdp_version' not in backup: self._json(400, {'error': 'Não é um backup SGDP'}); return
        with get_db() as conn:
            conn.execute('DELETE FROM documentos')
            conn.execute('DELETE FROM arquivos')
            conn.execute('DELETE FROM contadores')
            conn.execute('DELETE FROM signatures')
            for arq in backup.get('arquivos', []):
                nome_disco = f"{secrets.token_hex(16)}.pdf"
                with open(os.path.join(UPLOADS_DIR, nome_disco), 'wb') as f:
                    f.write(base64.b64decode(arq['data_b64']))
                conn.execute('INSERT INTO arquivos (id,nome_original,nome_disco,tamanho,enviado_por,enviado_em) VALUES (?,?,?,?,?,?)',
                             (arq['id'], arq['nome_original'], nome_disco, arq['tamanho'], arq.get('enviado_por'), arq.get('enviado_em')))
            for doc in backup.get('documentos', []):
                conn.execute(
                    'INSERT OR REPLACE INTO documentos '
                    '(id,tipo,numero,ano,data,ementa,partes,observacoes,arquivo_id,sigiloso,criado_por,atualizado_por,criado_em,atualizado_em)'
                    ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (doc['id'],doc['tipo'],doc['numero'],doc['ano'],doc['data'],doc['ementa'],
                     doc.get('partes'),doc.get('observacoes'),doc.get('arquivo_id'),int(bool(doc.get('sigiloso'))),
                     doc.get('criado_por'),doc.get('atualizado_por'),doc.get('criado_em'),doc.get('atualizado_em')))
            for c in backup.get('contadores', []):
                conn.execute('INSERT OR REPLACE INTO contadores VALUES (?,?,?)', (c['tipo'],c['ano'],c['ultimo']))
            for u in backup.get('usuarios', []):
                conn.execute('INSERT OR REPLACE INTO usuarios (id,username,nome,senha_hash,admin,ativo,departamento,criado_em) VALUES (?,?,?,?,?,?,?,?)',
                             (u['id'],u['username'],u['nome'],u['senha_hash'],u['admin'],u.get('ativo',1),
                              u.get('departamento') or DEPARTAMENTOS[0],u.get('criado_em')))
            for sig in backup.get('signatures', []):
                conn.execute(
                    '''INSERT OR REPLACE INTO signatures
                       (id,cod,documento_id,doc_tipo,doc_numero,doc_ano,doc_ementa,
                        signer_user_id,signer_name,method,cert_subject,hash_sha256,signed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (sig.get('id'), sig.get('cod'), sig.get('documento_id'), sig.get('doc_tipo'),
                     sig.get('doc_numero'), sig.get('doc_ano'), sig.get('doc_ementa'),
                     sig.get('signer_user_id'), sig.get('signer_name'), sig.get('method'),
                     sig.get('cert_subject'), sig.get('hash_sha256'), sig.get('signed_at'))
                )
            ndoc = len(backup.get('documentos', []))
            narq = len(backup.get('arquivos', []))
            audit(conn, s['user_id'], s['nome'], 'restaurar_backup', detalhes=f"{ndoc} documentos, {narq} arquivos")
            conn.commit()
        self._json(200, {'ok': True, 'documentos': ndoc, 'arquivos': narq})

    def _read_backup_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 500 * 1024 * 1024: self._json(413, {'error': 'Backup muito grande'}); return None
        try:
            backup = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            self._json(400, {'error': 'Arquivo inválido'}); return None
        if 'sgdp_version' not in backup: self._json(400, {'error': 'Não é um backup SGDP'}); return None
        return backup

    def _diff_sync(self, backup):
        """Compara documentos do backup com os locais por (tipo,numero,ano) — nunca por id,
        pois cada instalação tem seu próprio autoincrement e os ids colidem entre máquinas."""
        with get_db() as conn:
            locais = conn.execute('SELECT * FROM documentos').fetchall()
        por_chave = {(r['tipo'], r['numero'], r['ano']): dict(r) for r in locais}
        novos, conflitos = [], []
        for doc in backup.get('documentos', []):
            chave = (doc['tipo'], doc['numero'], doc['ano'])
            local = por_chave.get(chave)
            if local is None:
                novos.append(doc)
            elif (doc.get('atualizado_em') or '') > (local.get('atualizado_em') or '') and (
                doc.get('ementa') != local.get('ementa') or doc.get('partes') != local.get('partes') or
                doc.get('observacoes') != local.get('observacoes') or doc.get('assunto') != local.get('assunto')
            ):
                conflitos.append({'chave': f"{chave[0]}|{chave[1]}|{chave[2]}", 'local': local, 'backup': doc})
        return novos, conflitos

    def _diff_audit(self, backup):
        """Eventos de auditoria do backup ainda não presentes localmente. Dedup por
        (usuario_nome,acao,detalhes,em) — não por id, que colide entre instalações."""
        with get_db() as conn:
            locais = {(r['usuario_nome'], r['acao'], r['detalhes'], r['em'])
                      for r in conn.execute('SELECT usuario_nome,acao,detalhes,em FROM auditoria').fetchall()}
        novos = []
        for a in backup.get('auditoria', []):
            chave = (a.get('usuario_nome'), a.get('acao'), a.get('detalhes'), a.get('em'))
            if chave not in locais:
                novos.append(a)
        return novos

    def _sync_preview(self):
        backup = self._read_backup_body()
        if backup is None: return
        novos, conflitos = self._diff_sync(backup)
        novos_audit = self._diff_audit(backup)
        self._json(200, {
            'novos': len(novos), 'conflitos': conflitos, 'novos_auditoria': len(novos_audit),
            'exported_at': backup.get('exported_at'),
        })

    def _sync_apply(self, s):
        data = json.loads(self._body())
        backup = data.get('backup')
        aceitar = set(data.get('aceitar') or [])
        if not backup or 'sgdp_version' not in backup:
            self._json(400, {'error': 'Backup inválido'}); return
        novos, conflitos = self._diff_sync(backup)
        arqs_backup = {a['id']: a for a in backup.get('arquivos', [])}

        def _anexar_arquivo(conn, did, arquivo_id_backup):
            arq = arqs_backup.get(arquivo_id_backup)
            if not arq or not arq.get('data_b64'): return
            import base64
            nome_disco = f"{secrets.token_hex(16)}.pdf"
            with open(os.path.join(UPLOADS_DIR, nome_disco), 'wb') as f:
                f.write(base64.b64decode(arq['data_b64']))
            conn.execute('INSERT INTO arquivos (nome_original,nome_disco,tamanho,enviado_por) VALUES (?,?,?,?)',
                         (arq['nome_original'], nome_disco, arq['tamanho'], s['user_id']))
            aid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.execute('UPDATE documentos SET arquivo_id=? WHERE id=?', (aid, did))

        n_novos = n_conflitos = 0
        with get_db() as conn:
            for doc in novos:
                try:
                    conn.execute(
                        'INSERT INTO documentos (tipo,numero,ano,data,ementa,partes,observacoes,assunto,'
                        'processo_pa,processo_tipo,processo_ref,ato_tipo,cargo,sigiloso,criado_por,atualizado_por)'
                        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (doc['tipo'], doc['numero'], doc['ano'], doc['data'], doc['ementa'],
                         doc.get('partes') or '', doc.get('observacoes') or '', doc.get('assunto') or 'Outros',
                         doc.get('processo_pa') or '', doc.get('processo_tipo') or '', doc.get('processo_ref') or '',
                         doc.get('ato_tipo') or '', doc.get('cargo') or '', int(bool(doc.get('sigiloso'))),
                         s['user_id'], s['user_id'])
                    )
                    did = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                    bump_contador(conn, doc['tipo'], doc['ano'], doc['numero'])
                    if doc.get('arquivo_id'): _anexar_arquivo(conn, did, doc['arquivo_id'])
                    n_novos += 1
                except sqlite3.IntegrityError:
                    pass
            for c in conflitos:
                if c['chave'] not in aceitar: continue
                doc = c['backup']; local = c['local']
                conn.execute(
                    'UPDATE documentos SET data=?,ementa=?,partes=?,observacoes=?,assunto=?,'
                    'processo_pa=?,processo_tipo=?,processo_ref=?,ato_tipo=?,cargo=?,atualizado_por=?,atualizado_em=? '
                    'WHERE id=?',
                    (doc['data'], doc['ementa'], doc.get('partes') or '', doc.get('observacoes') or '',
                     doc.get('assunto') or 'Outros', doc.get('processo_pa') or '', doc.get('processo_tipo') or '',
                     doc.get('processo_ref') or '', doc.get('ato_tipo') or '', doc.get('cargo') or '',
                     s['user_id'], time.strftime('%Y-%m-%dT%H:%M:%S'), local['id'])
                )
                if doc.get('arquivo_id'): _anexar_arquivo(conn, local['id'], doc['arquivo_id'])
                n_conflitos += 1
            # Importa o histórico de auditoria da instância de origem, preservando autor/data
            # originais. documento_id e usuario_id NÃO são remapeados (ids locais colidem entre
            # instalações) — ficam NULL para não atribuir o evento à pessoa/documento errado.
            novos_audit = self._diff_audit(backup)
            for a in novos_audit:
                conn.execute(
                    'INSERT INTO auditoria (usuario_id,usuario_nome,acao,documento_id,detalhes,em) VALUES (NULL,?,?,NULL,?,?)',
                    (a.get('usuario_nome'), a.get('acao'), a.get('detalhes'), a.get('em'))
                )
            audit(conn, s['user_id'], s['nome'], 'sincronizar_backup',
                  detalhes=f"{n_novos} novos, {n_conflitos} conflitos resolvidos, {len(novos_audit)} eventos de auditoria importados")
            conn.commit()
        self._json(200, {'novos': n_novos, 'conflitos_aplicados': n_conflitos, 'auditoria_importada': len(novos_audit)})

    # ── Utilitários HTTP ──────────────────────────────────────────────────────

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization')

    def _body(self):
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n).decode('utf-8') if n else '{}'

    def _token(self):
        auth = self.headers.get('Authorization', '')
        return auth[7:] if auth.startswith('Bearer ') else None

    def _auth(self):
        s = get_session(self._token())
        if not s: self._json(401, {'error': 'Não autenticado'})
        return s

    def log_message(self, fmt, *args):
        pass

# ── Watchdog ──────────────────────────────────────────────────────────────────

def _get_backup_cfg():
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT key,value FROM sys_settings WHERE key IN ('backup_path','auto_backup_enabled','auto_backup_keep')"
            ).fetchall()
        cfg = {r['key']: r['value'] for r in rows}
    except Exception:
        cfg = {}
    try:
        keep = max(1, int(cfg.get('auto_backup_keep') or BACKUP_KEEP))
    except (TypeError, ValueError):
        keep = BACKUP_KEEP  # valor não-numérico salvo por engano (ex.: via chamada direta à API) — ignora em vez de derrubar o watchdog
    return {
        'path':    cfg.get('backup_path') or BACKUP_DIR,
        'enabled': cfg.get('auto_backup_enabled', '1') != '0',
        'keep':    keep,
    }

def _do_json_backup(cfg=None):
    import base64
    if cfg is None: cfg = _get_backup_cfg()
    bdir = cfg['path']; os.makedirs(bdir, exist_ok=True)
    name = time.strftime('SIS_SGDP_BACKUP_%Y-%m-%d_%H-%M-%S.json')
    try:
        with get_db() as conn:
            docs  = [dict(r) for r in conn.execute('SELECT * FROM documentos').fetchall()]
            users = [dict(r) for r in conn.execute(
                'SELECT id,username,nome,senha_hash,admin,ativo,departamento,criado_em FROM usuarios').fetchall()]
            conts = [dict(r) for r in conn.execute('SELECT * FROM contadores').fetchall()]
            settings = {r['key']: r['value'] for r in conn.execute('SELECT key,value FROM sys_settings').fetchall()}
            auditoria = [dict(r) for r in conn.execute('SELECT * FROM auditoria').fetchall()]
            arqs = []
            for arq in conn.execute('SELECT * FROM arquivos').fetchall():
                p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if os.path.isfile(p):
                    with open(p, 'rb') as f:
                        arqs.append({**dict(arq), 'data_b64': base64.b64encode(f.read()).decode()})
        backup = {'sgdp_version': '1.17.0', 'exported_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                  'documentos': docs, 'usuarios': users, 'contadores': conts,
                  'arquivos': arqs, 'settings': settings, 'auditoria': auditoria}
        with open(os.path.join(bdir, name), 'w', encoding='utf-8') as f:
            json.dump(backup, f, ensure_ascii=False, default=str)
        print(f'Backup JSON: {name}')
        return name
    except Exception as e:
        print(f'Erro no backup JSON: {e}'); return None

def _do_db_backup(cfg=None):
    if cfg is None: cfg = _get_backup_cfg()
    bdir = cfg['path']; os.makedirs(bdir, exist_ok=True)
    name = time.strftime('DB_SGDP_BACKUP_%Y-%m-%d_%H-%M-%S.db')
    try:
        with sqlite3.connect(DB_PATH, factory=_ConnAutoClose) as src, sqlite3.connect(os.path.join(bdir, name), factory=_ConnAutoClose) as bk:
            src.backup(bk)
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO sys_settings VALUES ('auto_backup_last',?)",
                         (time.strftime('%Y-%m-%dT%H:%M:%S'),))
            conn.commit()
        print(f'Backup DB: {name}')
        return name
    except Exception as e:
        print(f'Erro no backup DB: {e}'); return None

def _rotate_backups(cfg=None):
    if cfg is None: cfg = _get_backup_cfg()
    bdir = cfg['path']; keep = cfg['keep']
    if not os.path.isdir(bdir): return
    for prefix, ext in [('DB_SGDP_BACKUP_', '.db'), ('SIS_SGDP_BACKUP_', '.json')]:
        files = sorted(f for f in os.listdir(bdir) if f.startswith(prefix) and f.endswith(ext))
        for old in files[:-keep]:
            fp = os.path.join(bdir, old)
            for attempt in range(6):  # tenta por até ~10s (OneDrive pode manter o arquivo aberto)
                try:
                    os.remove(fp)
                    print(f'Rotação: removido {old}')
                    break
                except PermissionError:
                    if attempt < 5:
                        time.sleep(2)
                    else:
                        _log.error('Falha ao remover backup %s: arquivo bloqueado. Remova manualmente.', old)
                except Exception as e:
                    _log.error('Falha ao remover backup %s: %s', old, e)
                    break

def _backup_loop():
    while True:
        time.sleep(24 * 3600)
        cfg = _get_backup_cfg()
        if cfg['enabled']:
            _do_db_backup(cfg)
            _rotate_backups(cfg)

def _smtp_send(cfg, msg):
    """Conecta e envia usando as mesmas opções (SSL/STARTTLS/ignorar SSL) do SGCD."""
    import smtplib, ssl
    host, port = cfg.get('smtp_host', ''), int(cfg.get('smtp_port') or 587)
    ctx = ssl.create_default_context()
    if cfg.get('smtp_ignore_ssl') == '1':
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if cfg.get('smtp_secure') == '1':
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as smtp:
            if cfg.get('smtp_user'): smtp.login(cfg['smtp_user'], cfg.get('smtp_pass', ''))
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            if cfg.get('smtp_require_tls', '1') == '1': smtp.starttls(context=ctx)
            if cfg.get('smtp_user'): smtp.login(cfg['smtp_user'], cfg.get('smtp_pass', ''))
            smtp.send_message(msg)

def _send_plain_email(cfg, to, subject, body):
    from email.message import EmailMessage
    if not cfg.get('smtp_host') or not to: return False
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f"{cfg.get('smtp_from_name') or 'SGDP'} <{cfg.get('smtp_user')}>"
    msg['To'] = to
    msg.set_content(body)
    _smtp_send(cfg, msg)
    return True

def _lembrete_notify_loop():
    # ponytail: checa a cada hora; lembretes vencidos são avisados uma vez (notificado_em marcado)
    while True:
        try:
            cfg = get_config()
            hoje = time.strftime('%Y-%m-%d')
            with get_db() as conn:
                rows = conn.execute(
                    '''SELECT l.*, u.email criador_email FROM lembretes l
                       LEFT JOIN usuarios u ON l.criado_por=u.id
                       WHERE l.concluido=0 AND l.notificado_em IS NULL AND l.data_prazo<=?''',
                    (hoje,)).fetchall()
                for r in rows:
                    destino = (r['criador_email'] or '').strip() or cfg.get('smtp_to', '').strip()
                    if destino:
                        try:
                            _send_plain_email(cfg, destino, f'Lembrete SGDP vencendo: {r["titulo"]}',
                                               f'O lembrete "{r["titulo"]}" tem prazo em {r["data_prazo"]}.')
                        except Exception as e:
                            _log.error('Falha ao notificar lembrete %s: %s', r['id'], e)
                    conn.execute('UPDATE lembretes SET notificado_em=? WHERE id=?',
                                 (time.strftime('%Y-%m-%dT%H:%M:%S'), r['id']))
                conn.commit()
        except Exception as e:
            _log.error('Erro no loop de notificação de lembretes: %s', e)
        time.sleep(3600)

def _send_daily_summary():
    """Resumo diário por e-mail de lembretes vencidos/vencendo em breve.
    Só envia se SMTP estiver configurado no servidor e ainda não tiver enviado hoje."""
    cfg = get_config()
    if not (cfg.get('smtp_host') and cfg.get('smtp_user') and cfg.get('smtp_pass') and cfg.get('smtp_to')):
        return
    hoje = time.strftime('%Y-%m-%d')
    if cfg.get('alert_email_last_sent') == hoje:
        return

    with get_db() as conn:
        rows = conn.execute(
            "SELECT titulo, data_prazo FROM lembretes WHERE concluido=0 ORDER BY data_prazo ASC"
        ).fetchall()

    agora = time.strftime('%Y-%m-%d')
    vencidos, vencendo = [], []
    for r in rows:
        try:
            dias = (time.mktime(time.strptime(r['data_prazo'], '%Y-%m-%d')) - time.mktime(time.strptime(agora, '%Y-%m-%d'))) / 86400
            dias = round(dias)
        except Exception:
            continue
        if dias < 0:
            vencidos.append((r['titulo'], -dias))
        elif dias <= 7:
            vencendo.append((r['titulo'], dias))

    if not vencidos and not vencendo:
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO sys_settings (key,value) VALUES ('alert_email_last_sent',?)", (hoje,))
            conn.commit()
        return

    linhas = []
    if vencidos:
        linhas.append('Vencidos:')
        for titulo, dias in sorted(vencidos, key=lambda x: -x[1]):
            linhas.append(f'  - {titulo} — vencido há {dias} dia(s)')
    if vencendo:
        linhas.append('Vencendo em breve:')
        for titulo, dias in sorted(vencendo, key=lambda x: x[1]):
            txt = 'vence hoje' if dias == 0 else f'vence em {dias} dia(s)'
            linhas.append(f'  - {titulo} — {txt}')
    corpo = f'Resumo automático do SGDP — {hoje}\n\n' + '\n'.join(linhas)

    try:
        _send_plain_email(cfg, cfg['smtp_to'], f'SGDP — Resumo de pendências ({hoje})', corpo)
        print(f'  [ALERTAS] E-mail de resumo enviado ({len(vencidos)} vencido(s), {len(vencendo)} vencendo)', flush=True)
    except Exception as e:
        _log.error('Falha ao enviar e-mail de resumo diário: %s', e)
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO sys_settings (key,value) VALUES ('alert_email_last_sent',?)", (hoje,))
        conn.commit()

def _watchdog():
    # Limpa sessões expiradas a cada 5s e dispara o backup pós-sessão
    # (_check_shutdown — não encerra mais o servidor, só faz backup).
    # SESSION_TTL=60s dá folga de sobra sobre o ping a cada 5s: um TTL curto
    # (era 15s) expirava sessões à toa quando o ping atrasava por qualquer
    # motivo comum — carregamento inicial da página disputando conexão HTTP
    # com várias outras chamadas simultâneas, ou a aba principal perdendo
    # foco ao abrir um popup de documento.
    while True:
        time.sleep(5)
        if _watchdog_paused:
            continue
        sgx_base.purge_expired_sessions(get_db)
        try: _check_shutdown()
        except Exception as e: _log.error('Erro em _check_shutdown: %s', e)
        try: _send_daily_summary()
        except Exception as e: _log.error('Erro ao enviar resumo diário: %s', e)

# ── Inicialização ─────────────────────────────────────────────────────────────

def _find_browser():
    for c in [
        os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%LocalAppData%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
    ]:
        if os.path.isfile(c): return c
    return None

def _check_db_integrity():
    try:
        with get_db() as conn:
            result = conn.execute('PRAGMA integrity_check').fetchone()[0]
            if result != 'ok':
                _log.error('INTEGRITY CHECK FALHOU: %s', result)
                print(f'[AVISO] Banco de dados com problema de integridade: {result}')
            else:
                print('[DB] Integridade verificada: ok')
    except Exception as e:
        _log.error('Erro ao verificar integridade do banco: %s', e)

def _selecionar_modo():
    print()
    print('  ╔══════════════════════════════════════════════════╗')
    print('  ║   SGDP — Gestão de Documentos da Procuradoria    ║')
    print('  ╚══════════════════════════════════════════════════╝')
    print()
    print('  [1] Diagnóstico     — Verifica rede, firewall e acessibilidade')
    print('  [2] Iniciar Servidor')
    print()
    if not sys.stdin.isatty():
        op = '2'
    else:
        while True:
            try:
                op = input('  Opção [1/2]: ').strip()
            except (EOFError, KeyboardInterrupt):
                op = '2'
            if op in ('1', '2'):
                break
            print('  Digite 1 ou 2.')
    if op == '1':
        import importlib.util, pathlib
        diag = pathlib.Path(__file__).parent / 'diagnostico.py'
        spec = importlib.util.spec_from_file_location('diagnostico', diag)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.info_maquina()
        srv_ativo = mod.checar_porta()
        regra_fw  = mod.checar_firewall()
        mod.checar_conectividade(socket.gethostbyname(socket.gethostname()), srv_ativo)
        mod.resumo(socket.gethostbyname(socket.gethostname()), srv_ativo, regra_fw)
        input('  Pressione Enter para fechar...')
        sys.exit(0)
    print()
    print('  ─────────────────────────────────────────────────────────')

if __name__ == '__main__':
    _selecionar_modo()
    init_db()
    _check_db_integrity()
    _rotate_backups(_get_backup_cfg())
    threading.Thread(target=_watchdog,     daemon=True).start()
    threading.Thread(target=_backup_loop,  daemon=True).start()
    threading.Thread(target=_lembrete_notify_loop, daemon=True).start()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('', PORT), SGDPHandler) as srv:
        print(f'  Servidor: http://localhost:{PORT}/SGDP.html')
        import socket as _socket
        try:
            ip_local = _socket.gethostbyname(_socket.gethostname())
        except Exception:
            ip_local = 'desconhecido'
        print(f'  Rede:     http://{ip_local}:{PORT}/SGDP.html')
        print()

        browser = _find_browser()
        if browser:
            profile_dir = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), 'SGDP-Profile')
            subprocess.Popen([
                browser,
                f'--app=http://localhost:{PORT}/SGDP.html',
                '--start-maximized',
                '--disable-background-mode',
                f'--user-data-dir={profile_dir}',
            ])
            print('  App aberto no navegador.')
        else:
            print(f'  Chrome/Edge não encontrado. Abra manualmente: http://localhost:{PORT}/SGDP.html')

        print('  Aguardando conexões... (Ctrl+C para encerrar)')
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print('\n  Encerrando servidor...')
