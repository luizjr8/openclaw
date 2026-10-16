---
name: nfse-nacional
description: Emite, consulta, cancela e baixa NFS-e no padrão nacional (Sistema Nacional NFS-e — Sefin Nacional e ADN) por integração direta com mTLS e certificado ICP-Brasil, sem provedor intermediário. Multi-tenant — vários CNPJs emitentes, cada um com seu certificado. Use para "emitir nota fiscal de serviço", "cancelar NFS-e", "consultar nota por chave de acesso" ou "baixar documentos do ADN".
metadata:
  openclaw:
    emoji: "🧾"
    requires:
      bins: ["python3"]
      python: ["lxml", "cryptography", "requests"]
---

# NFS-e Padrão Nacional (emissão direta)

Fala direto com a **Sefin Nacional** (emissão/consulta/eventos) e com o **ADN**
(distribuição de DF-e). Sem Focus NFe, eNotas ou qualquer intermediário.

> ⚠️ **Isso é fisco de verdade.** O ambiente padrão é **homologação** (produção
> restrita). Produção só acontece com `--producao` **e** com o emitente cadastrado
> como `ambiente: producao` — as duas travas juntas.

## Caminhos

- Script: `scripts/nfse.py` (CLI; todos os comandos devolvem **JSON puro**)
- Esquemas XSD oficiais: `schemas/1.01/` (cópia byte-a-byte do gov.br)
- Autoteste offline: `tests/test_local.py`
- **Dados do usuário** (fora do repo): `/app/workspace/skills/nfse-nacional/`
  - `emitentes/<cnpj>.json` — cadastro por CNPJ (sem senha)
  - `certs/` — os `.pfx` (dir 0700)
  - `xml/` — DPS enviadas, NFS-e autorizadas, eventos
  - `nfse.db` — SQLite com as notas e o sequencial de nDPS

Sobrescreva o diretório de dados com `NFSE_DATA_DIR`.

## Setup por empresa (multi-tenant)

```bash
# 1) certificado A1 no diretório de certs
cp ~/cert-empresa.pfx /app/workspace/skills/nfse-nacional/certs/

# 2) senha SEMPRE em variável de ambiente, nunca no arquivo de config
export NFSE_CERT_PASS_12345678000199='senha-do-pfx'

# 3) cadastro do emitente
python3 scripts/nfse.py emitentes add \
  --cnpj 12345678000199 --razao-social "ACME SERVICOS LTDA" \
  --cmun 2611606 --im 123456 \
  --cert /app/workspace/skills/nfse-nacional/certs/cert-empresa.pfx \
  --op-simp-nac 1 --reg-esp-trib 0 \
  --logradouro "RUA EXEMPLO" --numero 100 --bairro CENTRO --cep 50000000 \
  --email fiscal@acme.com.br
```

`emitentes listar | mostrar --cnpj … | remover --cnpj …` completam o cadastro.
Nenhum CNPJ está embutido no código — tudo vem de config/args.

## Comandos

### emitir

Monta a DPS, assina (XMLDSIG enveloped), valida contra o XSD oficial e envia.

```bash
python3 scripts/nfse.py emitir --emitente 12345678000199 \
  --valor 1500 --cod-trib-nac 010701 \
  --descricao "Consultoria em tecnologia da informacao" \
  --tomador-doc 98765432000188 --tomador-nome "CLIENTE SA" \
  --aliquota 5 --dry-run
```

- `--dry-run` monta, assina e valida **sem enviar nada** e **sem consumir número**
  de DPS. É o primeiro comando a rodar em qualquer emitente novo.
- `--ndps` força o número; sem ele o próximo sequencial vem do SQLite, isolado por
  (emitente, ambiente, série).
- `--iss-retido` é atalho para `--tp-ret-issqn 2`.
- `--extra '<json>'` (ou caminho de arquivo) injeta campos que a CLI não expõe.
- Se o XSD reprovar, nada é enviado e o comando sai com código 1.

### consultar / baixar / cancelar / notas

```bash
python3 scripts/nfse.py consultar --emitente <cnpj> --chave <50 dígitos>
python3 scripts/nfse.py baixar    --emitente <cnpj> --nsu 0
python3 scripts/nfse.py cancelar  --emitente <cnpj> --chave <50 dígitos> \
    --cod-motivo 1 --motivo "Erro no valor do servico prestado"
python3 scripts/nfse.py notas --emitente <cnpj> --limite 20
```

