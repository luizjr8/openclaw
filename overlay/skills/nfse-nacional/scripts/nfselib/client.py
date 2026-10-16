"""Cliente HTTP mTLS para o Sistema Nacional NFS-e.

Dois hosts distintos:
  - Sefin Nacional (`/SefinNacional`): emissão da DPS, consulta de NFS-e, eventos;
  - ADN (`/contribuintes`): distribuição de DF-e por NSU.

Toda requisição usa autenticação mútua TLS com o certificado do emitente — não há
token nem header de autenticação. Sem o client cert o serviço responde 403 (Sefin)
ou 496 "SSL Certificate Required" (ADN).

XMLs trafegam sempre como GZip + base64 dentro do JSON.
"""

import base64
import gzip
import json
from typing import Optional

import requests

from .cert import pem_temporario

BASES = {
    'homologacao': {
        'sefin': 'https://sefin.producaorestrita.nfse.gov.br/SefinNacional',
        'adn': 'https://adn.producaorestrita.nfse.gov.br/contribuintes',
    },
    'producao': {
        'sefin': 'https://sefin.nfse.gov.br/SefinNacional',
        'adn': 'https://adn.nfse.gov.br/contribuintes',
    },
}

TIMEOUT = (15, 120)  # (connect, read) — a emissão é síncrona e pode demorar


class ApiError(Exception):
    def __init__(self, mensagem: str, status: Optional[int] = None, corpo=None):
        super().__init__(mensagem)
        self.status = status
        self.corpo = corpo


## Compactação

def empacotar(xml: bytes) -> str:
    """XML -> GZip -> base64, como exigido pelas APIs."""
    return base64.b64encode(gzip.compress(xml)).decode()


def desempacotar(b64: str) -> str:
    return gzip.decompress(base64.b64decode(b64)).decode('utf-8')


class ClienteNFSe:
    def __init__(self, certificado, ambiente: str = 'homologacao'):
        if ambiente not in BASES:
            raise ApiError(f'Ambiente inválido: {ambiente}')
        self.certificado = certificado
        self.ambiente = ambiente
        self.bases = BASES[ambiente]

    ## Transporte

    def _request(self, metodo: str, url: str, payload: Optional[dict] = None) -> dict:
        with pem_temporario(self.certificado) as pem:
            try:
                resp = requests.request(
                    metodo, url, json=payload, cert=pem, timeout=TIMEOUT,
                    headers={'Accept': 'application/json'},
                )
            except requests.exceptions.SSLError as e:
                raise ApiError(f'Falha no handshake mTLS: {e}') from e
            except requests.exceptions.RequestException as e:
                raise ApiError(f'Falha de rede ao chamar {url}: {e}') from e

        try:
            corpo = resp.json() if resp.content else {}
        except json.JSONDecodeError:
            corpo = {'resposta_bruta': resp.text[:2000]}

        if resp.status_code >= 400:
            raise ApiError(
                _descrever_erro(resp.status_code, corpo), status=resp.status_code, corpo=corpo
            )
        return corpo

    ## Sefin Nacional

    def emitir(self, dps_xml: bytes) -> dict:
        """POST /nfse — recepção síncrona da DPS assinada."""
        return self._request(
            'POST', f'{self.bases["sefin"]}/nfse', {'dpsXmlGZipB64': empacotar(dps_xml)}
        )

    def consultar_nfse(self, chave: str) -> dict:
        """GET /nfse/{chaveAcesso}."""
        return self._request('GET', f'{self.bases["sefin"]}/nfse/{chave}')

    def consultar_dps(self, id_dps: str) -> dict:
        """GET /dps/{id} — devolve a chave de acesso gerada para aquela DPS."""
        return self._request('GET', f'{self.bases["sefin"]}/dps/{id_dps}')

    def registrar_evento(self, chave: str, pedido_xml: bytes) -> dict:
        """POST /nfse/{chaveAcesso}/eventos."""
        return self._request(
            'POST', f'{self.bases["sefin"]}/nfse/{chave}/eventos',
            {'pedidoRegistroEventoXmlGZipB64': empacotar(pedido_xml)},
        )

    def consultar_eventos(self, chave: str, tipo: str, seq: int = 1) -> dict:
        """GET /nfse/{chaveAcesso}/eventos/{tipoEvento}/{numSeqEvento}."""
        return self._request(
            'GET', f'{self.bases["sefin"]}/nfse/{chave}/eventos/{tipo}/{seq}'
        )

    ## ADN — distribuição

    def baixar_dfe(self, nsu: int, cnpj: Optional[str] = None) -> dict:
        """GET /DFe/{NSU} — documentos em que o certificado figura como parte."""
        url = f'{self.bases["adn"]}/DFe/{nsu}'
        if cnpj:
            url += f'?cnpj={cnpj}'
        return self._request('GET', url)


def _descrever_erro(status: int, corpo: dict) -> str:
    mensagens = []
    for item in (corpo.get('erros') or []):
        mensagens.append(_msg(item))
    if corpo.get('erro'):
        mensagens.append(_msg(corpo['erro']))
    if mensagens:
        return f'HTTP {status}: ' + ' | '.join(mensagens)
    if status == 403:
        return ('HTTP 403: certificado de transmissão recusado. Confira se o .pfx é '
                'ICP-Brasil, está válido e pertence ao CNPJ raiz do emitente.')
    if status == 496:
        return 'HTTP 496: o servidor exigiu certificado de cliente e não recebeu nenhum.'
    return f'HTTP {status}: {json.dumps(corpo, ensure_ascii=False)[:500]}'


def _msg(item: dict) -> str:
    partes = [item.get('codigo'), item.get('mensagem') or item.get('descricao')]
    if item.get('complemento'):
        partes.append(f'({item["complemento"]})')
    return ' '.join(p for p in partes if p)
