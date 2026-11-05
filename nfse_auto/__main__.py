"""Linha de comando: python -m nfse_auto <comando>."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import dps as dps_mod
from . import email_gmail, extrator, pipeline
from .certificado import CertificadoA1
from .config import ErroConfig, carregar_config, carregar_segredos, cliente_por_id
from .estado import Estado


def cmd_verificar(args, cfg):
    seg = carregar_segredos()
    ids = pipeline.verificar_emails(cfg, seg, Estado(ambiente=cfg["ambiente"]), dias=args.dias)
    print(f"{len(ids)} relatório(s) novo(s) processado(s).")
    for i in ids:
        _mostrar(Estado(ambiente=cfg["ambiente"]).obter(i))


def cmd_aprovar(args, cfg):
    seg = carregar_segredos()
    estado = Estado(ambiente=cfg["ambiente"])
    pipeline.emitir_e_enviar(cfg, seg, estado, args.id, enviar_email=not args.sem_email)
    _mostrar(estado.obter(args.id))


def cmd_descartar(args, cfg):
    estado = Estado(ambiente=cfg["ambiente"])
    nota = estado.obter(args.id)
    if nota is None or nota["status"] in ("emitida", "enviada"):
        sys.exit("Só é possível descartar notas pendentes ou com erro.")
    estado.atualizar(args.id, status="descartada", motivo=args.motivo or "Descartada manualmente")
    print(f"Nota {args.id} descartada.")


def cmd_status(args, cfg):
    notas = Estado(ambiente=cfg["ambiente"]).listar(args.filtro)
    if not notas:
        print("Nenhuma nota registrada.")
    for n in notas:
        _mostrar(n)


def cmd_ler_relatorio(args, cfg):
    pdfs = [Path(p).read_bytes() for p in args.pdf]
    rel = None
    if not args.usar_claude:
        cnpj = dps_mod.so_digitos(cfg["prestador"]["cnpj"])
        rel = next((r for r in (extrator.ler_pdf_roma(c, cnpj) for c in pdfs) if r), None)
    rel = rel or extrator.extrair(pdfs, args.corpo or "", args.assunto or "")
    print(f"Leitor:        {rel.leitor}")
    print(f"Valor:         R$ {rel.valor}")
    print(f"Período:       {rel.periodo_inicio or '(não informado)'} até {rel.periodo_fim}")
    print(f"CNPJ tomador:  {rel.cnpj_tomador or '(não informado)'}")
    print(f"Confiança:     {rel.confianca}")
    print(f"Justificativa: {rel.justificativa}")
    if rel.instrucoes:
        print(f"Instruções:    {rel.instrucoes}")
    for a in rel.alertas:
        print(f"ALERTA:        {a}")
    if args.cliente:
        dados, motivos = pipeline.preparar_dados(
            cfg, cliente_por_id(cfg, args.cliente), rel,
            data_assunto=email_gmail.data_ate_no_assunto(args.assunto or ""),
        )
        print("\nDescrição da nota:\n" + dados["descricao"])
        print("\nBloqueios para emissão automática:", "; ".join(motivos) or "nenhum")


def cmd_gerar_dps(args, cfg):
    """Gera e assina uma DPS de teste sem enviar — para conferir o XML."""
    seg = carregar_segredos(exigir_email=False)
    cert = CertificadoA1.de_pfx(seg.cert_pfx, seg.cert_senha)
    cliente = cliente_por_id(cfg, args.cliente)
    serv = cliente["servico"]
    fim = date.fromisoformat(args.fim)
    descricao = serv["descricao"].format(periodo_inicio=serv["periodo_inicio_padrao"], periodo_fim=fim.strftime("%d/%m/%Y"))
    aliq = cfg["aliquota_simples_por_mes"].get(date.today().strftime("%Y-%m"))
    if aliq is None:
        sys.exit("Cadastre a alíquota do mês atual em 'aliquota_simples_por_mes'.")
    xml = dps_mod.montar_dps(cfg, cliente, dps_mod.DadosNota(
        numero_dps=args.numero, valor=Decimal(args.valor), descricao=descricao,
        competencia=date.today(), aliquota_simples=Decimal(str(aliq)),
    ))
    sys.stdout.write(dps_mod.xml_bytes(dps_mod.assinar_dps(xml, cert)).decode())
    print()


def cmd_testar_certificado(args, cfg):
    seg = carregar_segredos(exigir_email=False)
    cert = CertificadoA1.de_pfx(seg.cert_pfx, seg.cert_senha)
    cert.verificar_validade()
    print(f"Titular:  {cert.titular}")
    print(f"Validade: {cert.validade:%d/%m/%Y}")
    cnpj = cfg["prestador"]["cnpj"]
    if cnpj not in cert.titular:
        print(f"ATENÇÃO: o CNPJ {cnpj} do config.yaml não aparece no titular do certificado.")


def _mostrar(n):
    dados = json.loads(n["dados_json"] or "{}")
    print(
        f"#{n['id']:<4} {n['status']:<10} {n['cliente_id']:<16} "
        f"R$ {dados.get('valor', '-'):>10}  até {dados.get('periodo_fim', '-'):<10}  "
        f"NFS-e {n['numero_nfse'] or '-'}"
        + (f"\n       motivo: {n['motivo']}" if n["motivo"] else "")
    )


def main(argv=None):
    p = argparse.ArgumentParser(prog="nfse_auto", description="Emissão e envio automático de NFS-e")
    p.add_argument("--config", help="caminho do config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("verificar", help="procura relatórios novos no Gmail e processa")
    s.add_argument("--dias", type=int, default=30, help="quantos dias para trás buscar (padrão 30)")
    s.set_defaults(func=cmd_verificar)

    s = sub.add_parser("aprovar", help="emite e envia uma nota pendente")
    s.add_argument("id", type=int)
    s.add_argument("--sem-email", action="store_true", help="emite sem enviar ao cliente")
    s.set_defaults(func=cmd_aprovar)

    s = sub.add_parser("descartar", help="descarta uma nota pendente")
    s.add_argument("id", type=int)
    s.add_argument("--motivo")
    s.set_defaults(func=cmd_descartar)

    s = sub.add_parser("status", help="lista as notas registradas")
    s.add_argument("filtro", nargs="?", choices=["pendente", "emitida", "enviada", "erro", "descartada", "ja_faturado"])
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("ler-relatorio", help="testa a leitura de um PDF de relatório (não emite nada)")
    s.add_argument("pdf", nargs="+")
    s.add_argument("--corpo", help="texto do e-mail, se quiser incluir")
    s.add_argument("--assunto", help="assunto do e-mail, ex.: 'RELATÓRIO DE COMISSÃO <> ... (até 22/10/2026)'")
    s.add_argument("--cliente", help="id do cliente, para mostrar a descrição montada")
    s.add_argument("--usar-claude", action="store_true", help="pula o leitor exato e usa o Claude")
    s.set_defaults(func=cmd_ler_relatorio)

    s = sub.add_parser("gerar-dps", help="gera e assina uma DPS de teste e imprime o XML (não envia)")
    s.add_argument("--cliente", required=True)
    s.add_argument("--valor", required=True)
    s.add_argument("--fim", required=True, help="data final do período, AAAA-MM-DD")
    s.add_argument("--numero", type=int, default=999999)
    s.set_defaults(func=cmd_gerar_dps)

    s = sub.add_parser("testar-certificado", help="confere se o .pfx abre e está válido")
    s.set_defaults(func=cmd_testar_certificado)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        cfg = carregar_config(args.config)
        args.func(args, cfg)
    except ErroConfig as e:
        sys.exit(f"Erro de configuração: {e}")


if __name__ == "__main__":
    main()
