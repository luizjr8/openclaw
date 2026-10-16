"""Carregamento de certificado A1 (.pfx/.p12) e materialização temporária em PEM.

O `ssl` do Python não aceita chave privada em memória: `load_cert_chain` só lê de
arquivo. Por isso o PEM é escrito num diretório temporário 0700 com arquivo 0600 e
apagado ao sair do contexto — é o mínimo necessário para fazer mTLS com requests.
"""

import base64
import contextlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


class CertError(Exception):
    pass


# OID ICP-Brasil que carrega o CNPJ do titular no otherName do subjectAltName.
OID_CNPJ_ICP = '2.16.76.1.3.3'
OID_CPF_ICP = '2.16.76.1.3.1'


@dataclass
class Certificado:
    private_key: rsa.RSAPrivateKey
    cert: object  # cryptography.x509.Certificate
    cadeia: list

    ## Metadados

    @property
    def titular(self) -> str:
        try:
            return self.cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        except IndexError:
            return ''

    @property
    def nao_antes(self) -> datetime:
        return self.cert.not_valid_before_utc

    @property
    def nao_depois(self) -> datetime:
        return self.cert.not_valid_after_utc

    @property
    def expirado(self) -> bool:
        return datetime.now(timezone.utc) > self.nao_depois

    @property
    def cnpj(self) -> Optional[str]:
        """CNPJ extraído do otherName ICP-Brasil (OID 2.16.76.1.3.3).

        O conteúdo é uma string ASN.1 cujos 14 primeiros dígitos após os 12 de
        data-nascimento/CPF/PIS do responsável são o CNPJ. Na prática o valor do
        OID 3.3 já começa direto pelo CNPJ.
        """
        bruto = self._other_name(OID_CNPJ_ICP)
        if not bruto:
            return None
        digitos = ''.join(c for c in bruto if c.isdigit())
        return digitos[:14] if len(digitos) >= 14 else None

    def _other_name(self, oid: str) -> Optional[str]:
        from cryptography import x509
        try:
            san = self.cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            return None
        for nome in san.get_values_for_type(x509.OtherName):
            if nome.type_id.dotted_string == oid:
                # value é DER de um tipo string ASN.1; os bytes imprimíveis bastam
                return ''.join(chr(b) for b in nome.value if 32 <= b < 127)
        return None

    ## Operações criptográficas

    def assinar(self, dados: bytes, algoritmo: str = 'sha1') -> bytes:
        h = hashes.SHA256() if algoritmo == 'sha256' else hashes.SHA1()
        return self.private_key.sign(dados, padding.PKCS1v15(), h)

    def cert_base64(self) -> str:
        """Certificado do usuário final em base64 DER (padrão EndCertOnly)."""
        return base64.b64encode(self.cert.public_bytes(serialization.Encoding.DER)).decode()

    def info(self) -> dict:
        return {
            'titular': self.titular,
            'cnpj': self.cnpj,
            'valido_de': self.nao_antes.isoformat(),
            'valido_ate': self.nao_depois.isoformat(),
            'expirado': self.expirado,
            'emissor': self.cert.issuer.rfc4514_string(),
        }


def carregar_pfx(caminho: str | Path, senha: Optional[str]) -> Certificado:
    caminho = Path(caminho)
    if not caminho.exists():
        raise CertError(f'Certificado não encontrado: {caminho}')
    blob = caminho.read_bytes()
    senha_bytes = senha.encode('utf-8') if senha else None
    try:
        key, cert, cadeia = pkcs12.load_key_and_certificates(blob, senha_bytes)
    except ValueError as e:
        raise CertError(f'Falha ao abrir o .pfx (senha errada ou arquivo corrompido): {e}') from e
    if key is None or cert is None:
        raise CertError('O .pfx não contém chave privada e/ou certificado.')
    if not isinstance(key, rsa.RSAPrivateKey):
        raise CertError('Apenas chaves RSA são suportadas (padrão ICP-Brasil).')
    return Certificado(private_key=key, cert=cert, cadeia=list(cadeia or []))


@contextlib.contextmanager
def pem_temporario(certificado: Certificado) -> Iterator[str]:
    """Escreve cert+chave num PEM único 0600 para uso como client cert do requests."""
    tmpdir = tempfile.mkdtemp(prefix='nfse-mtls-')
    os.chmod(tmpdir, 0o700)
    destino = Path(tmpdir) / 'client.pem'
    try:
        partes = [
            certificado.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ),
            certificado.cert.public_bytes(serialization.Encoding.PEM),
        ]
        # A cadeia intermediária ajuda servidores que exigem o caminho completo.
        partes += [c.public_bytes(serialization.Encoding.PEM) for c in certificado.cadeia]
        fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as fh:
            fh.write(b''.join(partes))
        yield str(destino)
    finally:
        try:
            if destino.exists():
                # sobrescreve antes de remover para não deixar a chave no slack do fs
                tamanho = destino.stat().st_size
                with open(destino, 'r+b') as fh:
                    fh.write(b'\0' * tamanho)
                destino.unlink()
            os.rmdir(tmpdir)
        except OSError:
            pass
