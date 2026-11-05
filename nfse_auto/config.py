"""Carrega config.yaml e variáveis de ambiente (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

RAIZ = Path(__file__).resolve().parent.parent
DIR_DADOS = RAIZ / "dados"


class ErroConfig(Exception):
    pass


@dataclass
class Segredos:
    cert_pfx: str
    cert_senha: str
    gmail_usuario: str
    gmail_senha_app: str


def carregar_config(caminho: str | os.PathLike | None = None) -> dict:
    caminho = Path(caminho or os.environ.get("NFSE_CONFIG", RAIZ / "config.yaml"))
    if not caminho.exists():
        raise ErroConfig(
            f"Arquivo de configuração não encontrado: {caminho}. "
            "Copie config.example.yaml para config.yaml e ajuste."
        )
    with open(caminho, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if cfg.get("ambiente") not in ("homologacao", "producao"):
        raise ErroConfig("'ambiente' deve ser 'homologacao' ou 'producao'.")
    if cfg.get("modo_aprovacao") not in ("manual", "automatico"):
        raise ErroConfig("'modo_aprovacao' deve ser 'manual' ou 'automatico'.")
    for chave in ("prestador", "dps", "clientes", "email_responsavel"):
        if not cfg.get(chave):
            raise ErroConfig(f"Configuração sem '{chave}'.")
    ids = [c["id"] for c in cfg["clientes"]]
    if len(ids) != len(set(ids)):
        raise ErroConfig("Há clientes com 'id' repetido.")
    return cfg


def carregar_segredos(exigir_email: bool = True, exigir_cert: bool = True) -> Segredos:
    if load_dotenv:
        load_dotenv(RAIZ / ".env")
    s = Segredos(
        cert_pfx=os.environ.get("NFSE_CERT_PFX", ""),
        cert_senha=os.environ.get("NFSE_CERT_SENHA", ""),
        gmail_usuario=os.environ.get("GMAIL_USUARIO", ""),
        gmail_senha_app=os.environ.get("GMAIL_SENHA_APP", ""),
    )
    faltando = []
    if exigir_cert and not s.cert_pfx:
        faltando.append("NFSE_CERT_PFX")
    if exigir_email and not (s.gmail_usuario and s.gmail_senha_app):
        faltando.append("GMAIL_USUARIO/GMAIL_SENHA_APP")
    if faltando:
        raise ErroConfig("Variáveis ausentes no .env: " + ", ".join(faltando))
    return s


def cliente_por_id(cfg: dict, cliente_id: str) -> dict:
    for c in cfg["clientes"]:
        if c["id"] == cliente_id:
            return c
    raise ErroConfig(f"Cliente '{cliente_id}' não existe no config.yaml.")
