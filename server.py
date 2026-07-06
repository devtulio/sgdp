# SGDP v1.12.3 — Servidor local: SQLite, autenticação, REST API, uploads de PDF
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
import logging
import mimetypes
from urllib.parse import urlparse, parse_qs

PORT              = 3001
_BASE             = os.path.dirname(os.path.abspath(__file__))
DB_PATH           = os.path.join(_BASE, 'sgdp.db')
UPLOADS_DIR       = os.path.join(_BASE, 'uploads')
BACKUP_DIR        = os.path.join(_BASE, 'backups')
LOG_PATH          = os.path.join(_BASE, 'sgdp_errors.log')
BACKUP_KEEP       = 7
SESSION_TTL       = 15   # 15s — renovado pelo ping a cada 5s; expira rápido se browser fechar
MAX_UPLOAD_SIZE   = 50 * 1024 * 1024

logging.basicConfig(
    filename=LOG_PATH, level=logging.ERROR,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)
_log = logging.getLogger('sgdp')

os.chdir(_BASE)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR,  exist_ok=True)

_had_session      = False   # True após primeiro login; evita encerramento antes de qualquer usuário logar
_modo_servidor    = False   # True = modo servidor contínuo (sem encerramento automático)
_backup_pos_sess  = False   # True = backup pós-sessão já executado; aguarda nova sessão para resetar

TIPOS = ('lei', 'decreto', 'portaria', 'parecer', 'oficio')

# ── Banco de dados ────────────────────────────────────────────────────────────

class _ConnAutoClose(sqlite3.Connection):
    """sqlite3.Connection.__exit__ só faz commit/rollback da transação — não fecha
    a conexão. Sem isso, todo `with get_db() as conn:` vaza uma conexão aberta por
    chamada. Fecha a conexão junto, sem precisar alterar nenhum call site."""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