`notas` lê só o banco local — não vai à rede. O motivo do cancelamento precisa ter
**15 a 255 caracteres** (`TSMotivo`); códigos: `1` erro na emissão, `2` serviço não
prestado, `9` outros.

## Endpoints usados

| Ação                  | Método | URL                                                                      |
| --------------------- | ------ | ------------------------------------------------------------------------ |
| Emitir                | POST   | `{sefin}/nfse` — body `{"dpsXmlGZipB64": …}`                             |
| Consultar NFS-e       | GET    | `{sefin}/nfse/{chaveAcesso}`                                             |
| Chave a partir da DPS | GET    | `{sefin}/dps/{id}`                                                       |
| Registrar evento      | POST   | `{sefin}/nfse/{chave}/eventos` — `{"pedidoRegistroEventoXmlGZipB64": …}` |
| Distribuição DF-e     | GET    | `{adn}/DFe/{NSU}`                                                        |

- `{sefin}` homologação: `https://sefin.producaorestrita.nfse.gov.br/SefinNacional`
- `{sefin}` produção: `https://sefin.nfse.gov.br/SefinNacional`
- `{adn}` homologação: `https://adn.producaorestrita.nfse.gov.br/contribuintes`
- `{adn}` produção: `https://adn.nfse.gov.br/contribuintes`

Todo XML trafega **GZip + base64** dentro do JSON, nos dois sentidos.

## Detalhes que quebram a emissão (aprendidos do manual)

- **mTLS é a única autenticação.** Não há token. Sem client cert o Sefin devolve
  403 e o ADN devolve 496 ("SSL Certificate Required").
- **Prefixo de namespace é proibido** na área de dados (erro E1228). Por isso a
  `<Signature>` sai sem `ds:` e o namespace nacional é declarado como default.
- **Sem formatação no XML**: nada de indentação, quebra de linha ou comentário
  entre tags.
- **Id da DPS** = `DPS` + Cód.Mun.(7) + tipo de inscrição(1: `1`=CPF, `2`=CNPJ) +
  inscrição federal(14, CPF completado com zeros à esquerda) + série(5) +
  número(15) = 42 dígitos.
- **Série** 00001–49999 é a faixa de emissão por aplicativo próprio.
- O certificado precisa ser do **CNPJ raiz do emitente** — a CLI confere os 8
  primeiros dígitos antes de enviar e recusa localmente se divergir.

### Algoritmo de assinatura: SHA-1 por padrão

O Manual Integrado do SNNFS-e (seção 6.1.4) especifica `rsa-sha1` + `sha1` +
C14N inclusiva. Parte da documentação de terceiros afirma `rsa-sha256`. O padrão
aqui é **SHA-1**, conforme o manual; se a Sefin rejeitar a assinatura (erro
**E0714**), troque com `--sha256` na chamada ou grave a preferência no emitente
(`"assinatura": {"digest": "sha256"}`). **Isto ainda não foi confirmado contra o
ambiente real.**

### XSD da série vem quebrado do gov.br

`tiposSimples_v1.01.xsd` publica o padrão da série como `^0{0,4}\d{1,5}$`. XSD 1.0
não define `^`/`$` como âncoras — o libxml2 os lê como literais e reprova qualquer
série válida. Os arquivos em `schemas/` seguem idênticos ao publicado; a correção
é aplicada só na carga (`nfselib/dps.py:_ResolverXSD`).

## Autoteste

```bash
python3 tests/test_local.py
```

Gera um certificado autoassinado com o `otherName` de CNPJ da ICP-Brasil, monta
DPS e pedido de cancelamento, assina nos dois algoritmos, valida contra o XSD
oficial, confere digest e assinatura, e exercita as travas de ambiente e de
numeração. Não toca na rede.

## Referências oficiais

- Portal: <https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica>
- Swagger homologação: <https://adn.producaorestrita.nfse.gov.br/contribuintes/docs/index.html>
- "Manual dos Contribuintes — Guia para utilização das API's do Emissor Público Nacional"
- "Manual dos Contribuintes — Guia para utilização das API's do ADN"
- Anexo I (leiaute e regras de negócio da DPS/NFS-e)
