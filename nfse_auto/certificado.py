"""Leitura do certificado digital A1 (.pfx) para assinatura e mTLS."""
from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12


@dataclass
class CertificadoA1:
    chave_pem: bytes
    cert_pem: bytes
    cadeia_pem: list[bytes]
    titular: str
    validade: datetime

    @classmethod
    def de_pfx(cls, caminho: str, senha: str) -> "CertificadoA1":
        with open(caminho, "rb") as f:
            dados = f.read()
        chave, cert, extras = pkcs12.load_key_and_certificates(
            dados, senha.encode() if senha else None
        )
        if chave is None or cert is None:
            raise ValueError("O .pfx não contém chave privada e certificado.")
        return cls.de_objetos(chave, cert, extras or [])

    @classmethod
    def de_objetos(cls, chave, cert: x509.Certificate, extras) -> "CertificadoA1":
        chave_pem = chave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return cls(
            chave_pem=chave_pem,
            cert_pem=cert.public_bytes(serialization.Encoding.PEM),
            cadeia_pem=[c.public_bytes(serialization.Encoding.PEM) for c in extras],
            titular=cert.subject.rfc4514_string(),
            validade=cert.not_valid_after_utc,
        )

    def verificar_validade(self) -> None:
        if self.validade <= datetime.now(timezone.utc):
            raise ValueError(f"Certificado vencido em {self.validade:%d/%m/%Y}.")

    @contextlib.contextmanager
    def arquivos_mtls(self):
        """Grava cert+chave em arquivos temporários (0600) para o `requests` e apaga ao sair."""
        fd_c, caminho_c = tempfile.mkstemp(suffix=".pem")
        fd_k, caminho_k = tempfile.mkstemp(suffix=".pem")
        try:
            with os.fdopen(fd_c, "wb") as f:
                f.write(self.cert_pem + b"".join(self.cadeia_pem))
            with os.fdopen(fd_k, "wb") as f:
                f.write(self.chave_pem)
            yield (caminho_c, caminho_k)
        finally:
            for p in (caminho_c, caminho_k):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(p)
