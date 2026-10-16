#!/usr/bin/env bash
# validate-memory-pointers.sh
# Validação DETERMINÍSTICA de ponteiros de memória.
#
# Motivação: a rotina diária de manutenção de memória pedia pro LLM "procurar
# referências que apontam pra arquivos que não existem" — mas em prosa, olhando
# a olho. O modelo chutava existência de arquivo e errava (falso "não existe"
# por slug com underscore vs filename com hífen, ou por simplesmente não conferir).
# Este script faz o check de verdade: lista os arquivos reais e cruza.
#
# Uso: validate-memory-pointers.sh [WORKSPACE_DIR]   (default: /app/workspace)
# Saída: relatório OK/MISS. Exit 0 = tudo resolve; exit 1 = há ponteiro quebrado.
set -euo pipefail

WS="${1:-/app/workspace}"
cd "$WS" || { echo "workspace não encontrado: $WS" >&2; exit 2; }

MEM_FILES=()
[ -f MEMORY.md ] && MEM_FILES+=("MEMORY.md")
while IFS= read -r f; do MEM_FILES+=("$f"); done < <(find memory -name '*.md' 2>/dev/null | sort)
[ ${#MEM_FILES[@]} -eq 0 ] && { echo "nenhum arquivo de memória em $WS"; exit 0; }

# Conjunto de slugs existentes (basename sem .md), normalizado hífen<->underscore.
declare -A SLUGS
while IFS= read -r f; do
  b=$(basename "$f" .md)
  SLUGS["$b"]=1; SLUGS["${b//-/_}"]=1; SLUGS["${b//_/-}"]=1
done < <(find . -name '*.md' 2>/dev/null)

# Tokens que NÃO são referência de memória (diretivas OpenClaw + placeholders de doc).
declare -A SKIP
for t in audio_as_voice reply_to_current reply_to nome name their-name link title; do SKIP["$t"]=1; done

fail=0

# Remove inline code spans (`...`): ponteiros reais são sempre bare; o que estiver
# entre crases é exemplo/doc e não deve ser validado.
strip() { sed 's/`[^`]*`//g' "${MEM_FILES[@]}" 2>/dev/null; }

echo "### 1. Links markdown para memory/ (](memory/...))"
while read -r p; do
    [ -e "$p" ] && echo "OK   $p" || { echo "MISS $p"; echo miss >>/tmp/.vmp_fail; }
done < <(strip | grep -oE '\]\(memory/[^)]+\)' | sed 's/](//; s/)//' | sort -u)

echo
echo "### 2. Wikilinks de memória ([[slug]])"
while read -r w; do
    # pular tokens com caracteres não-slug (aspas, vírgula, dois-pontos, espaço) e skiplist
    if printf '%s' "$w" | grep -qE '[^a-zA-Z0-9_-]'; then continue; fi
    [ -n "${SKIP[$w]:-}" ] && continue
    norm="${w//_/-}"
    if [ -n "${SLUGS[$w]:-}" ] || [ -n "${SLUGS[$norm]:-}" ] || [ -n "${SLUGS[${norm//-/_}]:-}" ]; then
      echo "OK   [[${w}]]"
    else
      echo "MISS [[${w}]]"; echo miss >>/tmp/.vmp_fail
    fi
done < <(strip | grep -oE '\[\[[^]]+\]\]' | sed 's/\[\[//; s/\]\]//' | sort -u)

echo
echo "### 3. Nomes de arquivo de memória (só hífen, nunca underscore)"
# Underscore no path já devolveu text vazio SEM erro no memory_get (o índice
# slugificava _ -> - no path devolvido pelo memory_search). Não reverificado no
# índice builtin — mantido como convenção. Ver
# workspace/AGENTS.example.md > Memória.
under=$(find memory -name '*_*.md' 2>/dev/null | sort)
if [ -n "$under" ]; then
  while IFS= read -r f; do
    echo "BAD  $f  ->  ${f//_/-}"; echo miss >>/tmp/.vmp_fail
  done <<< "$under"
else
  echo "OK   nenhum arquivo de memória com underscore"
fi

echo
if [ -f /tmp/.vmp_fail ]; then
  n=$(wc -l </tmp/.vmp_fail); rm -f /tmp/.vmp_fail
  echo "RESULTADO: $n ponteiro(s) quebrado(s)."
  exit 1
fi
echo "RESULTADO: todos os ponteiros de memória resolvem."