def get_db():
    conn = sqlite3.connect(DB_PATH, factory=_ConnAutoClose)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

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
                criado_em    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_docs_tipo ON documentos(tipo);
            CREATE INDEX IF NOT EXISTS idx_docs_ano  ON documentos(ano);
            CREATE INDEX IF NOT EXISTS idx_audit_em  ON auditoria(em);
            CREATE INDEX IF NOT EXISTS idx_lembretes_prazo ON lembretes(data_prazo);
        ''')
        conn.executemany('INSERT OR IGNORE INTO sys_settings VALUES (?,?)', [
            ('orgao_nome',           'Procuradoria-Geral'),
            ('municipio',            ''),
            ('backup_path',          BACKUP_DIR),
            ('auto_backup_enabled',  '1'),
            ('auto_backup_keep',     str(BACKUP_KEEP)),
            ('smtp_host', ''), ('smtp_port', '587'), ('smtp_user', ''),
            ('smtp_pass', ''), ('smtp_from', ''), ('smtp_tls', '1'),
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
        # Sessões são descartadas a cada início do servidor (evita sessões órfãs)
        conn.execute('DELETE FROM sessions')
        conn.commit()
        if conn.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0] == 0:
            conn.execute(
                'INSERT INTO usuarios (username,nome,senha_hash,admin) VALUES (?,?,?,1)',
                ('admin', 'Administrador', _hash_password('sgdp2024'))
            )
            conn.commit()
            print('Usuário padrão criado: admin / sgdp2024 — troque a senha nas Configurações.')

# ── Segurança ─────────────────────────────────────────────────────────────────

def get_config():
    with get_db() as conn:
        return {r['key']: r['value'] for r in conn.execute('SELECT key,value FROM sys_settings').fetchall()}

def _hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100_000)
    return f'{salt}:{dk.hex()}'

def _verify_password(password, stored):
    try:
        salt, _ = stored.split(':', 1)
        return secrets.compare_digest(_hash_password(password, salt), stored)
    except Exception:
        return False

def create_session(user_id):
    token = secrets.token_urlsafe(32)
    expires = time.time() + SESSION_TTL
    with get_db() as conn:
        conn.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
        conn.execute('INSERT INTO sessions (token,user_id,expires) VALUES (?,?,?)',
                     (token, user_id, expires))
    return token

def get_session(token):
    if not token:
        return None
    with get_db() as conn:
        row = conn.execute(
            '''SELECT s.token, s.user_id, s.expires,
                      u.nome, u.username, u.admin, u.ativo
               FROM sessions s JOIN usuarios u ON u.id=s.user_id
               WHERE s.token=? AND s.expires>? AND u.ativo=1''',
            (token, time.time())
        ).fetchone()
    return dict(row) if row else None

def delete_session(token):
    with get_db() as conn:
        conn.execute('DELETE FROM sessions WHERE token=?', (token,))

def renew_session(token):
    with get_db() as conn:
        conn.execute('UPDATE sessions SET expires=? WHERE token=?',
                     (time.time() + SESSION_TTL, token))

def active_sessions():
    with get_db() as conn:
        return conn.execute('SELECT COUNT(*) FROM sessions WHERE expires>?', (time.time(),)).fetchone()[0]

def _check_shutdown():
    """Encerra o servidor quando não há mais sessões ativas (último logout).
    No modo servidor contínuo (_modo_servidor=True), apenas faz backup sem encerrar."""
    global _backup_pos_sess
    if _modo_servidor:
        if _had_session and active_sessions() == 0 and not _backup_pos_sess:
            _backup_pos_sess = True
            cfg = _get_backup_cfg()
            if cfg['enabled']:
                print('\nÚltima sessão encerrada. Executando backup automático...')
                _do_json_backup(cfg)
                _do_db_backup(cfg)
        return
    if not _had_session:
        return
    if active_sessions() > 0:
        return
    print('\nÚltima sessão encerrada. Executando backup e encerrando servidor...')
    cfg = _get_backup_cfg()
    if cfg['enabled']:
        _do_json_backup(cfg)
        _do_db_backup(cfg)
    try:
        with sqlite3.connect(DB_PATH) as c:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except Exception:
        pass
    os._exit(0)

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

    def do_GET(self):
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
                        "SELECT key,value FROM sys_settings WHERE key IN ('orgao','municipio','cnpj_orgao')"
                    ).fetchall()
                self._json(200, {r['key']: r['value'] for r in rows})
            except Exception:
                self._json(200, {})
        elif p.startswith('/api/'):
            s = self._auth()
            if s: self._route_get(p, qs, s)
        else:
            if p in ('', '/'):
                self.path = '/SGDP.html'
            super().do_GET()

    def do_POST(self):
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
        p = urlparse(self.path).path.rstrip('/')
        s = self._auth()
        if not s: return
        self._route_put(p, self._body(), s)

    def do_DELETE(self):
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
            self._json(200, {'id': s['user_id'], 'username': s['username'], 'nome': s['nome'], 'admin': bool(s['admin'])})

        elif p == '/api/documentos':
            self._list_docs(qs, s)
        elif re.fullmatch(r'/api/documentos/\d+', p):
            self._get_doc(int(p.split('/')[-1]))

        elif p == '/api/lixeira':
            self._list_lixeira(qs, s)

        elif p == '/api/lembretes':
            self._list_lembretes(qs, s)

        elif p == '/api/config/smtp':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            cfg = get_config()
            self._json(200, {k: cfg.get(k, '') for k in
                             ('smtp_host','smtp_port','smtp_user','smtp_from','smtp_tls')})

        elif re.fullmatch(r'/api/arquivos/\d+', p):
            self._download_arquivo(int(p.split('/')[-1]), qs)

        elif p == '/api/contadores':
            self._get_contadores(qs)

        elif p == '/api/dashboard':
            self._dashboard()

        elif p == '/api/relatorio':
            self._relatorio(qs)

        elif p == '/api/usuarios':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            with get_db() as conn:
                rows = conn.execute('SELECT id,username,nome,admin,ativo,criado_em FROM usuarios ORDER BY nome').fetchall()
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

        elif p == '/api/config':
            cfg = get_config()
            self._json(200, {'orgao_nome': cfg.get('orgao_nome',''), 'municipio': cfg.get('municipio',''),
                             'auto_backup_enabled': cfg.get('auto_backup_enabled','1'),
                             'auto_backup_keep': cfg.get('auto_backup_keep', str(BACKUP_KEEP)),
                             'backup_path': cfg.get('backup_path', BACKUP_DIR)})

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

        elif re.fullmatch(r'/api/documentos/\d+/email', p):
            self._enviar_email(int(p.split('/')[3]), self._body(), s)

        elif re.fullmatch(r'/api/lixeira/\d+/restaurar', p):
            self._restaurar_doc(int(p.split('/')[3]), s)

        elif p == '/api/lembretes':
            self._create_lembrete(self._body(), s)

        elif p == '/api/import/csv':
            self._import_csv(self._body(), s)

        elif p == '/api/usuarios':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._create_usuario(self._body(), s)

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
        else:
            self._json(404, {'error': 'Rota não encontrada'})

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _login(self, body):
        data = json.loads(body) if body else {}
        username = data.get('username', '').strip()
        password = data.get('password', '')
        if not username or not password:
            self._json(400, {'error': 'Usuário e senha são obrigatórios'}); return
        with get_db() as conn:
            row = conn.execute('SELECT * FROM usuarios WHERE username=? AND ativo=1', (username,)).fetchone()
        if not row or not _verify_password(password, row['senha_hash']):
            self._json(401, {'error': 'Usuário ou senha incorretos'}); return
        global _had_session, _backup_pos_sess
        _had_session = True
        _backup_pos_sess = False  # nova sessão — permite backup ao próximo logout
        token = create_session(row['id'])
        with get_db() as conn:
            audit(conn, row['id'], row['nome'], 'login', detalhes=f"Acesso de {self.client_address[0]}")
            conn.commit()
        self._json(200, {
            'token': token,
            'user': {'id': row['id'], 'username': row['username'], 'nome': row['nome'], 'admin': bool(row['admin'])}
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
            where.append('(d.ementa LIKE ? OR d.partes LIKE ? OR CAST(d.numero AS TEXT) LIKE ?)')
            params += [f'%{search}%'] * 3
        if ano:    where.append('d.ano=?');    params.append(int(ano))
        if qp('sem_pdf'): where.append('d.arquivo_id IS NULL')
        w = 'WHERE ' + ' AND '.join(where)

        with get_db() as conn:
            total = conn.execute(f'SELECT COUNT(*) FROM documentos d {w}', params).fetchone()[0]
            rows  = conn.execute(
                f'''SELECT d.*, u1.nome criado_por_nome, u2.nome atualizado_por_nome,
                           a.nome_original arquivo_nome, a.tamanho arquivo_tamanho
                    FROM documentos d
                    LEFT JOIN usuarios u1 ON d.criado_por=u1.id
                    LEFT JOIN usuarios u2 ON d.atualizado_por=u2.id
                    LEFT JOIN arquivos a ON d.arquivo_id=a.id
                    {w} ORDER BY d.ano DESC, d.numero DESC LIMIT ? OFFSET ?''',
                params + [per, (page-1)*per]
            ).fetchall()
        self._json(200, {'total': total, 'page': page, 'per': per, 'items': [dict(r) for r in rows]})

    def _get_doc(self, did):
        with get_db() as conn:
            row = conn.execute(
                '''SELECT d.*, u1.nome criado_por_nome, u2.nome atualizado_por_nome,
                          a.nome_original arquivo_nome, a.tamanho arquivo_tamanho
                   FROM documentos d
                   LEFT JOIN usuarios u1 ON d.criado_por=u1.id
                   LEFT JOIN usuarios u2 ON d.atualizado_por=u2.id
                   LEFT JOIN arquivos a ON d.arquivo_id=a.id
                   WHERE d.id=?''', (did,)
            ).fetchone()
        if not row: self._json(404, {'error': 'Não encontrado'}); return
        self._json(200, dict(row))

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
                    'INSERT INTO documentos (tipo,numero,ano,data,ementa,partes,observacoes,assunto,processo_pa,processo_tipo,processo_ref,ato_tipo,cargo,criado_por,atualizado_por)'
                    ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (tipo, numero, ano, data_d, ementa,
                     data.get('partes') or '', data.get('observacoes') or '',
                     data.get('assunto') or 'Outros',
                     data.get('processo_pa') or '', data.get('processo_tipo') or '', data.get('processo_ref') or '',
                     data.get('ato_tipo') or '', data.get('cargo') or '',
                     s['user_id'], s['user_id'])
                )
                # captura o rowid ANTES de bump_contador — que pode fazer seu próprio
                # INSERT em contadores na primeira vez que o tipo/ano é usado, o que
                # sobrescreveria um last_insert_rowid() consultado depois dele
                did = cur.lastrowid
                bump_contador(conn, tipo, ano, numero)
                audit(conn, s['user_id'], s['nome'], 'criar', did, f"{tipo} nº {numero}/{ano}")
                conn.commit()
            except sqlite3.IntegrityError:
                self._json(409, {'error': f'Já existe {tipo} nº {numero}/{ano}'}); return
            row = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
        self._json(201, dict(row))

    def _update_doc(self, did, body, s):
        data = json.loads(body) if body else {}
        with get_db() as conn:
            row = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            if not row: self._json(404, {'error': 'Não encontrado'}); return
            fields = {'atualizado_por': s['user_id'], 'atualizado_em': time.strftime('%Y-%m-%dT%H:%M:%S')}
            for f in ('ementa', 'partes', 'observacoes', 'data', 'assunto', 'processo_pa', 'processo_tipo', 'processo_ref', 'ato_tipo', 'cargo'):
                if f in data: fields[f] = data[f]
            if 'numero' in data: fields['numero'] = int(data['numero'])
            if 'ano'    in data: fields['ano']    = int(data['ano'])
            try:
                conn.execute(f"UPDATE documentos SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                             list(fields.values()) + [did])
                if 'numero' in fields:
                    bump_contador(conn, row['tipo'], fields.get('ano', row['ano']), fields['numero'])
                audit(conn, s['user_id'], s['nome'], 'editar', did)
                conn.commit()
            except sqlite3.IntegrityError:
                self._json(409, {'error': 'Número/ano já existe para este tipo'}); return
            updated = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
        self._json(200, dict(updated))

    def _delete_doc(self, did, s):
        with get_db() as conn:
            row = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            if not row: self._json(404, {'error': 'Não encontrado'}); return
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

    def _relatorio(self, qs):
        def qp(k, d=None): v = qs.get(k); return v[0] if v else d
        de  = qp('de',  '1900-01-01')
        ate = qp('ate', '2999-12-31')
        with get_db() as conn:
            total = conn.execute(
                'SELECT COUNT(*) FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL', (de, ate)).fetchone()[0]
            por_tipo = [dict(r) for r in conn.execute(
                'SELECT tipo, COUNT(*) n FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL GROUP BY tipo ORDER BY n DESC',
                (de, ate)).fetchall()]
            por_assunto = [dict(r) for r in conn.execute(
                'SELECT assunto, COUNT(*) n FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL GROUP BY assunto ORDER BY n DESC',
                (de, ate)).fetchall()]
            por_mes = [dict(r) for r in conn.execute(
                "SELECT strftime('%Y-%m', data) mes, COUNT(*) n FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL GROUP BY mes ORDER BY mes",
                (de, ate)).fetchall()]
            docs = [dict(r) for r in conn.execute(
                'SELECT id,tipo,numero,ano,data,ementa,assunto FROM documentos WHERE data BETWEEN ? AND ? AND excluido_em IS NULL ORDER BY data DESC, id DESC LIMIT 200',
                (de, ate)).fetchall()]
        self._json(200, {'total': total, 'por_tipo': por_tipo, 'por_assunto': por_assunto, 'por_mes': por_mes, 'documentos': docs})

    # ── Agenda / Lembretes ────────────────────────────────────────────────────

    def _list_lembretes(self, qs, s):
        so_pendentes = qs.get('pendentes', [None])[0]
        w = 'WHERE l.concluido=0' if so_pendentes else ''
        with get_db() as conn:
            rows = conn.execute(
                f'''SELECT l.*, d.tipo doc_tipo, d.numero doc_numero, d.ano doc_ano
                    FROM lembretes l LEFT JOIN documentos d ON l.documento_id=d.id
                    {w} ORDER BY l.data_prazo ASC'''
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
        allowed = ('smtp_host','smtp_port','smtp_user','smtp_pass','smtp_from','smtp_tls')
        with get_db() as conn:
            for k in allowed:
                if k in data:
                    conn.execute('INSERT INTO sys_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                 (k, str(data[k])))
            audit(conn, s['user_id'], s['nome'], 'alterar_smtp')
            conn.commit()
        self._json(200, {'ok': True})

    def _enviar_email(self, did, body, s):
        data = json.loads(body) if body else {}
        destinatario = (data.get('to') or '').strip()
        assunto      = (data.get('subject') or '').strip()
        corpo        = data.get('body') or ''
        if not destinatario: self._json(400, {'error': 'Destinatário obrigatório'}); return
        cfg = get_config()
        host, port = cfg.get('smtp_host',''), int(cfg.get('smtp_port') or 587)
        if not host: self._json(400, {'error': 'SMTP não configurado. Configure em Configurações → Segurança.'}); return
        with get_db() as conn:
            doc = conn.execute(
                'SELECT d.*, a.nome_original, a.nome_disco FROM documentos d LEFT JOIN arquivos a ON d.arquivo_id=a.id WHERE d.id=?',
                (did,)).fetchone()
        if not doc: self._json(404, {'error': 'Documento não encontrado'}); return
        try:
            import smtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg['Subject'] = assunto or f"{doc['tipo'].capitalize()} nº {doc['numero']}/{doc['ano']}"
            msg['From'] = cfg.get('smtp_from') or cfg.get('smtp_user')
            msg['To'] = destinatario
            msg.set_content(corpo or doc['ementa'])
            if doc['nome_disco']:
                fp = os.path.join(UPLOADS_DIR, doc['nome_disco'])
                if os.path.isfile(fp):
                    with open(fp, 'rb') as f:
                        msg.add_attachment(f.read(), maintype='application', subtype='pdf',
                                           filename=doc['nome_original'] or 'documento.pdf')
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if cfg.get('smtp_tls', '1') == '1': smtp.starttls()
                if cfg.get('smtp_user'): smtp.login(cfg['smtp_user'], cfg.get('smtp_pass',''))
                smtp.send_message(msg)
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
            doc = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            if not doc: self._json(404, {'error': 'Documento não encontrado'}); return
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

    def _remove_arquivo(self, did, s):
        with get_db() as conn:
            doc = conn.execute('SELECT * FROM documentos WHERE id=?', (did,)).fetchone()
            if not doc or not doc['arquivo_id']:
                self._json(404, {'error': 'Sem arquivo para remover'}); return
            arq = conn.execute('SELECT * FROM arquivos WHERE id=?', (doc['arquivo_id'],)).fetchone()
            if arq:
                p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if os.path.isfile(p): os.remove(p)
                conn.execute('DELETE FROM arquivos WHERE id=?', (arq['id'],))
            conn.execute('UPDATE documentos SET arquivo_id=NULL WHERE id=?', (did,))
            audit(conn, s['user_id'], s['nome'], 'remover_arquivo', did)
            conn.commit()
        self._json(200, {'ok': True})

    def _download_arquivo(self, aid, qs):
        with get_db() as conn:
            arq = conn.execute('SELECT * FROM arquivos WHERE id=?', (aid,)).fetchone()
        if not arq: self._json(404, {'error': 'Não encontrado'}); return
        filepath = os.path.join(UPLOADS_DIR, arq['nome_disco'])
        if not os.path.isfile(filepath): self._json(404, {'error': 'Arquivo não encontrado no disco'}); return
        with open(filepath, 'rb') as f:
            data = f.read()
        inline = (qs.get('inline') or ['0'])[0] == '1'
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/pdf')
        self.send_header('Content-Disposition', 'inline' if inline else f'attachment; filename="{arq["nome_original"]}"')
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

    def _dashboard(self):
        ano = time.localtime().tm_year
        with get_db() as conn:
            totais   = {t: conn.execute('SELECT COUNT(*) FROM documentos WHERE tipo=? AND excluido_em IS NULL', (t,)).fetchone()[0] for t in TIPOS}
            ano_atual = {t: conn.execute('SELECT COUNT(*) FROM documentos WHERE tipo=? AND ano=? AND excluido_em IS NULL', (t, ano)).fetchone()[0] for t in TIPOS}
            recentes = conn.execute(
                '''SELECT d.id, d.tipo, d.numero, d.ano, d.data, d.ementa, d.arquivo_id, u.nome criado_por_nome
                   FROM documentos d LEFT JOIN usuarios u ON d.criado_por=u.id
                   WHERE d.excluido_em IS NULL
                   ORDER BY d.criado_em DESC LIMIT 10'''
            ).fetchall()
        self._json(200, {'totais': totais, 'ano_atual': ano_atual, 'recentes': [dict(r) for r in recentes]})

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
        try:
            with get_db() as conn:
                conn.execute('INSERT INTO usuarios (username,nome,senha_hash,admin) VALUES (?,?,?,?)',
                             (username, nome, _hash_password(senha), int(bool(data.get('admin')))))
                uid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                audit(conn, s['user_id'], s['nome'], 'criar_usuario', detalhes=f"@{username} ({nome})")
                conn.commit()
            self._json(201, {'id': uid, 'username': username, 'nome': nome, 'admin': bool(data.get('admin'))})
        except sqlite3.IntegrityError:
            self._json(409, {'error': f'Usuário "{username}" já existe'})

    def _update_usuario(self, uid, body, s):
        data = json.loads(body) if body else {}
        fields = {}
        if 'nome'  in data: fields['nome']  = data['nome'].strip()
        if 'admin' in data: fields['admin'] = int(bool(data['admin']))
        if 'ativo' in data: fields['ativo'] = int(bool(data['ativo']))
        if data.get('senha'):
            if len(data['senha']) < 6: self._json(400, {'error': 'Senha mínima: 6 caracteres'}); return
            fields['senha_hash'] = _hash_password(data['senha'])
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
            conn.execute('DELETE FROM usuarios WHERE id=?', (uid,))
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
        allowed = ('orgao_nome', 'municipio', 'backup_path', 'auto_backup_enabled', 'auto_backup_keep')
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
            with sqlite3.connect(tmp.name) as tc:
                tables = {r[0] for r in tc.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if not {'documentos', 'arquivos', 'usuarios'}.issubset(tables):
                self._json(400, {'error': 'Banco inválido: tabelas obrigatórias ausentes'}); return
            _do_db_backup()  # backup do atual antes de restaurar
            with sqlite3.connect(tmp.name) as src, sqlite3.connect(DB_PATH) as dst:
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
                'SELECT id,username,nome,senha_hash,admin,ativo,criado_em FROM usuarios').fetchall()]
            conts = [dict(r) for r in conn.execute('SELECT * FROM contadores').fetchall()]
            arqs  = []
            for arq in conn.execute('SELECT * FROM arquivos').fetchall():
                p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if os.path.isfile(p):
                    with open(p, 'rb') as f:
                        arqs.append({**dict(arq), 'data_b64': base64.b64encode(f.read()).decode()})
        backup = {'sgdp_version': '1.12.3', 'exported_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                  'documentos': docs, 'usuarios': users, 'contadores': conts, 'arquivos': arqs}
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
            for arq in backup.get('arquivos', []):
                nome_disco = f"{secrets.token_hex(16)}.pdf"
                with open(os.path.join(UPLOADS_DIR, nome_disco), 'wb') as f:
                    f.write(base64.b64decode(arq['data_b64']))
                conn.execute('INSERT INTO arquivos (id,nome_original,nome_disco,tamanho,enviado_por,enviado_em) VALUES (?,?,?,?,?,?)',
                             (arq['id'], arq['nome_original'], nome_disco, arq['tamanho'], arq.get('enviado_por'), arq.get('enviado_em')))
            for doc in backup.get('documentos', []):
                conn.execute(
                    'INSERT OR REPLACE INTO documentos '
                    '(id,tipo,numero,ano,data,ementa,partes,observacoes,arquivo_id,criado_por,atualizado_por,criado_em,atualizado_em)'
                    ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (doc['id'],doc['tipo'],doc['numero'],doc['ano'],doc['data'],doc['ementa'],
                     doc.get('partes'),doc.get('observacoes'),doc.get('arquivo_id'),
                     doc.get('criado_por'),doc.get('atualizado_por'),doc.get('criado_em'),doc.get('atualizado_em')))
            for c in backup.get('contadores', []):
                conn.execute('INSERT OR REPLACE INTO contadores VALUES (?,?,?)', (c['tipo'],c['ano'],c['ultimo']))
            for u in backup.get('usuarios', []):
                conn.execute('INSERT OR REPLACE INTO usuarios (id,username,nome,senha_hash,admin,ativo,criado_em) VALUES (?,?,?,?,?,?,?)',
                             (u['id'],u['username'],u['nome'],u['senha_hash'],u['admin'],u.get('ativo',1),u.get('criado_em')))
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

    def _sync_preview(self):
        backup = self._read_backup_body()
        if backup is None: return
        novos, conflitos = self._diff_sync(backup)
        self._json(200, {
            'novos': len(novos), 'conflitos': conflitos,
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
                        'processo_pa,processo_tipo,processo_ref,ato_tipo,cargo,criado_por,atualizado_por)'
                        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (doc['tipo'], doc['numero'], doc['ano'], doc['data'], doc['ementa'],
                         doc.get('partes') or '', doc.get('observacoes') or '', doc.get('assunto') or 'Outros',
                         doc.get('processo_pa') or '', doc.get('processo_tipo') or '', doc.get('processo_ref') or '',
                         doc.get('ato_tipo') or '', doc.get('cargo') or '', s['user_id'], s['user_id'])
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
            audit(conn, s['user_id'], s['nome'], 'sincronizar_backup',
                  detalhes=f"{n_novos} novos, {n_conflitos} conflitos resolvidos")
            conn.commit()
        self._json(200, {'novos': n_novos, 'conflitos_aplicados': n_conflitos})

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

    def handle_error(self, request, client_address):
        import traceback
        _log.error('Erro na requisição de %s:\n%s', client_address, traceback.format_exc())

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
    return {
        'path':    cfg.get('backup_path') or BACKUP_DIR,
        'enabled': cfg.get('auto_backup_enabled', '1') != '0',
        'keep':    max(1, int(cfg.get('auto_backup_keep') or BACKUP_KEEP)),
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
                'SELECT id,username,nome,senha_hash,admin,ativo,criado_em FROM usuarios').fetchall()]
            conts = [dict(r) for r in conn.execute('SELECT * FROM contadores').fetchall()]
            settings = {r['key']: r['value'] for r in conn.execute('SELECT key,value FROM sys_settings').fetchall()}
            arqs = []
            for arq in conn.execute('SELECT * FROM arquivos').fetchall():
                p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if os.path.isfile(p):
                    with open(p, 'rb') as f:
                        arqs.append({**dict(arq), 'data_b64': base64.b64encode(f.read()).decode()})
        backup = {'sgdp_version': '1.12.3', 'exported_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                  'documentos': docs, 'usuarios': users, 'contadores': conts,
                  'arquivos': arqs, 'settings': settings}
        with open(os.path.join(bdir, name), 'w', encoding='utf-8') as f:
            json.dump(backup, f, ensure_ascii=False, default=str)
        print(f'Backup JSON: {name}')
        return name
    except Exception as e:
        print(f'Erro no backup JSON: {e}'); return None

def _do_db_backup(cfg=None):
    if cfg is None: cfg = _get_backup_cfg()
    bdir = cfg['path']; os.makedirs(bdir, exist_ok=True)
    name = time.strftime('SIS_SGDP_BACKUP_%Y-%m-%d_%H-%M-%S.db')
    try:
        with sqlite3.connect(DB_PATH) as src, sqlite3.connect(os.path.join(bdir, name)) as bk:
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

def _watchdog():
    # Limpa sessões expiradas a cada 5s e verifica encerramento.
    # Com SESSION_TTL=15s e ping a cada 5s, um browser fechado sem logout
    # causa encerramento do servidor em no máximo ~20 segundos.
    while True:
        time.sleep(5)
        with get_db() as conn:
            conn.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        _check_shutdown()

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
    global _modo_servidor
    print()
    print('  ╔══════════════════════════════════════════════════╗')
    print('  ║   SGDP — Gestão de Documentos da Procuradoria    ║')
    print('  ╚══════════════════════════════════════════════════╝')
    print()
    print('  Selecione o modo de operação:')
    print()
    print('  [1] Pessoal   — Uso individual no próprio computador')
    print('                  Abre o app automaticamente no navegador')
    print('                  Encerra quando o último usuário sair')
    print()
    print('  [2] Servidor  — Máquina central / acesso pela rede')
    print('                  Não abre navegador automaticamente')
    print('                  Fica rodando continuamente (Ctrl+C para parar)')
    print()
    print('  [3] Diagnóstico — Verifica rede, firewall e acessibilidade')
    print()
    while True:
        try:
            op = input('  Opção [1/2/3]: ').strip()
        except (EOFError, KeyboardInterrupt):
            op = '1'
        if op in ('1', '2', '3'):
            break
        print('  Digite 1, 2 ou 3.')
    if op == '3':
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
    _modo_servidor = (op == '2')
    print()
    print(f'  Modo: {"SERVIDOR CONTÍNUO" if _modo_servidor else "PESSOAL"}')
    print('  ─────────────────────────────────────────────────────────')

if __name__ == '__main__':
    _selecionar_modo()
    init_db()
    _check_db_integrity()
    _rotate_backups(_get_backup_cfg())
    threading.Thread(target=_watchdog,     daemon=True).start()
    threading.Thread(target=_backup_loop,  daemon=True).start()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('', PORT), SGDPHandler) as srv:
        print(f'  Servidor: http://localhost:{PORT}/SGDP.html')

        if _modo_servidor:
            import socket as _socket
            try:
                ip_local = _socket.gethostbyname(_socket.gethostname())
            except Exception:
                ip_local = 'desconhecido'
            print(f'  Rede:     http://{ip_local}:{PORT}/SGDP.html')
            print()
            print('  Aguardando conexões... (Ctrl+C para encerrar)')
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                print('\n  Encerrando servidor...')
        else:
            browser = _find_browser()
            if browser:
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                time.sleep(1)
                profile_dir = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), 'SGDP-Profile')
                proc = subprocess.Popen([
                    browser,
                    f'--app=http://localhost:{PORT}/SGDP.html',
                    '--start-maximized',
                    '--disable-background-mode',
                    f'--user-data-dir={profile_dir}',
                ])
                print('  App aberto. Feche a janela do SGDP para encerrar.')
                proc.wait()
                print('  Encerrando servidor...')
                while True: time.sleep(1)
            else:
                print(f'  Chrome/Edge não encontrado. Abra manualmente: http://localhost:{PORT}/SGDP.html')
                srv.serve_forever()
