"""Leitura (IMAP) e envio (SMTP) pelo Gmail usando senha de app."""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
import smtplib
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"


@dataclass
class EmailRecebido:
    message_id: str
    remetente: str
    assunto: str
    data: str
    corpo: str
    pdfs: list[tuple[str, bytes]] = field(default_factory=list)  # (nome, conteúdo)
    references: str = ""


def _corpo_texto(msg: email.message.EmailMessage) -> str:
    parte = msg.get_body(preferencelist=("plain", "html"))
    if parte is None:
        return ""
    try:
        return parte.get_content()
    except (LookupError, UnicodeDecodeError):
        return parte.get_payload(decode=True).decode("latin-1", errors="replace")


def _pdfs(msg: email.message.EmailMessage) -> list[tuple[str, bytes]]:
    saida = []
    for parte in msg.iter_attachments():
        nome = parte.get_filename() or ""
        if parte.get_content_type() == "application/pdf" or nome.lower().endswith(".pdf"):
            saida.append((nome or "relatorio.pdf", parte.get_payload(decode=True)))
    return saida


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos e com espaços simples — para comparar assuntos."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().casefold()


def _termo_imap(assunto: str) -> str:
    """Maior palavra ASCII do assunto: o IMAP do imaplib não aceita acentos na busca."""
    palavras = [w for w in re.split(r"[\s<>()]+", assunto) if w.isascii() and len(w) >= 4]
    return max(palavras, key=len) if palavras else ""


def _e_resposta(assunto: str) -> bool:
    return bool(re.match(r"^\s*(re|res|fw|fwd|enc|encaminhada)\s*:", assunto, re.I))


def buscar_relatorios(
    usuario: str, senha: str, remetentes: list[str] | None, assunto_contem: str = "", dias: int = 30
) -> list[EmailRecebido]:
    """Busca e-mails com PDF anexo nos últimos `dias`.

    Filtra pelos remetentes (se houver) e pelo trecho do assunto (sem diferenciar acentos
    ou maiúsculas). Ignora respostas/encaminhamentos e mensagens enviadas por você mesmo,
    para não confundir a própria nota enviada com um relatório novo.
    """
    if not remetentes and not assunto_contem:
        raise ValueError("Configure 'email_remetentes' e/ou 'email_assunto_contem' para o cliente.")
    desde = (date.today() - timedelta(days=dias)).strftime("%d-%b-%Y")
    alvo = normalizar(assunto_contem)
    termo = _termo_imap(assunto_contem)
    achados: dict[str, EmailRecebido] = {}
    with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
        imap.login(usuario, senha)
        imap.select(_caixa_todos(imap), readonly=True)
        for rem in remetentes or [None]:
            crit = ["SINCE", desde]
            if rem:
                crit += ["FROM", f'"{rem}"']
            if termo:
                crit += ["SUBJECT", f'"{termo}"']
            typ, dados = imap.search(None, *crit)
            if typ != "OK":
                continue
            for num in dados[0].split():
                typ, partes = imap.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not partes or not isinstance(partes[0], tuple):
                    continue
                msg = email.message_from_bytes(partes[0][1], policy=email.policy.default)
                assunto = str(msg.get("Subject", ""))
                remetente = parseaddr(msg.get("From", ""))[1].lower()
                if remetente == usuario.lower() or _e_resposta(assunto):
                    continue
                if alvo and alvo not in normalizar(assunto):
                    continue
                pdfs = _pdfs(msg)
                mid = (msg.get("Message-ID") or "").strip()
                if not pdfs or not mid:
                    continue
                achados[mid] = EmailRecebido(
                    message_id=mid,
                    remetente=remetente,
                    assunto=assunto,
                    data=str(msg.get("Date", "")),
                    corpo=_corpo_texto(msg),
                    pdfs=pdfs,
                    references=str(msg.get("References", "")),
                )
    return list(achados.values())


def data_ate_no_assunto(assunto: str):
    """Extrai a data de "(até 22/10/2026)" do assunto, se houver."""
    from datetime import datetime

    m = re.search(r"at[eé]\s*(\d{1,2}/\d{1,2}/\d{4})", assunto or "", re.I)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y").date()
    except ValueError:
        return None


def _caixa_todos(imap: imaplib.IMAP4_SSL) -> str:
    """Nome da pasta "Todos os e-mails" (varia com o idioma da conta); cai para INBOX."""
    typ, caixas = imap.list()
    for linha in caixas or []:
        if typ == "OK" and b"\\All" in linha:
            return '"' + linha.decode().rsplit(' "/" ', 1)[-1].strip('"') + '"'
    return "INBOX"


def enviar(
    usuario: str,
    senha: str,
    para: list[str],
    assunto: str,
    corpo: str,
    anexos: list[tuple[str, bytes, str]] | None = None,  # (nome, conteúdo, mime "tipo/subtipo")
    cc: list[str] | None = None,
    em_resposta_a: EmailRecebido | None = None,
) -> str:
    msg = EmailMessage()
    msg["From"] = usuario
    msg["To"] = ", ".join(para)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = assunto
    msg["Message-ID"] = make_msgid(domain=usuario.split("@")[-1])
    if em_resposta_a and em_resposta_a.message_id:
        msg["In-Reply-To"] = em_resposta_a.message_id
        msg["References"] = (em_resposta_a.references + " " + em_resposta_a.message_id).strip()
        if not assunto.lower().startswith("re:"):
            msg.replace_header("Subject", f"Re: {em_resposta_a.assunto}")
    msg.set_content(corpo)
    for nome, conteudo, mime in anexos or []:
        tipo, sub = mime.split("/", 1)
        msg.add_attachment(conteudo, maintype=tipo, subtype=sub, filename=nome)
    with smtplib.SMTP_SSL(SMTP_HOST, 465) as smtp:
        smtp.login(usuario, senha)
        smtp.send_message(msg)
    return msg["Message-ID"]
