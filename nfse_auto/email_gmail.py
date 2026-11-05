"""Leitura (IMAP) e envio (SMTP) pelo Gmail usando senha de app."""
from __future__ import annotations

import email
import email.policy
import imaplib
import smtplib
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


def buscar_relatorios(
    usuario: str, senha: str, remetentes: list[str], assunto_contem: str = "", dias: int = 30
) -> list[EmailRecebido]:
    """Busca e-mails com PDF anexo dos remetentes informados nos últimos `dias`."""
    desde = (date.today() - timedelta(days=dias)).strftime("%d-%b-%Y")
    achados: list[EmailRecebido] = []
    with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
        imap.login(usuario, senha)
        imap.select(_caixa_todos(imap), readonly=True)
        for rem in remetentes:
            crit = ["SINCE", desde, "FROM", f'"{rem}"']
            if assunto_contem:
                crit += ["SUBJECT", f'"{assunto_contem}"']
            typ, dados = imap.search("UTF-8", *crit)
            if typ != "OK":
                continue
            for num in dados[0].split():
                typ, partes = imap.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not partes or not isinstance(partes[0], tuple):
                    continue
                msg = email.message_from_bytes(partes[0][1], policy=email.policy.default)
                pdfs = _pdfs(msg)
                if not pdfs:
                    continue
                achados.append(
                    EmailRecebido(
                        message_id=(msg.get("Message-ID") or "").strip(),
                        remetente=parseaddr(msg.get("From", ""))[1].lower(),
                        assunto=str(msg.get("Subject", "")),
                        data=str(msg.get("Date", "")),
                        corpo=_corpo_texto(msg),
                        pdfs=pdfs,
                        references=str(msg.get("References", "")),
                    )
                )
    return achados


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
