"""Pedido de Registro de Evento — hoje cobre o cancelamento (e101101).

Leiaute: pedRegEvento_v1.01.xsd / tiposEventos_v1.01.xsd.
Id do pedido: "PRE" + 56 dígitos = chave de acesso (50) + tipo de evento (6).
"""

from datetime import datetime
from typing import Optional

from lxml import etree

from .dps import NFSE_NS, TP_AMB, VER_APLIC, VERSAO_PADRAO, DPSError, _sub, dh_emissao, so_digitos
from .dps import _schema

# Códigos de justificativa aceitos no cancelamento (TSCodJustCanc)
MOTIVOS_CANCELAMENTO = {
    '1': 'Erro na emissão',
    '2': 'Serviço não prestado',
    '9': 'Outros',
}

TIPO_EVENTO_CANCELAMENTO = '101101'


class EventoError(Exception):
    pass


def montar_cancelamento(chave: str, cod_motivo: str, motivo: str, cnpj_autor: str,
                        ambiente: str, quando: Optional[datetime] = None,
                        versao: str = VERSAO_PADRAO):
    chave = so_digitos(chave)
    if len(chave) != 50:
        raise EventoError(f'Chave de acesso deve ter 50 dígitos, recebi {len(chave)}')
    cod_motivo = str(cod_motivo)
    if cod_motivo not in MOTIVOS_CANCELAMENTO:
        raise EventoError(
            f'cod_motivo inválido: {cod_motivo}. Use um de: '
            + ', '.join(f'{k} ({v})' for k, v in MOTIVOS_CANCELAMENTO.items())
        )
    # TSMotivo exige de 15 a 255 caracteres — rejeitar aqui é mais barato que levar E-rejeição
    if not 15 <= len(motivo) <= 255:
        raise EventoError(
            f'Motivo precisa ter entre 15 e 255 caracteres (tem {len(motivo)}).'
        )

    raiz = etree.Element(f'{{{NFSE_NS}}}pedRegEvento', nsmap={None: NFSE_NS})
    raiz.set('versao', versao)
    inf = _sub(raiz, 'infPedReg')
    inf.set('Id', f'PRE{chave}{TIPO_EVENTO_CANCELAMENTO}')

    _sub(inf, 'tpAmb', TP_AMB[ambiente])
    _sub(inf, 'verAplic', VER_APLIC)
    _sub(inf, 'dhEvento', dh_emissao(quando))
    _sub(inf, 'CNPJAutor', so_digitos(cnpj_autor))
    _sub(inf, 'chNFSe', chave)

    ev = _sub(inf, 'e101101')
    _sub(ev, 'xDesc', 'Cancelamento de NFS-e')  # valor fixo exigido pelo XSD
    _sub(ev, 'cMotivo', cod_motivo)
    _sub(ev, 'xMotivo', motivo)
    return raiz


def validar_xsd(raiz, versao: str = VERSAO_PADRAO) -> list[str]:
    from .dps import serializar
    schema = _schema('pedRegEvento_v1.01.xsd', versao)
    doc = etree.fromstring(serializar(raiz))
    if schema.validate(doc):
        return []
    return [f'linha {e.line}: {e.message}' for e in schema.error_log]
