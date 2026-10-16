#!/usr/bin/env python3
"""NFS-e padrão nacional (Sefin Nacional / ADN) — emissão direta, sem intermediário.

Multi-tenant: cada CNPJ emitente tem seu próprio cadastro e certificado. Nenhum
dado de empresa é embutido no código.

Uso rápido:
  nfse.py emitentes add --cnpj 12345678000199 --razao-social "ACME LTDA" \
      --cmun 2611606 --im 123456 --cert /caminho/cert.pfx
  nfse.py emitir --emitente 12345678000199 --valor 1500 --cod-trib-nac 010701 \
      --descricao "Consultoria em TI" --tomador-doc 98765432000188 \
      --tomador-nome "CLIENTE SA" --aliquota 5 --dry-run
  nfse.py consultar --chave <50 dígitos> --emitente 12345678000199
  nfse.py cancelar --chave <50 dígitos> --emitente 12345678000199 \
      --cod-motivo 1 --motivo "Erro no valor do serviço prestado"
  nfse.py baixar --nsu 0 --emitente 12345678000199

Todos os comandos escrevem JSON puro no stdout; o LLM formata para o usuário.
AMBIENTE PADRÃO É HOMOLOGAÇÃO. Produção só com --producao.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nfselib import config, store  # noqa: E402
from nfselib.cert import CertError, carregar_pfx  # noqa: E402
from nfselib.client import ApiError, ClienteNFSe, desempacotar  # noqa: E402
from nfselib.dps import (  # noqa: E402
    DPSError, montar_dps, serializar, so_digitos, validar_xsd,
)
from nfselib.evento import (  # noqa: E402
    EventoError, MOTIVOS_CANCELAMENTO, montar_cancelamento,
)
from nfselib.evento import validar_xsd as validar_evento_xsd  # noqa: E402
from nfselib.paths import XML_DIR, ensure_dirs  # noqa: E402
from nfselib.xmlsig import assinar, conferir  # noqa: E402


def emitir_json(dados) -> None:
    print(json.dumps(dados, ensure_ascii=False, indent=2, default=str))


## Ambiente e certificado

def resolver_ambiente(emitente: dict, producao: bool) -> str:
    """Homologação é o padrão. Produção exige --producao E cadastro liberado."""
    if not producao:
        return 'homologacao'
    if emitente.get('ambiente') != 'producao':
        raise config.ConfigError(
            'Emitente cadastrado com ambiente="homologacao"; --producao foi recusado. '
            'Para liberar produção rode: nfse.py emitentes add --cnpj <cnpj> '
            '--ambiente producao (e demais campos).'
        )
    return 'producao'


def abrir_certificado(emitente: dict):
    senha = config.senha_certificado(emitente)
    cert = carregar_pfx(emitente['cert_path'], senha)
    if cert.expirado:
        raise CertError(
            f'Certificado expirado em {cert.nao_depois.isoformat()} — a Sefin rejeita (E1203).'
        )
    cnpj_cert = cert.cnpj
    if cnpj_cert and cnpj_cert[:8] != so_digitos(emitente['cnpj'])[:8]:
        raise CertError(
            f'CNPJ raiz do certificado ({cnpj_cert[:8]}) difere do emitente '
            f'({so_digitos(emitente["cnpj"])[:8]}). A Sefin rejeita a transmissão.'
        )
    return cert


def algoritmo_assinatura(emitente: dict, args) -> str:
    if getattr(args, 'sha256', False):
        return 'sha256'
    return (emitente.get('assinatura') or {}).get('digest', 'sha1')


## Comando: emitentes

def cmd_emitentes(args) -> None:
    if args.sub == 'listar':
        emitir_json({'emitentes': config.listar_emitentes()})
    elif args.sub == 'mostrar':
        emitir_json(config.resumo_publico(config.carregar_emitente(args.cnpj)))
    elif args.sub == 'remover':
        caminho = config.remover_emitente(args.cnpj)
        emitir_json({'removido': str(caminho)})
    elif args.sub == 'add':
        emitir_json(_salvar_emitente(args))


def _salvar_emitente(args) -> dict:
    cnpj = config.so_digitos(args.cnpj)
    dados = {
        'cnpj': cnpj,
        'razao_social': args.razao_social,
        'im': args.im,
        'cmun': config.so_digitos(args.cmun),
        'ambiente': args.ambiente,
        'serie_padrao': args.serie or '1',
        'cert_path': str(Path(args.cert).expanduser().resolve()),
        'cert_pass_env': args.cert_pass_env or config.env_senha_padrao(cnpj),
        'regime': {
            'opSimpNac': args.op_simp_nac,
            'regApTribSN': args.reg_ap_trib_sn,
            'regEspTrib': args.reg_esp_trib,
        },
        'fone': args.fone,
        'email': args.email,
        'assinatura': {'digest': 'sha256' if args.sha256 else 'sha1'},
    }
    if args.logradouro:
        dados['endereco'] = {
            'logradouro': args.logradouro,
            'numero': args.numero,
            'complemento': args.complemento,
            'bairro': args.bairro,
            'cmun': config.so_digitos(args.endereco_cmun or args.cmun),
            'cep': config.so_digitos(args.cep),
        }
    caminho = config.salvar_emitente(dados)
    resumo = config.resumo_publico(dados)
    resumo['arquivo'] = str(caminho)
    if not resumo['senha_definida']:
        resumo['proximo_passo'] = (
            f'Defina a senha do .pfx: export {resumo["cert_pass_env"]}="<senha>"'
        )
    return resumo


## Comando: emitir

def _montar_pedido(args) -> dict:
    pedido = {
        'ndps': None,
        'serie': args.serie,
        'competencia': args.competencia,
        'tp_emit': args.tp_emit,
        'local_prestacao': args.local_prestacao,
        'cod_trib_nac': args.cod_trib_nac,
        'cod_nbs': args.cod_nbs,
        'cod_interno': args.cod_interno,
        'descricao': args.descricao,
        'info_complementar': args.info_complementar,
        'valor': args.valor,
        'valor_recebido': args.valor_recebido,
        'desconto_incond': args.desconto_incondicional,
        'desconto_cond': args.desconto_condicional,
        'aliquota': args.aliquota,
        'trib_issqn': args.trib_issqn,
        'tp_ret_issqn': 2 if args.iss_retido else args.tp_ret_issqn,
        'ret_cp': args.ret_cp,
        'ret_irrf': args.ret_irrf,
        'ret_csll': args.ret_csll,
    }
    if args.tomador_doc or args.tomador_nome:
        pedido['tomador'] = {
            'doc': args.tomador_doc,
            'nome': args.tomador_nome,
            'im': args.tomador_im,
            'email': args.tomador_email,
            'fone': args.tomador_fone,
        }
    if args.extra:
        # escape hatch para campos que a CLI não expõe (ver leiaute Anexo I).
        # Aceita JSON inline ou caminho de arquivo — um JSON longo estoura o
        # limite de nome de arquivo, então o teste de path é protegido.
        bruto = args.extra
        try:
            if Path(bruto).is_file():
                bruto = Path(bruto).read_text(encoding='utf-8')
        except OSError:
            pass
        pedido.update(json.loads(bruto))
    if not pedido.get('cod_trib_nac'):
        raise DPSError('--cod-trib-nac é obrigatório (código de tributação nacional, 6 dígitos)')
    if not pedido.get('descricao'):
        raise DPSError('--descricao é obrigatória')
    if pedido.get('valor') is None:
        raise DPSError('--valor é obrigatório')
    return pedido


def cmd_emitir(args) -> None:
    ensure_dirs()
    emitente = config.carregar_emitente(args.emitente)
    ambiente = resolver_ambiente(emitente, args.producao)
    pedido = _montar_pedido(args)
    cert = abrir_certificado(emitente)
    algoritmo = algoritmo_assinatura(emitente, args)

    cnpj = so_digitos(emitente['cnpj'])
    serie = so_digitos(pedido.get('serie') or emitente.get('serie_padrao') or '1')
    conn = store.conectar()
    ndps = store.reservar_ndps(conn, cnpj, ambiente, serie, args.ndps)
    pedido['serie'], pedido['ndps'] = serie, ndps

    ## Montagem, assinatura e validação contra o XSD oficial
    raiz = montar_dps(emitente, pedido, ambiente)
    id_dps = raiz[0].get('Id')
    assinar(raiz, 'infDPS', cert, algoritmo)
    erros = validar_xsd(raiz)
    xml = serializar(raiz)

    destino = XML_DIR / f'{id_dps}-dps.xml'
    destino.write_bytes(xml)

    resultado = {
        'ambiente': ambiente,
        'id_dps': id_dps,
        'serie': serie,
        'ndps': ndps,
        'algoritmo_assinatura': algoritmo,
        'xml_dps': str(destino),
        'validacao_xsd': {'valido': not erros, 'erros': erros},
        'certificado': cert.info(),
    }
    if erros:
        resultado['status'] = 'rejeitado_localmente'
        resultado['aviso'] = 'DPS não passou no XSD oficial; nada foi enviado à Sefin.'
        emitir_json(resultado)
        sys.exit(1)

    if args.dry_run:
        resultado['status'] = 'dry_run'
        resultado['conferencia_assinatura'] = conferir(raiz, cert, algoritmo)
        resultado['aviso'] = 'Nada foi enviado à Sefin (--dry-run).'
        emitir_json(resultado)
        return

    nota_id = store.registrar(
        conn, cnpj=cnpj, ambiente=ambiente, serie=serie, ndps=ndps, id_dps=id_dps,
        status='pendente', dh_emissao=datetime.now().astimezone().isoformat(),
        valor=str(pedido['valor']), descricao=pedido['descricao'],
        tomador_doc=(pedido.get('tomador') or {}).get('doc'),
        tomador_nome=(pedido.get('tomador') or {}).get('nome'),
        xml_path=str(destino),
    )

    cliente = ClienteNFSe(cert, ambiente)
    try:
        resposta = cliente.emitir(xml)
    except ApiError as e:
        store.atualizar(conn, nota_id, status='rejeitado', erro=str(e),
                        resposta=json.dumps(e.corpo, ensure_ascii=False))
        resultado.update({'status': 'rejeitado', 'erro': str(e), 'http_status': e.status,
                          'resposta': e.corpo})
        emitir_json(resultado)
        sys.exit(1)

    chave = resposta.get('chaveAcesso')
    xml_nfse = None
    if resposta.get('nfseXmlGZipB64'):
        xml_nfse = XML_DIR / f'{chave}-nfse.xml'
        xml_nfse.write_text(desempacotar(resposta['nfseXmlGZipB64']), encoding='utf-8')

    store.atualizar(conn, nota_id, status='autorizada', chave_acesso=chave,
                    resposta=json.dumps(resposta, ensure_ascii=False))
    resultado.update({
        'status': 'autorizada',
        'chave_acesso': chave,
        'data_processamento': resposta.get('dataHoraProcessamento'),
        'xml_nfse': str(xml_nfse) if xml_nfse else None,
    })
    emitir_json(resultado)


## Comando: consultar / baixar / cancelar

def cmd_consultar(args) -> None:
    emitente = config.carregar_emitente(args.emitente)
    ambiente = resolver_ambiente(emitente, args.producao)
    cert = abrir_certificado(emitente)
    cliente = ClienteNFSe(cert, ambiente)

    chave = so_digitos(args.chave)
    if len(chave) != 50:
        raise DPSError(f'Chave de acesso deve ter 50 dígitos, recebi {len(chave)}')
    resposta = cliente.consultar_nfse(chave)

    saida = {'ambiente': ambiente, 'chave_acesso': resposta.get('chaveAcesso', chave),
             'data_processamento': resposta.get('dataHoraProcessamento')}
    if resposta.get('nfseXmlGZipB64'):
        destino = XML_DIR / f'{chave}-nfse.xml'
        ensure_dirs()
        destino.write_text(desempacotar(resposta['nfseXmlGZipB64']), encoding='utf-8')
        saida['xml_nfse'] = str(destino)
        if args.xml:
            saida['xml'] = destino.read_text(encoding='utf-8')
    saida['registro_local'] = store.por_chave(store.conectar(), chave)
    emitir_json(saida)


def cmd_baixar(args) -> None:
    emitente = config.carregar_emitente(args.emitente)
    ambiente = resolver_ambiente(emitente, args.producao)
    cert = abrir_certificado(emitente)
    resposta = ClienteNFSe(cert, ambiente).baixar_dfe(args.nsu, args.cnpj_consulta)
    emitir_json({'ambiente': ambiente, 'nsu': args.nsu, 'resposta': resposta})


def cmd_cancelar(args) -> None:
    emitente = config.carregar_emitente(args.emitente)
    ambiente = resolver_ambiente(emitente, args.producao)
    cert = abrir_certificado(emitente)
    algoritmo = algoritmo_assinatura(emitente, args)

    chave = so_digitos(args.chave)
    raiz = montar_cancelamento(chave, args.cod_motivo, args.motivo,
                               so_digitos(emitente['cnpj']), ambiente)
    assinar(raiz, 'infPedReg', cert, algoritmo)
    erros = validar_evento_xsd(raiz)
    xml = serializar(raiz)

    ensure_dirs()
    destino = XML_DIR / f'{chave}-cancelamento.xml'
    destino.write_bytes(xml)

    saida = {'ambiente': ambiente, 'chave_acesso': chave, 'xml_pedido': str(destino),
             'motivo': {'codigo': args.cod_motivo,
                        'rotulo': MOTIVOS_CANCELAMENTO[str(args.cod_motivo)],
                        'texto': args.motivo},
             'validacao_xsd': {'valido': not erros, 'erros': erros}}
    if erros:
        saida['status'] = 'rejeitado_localmente'
        emitir_json(saida)
        sys.exit(1)
    if args.dry_run:
        saida['status'] = 'dry_run'
        emitir_json(saida)
        return

    try:
        resposta = ClienteNFSe(cert, ambiente).registrar_evento(chave, xml)
    except ApiError as e:
        saida.update({'status': 'rejeitado', 'erro': str(e), 'resposta': e.corpo})
        emitir_json(saida)
        sys.exit(1)

    if resposta.get('eventoXmlGZipB64'):
        ev = XML_DIR / f'{chave}-evento-101101.xml'
        ev.write_text(desempacotar(resposta['eventoXmlGZipB64']), encoding='utf-8')
        saida['xml_evento'] = str(ev)

    conn = store.conectar()
    if store.por_chave(conn, chave):
        store.atualizar(conn, store.por_chave(conn, chave)['id'], status='cancelada')
    saida.update({'status': 'cancelada',
                  'data_processamento': resposta.get('dataHoraProcessamento')})
    emitir_json(saida)


def cmd_notas(args) -> None:
    conn = store.conectar()
    emitir_json({'notas': store.listar(
        conn, config.so_digitos(args.emitente) if args.emitente else None,
        args.ambiente, args.limite)})


## Parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='nfse.py', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    def com_emitente(sp, producao=True):
        sp.add_argument('--emitente', required=True, metavar='CNPJ',
                        help='CNPJ do emitente cadastrado')
        if producao:
            sp.add_argument('--producao', action='store_true',
                            help='AMBIENTE REAL. Sem esta flag tudo roda em homologação.')

    ## emitentes
    pe = sub.add_parser('emitentes', help='Cadastro multi-tenant de emitentes')
    pes = pe.add_subparsers(dest='sub', required=True)
    pes.add_parser('listar', help='Lista emitentes cadastrados')
    pm = pes.add_parser('mostrar', help='Detalha um emitente')
    pm.add_argument('--cnpj', required=True)
    pr = pes.add_parser('remover', help='Remove o cadastro de um emitente')
    pr.add_argument('--cnpj', required=True)

    pa = pes.add_parser('add', help='Cria/atualiza um emitente')
    pa.add_argument('--cnpj', required=True)
    pa.add_argument('--razao-social', required=True)
    pa.add_argument('--cmun', required=True, metavar='IBGE7',
                    help='Código IBGE (7 dígitos) do município emissor')
    pa.add_argument('--cert', required=True, metavar='CAMINHO',
                    help='Caminho do certificado A1 .pfx/.p12')
    pa.add_argument('--im', help='Inscrição municipal')
    pa.add_argument('--ambiente', choices=['homologacao', 'producao'], default='homologacao',
                    help='Ambiente máximo liberado para este emitente (padrão: homologacao)')
    pa.add_argument('--serie', help='Série padrão da DPS (padrão: 1; faixa própria: 00001-49999)')
    pa.add_argument('--cert-pass-env', metavar='VAR',
                    help='Nome da env var com a senha do .pfx (padrão: NFSE_CERT_PASS_<CNPJ>)')
    pa.add_argument('--op-simp-nac', type=int, choices=[1, 2, 3], default=1,
                    help='1-Não optante; 2-MEI; 3-ME/EPP')
    pa.add_argument('--reg-ap-trib-sn', type=int, choices=[1, 2, 3],
                    help='Regime de apuração do Simples Nacional (só para ME/EPP)')
    pa.add_argument('--reg-esp-trib', type=int, default=0,
                    help='Regime especial de tributação (0 = nenhum)')
    pa.add_argument('--fone')
    pa.add_argument('--email')
    pa.add_argument('--logradouro')
    pa.add_argument('--numero')
    pa.add_argument('--complemento')
    pa.add_argument('--bairro')
    pa.add_argument('--cep')
    pa.add_argument('--endereco-cmun', help='IBGE do endereço, se diferente do município emissor')
    pa.add_argument('--sha256', action='store_true',
                    help='Assinar com RSA-SHA256 (o manual publicado especifica SHA-1)')

    ## emitir
    pem = sub.add_parser('emitir', help='Monta, assina e envia a DPS')
    com_emitente(pem)
    pem.add_argument('--valor', type=float, required=True, help='Valor do serviço (vServ)')
    pem.add_argument('--descricao', help='Descrição do serviço (xDescServ)')
    pem.add_argument('--cod-trib-nac', help='Código de tributação nacional, 6 dígitos (ex: 010701)')
    pem.add_argument('--aliquota', type=float, help='Alíquota do ISSQN em %% (pAliq)')
    pem.add_argument('--tomador-doc', help='CPF ou CNPJ do tomador')
    pem.add_argument('--tomador-nome')
    pem.add_argument('--tomador-im')
    pem.add_argument('--tomador-email')
    pem.add_argument('--tomador-fone')
    pem.add_argument('--serie', help='Série da DPS (padrão: a do emitente)')
    pem.add_argument('--ndps', type=int, help='Forçar número da DPS (padrão: próximo sequencial)')
    pem.add_argument('--competencia', metavar='YYYY-MM-DD', help='Data de competência (padrão: hoje)')
    pem.add_argument('--local-prestacao', metavar='IBGE7',
                     help='Município da prestação (padrão: o do emitente)')
    pem.add_argument('--tp-emit', type=int, choices=[1, 2, 3], default=1,
                     help='1-Prestador; 2-Tomador; 3-Intermediário')
    pem.add_argument('--trib-issqn', type=int, choices=[1, 2, 3, 4], default=1,
                     help='1-Operação tributável; 2-Exportação; 3-Não incidência; 4-Imunidade')
    pem.add_argument('--tp-ret-issqn', type=int, choices=[1, 2, 3], default=1,
                     help='1-Não retido; 2-Retido pelo tomador; 3-Retido pelo intermediário')
    pem.add_argument('--iss-retido', action='store_true', help='Atalho para --tp-ret-issqn 2')
    pem.add_argument('--valor-recebido', type=float, help='vReceb, quando difere de vServ')
    pem.add_argument('--desconto-incondicional', type=float)
    pem.add_argument('--desconto-condicional', type=float)
    pem.add_argument('--ret-cp', type=float, help='Retenção de contribuição previdenciária')
    pem.add_argument('--ret-irrf', type=float)
    pem.add_argument('--ret-csll', type=float)
    pem.add_argument('--cod-nbs', help='Código NBS')
    pem.add_argument('--cod-interno', help='Código interno do contribuinte')
    pem.add_argument('--info-complementar', help='Informações complementares (xInfComp)')
    pem.add_argument('--extra', metavar='JSON|ARQUIVO',
                     help='JSON com campos extras do pedido (sobrepõe os args)')
    pem.add_argument('--sha256', action='store_true', help='Forçar RSA-SHA256 nesta emissão')
    pem.add_argument('--dry-run', action='store_true',
                     help='Monta, assina e valida no XSD sem enviar nada à Sefin')

    ## consultar
    pc = sub.add_parser('consultar', help='Consulta NFS-e por chave de acesso')
    com_emitente(pc)
    pc.add_argument('--chave', required=True, metavar='CHAVE50')
    pc.add_argument('--xml', action='store_true', help='Inclui o XML inteiro na saída')

    ## baixar
    pb = sub.add_parser('baixar', help='Distribuição de DF-e no ADN por NSU')
    com_emitente(pb)
    pb.add_argument('--nsu', type=int, required=True, help='Número Sequencial Único')
    pb.add_argument('--cnpj-consulta', help='Consultar outro CNPJ da mesma raiz')

    ## cancelar
    pcan = sub.add_parser('cancelar', help='Cancela NFS-e (evento 101101)')
    com_emitente(pcan)
    pcan.add_argument('--chave', required=True, metavar='CHAVE50')
    pcan.add_argument('--motivo', required=True, help='Justificativa (15 a 255 caracteres)')
    pcan.add_argument('--cod-motivo', default='1', choices=list(MOTIVOS_CANCELAMENTO),
                      help='1-Erro na emissão; 2-Serviço não prestado; 9-Outros')
    pcan.add_argument('--sha256', action='store_true')
    pcan.add_argument('--dry-run', action='store_true',
                      help='Monta e valida o pedido sem enviar')

    ## notas
    pn = sub.add_parser('notas', help='Lista notas registradas localmente')
    pn.add_argument('--emitente', metavar='CNPJ')
    pn.add_argument('--ambiente', choices=['homologacao', 'producao'])
    pn.add_argument('--limite', type=int, default=50)

    return p


def main() -> None:
    args = build_parser().parse_args()
    dispatch = {
        'emitentes': cmd_emitentes,
        'emitir': cmd_emitir,
        'consultar': cmd_consultar,
        'baixar': cmd_baixar,
        'cancelar': cmd_cancelar,
        'notas': cmd_notas,
    }
    try:
        dispatch[args.cmd](args)
    except (config.ConfigError, CertError, DPSError, EventoError, ApiError, ValueError) as e:
        emitir_json({'erro': str(e), 'tipo': type(e).__name__})
        sys.exit(1)


if __name__ == '__main__':
    main()
