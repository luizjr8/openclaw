"""Assinatura XMLDSIG enveloped no padrão do Sistema Nacional NFS-e.

Spec (Manual Integrado SNNFS-e, seção 6.1.4 "Assinatura Digital"):
  - formato Enveloped, assinando a TAG interna identificada pelo atributo Id;
  - CanonicalizationMethod: C14N inclusiva (REC-xml-c14n-20010315);
  - Transforms (2, nesta ordem): enveloped-signature + C14N inclusiva;
  - KeyInfo/X509Data/X509Certificate com APENAS o certificado do usuário final;
  - namespace da assinatura declarado na própria tag <Signature>, SEM prefixo
    (o SNNFS-e rejeita prefixo de namespace na área de dados — erro E1228).

O manual publicado especifica rsa-sha1/sha1. Parte da documentação de terceiros
cita rsa-sha256/sha256, por isso o algoritmo é parametrizável — ver `algoritmo`.
"""

import base64

from lxml import etree

DSIG_NS = 'http://www.w3.org/2000/09/xmldsig#'
C14N_ALG = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
ENVELOPED_ALG = 'http://www.w3.org/2000/09/xmldsig#enveloped-signature'

_ALGORITMOS = {
    'sha1': {
        'signature': 'http://www.w3.org/2000/09/xmldsig#rsa-sha1',
        'digest': 'http://www.w3.org/2000/09/xmldsig#sha1',
    },
    'sha256': {
        'signature': 'http://www.w3.org/2001/04/xmldsig-more#rsa-sha256',
        'digest': 'http://www.w3.org/2001/04/xmlenc#sha256',
    },
}


class AssinaturaError(Exception):
    pass


def _hash(dados: bytes, algoritmo: str) -> bytes:
    import hashlib
    return (hashlib.sha256 if algoritmo == 'sha256' else hashlib.sha1)(dados).digest()


def _c14n(elemento) -> bytes:
    """C14N inclusiva, sem comentários, preservando namespaces herdados."""
    return etree.tostring(elemento, method='c14n', exclusive=False, with_comments=False)


def assinar(raiz, tag_alvo: str, certificado, algoritmo: str = 'sha1'):
    """Assina `raiz/<tag_alvo>` (que precisa ter atributo Id) e anexa <Signature> a `raiz`.

    Retorna a própria `raiz`, já com a assinatura como último filho.
    """
    if algoritmo not in _ALGORITMOS:
        raise AssinaturaError(f'Algoritmo não suportado: {algoritmo}')
    algs = _ALGORITMOS[algoritmo]

    ns_doc = etree.QName(raiz).namespace
    alvo = raiz.find(f'{{{ns_doc}}}{tag_alvo}') if ns_doc else raiz.find(tag_alvo)
    if alvo is None:
        raise AssinaturaError(f'Tag a assinar não encontrada: {tag_alvo}')
    id_alvo = alvo.get('Id')
    if not id_alvo:
        raise AssinaturaError(f'<{tag_alvo}> precisa do atributo Id para ser referenciado')

    ## Reference: digest do subconjunto assinado
    # A Signature ainda não existe na árvore, então a transform "enveloped" é
    # no-op aqui — o digest é o C14N puro do subtree de <tag_alvo>.
    digest = base64.b64encode(_hash(_c14n(alvo), algoritmo)).decode()

    signature = etree.SubElement(raiz, f'{{{DSIG_NS}}}Signature', nsmap={None: DSIG_NS})
    signed_info = etree.SubElement(signature, f'{{{DSIG_NS}}}SignedInfo')
    etree.SubElement(signed_info, f'{{{DSIG_NS}}}CanonicalizationMethod', Algorithm=C14N_ALG)
    etree.SubElement(signed_info, f'{{{DSIG_NS}}}SignatureMethod', Algorithm=algs['signature'])

    reference = etree.SubElement(signed_info, f'{{{DSIG_NS}}}Reference', URI=f'#{id_alvo}')
    transforms = etree.SubElement(reference, f'{{{DSIG_NS}}}Transforms')
    etree.SubElement(transforms, f'{{{DSIG_NS}}}Transform', Algorithm=ENVELOPED_ALG)
    etree.SubElement(transforms, f'{{{DSIG_NS}}}Transform', Algorithm=C14N_ALG)
    etree.SubElement(reference, f'{{{DSIG_NS}}}DigestMethod', Algorithm=algs['digest'])
    etree.SubElement(reference, f'{{{DSIG_NS}}}DigestValue').text = digest

    ## SignatureValue: RSA sobre o C14N do SignedInfo já no contexto do documento
    assinatura = certificado.assinar(_c14n(signed_info), algoritmo)
    etree.SubElement(signature, f'{{{DSIG_NS}}}SignatureValue').text = (
        base64.b64encode(assinatura).decode()
    )

    key_info = etree.SubElement(signature, f'{{{DSIG_NS}}}KeyInfo')
    x509_data = etree.SubElement(key_info, f'{{{DSIG_NS}}}X509Data')
    etree.SubElement(x509_data, f'{{{DSIG_NS}}}X509Certificate').text = certificado.cert_base64()

    return raiz


def conferir(raiz, certificado=None, algoritmo: str = 'sha1') -> dict:
    """Revalida digest e assinatura de um XML já assinado (autoteste local)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.exceptions import InvalidSignature

    signature = raiz.find(f'{{{DSIG_NS}}}Signature')
    if signature is None:
        return {'assinado': False}
    signed_info = signature.find(f'{{{DSIG_NS}}}SignedInfo')
    uri = signed_info.find(f'.//{{{DSIG_NS}}}Reference').get('URI')[1:]

    alvo = next(el for el in raiz.iter() if el.get('Id') == uri)
    # remove a Signature para reproduzir a transform enveloped
    raiz.remove(signature)
    digest_calc = base64.b64encode(_hash(_c14n(alvo), algoritmo)).decode()
    raiz.append(signature)

    digest_xml = signed_info.find(f'.//{{{DSIG_NS}}}DigestValue').text
    resultado = {'assinado': True, 'uri': uri, 'digest_confere': digest_calc == digest_xml}

    if certificado is not None:
        assinatura = base64.b64decode(signature.find(f'{{{DSIG_NS}}}SignatureValue').text)
        h = hashes.SHA256() if algoritmo == 'sha256' else hashes.SHA1()
        try:
            certificado.cert.public_key().verify(
                assinatura, _c14n(signed_info), padding.PKCS1v15(), h
            )
            resultado['assinatura_confere'] = True
        except InvalidSignature:
            resultado['assinatura_confere'] = False
    return resultado
