"""Registro local (SQLite) de e-mails processados, notas e numeração da DPS."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .config import DIR_DADOS

ESQUEMA = """
CREATE TABLE IF NOT EXISTS notas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id        TEXT UNIQUE NOT NULL,
    cliente_id      TEXT NOT NULL,
    status          TEXT NOT NULL,   -- pendente | emitida | enviada | erro | descartada
    motivo          TEXT,
    dados_json      TEXT NOT NULL,   -- valor, período, descrição, competência...
    email_json      TEXT NOT NULL,   -- dados do e-mail original (para responder no fio)
    numero_dps      INTEGER,
    id_dps          TEXT,
    chave_acesso    TEXT,
    numero_nfse     TEXT,
    criada_em       TEXT NOT NULL,
    atualizada_em   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contadores (
    nome  TEXT PRIMARY KEY,
    valor INTEGER NOT NULL
);
"""


class Estado:
    def __init__(self, caminho: Path | None = None, ambiente: str = "homologacao"):
        """Um registro por ambiente: o teste em homologação não marca relatórios como
        processados para a produção (dados/homologacao/ e dados/producao/)."""
        caminho = Path(caminho or DIR_DADOS / ambiente / "estado.db")
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.pasta = caminho.parent
        self.db = sqlite3.connect(caminho)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(ESQUEMA)

    @contextmanager
    def transacao(self):
        with self.db:
            yield self.db

    # --- numeração ---------------------------------------------------------
    def proximo_numero_dps(self, serie: str, inicial: int) -> int:
        """Reserva o próximo nDPS. Nunca reaproveita um número, mesmo se a emissão falhar."""
        nome = f"dps_serie_{serie}"
        with self.transacao() as db:
            linha = db.execute("SELECT valor FROM contadores WHERE nome=?", (nome,)).fetchone()
            numero = max(inicial, (linha["valor"] + 1) if linha else inicial)
            db.execute(
                "INSERT INTO contadores(nome, valor) VALUES(?, ?) "
                "ON CONFLICT(nome) DO UPDATE SET valor=excluded.valor",
                (nome, numero),
            )
        return numero

    # --- notas -------------------------------------------------------------
    def ja_processado(self, email_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM notas WHERE email_id=?", (email_id,)).fetchone() is not None

    def criar(self, email_id: str, cliente_id: str, status: str, dados: dict, email: dict, motivo: str = "") -> int:
        agora = datetime.now().isoformat(timespec="seconds")
        with self.transacao() as db:
            cur = db.execute(
                "INSERT INTO notas(email_id, cliente_id, status, motivo, dados_json, email_json, criada_em, atualizada_em)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (email_id, cliente_id, status, motivo, json.dumps(dados, ensure_ascii=False),
                 json.dumps(email, ensure_ascii=False), agora, agora),
            )
            return cur.lastrowid

    def atualizar(self, nota_id: int, **campos) -> None:
        if "dados" in campos:
            campos["dados_json"] = json.dumps(campos.pop("dados"), ensure_ascii=False)
        campos["atualizada_em"] = datetime.now().isoformat(timespec="seconds")
        colunas = ", ".join(f"{k}=?" for k in campos)
        with self.transacao() as db:
            db.execute(f"UPDATE notas SET {colunas} WHERE id=?", (*campos.values(), nota_id))

    def obter(self, nota_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM notas WHERE id=?", (nota_id,)).fetchone()

    def listar(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.db.execute("SELECT * FROM notas WHERE status=? ORDER BY id", (status,)).fetchall()
        return self.db.execute("SELECT * FROM notas ORDER BY id").fetchall()

    def ultima_enviada(self, cliente_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM notas WHERE cliente_id=? AND status='enviada' ORDER BY id DESC LIMIT 1",
            (cliente_id,),
        ).fetchone()
