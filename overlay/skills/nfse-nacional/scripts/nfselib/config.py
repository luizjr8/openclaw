"""Cadastro multi-tenant de emitentes.

Cada emitente é um JSON em `<DATA_DIR>/emitentes/<cnpj>.json`. A senha do
certificado NUNCA vai para o arquivo: o JSON guarda apenas o nome da variável de
ambiente que a contém.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

from .paths import EMITENTES_DIR, ensure_dirs


class ConfigError(Exception):
    pass


def so_digitos(valor: str) -> str:
    return re.sub(r'\D', '', valor or '')


def env_senha_padrao(cnpj: str) -> str:
    return f'NFSE_CERT_PASS_{so_digitos(cnpj)}'


## Leitura / escrita

def caminho_emitente(cnpj: str) -> Path:
    cnpj = so_digitos(cnpj)
    if len(cnpj) != 14:
        raise ConfigError(f'CNPJ deve ter 14 dígitos, recebi {len(cnpj)}: {cnpj!r}')
    return EMITENTES_DIR / f'{cnpj}.json'


def listar_emitentes() -> list[dict]:
    ensure_dirs()
    saida = []
    for p in sorted(EMITENTES_DIR.glob('*.json')):
        try:
            dados = json.loads(p.read_text(encoding='utf-8'))
        except json.JSONDecodeError as e:
            saida.append({'arquivo': p.name, 'erro': f'JSON inválido: {e}'})
            continue
        saida.append(_resumo(dados))
    return saida


def carregar_emitente(cnpj: str) -> dict:
    p = caminho_emitente(cnpj)
    if not p.exists():
        raise ConfigError(
            f'Emitente {so_digitos(cnpj)} não cadastrado. Rode: '
            f'nfse.py emitentes add --cnpj ... (arquivo esperado: {p})'
        )
    dados = json.loads(p.read_text(encoding='utf-8'))
    _validar(dados)
    return dados


def salvar_emitente(dados: dict) -> Path:
    ensure_dirs()
    _validar(dados)
    p = caminho_emitente(dados['cnpj'])
    p.write_text(json.dumps(dados, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.chmod(p, 0o600)
    return p


def remover_emitente(cnpj: str) -> Path:
    p = caminho_emitente(cnpj)
    if not p.exists():
        raise ConfigError(f'Emitente {so_digitos(cnpj)} não cadastrado.')
    p.unlink()
    return p


## Validação e derivados

_OBRIGATORIOS = ('cnpj', 'razao_social', 'cmun', 'cert_path')


def _validar(dados: dict) -> None:
    faltando = [c for c in _OBRIGATORIOS if not dados.get(c)]
    if faltando:
        raise ConfigError(f'Campos obrigatórios ausentes no emitente: {", ".join(faltando)}')
    if len(so_digitos(dados['cnpj'])) != 14:
        raise ConfigError('cnpj deve ter 14 dígitos')
    if len(so_digitos(str(dados['cmun']))) != 7:
        raise ConfigError('cmun deve ser o código IBGE de 7 dígitos do município emissor')
    amb = dados.get('ambiente', 'homologacao')
    if amb not in ('homologacao', 'producao'):
        raise ConfigError("ambiente deve ser 'homologacao' ou 'producao'")


def _resumo(dados: dict) -> dict:
    return {
        'cnpj': dados.get('cnpj'),
        'razao_social': dados.get('razao_social'),
        'im': dados.get('im'),
        'cmun': dados.get('cmun'),
        'ambiente': dados.get('ambiente', 'homologacao'),
        'cert_path': dados.get('cert_path'),
        'cert_existe': bool(dados.get('cert_path')) and Path(dados['cert_path']).exists(),
        'cert_pass_env': dados.get('cert_pass_env') or env_senha_padrao(dados.get('cnpj', '')),
        'senha_definida': bool(senha_certificado(dados, obrigatoria=False)),
    }


def senha_certificado(dados: dict, obrigatoria: bool = True) -> Optional[str]:
    """Lê a senha do .pfx da variável de ambiente apontada pelo emitente."""
    var = dados.get('cert_pass_env') or env_senha_padrao(dados.get('cnpj', ''))
    senha = os.environ.get(var)
    if senha is None and obrigatoria:
        raise ConfigError(
            f'Senha do certificado não encontrada. Defina a variável de ambiente {var}.'
        )
    return senha


def resumo_publico(dados: dict) -> dict:
    """Versão exibível de um emitente (não vaza senha; a senha nunca está no arquivo)."""
    return _resumo(dados)
