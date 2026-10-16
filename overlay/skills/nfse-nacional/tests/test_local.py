#!/usr/bin/env python3
"""Autoteste offline: gera um certificado ICP-like autoassinado, monta uma DPS,
assina, valida contra o XSD oficial e confere a assinatura.

NÃO fala com a Sefin — valida só o que dá para validar sem certificado real.

    python3 tests/test_local.py
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / 'scripts'))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs12  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

CNPJ = '11222333000181'
SENHA = 'teste123'
OID_CNPJ_ICP = '2.16.76.1.3.3'

falhas: list[str] = []


def checar(nome: str, condicao: bool, detalhe: str = '') -> None:
    print(f'{"ok  " if condicao else "FALHA"} {nome}{" — " + detalhe if detalhe else ""}')
    if not condicao:
        falhas.append(nome)


def gerar_pfx(destino: Path) -> None:
    """Certificado autoassinado com o otherName de CNPJ da ICP-Brasil."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'BR'),
        x509.NameAttribute(NameOID.COMMON_NAME, f'EMPRESA TESTE LTDA:{CNPJ}'),
    ])
    # otherName ICP-Brasil: valor DER de uma PrintableString com o CNPJ
    valor = CNPJ.encode()
    der = bytes([0x13, len(valor)]) + valor
    agora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome).issuer_name(nome)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=1))
        .not_valid_after(agora + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=True,
                          key_encipherment=True, data_encipherment=False,
                          key_agreement=False, key_cert_sign=False, crl_sign=False,
                          encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.OtherName(x509.ObjectIdentifier(OID_CNPJ_ICP), der)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    destino.write_bytes(pkcs12.serialize_key_and_certificates(
        b'teste', key, cert, None,
        serialization.BestAvailableEncryption(SENHA.encode())))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='nfse-test-'))
    os.environ['NFSE_DATA_DIR'] = str(tmp / 'dados')
    os.environ[f'NFSE_CERT_PASS_{CNPJ}'] = SENHA

    pfx = tmp / 'teste.pfx'
    gerar_pfx(pfx)

    from nfselib import config
    from nfselib.cert import carregar_pfx
    from nfselib.client import desempacotar, empacotar
    from nfselib.dps import montar_dps, montar_id, serializar, validar_xsd
    from nfselib.evento import montar_cancelamento
    from nfselib.evento import validar_xsd as validar_evento
    from nfselib.xmlsig import assinar, conferir

    ## Certificado
    cert = carregar_pfx(pfx, SENHA)
    checar('carrega .pfx e extrai CNPJ do OID ICP-Brasil', cert.cnpj == CNPJ, str(cert.cnpj))
    checar('certificado não expirado', not cert.expirado)

    ## Id da DPS
    id_dps = montar_id('2611606', CNPJ, '1', '7')
    checar('Id da DPS = DPS + 42 dígitos',
           id_dps.startswith('DPS') and len(id_dps) == 45 and id_dps[3:].isdigit(), id_dps)
    checar('Id embute tipo de inscrição 2 (CNPJ)', id_dps[10] == '2')
    checar('Id zera à esquerda série e número',
           id_dps.endswith('00001' + '7'.zfill(15)))

    ## DPS completa
    emitente = {
        'cnpj': CNPJ, 'razao_social': 'EMPRESA TESTE LTDA', 'im': '123456',
        'cmun': '2611606', 'email': 'fiscal@teste.com.br', 'fone': '8133334444',
        'regime': {'opSimpNac': 1, 'regEspTrib': 0},
        'endereco': {'logradouro': 'RUA DOS TESTES', 'numero': '100', 'bairro': 'CENTRO',
                     'cmun': '2611606', 'cep': '50000000'},
    }
    pedido = {
        'serie': '1', 'ndps': 7, 'cod_trib_nac': '010701', 'valor': 1500,
        'descricao': 'Servicos de consultoria em tecnologia da informacao',
        'aliquota': 5, 'tomador': {'doc': '98765432000188', 'nome': 'CLIENTE EXEMPLO SA',
                                   'email': 'financeiro@cliente.com.br'},
        'info_complementar': 'Referente ao contrato 2026/001',
    }
    raiz = montar_dps(emitente, pedido, 'homologacao')
    checar('DPS válida no XSD antes de assinar', validar_xsd(raiz) == [],
           '; '.join(validar_xsd(raiz))[:200])

    for algoritmo in ('sha1', 'sha256'):
        raiz = montar_dps(emitente, pedido, 'homologacao')
        assinar(raiz, 'infDPS', cert, algoritmo)
        erros = validar_xsd(raiz)
        checar(f'DPS assinada ({algoritmo}) válida no XSD', erros == [], '; '.join(erros)[:300])
        res = conferir(raiz, cert, algoritmo)
        checar(f'digest confere ({algoritmo})', res.get('digest_confere') is True)
        checar(f'assinatura RSA confere ({algoritmo})', res.get('assinatura_confere') is True)

    ## Serialização exigida pelo manual
    xml = serializar(raiz)
    texto = xml.decode('utf-8')
    checar('XML declara UTF-8', texto.startswith("<?xml version='1.0' encoding='UTF-8'?>")
           or texto.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
    checar('sem prefixo de namespace nas tags', ':DPS' not in texto and '<ds:' not in texto)
    checar('namespace nacional declarado sem prefixo',
           'xmlns="http://www.sped.fazenda.gov.br/nfse"' in texto)
    checar('Signature declara o próprio namespace',
           'xmlns="http://www.w3.org/2000/09/xmldsig#"' in texto)
    checar('sem quebra de linha entre tags', '>\n' not in texto[texto.index('<DPS'):])

    ## GZip + base64 (formato de transporte)
    pacote = empacotar(xml)
    checar('round-trip gzip+base64', desempacotar(pacote) == texto)

    ## Evento de cancelamento
    chave = '2' * 50
    ped = montar_cancelamento(chave, '1', 'Erro no valor do servico prestado', CNPJ, 'homologacao')
    assinar(ped, 'infPedReg', cert, 'sha1')
    erros = validar_evento(ped)
    checar('pedido de cancelamento válido no XSD', erros == [], '; '.join(erros)[:300])
    checar('Id do pedido = PRE + 56 dígitos',
           ped[0].get('Id') == f'PRE{chave}101101')

    ## Guardas de segurança
    from nfselib.evento import EventoError
    try:
        montar_cancelamento(chave, '1', 'curto', CNPJ, 'homologacao')
        checar('motivo curto é rejeitado', False)
    except EventoError:
        checar('motivo curto é rejeitado', True)

    emit_prod = dict(emitente, ambiente='homologacao', cert_path=str(pfx))
    sys.path.insert(0, str(SKILL / 'scripts'))
    import nfse as cli
    try:
        cli.resolver_ambiente(emit_prod, producao=True)
        checar('--producao bloqueado por emitente de homologação', False)
    except config.ConfigError:
        checar('--producao bloqueado por emitente de homologação', True)
    checar('sem flag, ambiente é homologação',
           cli.resolver_ambiente(emit_prod, producao=False) == 'homologacao')

    ## Fluxo real da CLI (dry-run), sem tocar na rede
    config.salvar_emitente({**emitente, 'cert_path': str(pfx), 'ambiente': 'homologacao',
                            'cert_pass_env': f'NFSE_CERT_PASS_{CNPJ}'})
    saida = subprocess.run(
        [sys.executable, str(SKILL / 'scripts' / 'nfse.py'), 'emitir',
         '--emitente', CNPJ, '--valor', '1500', '--cod-trib-nac', '010701',
         '--descricao', 'Consultoria em TI', '--tomador-doc', '98765432000188',
         '--tomador-nome', 'CLIENTE EXEMPLO SA', '--aliquota', '5', '--dry-run'],
        capture_output=True, text=True, env=os.environ,
    )
    checar('CLI emitir --dry-run sai com 0', saida.returncode == 0, saida.stderr[-300:])
    if saida.returncode == 0:
        dados = json.loads(saida.stdout)
        checar('CLI devolve JSON puro com status dry_run', dados['status'] == 'dry_run')
        checar('CLI validou no XSD', dados['validacao_xsd']['valido'] is True)
        checar('CLI reservou nDPS 1', dados['ndps'] == 1)
        checar('CLI gravou o XML', Path(dados['xml_dps']).exists())

    # dry-run não pode queimar número: nada foi enviado, então o 1 continua livre
    saida2 = subprocess.run(
        [sys.executable, str(SKILL / 'scripts' / 'nfse.py'), 'emitir',
         '--emitente', CNPJ, '--valor', '10', '--cod-trib-nac', '010701',
         '--descricao', 'Outro servico', '--dry-run'],
        capture_output=True, text=True, env=os.environ)
    checar('dry-run não consome nDPS',
           saida2.returncode == 0 and json.loads(saida2.stdout)['ndps'] == 1,
           saida2.stdout[-200:])

    ## Sequencial: só avança depois que uma nota é de fato registrada
    from nfselib import store
    conn = store.conectar()
    store.registrar(conn, cnpj=CNPJ, ambiente='homologacao', serie='1', ndps=1,
                    status='autorizada')
    checar('nDPS avança após nota registrada',
           store.reservar_ndps(conn, CNPJ, 'homologacao', '1') == 2)
    checar('sequencial é isolado por ambiente',
           store.reservar_ndps(conn, CNPJ, 'producao', '1') == 1)
    try:
        store.reservar_ndps(conn, CNPJ, 'homologacao', '1', forcado=1)
        checar('nDPS repetido é recusado', False)
    except ValueError:
        checar('nDPS repetido é recusado', True)

    print()
    if falhas:
        print(f'{len(falhas)} falha(s): ' + ', '.join(falhas))
        return 1
    print('todos os testes passaram')
    return 0


if __name__ == '__main__':
    sys.exit(main())
