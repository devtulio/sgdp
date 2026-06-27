# SGDP v1.1.0 — Servidor local: SQLite, autenticação, REST API, uploads de PDF
import http.server
import socketserver
import os
import json
import sqlite3
import hashlib
import secrets
import threading
import time
import subprocess
import re
import mimetypes
from urllib.parse import urlparse, parse_qs

PORT              = 3001
DB_PATH           = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sgdp.db')
UPLOADS_DIR       = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
HEARTBEAT_TIMEOUT = 60   # ponytail: 60s (vs 30s do SGCD) — multiusuário precisa de margem
SESSION_TTL       = 8 * 3600
MAX_UPLOAD_SIZE   = 50 * 1024 * 1024

os.chdir(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(UPLOADS_DIR, exist_ok=True)

_last_heartbeat = time.time()

TIPOS = ('lei', 'decreto', 'portaria', 'parecer', 'oficio')

# ── Banco de dados ────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
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
            CREATE INDEX IF NOT EXISTS idx_docs_tipo ON documentos(tipo);
            CREATE INDEX IF NOT EXISTS idx_docs_ano  ON documentos(ano);
            CREATE INDEX IF NOT EXISTS idx_audit_em  ON auditoria(em);
        ''')
        if conn.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0] == 0:
            conn.execute(
                'INSERT INTO usuarios (username,nome,senha_hash,admin) VALUES (?,?,?,1)',
                ('admin', 'Administrador', _hash_password('sgdp2024'))
            )
            conn.commit()
            print('Usuário padrão criado: admin / sgdp2024 — troque a senha nas Configurações.')

# ── Segurança ─────────────────────────────────────────────────────────────────

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

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        global _last_heartbeat
        p  = urlparse(self.path).path.rstrip('/')
        qs = parse_qs(urlparse(self.path).query)

        if p in ('/health', '/heartbeat'):
            _last_heartbeat = time.time()
            self._json(200, {'ok': True})
        elif p.startswith('/api/'):
            s = self._auth()
            if s: self._route_get(p, qs, s)
        else:
            super().do_GET()

    def do_POST(self):
        p = urlparse(self.path).path.rstrip('/')
        if p == '/api/auth/login':
            self._login(self._body())
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

        if p == '/api/auth/me':
            self._json(200, {'id': s['user_id'], 'username': s['username'], 'nome': s['nome'], 'admin': bool(s['admin'])})

        elif p == '/api/documentos':
            self._list_docs(qs, s)
        elif re.fullmatch(r'/api/documentos/\d+', p):
            self._get_doc(int(p.split('/')[-1]))

        elif re.fullmatch(r'/api/arquivos/\d+', p):
            self._download_arquivo(int(p.split('/')[-1]), qs)

        elif p == '/api/contadores':
            self._get_contadores(qs)

        elif p == '/api/dashboard':
            self._dashboard()

        elif p == '/api/usuarios':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            with get_db() as conn:
                rows = conn.execute('SELECT id,username,nome,admin,ativo,criado_em FROM usuarios ORDER BY nome').fetchall()
            self._json(200, [dict(r) for r in rows])

        elif p == '/api/auditoria':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            page = int(qp('page', 1)); per = int(qp('per', 100))
            with get_db() as conn:
                total = conn.execute('SELECT COUNT(*) FROM auditoria').fetchone()[0]
                rows  = conn.execute('SELECT * FROM auditoria ORDER BY id DESC LIMIT ? OFFSET ?',
                                     (per, (page-1)*per)).fetchall()
            self._json(200, {'total': total, 'items': [dict(r) for r in rows]})

        elif p == '/api/backup':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._export_backup()

        else:
            self._json(404, {'error': 'Rota não encontrada'})

    def _route_post(self, p, s):
        if p == '/api/auth/logout':
            delete_session(self._token())
            self._json(200, {'ok': True})

        elif p == '/api/documentos':
            self._create_doc(self._body(), s)

        elif re.fullmatch(r'/api/documentos/\d+/arquivo', p):
            self._upload_arquivo(int(p.split('/')[3]), s)

        elif p == '/api/usuarios':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._create_usuario(self._body())

        elif p == '/api/backup/restore':
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._import_backup()

        else:
            self._json(404, {'error': 'Rota não encontrada'})

    def _route_put(self, p, body, s):
        if re.fullmatch(r'/api/documentos/\d+', p):
            self._update_doc(int(p.split('/')[-1]), body, s)
        elif re.fullmatch(r'/api/usuarios/\d+', p):
            if not s['admin']: self._json(403, {'error': 'Acesso restrito'}); return
            self._update_usuario(int(p.split('/')[-1]), body)
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
        token = create_session(row['id'])
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

        where, params = [], []
        if tipo:   where.append('d.tipo=?');   params.append(tipo)
        if search:
            where.append('(d.ementa LIKE ? OR d.partes LIKE ? OR CAST(d.numero AS TEXT) LIKE ?)')
            params += [f'%{search}%'] * 3
        if ano:    where.append('d.ano=?');    params.append(int(ano))
        w = ('WHERE ' + ' AND '.join(where)) if where else ''

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
                conn.execute(
                    'INSERT INTO documentos (tipo,numero,ano,data,ementa,partes,observacoes,criado_por,atualizado_por)'
                    ' VALUES (?,?,?,?,?,?,?,?,?)',
                    (tipo, numero, ano, data_d, ementa,
                     data.get('partes') or '', data.get('observacoes') or '',
                     s['user_id'], s['user_id'])
                )
                bump_contador(conn, tipo, ano, numero)
                did = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
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
            for f in ('ementa', 'partes', 'observacoes', 'data'):
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
            if row['arquivo_id']:
                arq = conn.execute('SELECT * FROM arquivos WHERE id=?', (row['arquivo_id'],)).fetchone()
                if arq:
                    p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                    if os.path.isfile(p): os.remove(p)
                    conn.execute('DELETE FROM arquivos WHERE id=?', (row['arquivo_id'],))
            audit(conn, s['user_id'], s['nome'], 'excluir', did, f"{row['tipo']} nº {row['numero']}/{row['ano']}")
            conn.execute('DELETE FROM documentos WHERE id=?', (did,))
            conn.commit()
        self._json(200, {'ok': True})

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
            totais   = {t: conn.execute('SELECT COUNT(*) FROM documentos WHERE tipo=?', (t,)).fetchone()[0] for t in TIPOS}
            ano_atual = {t: conn.execute('SELECT COUNT(*) FROM documentos WHERE tipo=? AND ano=?', (t, ano)).fetchone()[0] for t in TIPOS}
            recentes = conn.execute(
                '''SELECT d.id, d.tipo, d.numero, d.ano, d.data, d.ementa, d.arquivo_id, u.nome criado_por_nome
                   FROM documentos d LEFT JOIN usuarios u ON d.criado_por=u.id
                   ORDER BY d.criado_em DESC LIMIT 10'''
            ).fetchall()
        self._json(200, {'totais': totais, 'ano_atual': ano_atual, 'recentes': [dict(r) for r in recentes]})

    # ── Usuários ──────────────────────────────────────────────────────────────

    def _create_usuario(self, body):
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
                conn.commit()
            self._json(201, {'id': uid, 'username': username, 'nome': nome, 'admin': bool(data.get('admin'))})
        except sqlite3.IntegrityError:
            self._json(409, {'error': f'Usuário "{username}" já existe'})

    def _update_usuario(self, uid, body):
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
            if not conn.execute('SELECT id FROM usuarios WHERE id=?', (uid,)).fetchone():
                self._json(404, {'error': 'Usuário não encontrado'}); return
            conn.execute(f"UPDATE usuarios SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                         list(fields.values()) + [uid])
            conn.commit()
        self._json(200, {'ok': True})

    def _delete_usuario(self, uid, s):
        if uid == s['user_id']:
            self._json(400, {'error': 'Não pode excluir seu próprio usuário'}); return
        with get_db() as conn:
            conn.execute('DELETE FROM usuarios WHERE id=?', (uid,))
            conn.commit()
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
        backup = {'sgdp_version': '1.1.0', 'exported_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                  'documentos': docs, 'usuarios': users, 'contadores': conts, 'arquivos': arqs}
        body = json.dumps(backup, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="sgdp_backup_{time.strftime("%Y%m%d_%H%M%S")}.json"')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _import_backup(self):
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
            conn.commit()
        self._json(200, {'ok': True, 'documentos': len(backup.get('documentos',[])), 'arquivos': len(backup.get('arquivos',[]))})

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

def _watchdog():
    time.sleep(HEARTBEAT_TIMEOUT)
    while True:
        time.sleep(5)
        idle = time.time() - _last_heartbeat
        if idle > HEARTBEAT_TIMEOUT:
            print(f'\nSem heartbeat há {idle:.0f}s. Encerrando servidor...')
            os._exit(0)

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

if __name__ == '__main__':
    init_db()
    threading.Thread(target=_watchdog, daemon=True).start()
    url = f'http://localhost:{PORT}/'
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), SGDPHandler) as srv:
        print(f'SGDP iniciado — {url}')
        print('Pressione Ctrl+C para encerrar.\n')
        browser = _find_browser()
        if browser:
            subprocess.Popen([browser, f'--app={url}', '--new-window', '--window-size=1400,900', '--no-first-run'])
        else:
            import webbrowser; webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print('\nServidor encerrado.')
