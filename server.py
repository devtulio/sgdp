# SGDP v1.0.0 — Sistema de Gestão de Documentos da Procuradoria
import http.server
import socketserver
import os
import json
import sqlite3
import secrets
import hashlib
import time
import re
import subprocess
import threading
import mimetypes
from urllib.parse import urlparse, parse_qs

PORT = 3001
DB_PATH = 'sgdp.db'
UPLOADS_DIR = 'uploads'
SESSIONS = {}  # token -> {user_id, username, nome, admin, expires}
SESSION_TTL = 8 * 3600  # 8 horas
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
# Encerra o servidor se nenhum cliente enviar heartbeat por este período.
# 60s é generoso o suficiente para múltiplos usuários reconectarem/recarregarem.
HEARTBEAT_TIMEOUT = 60

os.chdir(os.path.dirname(os.path.abspath(__file__)))

_last_heartbeat = time.time()

TIPOS = ('lei', 'decreto', 'portaria', 'parecer', 'oficio')

# ── Segurança ─────────────────────────────────────────────────────────────────

def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return f"{salt}:{dk.hex()}"

def verify_password(password, stored):
    try:
        salt, _ = stored.split(':', 1)
        return secrets.compare_digest(hash_password(password, salt), stored)
    except Exception:
        return False

# ── Banco de dados ────────────────────────────────────────────────────────────

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL COLLATE NOCASE,
            nome        TEXT NOT NULL,
            senha_hash  TEXT NOT NULL,
            admin       INTEGER DEFAULT 0,
            ativo       INTEGER DEFAULT 1,
            criado_em   TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
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
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo          TEXT NOT NULL CHECK(tipo IN ('lei','decreto','portaria','parecer','oficio')),
            numero        INTEGER NOT NULL,
            ano           INTEGER NOT NULL,
            data          TEXT NOT NULL,
            ementa        TEXT NOT NULL,
            partes        TEXT,
            observacoes   TEXT,
            arquivo_id    INTEGER REFERENCES arquivos(id) ON DELETE SET NULL,
            criado_por    INTEGER REFERENCES usuarios(id),
            atualizado_por INTEGER REFERENCES usuarios(id),
            criado_em     TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
            atualizado_em TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
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
    ''')
    # Cria usuário admin padrão se não existir nenhum
    if conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO usuarios (username, nome, senha_hash, admin) VALUES (?,?,?,1)",
            ('admin', 'Administrador', hash_password('sgdp2024'))
        )
        conn.commit()
    conn.close()
    os.makedirs(UPLOADS_DIR, exist_ok=True)

def proximo_numero(conn, tipo, ano):
    row = conn.execute("SELECT ultimo FROM contadores WHERE tipo=? AND ano=?", (tipo, ano)).fetchone()
    return (row['ultimo'] + 1) if row else 1

def bump_contador(conn, tipo, ano, numero):
    conn.execute(
        "INSERT INTO contadores (tipo,ano,ultimo) VALUES (?,?,?) "
        "ON CONFLICT(tipo,ano) DO UPDATE SET ultimo=MAX(ultimo,excluded.ultimo)",
        (tipo, ano, numero)
    )

def audit(conn, uid, nome, acao, doc_id=None, detalhes=None):
    conn.execute(
        "INSERT INTO auditoria (usuario_id,usuario_nome,acao,documento_id,detalhes) VALUES (?,?,?,?,?)",
        (uid, nome, acao, doc_id, detalhes)
    )

# ── Sessões ───────────────────────────────────────────────────────────────────

def get_session(token):
    if not token:
        return None
    s = SESSIONS.get(token)
    if not s:
        return None
    if time.time() > s['expires']:
        del SESSIONS[token]
        return None
    return s

# ── Handler HTTP ──────────────────────────────────────────────────────────────

class SGDPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suprime log de acesso

    def do_GET(self):    self._route('GET')
    def do_POST(self):   self._route('POST')
    def do_PUT(self):    self._route('PUT')
    def do_DELETE(self): self._route('DELETE')
    def do_OPTIONS(self):
        self._cors()
        self.send_response(204)
        self.end_headers()

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization')

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(n).decode('utf-8')) if n else {}

    def _token(self):
        auth = self.headers.get('Authorization', '')
        return auth[7:] if auth.startswith('Bearer ') else None

    def _sess(self):
        return get_session(self._token())

    def _need_auth(self):
        s = self._sess()
        if not s:
            self._json(401, {'error': 'Não autenticado'})
        return s

    def _need_admin(self):
        s = self._need_auth()
        if s and not s['admin']:
            self._json(403, {'error': 'Acesso restrito a administradores'})
            return None
        return s

    def _route(self, method):
        parsed = urlparse(self.path)
        p = parsed.path
        qs = parse_qs(parsed.query, keep_blank_values=True)

        # Heartbeat
        if p in ('/heartbeat', '/health'):
            global _last_heartbeat
            _last_heartbeat = time.time()
            return self._json(200, {'ok': True})

        # Arquivos estáticos
        if method == 'GET' and not p.startswith('/api/'):
            target = 'SGDP.html' if p == '/' else p.lstrip('/')
            if os.path.isfile(target):
                return self._serve_file(target)
            return self._json(404, {'error': 'Não encontrado'})

        # ── Rotas da API ─────────────────────────────────────────────────────

        # Auth
        if method == 'POST' and p == '/api/auth/login':   return self._login()
        if method == 'POST' and p == '/api/auth/logout':  return self._logout()
        if method == 'GET'  and p == '/api/auth/me':      return self._me()

        # Documentos
        if method == 'GET'  and p == '/api/documentos':   return self._list_docs(qs)
        if method == 'POST' and p == '/api/documentos':   return self._create_doc()

        m = re.match(r'^/api/documentos/(\d+)$', p)
        if m:
            did = int(m.group(1))
            if method == 'GET':    return self._get_doc(did)
            if method == 'PUT':    return self._update_doc(did)
            if method == 'DELETE': return self._delete_doc(did)

        m = re.match(r'^/api/documentos/(\d+)/arquivo$', p)
        if m:
            did = int(m.group(1))
            if method == 'POST':   return self._upload_arquivo(did)
            if method == 'DELETE': return self._remove_arquivo(did)

        # Arquivos (download/view)
        m = re.match(r'^/api/arquivos/(\d+)$', p)
        if m:
            aid = int(m.group(1))
            if method == 'GET': return self._download_arquivo(aid, qs)

        # Contadores
        if method == 'GET' and p == '/api/contadores': return self._get_contadores(qs)

        # Dashboard stats
        if method == 'GET' and p == '/api/dashboard': return self._dashboard()

        # Usuários
        if method == 'GET'  and p == '/api/usuarios': return self._list_usuarios()
        if method == 'POST' and p == '/api/usuarios': return self._create_usuario()

        m = re.match(r'^/api/usuarios/(\d+)$', p)
        if m:
            uid = int(m.group(1))
            if method == 'PUT':    return self._update_usuario(uid)
            if method == 'DELETE': return self._delete_usuario(uid)

        # Backup
        if method == 'GET'  and p == '/api/backup':         return self._export_backup()
        if method == 'POST' and p == '/api/backup/restore': return self._import_backup()

        # Auditoria
        if method == 'GET' and p == '/api/auditoria': return self._list_auditoria(qs)

        self._json(404, {'error': 'Rota não encontrada'})

    def _serve_file(self, path):
        mime, _ = mimetypes.guess_type(path)
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', mime or 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _login(self):
        data = self._read_json()
        username = data.get('username', '').strip()
        password = data.get('password', '')
        if not username or not password:
            return self._json(400, {'error': 'Usuário e senha são obrigatórios'})
        conn = db()
        row = conn.execute(
            "SELECT * FROM usuarios WHERE username=? AND ativo=1", (username,)
        ).fetchone()
        conn.close()
        if not row or not verify_password(password, row['senha_hash']):
            return self._json(401, {'error': 'Usuário ou senha incorretos'})
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = {
            'user_id': row['id'], 'username': row['username'],
            'nome': row['nome'], 'admin': bool(row['admin']),
            'expires': time.time() + SESSION_TTL,
        }
        return self._json(200, {
            'token': token,
            'user': {'id': row['id'], 'username': row['username'], 'nome': row['nome'], 'admin': bool(row['admin'])}
        })

    def _logout(self):
        token = self._token()
        if token and token in SESSIONS:
            del SESSIONS[token]
        return self._json(200, {'ok': True})

    def _me(self):
        s = self._need_auth()
        if not s: return
        return self._json(200, {'id': s['user_id'], 'username': s['username'], 'nome': s['nome'], 'admin': s['admin']})

    # ── Documentos ────────────────────────────────────────────────────────────

    def _list_docs(self, qs):
        s = self._need_auth()
        if not s: return
        tipo   = qs.get('tipo',     [None])[0]
        search = qs.get('q',        [''])[0].strip()
        ano    = qs.get('ano',      [None])[0]
        page   = int(qs.get('page', ['1'])[0])
        per    = int(qs.get('per',  ['50'])[0])

        where, params = [], []
        if tipo:   where.append("d.tipo=?");   params.append(tipo)
        if search:
            where.append("(d.ementa LIKE ? OR d.partes LIKE ? OR CAST(d.numero AS TEXT) LIKE ?)")
            like = f"%{search}%"
            params += [like, like, like]
        if ano:    where.append("d.ano=?");    params.append(int(ano))

        w = ("WHERE " + " AND ".join(where)) if where else ""
        conn = db()
        total = conn.execute(f"SELECT COUNT(*) FROM documentos d {w}", params).fetchone()[0]
        rows  = conn.execute(
            f"""SELECT d.*, u1.nome criado_por_nome, u2.nome atualizado_por_nome,
                       a.nome_original arquivo_nome, a.tamanho arquivo_tamanho
                FROM documentos d
                LEFT JOIN usuarios u1 ON d.criado_por=u1.id
                LEFT JOIN usuarios u2 ON d.atualizado_por=u2.id
                LEFT JOIN arquivos a ON d.arquivo_id=a.id
                {w} ORDER BY d.ano DESC, d.numero DESC
                LIMIT ? OFFSET ?""",
            params + [per, (page-1)*per]
        ).fetchall()
        conn.close()
        return self._json(200, {'total': total, 'page': page, 'per': per, 'items': [dict(r) for r in rows]})

    def _create_doc(self):
        s = self._need_auth()
        if not s: return
        data = self._read_json()
        tipo = data.get('tipo', '').lower()
        if tipo not in TIPOS:
            return self._json(400, {'error': f'Tipo inválido. Use: {", ".join(TIPOS)}'})
        ementa = (data.get('ementa') or '').strip()
        if not ementa:
            return self._json(400, {'error': 'Ementa é obrigatória'})
        data_doc = (data.get('data') or '').strip()
        if not data_doc:
            return self._json(400, {'error': 'Data é obrigatória'})
        ano = int(data.get('ano') or data_doc[:4])
        conn = db()
        numero = int(data['numero']) if data.get('numero') not in (None, '') else proximo_numero(conn, tipo, ano)
        try:
            conn.execute(
                "INSERT INTO documentos (tipo,numero,ano,data,ementa,partes,observacoes,criado_por,atualizado_por)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (tipo, numero, ano, data_doc, ementa,
                 data.get('partes') or '', data.get('observacoes') or '',
                 s['user_id'], s['user_id'])
            )
            bump_contador(conn, tipo, ano, numero)
            did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            audit(conn, s['user_id'], s['nome'], 'criar', did, f"{tipo.capitalize()} nº {numero}/{ano}")
            conn.commit()
            row = conn.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
            conn.close()
            return self._json(201, dict(row))
        except sqlite3.IntegrityError:
            conn.close()
            return self._json(409, {'error': f'Já existe {tipo} nº {numero}/{ano}'})

    def _get_doc(self, did):
        s = self._need_auth()
        if not s: return
        conn = db()
        row = conn.execute(
            """SELECT d.*, u1.nome criado_por_nome, u2.nome atualizado_por_nome,
                      a.nome_original arquivo_nome, a.tamanho arquivo_tamanho
               FROM documentos d
               LEFT JOIN usuarios u1 ON d.criado_por=u1.id
               LEFT JOIN usuarios u2 ON d.atualizado_por=u2.id
               LEFT JOIN arquivos a ON d.arquivo_id=a.id
               WHERE d.id=?""", (did,)
        ).fetchone()
        conn.close()
        if not row:
            return self._json(404, {'error': 'Documento não encontrado'})
        return self._json(200, dict(row))

    def _update_doc(self, did):
        s = self._need_auth()
        if not s: return
        data = self._read_json()
        conn = db()
        row = conn.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
        if not row:
            conn.close()
            return self._json(404, {'error': 'Documento não encontrado'})
        fields = {'atualizado_por': s['user_id'], 'atualizado_em': time.strftime('%Y-%m-%dT%H:%M:%S')}
        for f in ('ementa', 'partes', 'observacoes', 'data'):
            if f in data: fields[f] = data[f]
        if 'numero' in data: fields['numero'] = int(data['numero'])
        if 'ano'    in data: fields['ano']    = int(data['ano'])
        set_sql = ', '.join(f"{k}=?" for k in fields)
        try:
            conn.execute(f"UPDATE documentos SET {set_sql} WHERE id=?", list(fields.values()) + [did])
            if 'numero' in fields:
                bump_contador(conn, row['tipo'], fields.get('ano', row['ano']), fields['numero'])
            audit(conn, s['user_id'], s['nome'], 'editar', did)
            conn.commit()
            updated = conn.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
            conn.close()
            return self._json(200, dict(updated))
        except sqlite3.IntegrityError:
            conn.close()
            return self._json(409, {'error': 'Número/ano já existe para este tipo'})

    def _delete_doc(self, did):
        s = self._need_auth()
        if not s: return
        conn = db()
        row = conn.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
        if not row:
            conn.close()
            return self._json(404, {'error': 'Documento não encontrado'})
        if row['arquivo_id']:
            arq = conn.execute("SELECT * FROM arquivos WHERE id=?", (row['arquivo_id'],)).fetchone()
            if arq:
                path = os.path.join(UPLOADS_DIR, arq['nome_disco'])
                if os.path.isfile(path): os.remove(path)
                conn.execute("DELETE FROM arquivos WHERE id=?", (row['arquivo_id'],))
        audit(conn, s['user_id'], s['nome'], 'excluir', did, f"{row['tipo']} nº {row['numero']}/{row['ano']}")
        conn.execute("DELETE FROM documentos WHERE id=?", (did,))
        conn.commit()
        conn.close()
        return self._json(200, {'ok': True})

    # ── Arquivos ──────────────────────────────────────────────────────────────

    def _upload_arquivo(self, did):
        s = self._need_auth()
        if not s: return
        ct = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in ct:
            return self._json(400, {'error': 'Envie como multipart/form-data'})
        length = int(self.headers.get('Content-Length', 0))
        if length > MAX_UPLOAD_SIZE:
            return self._json(413, {'error': f'Arquivo muito grande (máx. {MAX_UPLOAD_SIZE//1024//1024} MB)'})
        boundary = None
        for part in ct.split(';'):
            part = part.strip()
            if part.startswith('boundary='):
                boundary = part[9:].strip('"')
                break
        if not boundary:
            return self._json(400, {'error': 'Boundary não encontrado'})
        body = self.rfile.read(length)
        filename, filedata = self._parse_multipart(body, boundary)
        if not filename or filedata is None:
            return self._json(400, {'error': 'Arquivo não encontrado no formulário'})
        if not filename.lower().endswith('.pdf'):
            return self._json(400, {'error': 'Apenas PDFs são aceitos'})
        conn = db()
        doc = conn.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
        if not doc:
            conn.close()
            return self._json(404, {'error': 'Documento não encontrado'})
        # Remove arquivo anterior
        if doc['arquivo_id']:
            old = conn.execute("SELECT * FROM arquivos WHERE id=?", (doc['arquivo_id'],)).fetchone()
            if old:
                p = os.path.join(UPLOADS_DIR, old['nome_disco'])
                if os.path.isfile(p): os.remove(p)
                conn.execute("DELETE FROM arquivos WHERE id=?", (old['id'],))
        nome_disco = f"{secrets.token_hex(16)}.pdf"
        with open(os.path.join(UPLOADS_DIR, nome_disco), 'wb') as f:
            f.write(filedata)
        conn.execute(
            "INSERT INTO arquivos (nome_original,nome_disco,tamanho,enviado_por) VALUES (?,?,?,?)",
            (filename, nome_disco, len(filedata), s['user_id'])
        )
        aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE documentos SET arquivo_id=?,atualizado_por=?,atualizado_em=? WHERE id=?",
            (aid, s['user_id'], time.strftime('%Y-%m-%dT%H:%M:%S'), did)
        )
        audit(conn, s['user_id'], s['nome'], 'upload', did, filename)
        conn.commit()
        conn.close()
        return self._json(200, {'ok': True, 'arquivo_id': aid, 'nome_original': filename, 'tamanho': len(filedata)})

    def _parse_multipart(self, body, boundary):
        sep = f'--{boundary}'.encode()
        for part in body.split(sep)[1:]:
            if part.startswith(b'--'):
                break
            if b'\r\n\r\n' in part:
                hdrs_raw, content = part.split(b'\r\n\r\n', 1)
            elif b'\n\n' in part:
                hdrs_raw, content = part.split(b'\n\n', 1)
            else:
                continue
            hdrs = hdrs_raw.decode('utf-8', errors='replace')
            if 'filename=' not in hdrs:
                continue
            m = re.search(r'filename="([^"]*)"', hdrs)
            if not m:
                continue
            fname = m.group(1)
            content = content[:-2] if content.endswith(b'\r\n') else content[:-1] if content.endswith(b'\n') else content
            return fname, content
        return None, None

    def _remove_arquivo(self, did):
        s = self._need_auth()
        if not s: return
        conn = db()
        doc = conn.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
        if not doc or not doc['arquivo_id']:
            conn.close()
            return self._json(404, {'error': 'Sem arquivo para remover'})
        arq = conn.execute("SELECT * FROM arquivos WHERE id=?", (doc['arquivo_id'],)).fetchone()
        if arq:
            p = os.path.join(UPLOADS_DIR, arq['nome_disco'])
            if os.path.isfile(p): os.remove(p)
            conn.execute("DELETE FROM arquivos WHERE id=?", (arq['id'],))
        conn.execute("UPDATE documentos SET arquivo_id=NULL WHERE id=?", (did,))
        audit(conn, s['user_id'], s['nome'], 'remover_arquivo', did)
        conn.commit()
        conn.close()
        return self._json(200, {'ok': True})

    def _download_arquivo(self, aid, qs):
        s = self._need_auth()
        if not s: return
        conn = db()
        arq = conn.execute("SELECT * FROM arquivos WHERE id=?", (aid,)).fetchone()
        conn.close()
        if not arq:
            return self._json(404, {'error': 'Arquivo não encontrado'})
        filepath = os.path.join(UPLOADS_DIR, arq['nome_disco'])
        if not os.path.isfile(filepath):
            return self._json(404, {'error': 'Arquivo não encontrado no disco'})
        with open(filepath, 'rb') as f:
            data = f.read()
        inline = qs.get('inline', ['0'])[0] == '1'
        disp = 'inline' if inline else f'attachment; filename="{arq["nome_original"]}"'
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/pdf')
        self.send_header('Content-Disposition', disp)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Contadores / Dashboard ─────────────────────────────────────────────────

    def _get_contadores(self, qs):
        s = self._need_auth()
        if not s: return
        tipo = qs.get('tipo', [None])[0]
        ano  = int(qs.get('ano', [str(time.localtime().tm_year)])[0])
        conn = db()
        if tipo:
            prox = proximo_numero(conn, tipo, ano)
            conn.close()
            return self._json(200, {'tipo': tipo, 'ano': ano, 'proximo': prox})
        result = {t: proximo_numero(conn, t, ano) for t in TIPOS}
        conn.close()
        return self._json(200, result)

    def _dashboard(self):
        s = self._need_auth()
        if not s: return
        ano = time.localtime().tm_year
        conn = db()
        totais = {}
        for t in TIPOS:
            totais[t] = conn.execute("SELECT COUNT(*) FROM documentos WHERE tipo=?", (t,)).fetchone()[0]
        ano_atual = {}
        for t in TIPOS:
            ano_atual[t] = conn.execute("SELECT COUNT(*) FROM documentos WHERE tipo=? AND ano=?", (t, ano)).fetchone()[0]
        recentes = conn.execute(
            """SELECT d.id, d.tipo, d.numero, d.ano, d.data, d.ementa, d.arquivo_id,
                      u.nome criado_por_nome
               FROM documentos d
               LEFT JOIN usuarios u ON d.criado_por=u.id
               ORDER BY d.criado_em DESC LIMIT 10"""
        ).fetchall()
        conn.close()
        return self._json(200, {
            'totais': totais, 'ano_atual': ano_atual,
            'recentes': [dict(r) for r in recentes]
        })

    # ── Usuários ──────────────────────────────────────────────────────────────

    def _list_usuarios(self):
        s = self._need_admin()
        if not s: return
        conn = db()
        rows = conn.execute(
            "SELECT id,username,nome,admin,ativo,criado_em FROM usuarios ORDER BY nome"
        ).fetchall()
        conn.close()
        return self._json(200, [dict(r) for r in rows])

    def _create_usuario(self):
        s = self._need_admin()
        if not s: return
        data = self._read_json()
        username = data.get('username', '').strip()
        nome     = data.get('nome', '').strip()
        senha    = data.get('senha', '')
        admin    = bool(data.get('admin', False))
        if not username or not nome or not senha:
            return self._json(400, {'error': 'username, nome e senha são obrigatórios'})
        if len(senha) < 6:
            return self._json(400, {'error': 'Senha deve ter no mínimo 6 caracteres'})
        conn = db()
        try:
            conn.execute(
                "INSERT INTO usuarios (username,nome,senha_hash,admin) VALUES (?,?,?,?)",
                (username, nome, hash_password(senha), int(admin))
            )
            conn.commit()
            uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.close()
            return self._json(201, {'id': uid, 'username': username, 'nome': nome, 'admin': admin, 'ativo': True})
        except sqlite3.IntegrityError:
            conn.close()
            return self._json(409, {'error': f'Usuário "{username}" já existe'})

    def _update_usuario(self, uid):
        s = self._need_admin()
        if not s: return
        data = self._read_json()
        conn = db()
        if not conn.execute("SELECT id FROM usuarios WHERE id=?", (uid,)).fetchone():
            conn.close()
            return self._json(404, {'error': 'Usuário não encontrado'})
        fields = {}
        if 'nome'  in data: fields['nome']  = data['nome'].strip()
        if 'admin' in data: fields['admin'] = int(bool(data['admin']))
        if 'ativo' in data: fields['ativo'] = int(bool(data['ativo']))
        if data.get('senha'):
            if len(data['senha']) < 6:
                conn.close()
                return self._json(400, {'error': 'Senha deve ter no mínimo 6 caracteres'})
            fields['senha_hash'] = hash_password(data['senha'])
        if not fields:
            conn.close()
            return self._json(400, {'error': 'Nenhum campo para atualizar'})
        set_sql = ', '.join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE usuarios SET {set_sql} WHERE id=?", list(fields.values()) + [uid])
        conn.commit()
        conn.close()
        return self._json(200, {'ok': True})

    def _delete_usuario(self, uid):
        s = self._need_admin()
        if not s: return
        if uid == s['user_id']:
            return self._json(400, {'error': 'Você não pode excluir seu próprio usuário'})
        conn = db()
        conn.execute("DELETE FROM usuarios WHERE id=?", (uid,))
        conn.commit()
        conn.close()
        return self._json(200, {'ok': True})

    # ── Backup / Restore ──────────────────────────────────────────────────────

    def _export_backup(self):
        s = self._need_admin()
        if not s: return
        import base64
        conn = db()
        docs  = [dict(r) for r in conn.execute("SELECT * FROM documentos").fetchall()]
        users = [dict(r) for r in conn.execute(
            "SELECT id,username,nome,senha_hash,admin,ativo,criado_em FROM usuarios"
        ).fetchall()]
        conts = [dict(r) for r in conn.execute("SELECT * FROM contadores").fetchall()]
        arqs  = []
        for arq in conn.execute("SELECT * FROM arquivos").fetchall():
            path = os.path.join(UPLOADS_DIR, arq['nome_disco'])
            if os.path.isfile(path):
                with open(path, 'rb') as f:
                    arqs.append({**dict(arq), 'data_b64': base64.b64encode(f.read()).decode()})
        conn.close()
        backup = {
            'sgdp_version': '1.0.0',
            'exported_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'documentos': docs, 'usuarios': users, 'contadores': conts, 'arquivos': arqs,
        }
        body = json.dumps(backup, ensure_ascii=False, default=str).encode('utf-8')
        ts = time.strftime('%Y%m%d_%H%M%S')
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="sgdp_backup_{ts}.json"')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _import_backup(self):
        s = self._need_admin()
        if not s: return
        import base64
        length = int(self.headers.get('Content-Length', 0))
        if length > 500 * 1024 * 1024:
            return self._json(413, {'error': 'Backup muito grande'})
        try:
            backup = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return self._json(400, {'error': 'Arquivo inválido'})
        if 'sgdp_version' not in backup:
            return self._json(400, {'error': 'Não é um backup SGDP'})
        conn = db()
        conn.execute("DELETE FROM documentos")
        conn.execute("DELETE FROM arquivos")
        conn.execute("DELETE FROM contadores")
        for arq in backup.get('arquivos', []):
            nome_disco = f"{secrets.token_hex(16)}.pdf"
            with open(os.path.join(UPLOADS_DIR, nome_disco), 'wb') as f:
                f.write(base64.b64decode(arq['data_b64']))
            conn.execute(
                "INSERT INTO arquivos (id,nome_original,nome_disco,tamanho,enviado_por,enviado_em) VALUES (?,?,?,?,?,?)",
                (arq['id'], arq['nome_original'], nome_disco, arq['tamanho'], arq.get('enviado_por'), arq.get('enviado_em'))
            )
        for doc in backup.get('documentos', []):
            conn.execute(
                "INSERT OR REPLACE INTO documentos "
                "(id,tipo,numero,ano,data,ementa,partes,observacoes,arquivo_id,criado_por,atualizado_por,criado_em,atualizado_em)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc['id'],doc['tipo'],doc['numero'],doc['ano'],doc['data'],doc['ementa'],
                 doc.get('partes'),doc.get('observacoes'),doc.get('arquivo_id'),
                 doc.get('criado_por'),doc.get('atualizado_por'),doc.get('criado_em'),doc.get('atualizado_em'))
            )
        for c in backup.get('contadores', []):
            conn.execute("INSERT OR REPLACE INTO contadores VALUES (?,?,?)", (c['tipo'],c['ano'],c['ultimo']))
        for u in backup.get('usuarios', []):
            conn.execute(
                "INSERT OR REPLACE INTO usuarios (id,username,nome,senha_hash,admin,ativo,criado_em) VALUES (?,?,?,?,?,?,?)",
                (u['id'],u['username'],u['nome'],u['senha_hash'],u['admin'],u.get('ativo',1),u.get('criado_em'))
            )
        conn.commit()
        conn.close()
        return self._json(200, {
            'ok': True,
            'documentos': len(backup.get('documentos',[])),
            'arquivos': len(backup.get('arquivos',[]))
        })

    # ── Auditoria ─────────────────────────────────────────────────────────────

    def _list_auditoria(self, qs):
        s = self._need_admin()
        if not s: return
        page = int(qs.get('page', ['1'])[0])
        per  = int(qs.get('per',  ['100'])[0])
        conn = db()
        total = conn.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0]
        rows  = conn.execute(
            "SELECT * FROM auditoria ORDER BY id DESC LIMIT ? OFFSET ?", (per, (page-1)*per)
        ).fetchall()
        conn.close()
        return self._json(200, {'total': total, 'items': [dict(r) for r in rows]})


# ── Inicialização ─────────────────────────────────────────────────────────────

def _watchdog():
    # Aguarda o browser conectar antes de começar a monitorar
    time.sleep(HEARTBEAT_TIMEOUT)
    while True:
        time.sleep(5)
        idle = time.time() - _last_heartbeat
        if idle > HEARTBEAT_TIMEOUT:
            print(f'\nSem heartbeat há {idle:.0f}s. Encerrando servidor...')
            os._exit(0)

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
        print('Pressione Ctrl+C para encerrar o servidor.\n')
        browser = _find_browser()
        if browser:
            subprocess.Popen([browser, f'--app={url}', '--new-window', '--window-size=1400,900', '--no-first-run'])
        else:
            import webbrowser
            webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print('\nServidor encerrado.')
