"""Montagem e validação da DPS (Declaração de Prestação de Serviços), leiaute nacional.

A ordem dos elementos é rígida (xs:sequence no XSD) — o builder segue exatamente a
sequência de `TCInfDPS` / `tiposComplexos_v1.01.xsd`. Nada é montado por dict
genérico justamente para não embaralhar essa ordem.
"""

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

from lxml import etree

from .paths import SCHEMAS_DIR

NFSE_NS = 'http://www.sped.fazenda.gov.br/nfse'
VERSAO_PADRAO = '1.01'
VER_APLIC = 'lzbot-nfse-1.0'

# tpAmb da DPS: 1 = produção, 2 = homologação (produção restrita)
TP_AMB = {'producao': '1', 'homologacao': '2'}


class DPSError(Exception):
    pass


## Helpers de formatação

def so_digitos(v) -> str:
    return re.sub(r'\D', '', str(v or ''))


def dec2(valor) -> str:
    """Formata monetário no padrão TSDec15V2 (sem zeros não significativos)."""
    d = Decimal(str(valor)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if d < 0:
        raise DPSError(f'Valor negativo não é aceito na DPS: {valor}')
    return '0' if d == 0 else f'{d:f}'


def _sub(pai, nome: str, texto=None):
    el = etree.SubElement(pai, f'{{{NFSE_NS}}}{nome}')
    if texto is not None:
        el.text = str(texto)
    return el


def montar_id(cmun: str, inscricao: str, serie, ndps) -> str:
    """"DPS" + Cód.Mun.(7) + TipoInscr(1) + Inscrição(14) + Série(5) + Núm(15) = 42 dígitos."""
    cmun = so_digitos(cmun).zfill(7)
    insc = so_digitos(inscricao)
    if len(insc) == 11:      # CPF: tipo 1, completar com zeros à esquerda até 14
        tipo, insc = '1', insc.zfill(14)
    elif len(insc) == 14:    # CNPJ: tipo 2
        tipo = '2'
    else:
        raise DPSError(f'Inscrição federal deve ser CPF (11) ou CNPJ (14 dígitos): {insc!r}')
    return f'DPS{cmun}{tipo}{insc}{so_digitos(serie).zfill(5)}{so_digitos(ndps).zfill(15)}'


def dh_emissao(quando: Optional[datetime] = None) -> str:
    """AAAA-MM-DDThh:mm:ss±hh:00 — o XSD exige offset explícito, não aceita 'Z'."""
    agora = quando or datetime.now().astimezone()
    if agora.tzinfo is None:
        agora = agora.astimezone()
    return agora.replace(microsecond=0).isoformat()


## Builder

def montar_dps(emitente: dict, pedido: dict, ambiente: str,
               versao: str = VERSAO_PADRAO):
    """Monta a árvore <DPS> a partir do cadastro do emitente e do pedido de emissão.

    `pedido` já vem normalizado pela CLI: ver `nfse.py:_montar_pedido`.
    """
    cmun_emi = so_digitos(emitente['cmun']).zfill(7)
    cnpj_emi = so_digitos(emitente['cnpj'])
    serie = so_digitos(pedido.get('serie') or emitente.get('serie_padrao') or '1')
    ndps = so_digitos(pedido['ndps'])
    if not ndps or ndps == '0':
        raise DPSError('nDPS deve ser >= 1')

    raiz = etree.Element(f'{{{NFSE_NS}}}DPS', nsmap={None: NFSE_NS})
    raiz.set('versao', versao)
    inf = _sub(raiz, 'infDPS')
    inf.set('Id', montar_id(cmun_emi, cnpj_emi, serie, ndps))

    ## Identificação da declaração
    _sub(inf, 'tpAmb', TP_AMB[ambiente])
    _sub(inf, 'dhEmi', pedido.get('dh_emi') or dh_emissao())
    _sub(inf, 'verAplic', VER_APLIC)
    _sub(inf, 'serie', str(int(serie)))
    _sub(inf, 'nDPS', str(int(ndps)))
    _sub(inf, 'dCompet', pedido.get('competencia') or date.today().isoformat())
    _sub(inf, 'tpEmit', str(pedido.get('tp_emit', 1)))
    _sub(inf, 'cLocEmi', cmun_emi)

    ## Prestador (emitente)
    prest = _sub(inf, 'prest')
    _sub(prest, 'CNPJ', cnpj_emi)
    if emitente.get('im'):
        _sub(prest, 'IM', so_digitos(emitente['im']) or emitente['im'])
    if emitente.get('razao_social'):
        _sub(prest, 'xNome', emitente['razao_social'])
    _endereco(prest, emitente.get('endereco'))
    if emitente.get('fone'):
        _sub(prest, 'fone', so_digitos(emitente['fone']))
    if emitente.get('email'):
        _sub(prest, 'email', emitente['email'])
    regime = emitente.get('regime') or {}
    reg = _sub(prest, 'regTrib')
    _sub(reg, 'opSimpNac', str(regime.get('opSimpNac', 1)))
    if regime.get('regApTribSN') is not None:
        _sub(reg, 'regApTribSN', str(regime['regApTribSN']))
    _sub(reg, 'regEspTrib', str(regime.get('regEspTrib', 0)))

    ## Tomador (opcional no leiaute, mas exigido pela maioria dos municípios)
    tomador = pedido.get('tomador') or {}
    if tomador:
        toma = _sub(inf, 'toma')
        doc = so_digitos(tomador.get('doc'))
        if len(doc) == 14:
            _sub(toma, 'CNPJ', doc)
        elif len(doc) == 11:
            _sub(toma, 'CPF', doc)
        elif tomador.get('nif'):
            _sub(toma, 'NIF', tomador['nif'])
        else:
            _sub(toma, 'cNaoNIF', str(tomador.get('cNaoNIF', 0)))
        if tomador.get('im'):
            _sub(toma, 'IM', tomador['im'])
        _sub(toma, 'xNome', tomador.get('nome') or 'CONSUMIDOR NAO IDENTIFICADO')
        _endereco(toma, tomador.get('endereco'))
        if tomador.get('fone'):
            _sub(toma, 'fone', so_digitos(tomador['fone']))
        if tomador.get('email'):
            _sub(toma, 'email', tomador['email'])

    ## Serviço
    serv = _sub(inf, 'serv')
    loc = _sub(serv, 'locPrest')
    _sub(loc, 'cLocPrestacao', so_digitos(pedido.get('local_prestacao') or cmun_emi).zfill(7))
    cserv = _sub(serv, 'cServ')
    _sub(cserv, 'cTribNac', so_digitos(pedido['cod_trib_nac']).zfill(6))
    _sub(cserv, 'xDescServ', pedido['descricao'])
    if pedido.get('cod_nbs'):
        _sub(cserv, 'cNBS', so_digitos(pedido['cod_nbs']))
    if pedido.get('cod_interno'):
        _sub(cserv, 'cIntContrib', pedido['cod_interno'])
    if pedido.get('info_complementar'):
        compl = _sub(serv, 'infoCompl')
        _sub(compl, 'xInfComp', pedido['info_complementar'])

    ## Valores e tributação
    valores = _sub(inf, 'valores')
    vserv = _sub(valores, 'vServPrest')
    if pedido.get('valor_recebido') is not None:
        _sub(vserv, 'vReceb', dec2(pedido['valor_recebido']))
    _sub(vserv, 'vServ', dec2(pedido['valor']))

    if pedido.get('desconto_incond') or pedido.get('desconto_cond'):
        desc = _sub(valores, 'vDescCondIncond')
        if pedido.get('desconto_incond'):
            _sub(desc, 'vDescIncond', dec2(pedido['desconto_incond']))
        if pedido.get('desconto_cond'):
            _sub(desc, 'vDescCond', dec2(pedido['desconto_cond']))

    trib = _sub(valores, 'trib')
    tmun = _sub(trib, 'tribMun')
    _sub(tmun, 'tribISSQN', str(pedido.get('trib_issqn', 1)))
    _sub(tmun, 'tpRetISSQN', str(pedido.get('tp_ret_issqn', 1)))
    if pedido.get('aliquota') is not None:
        _sub(tmun, 'pAliq', dec2(pedido['aliquota']))

    tfed = _tributos_federais(pedido)
    if tfed is not None:
        trib.append(tfed)

    tot = _sub(trib, 'totTrib')
    tot_trib = pedido.get('tot_trib') or {}
    if tot_trib:
        # vTotTrib exige os três entes juntos (federal/estadual/municipal)
        grupo = _sub(tot, 'vTotTrib')
        for tag, chave in (('vTotTribFed', 'fed'), ('vTotTribEst', 'est'), ('vTotTribMun', 'mun')):
            _sub(grupo, tag, dec2(tot_trib.get(chave, 0)))
    else:
        # 0 = "não informado" — evita declarar carga tributária estimada errada
        _sub(tot, 'indTotTrib', '0')

    return raiz


def _endereco(pai, endereco: Optional[dict]) -> None:
    if not endereco:
        return
    end = _sub(pai, 'end')
    nac = _sub(end, 'endNac')
    _sub(nac, 'cMun', so_digitos(endereco['cmun']).zfill(7))
    _sub(nac, 'CEP', so_digitos(endereco['cep']).zfill(8))
    _sub(end, 'xLgr', endereco['logradouro'])
    _sub(end, 'nro', str(endereco.get('numero') or 'S/N'))
    if endereco.get('complemento'):
        _sub(end, 'xCpl', endereco['complemento'])
    _sub(end, 'xBairro', endereco['bairro'])


def _tributos_federais(pedido: dict):
    campos = [('vRetCP', 'ret_cp'), ('vRetIRRF', 'ret_irrf'), ('vRetCSLL', 'ret_csll')]
    informados = [(tag, pedido[chave]) for tag, chave in campos if pedido.get(chave)]
    if not informados:
        return None
    tfed = etree.Element(f'{{{NFSE_NS}}}tribFed')
    for tag, valor in informados:
        _sub(tfed, tag, dec2(valor))
    return tfed


## Serialização e validação

def serializar(raiz) -> bytes:
    """UTF-8, sem BOM, sem pretty-print (o manual proíbe caracteres de formatação)."""
    return etree.tostring(raiz, xml_declaration=True, encoding='UTF-8', pretty_print=False)


_ANCORA_XSD = re.compile(r'(<xs:pattern value=")\^(.*?)\$(")')


class _ResolverXSD(etree.Resolver):
    """Carrega os XSD oficiais corrigindo âncoras de regex inválidas em XSD.

    `tiposSimples_v1.01.xsd` publica o padrão da série como `^0{0,4}\\d{1,5}$`.
    XSD 1.0 já ancora o pattern implicitamente e NÃO define `^`/`$` como âncoras —
    o libxml2 os trata como caracteres literais e rejeita qualquer série válida.
    O validador .NET usado pela Sefin engole a construção, então o defeito só
    aparece aqui. Os arquivos em `schemas/` seguem idênticos ao publicado pelo
    gov.br; a correção acontece só na carga.
    """

    def __init__(self, base: Path):
        super().__init__()
        self.base = base

    def resolve(self, url, id, context):
        caminho = self.base / Path(url).name
        if not caminho.exists():
            return None
        texto = _ANCORA_XSD.sub(r'\1\2\3', caminho.read_text(encoding='utf-8-sig'))
        return self.resolve_string(texto, context, base_url=str(caminho))


def _schema(nome_xsd: str, versao: str):
    caminho = SCHEMAS_DIR / versao / nome_xsd
    if not caminho.exists():
        raise DPSError(f'XSD não encontrado: {caminho}')
    parser = etree.XMLParser()
    parser.resolvers.add(_ResolverXSD(caminho.parent))
    return etree.XMLSchema(etree.parse(str(caminho), parser))


def validar_xsd(raiz, nome_xsd: str = 'DPS_v1.01.xsd', versao: str = VERSAO_PADRAO) -> list[str]:
    """Valida contra o esquema oficial. Retorna [] quando válido."""
    schema = _schema(nome_xsd, versao)
    # revalida a partir do XML serializado — é o que o fisco vai receber de fato
    doc = etree.fromstring(serializar(raiz))
    if schema.validate(doc):
        return []
    return [f'linha {e.line}: {e.message}' for e in schema.error_log]


def xsd_disponivel(versao: str = VERSAO_PADRAO) -> bool:
    return (Path(SCHEMAS_DIR) / versao / 'DPS_v1.01.xsd').exists()
