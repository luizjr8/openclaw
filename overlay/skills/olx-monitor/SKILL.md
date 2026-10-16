---
name: "olx-monitor"
description: "Monitora OLX + Mercado Livre por itens de segunda mão parametrizados; MUST sempre checado, SHOULD só notifica se for pechincha"
---

# OLX Monitor

Monitor genérico de anúncios de segunda mão. Busca **OLX** e **Mercado Livre** por termos configurados pela instância, avalia cada anúncio por **condição** (não só preço), acumula achados em um digest e notifica conforme a prioridade do item (MUST vs SHOULD). Serve pra qualquer categoria de item (CDs, discos, eletrônicos, colecionáveis, etc.) — o que buscar é definido inteiramente pela instância.

## Dados de instância vivem no workspace, NÃO nesta skill

Esta skill é genérica e reutilizável. **Nada específico da instância entra aqui.** Wishlist, termos de busca, teto de preço, IDs de chat/tópico, chaves e estado ficam em:

- `/app/workspace/skills/olx-monitor/wishlist.md` — itens monitorados, com colunas Item / Prioridade (`MUST` | `SHOULD`) / Status / Link. (Uma instância pode em vez disso apontar pra um arquivo de referência já existente no seu workspace — ver README da pasta.)
- `/app/workspace/skills/olx-monitor/seen.json` (ou o state file, ver abaixo) — estado de execução: URLs vistas, digest pendente, ponteiros de rotação.
- Teto de preço, cadência do cron, alvo de notificação (canal/tópico) e quaisquer termos de "rotação" adicionais também são parâmetros de instância — **nunca fixar valores no corpo desta skill.**

Se a instância já mantém esses dados em outro caminho de referência (ex: `/app/workspace/memory/ref/<arquivo>.md`), a skill deve ler daquele caminho; a pasta `/app/workspace/skills/olx-monitor/` serve como local padrão e ponto de entrada.

## Itens monitorados

A instância define quais itens/termos monitorar. Dois grupos:

1. **Wishlist** (`wishlist.md`) — itens específicos que a instância quer, cada um com prioridade `MUST` ou `SHOULD`. Checados por rotação em lotes (ver passo a passo).
2. **Rotação genérica** (opcional, no state file em `queries`) — termos amplos de garimpo (ex: artistas favoritos, categorias) que giram 1 por execução. Cada instância popula essa lista como quiser; a skill não presume nenhum termo.

## Teto de preço — parâmetro de instância

- O teto de preço é **configurável pela instância**, não fixo na skill. A instância define um teto padrão (média) e, opcionalmente, uma tolerância para exceções justificadas (edição rara, importado, estado excepcional, item que não aparece em outro lugar).
- Acima do teto (fora de exceção justificada): **não notificar** — mas ainda registrar a URL em `seenListingUrls` pra não reavaliar de novo.
- **MUST não tem teto fixo** — julgamento por condição (aceitar preço mais alto se a condição for boa). O teto padrão se aplica à rotação genérica e como referência para classificar "pechincha".

## MUST vs SHOULD

A coluna Prioridade da wishlist define o comportamento de cada item:

- **MUST**: item que a instância quer bastante e compra assim que achar uma cópia decente. Checar em toda rotação de lote. Sem teto fixo — julgamento por condição. **Qualquer achado aceitável interessa** → enfileira no digest.
- **SHOULD**: item de menor prioridade, só comprado depois dos MUST ou se aparecer pechincha real. Mesma busca, mas **só enfileira/notifica se for achado excepcional** — preço muito abaixo do normal pro item, raridade, lacrado. Achado comum de SHOULD (dentro do esperado, sem barganha) **não entra no digest** — só registra em `seenListingUrls` pra não reavaliar.

Diferença chave: **MUST → qualquer achado aceitável interessa. SHOULD → só achado excepcional interessa;** o resto é ruído que não deve incomodar.

## Condição importa mais que preço

⚠️ **SEMPRE abrir o anúncio e ler a descrição antes de classificar um achado.** Preço baixo com item danificado NÃO é achado. Um preço muito abaixo do normal quase sempre significa item em má condição — desconfiar.

- **Rejeitar** (registrar em `seenListingUrls`, manter status "Procurando", não notificar) quando a descrição/fotos indicarem dano: "riscado", "poucos riscos", "riscos leves", "lê/toca com dificuldade", peças/encarte faltando ou muito danificados, bolor/mofo, defeito funcional.
- **Aceitável**: "lacrado", "novo", "seminovo", "excelente estado", "sem riscos", "impecável".
- Na dúvida sobre a condição, tratar como **bom** (fila do digest) e mencionar a ressalva de condição no texto — nunca marcar como achado excepcional só por causa do preço.

## Classificação de achado (rotação genérica)

Todo anúncio novo (não visto antes) dentro do teto configurado cai em uma de três categorias:

- **Excepcional / destaque no digest**: raridade real (edição descontinuada, importado, box set, autografado, lacrado), preço muito abaixo do normal pro item (comparar com os outros anúncios do mesmo item na mesma busca), ou estado excepcional documentado. Acumular em `pendingDigest` marcado `foda: true` (ou flag equivalente) — sai em destaque no digest.
- **Bom / fila do digest**: dentro do teto, condição normal/boa, sem ser raridade. Acumular em `pendingDigest`.
- **Comum / ignorar**: múltiplas cópias já vistas, item genérico muito comum, preço na média sem nada de especial. Registrar em `seenListingUrls`, não entra no digest.

Na dúvida entre "bom" e "comum", classificar como **bom** — o filtro final de ruído é a pessoa lendo o digest.

Se a instância preferir notificação imediata vs digest acumulado, isso é escolha da instância (ver "Notificação"). O modelo padrão recomendado é digest acumulado.

## Ritmo anti-recaptcha (OLX) — aprendizado confirmado

⚠️ O OLX aciona recaptcha/bloqueio depois de **poucas requisições rápidas na mesma sessão** (confirmado em 2026-07-29). Mitigação = ritmo:

- Usar o perfil de browser `openclaw` (managed, sessão persistida).
- **Não** fazer várias buscas seguidas sem necessidade.
- Regra: normalmente **~1 busca de rotação genérica por execução** do cron, **mais** as buscas da wishlist em lote.
- A wishlist **não** é varrida inteira a cada execução — é rotacionada em lotes pequenos (ver passo a passo). Com dezenas de itens × 2 sites, buscar tudo de uma vez (100+ requisições) dispara bloqueio.
- Se o recaptcha aparecer no meio de um lote: parar o lote, **não** avançar os ponteiros, tratar como erro (backoff).

## Extração de anúncios — OLX

Navegar pra `https://www.olx.com.br/brasil?q=<termo-url-encoded>` (ou com a categoria/UF que a instância preferir). Depois de carregar, **uma única** chamada de extração:

```js
() =>
  Array.from(document.querySelectorAll("section.olx-adcard"))
    .map((s) => {
      const a = s.querySelector("a.olx-adcard__link");
      return { href: a ? a.href : null, text: s.innerText.replace(/\n+/g, " | ").slice(0, 180) };
    })
    .filter((x) => x.text);
```

## Extração de anúncios — Mercado Livre

Navegar pra `https://lista.mercadolivre.com.br/<termo-com-espacos-como-hifen>`. Extrair com:

```js
() =>
  Array.from(document.querySelectorAll("a"))
    .filter((a) => /\/MLB-?\d+/.test(a.href) || /\.mercadolivre\.com\.br\/.*\/p\//.test(a.href))
    .slice(0, 10)
    .map((a) => {
      let p = a;
      for (let i = 0; i < 5 && p; i++) p = p.parentElement;
      return {
        href: a.href.split("#")[0],
        text: (p ? p.innerText : "").replace(/\n+/g, " | ").slice(0, 180),
      };
    });
```

Cuidado: resultados do ML costumam misturar variantes (vinil, pôsteres, acessórios, produtos de nome parecido) — filtrar pelo título contendo o termo certo e o tipo de produto explícito quando possível.

## State file

Local padrão: `/app/workspace/skills/olx-monitor/seen.json` (a instância pode usar outro caminho). Estrutura:

```json
{
  "rotationIndex": 0,
  "queries": [{ "type": "rotation", "term": "<termo definido pela instância>" }],
  "seenListingUrls": ["https://..."],
  "pendingDigest": [
    {
      "title": "...",
      "price": "R$ NN",
      "location": "Cidade - UF",
      "url": "https://...",
      "term": "...",
      "foda": false,
      "addedAt": "YYYY-MM-DDTHH:MM:SS-03:00"
    }
  ],
  "lastCheckedAt": "YYYY-MM-DDTHH:MM:SS-03:00",
  "lastQueryChecked": "...",
  "consecutiveErrors": 0,
  "wishlistMustIndex": 0,
  "wishlistShouldIndex": 0
}
```

- `queries`: rotação genérica; 1 item por execução (`rotationIndex` com wrap-around). A instância popula/edita esse array.
- `seenListingUrls`: dedup — evita re-notificar ou reavaliar o mesmo anúncio (achados comuns e acima do teto também entram, inclusive achados comuns de SHOULD). Cap sugerido ~300, remove os mais antigos ao estourar.
- `pendingDigest`: acumula achados "bons" e "excepcionais" até o próximo digest.
- `wishlistMustIndex` / `wishlistShouldIndex`: ponteiros de rotação em lote da wishlist. Os itens MUST/SHOULD em si ficam em `wishlist.md`, **não** duplicados no state — o monitor só lê o arquivo pra saber o que buscar e a prioridade.

## Passo a passo — execução por cron

1. Ler o state file. Se não existir, criar com `rotationIndex: 0`, `queries` (rotação genérica da instância, pode ser vazia), `seenListingUrls: []`, `pendingDigest: []`, ponteiros em 0.
2. **Parte wishlist — rotação em lotes** (roda toda execução, antes da rotação genérica): ler `wishlist.md`.
   - Montar duas listas ordenadas: `mustItems` (Prioridade=MUST, status "Procurando") e `shouldItems` (Prioridade=SHOULD, status "Procurando").
   - Pegar um lote de **até N MUST** começando em `wishlistMustIndex` (wrap-around) e **até M SHOULD** começando em `wishlistShouldIndex` (wrap-around). Tamanhos de lote (N/M) são parâmetros de instância; valores conservadores (ex: 6 MUST + 2 SHOULD) mantêm o ritmo abaixo do bloqueio do OLX.
   - Buscar só esse lote nesta execução: cada item no OLX e depois no Mercado Livre.
   - Ao terminar o lote sem erro, avançar os ponteiros com wrap-around e salvar no state. Assim a lista inteira é varrida em várias execuções em vez de uma só.
   - Se recaptcha aparecer no meio do lote: parar, **não** avançar os ponteiros, tratar como erro (backoff).
   - Pra cada item, aplicar a lógica MUST vs SHOULD (ver acima). Anúncio novo aceitável pra notificar: registrar `href` em `seenListingUrls`, enfileirar no `pendingDigest`, e atualizar o Status do item em `wishlist.md` de "Procurando" pra "Achado (aguardando decisão)" com o link do melhor achado.
3. **Parte rotação genérica**: pegar `queries[rotationIndex]` (se a lista não estiver vazia).
4. `browser` tool `action=status` no perfil `openclaw`; se não estiver rodando, `start`.
5. `action=navigate` pra URL de busca do OLX com o termo url-encoded.
6. Depois de carregar, **uma única** chamada de extração OLX.
7. Filtrar `seenListingUrls` — só considerar `href` novos.
8. Pra cada anúncio novo: extrair preço, aplicar teto configurado, abrir/ler descrição pra avaliar condição, e classificar em excepcional / bom / comum. Confirmar relevância (título contém o termo e é claramente o produto certo; evitar falsos positivos).
9. Achado **excepcional**: `pendingDigest` com `foda: true` (title, price, location, url, term, addedAt, motivo).
10. Achado **bom**: `pendingDigest` (sem flag de destaque).
11. Achado **comum** ou acima do teto (rotação), ou comum de SHOULD: só `seenListingUrls`.
12. Atualizar o state: novos hrefs em `seenListingUrls` (cap ~300), `rotationIndex = (rotationIndex + 1) % queries.length`, `lastCheckedAt`, `lastQueryChecked`, `consecutiveErrors: 0`.

## Passo a passo — digest

Cadência (diário, quinzenal, dias específicos) e destino (canal/tópico) são parâmetros de instância.

1. Ler o state file.
2. Se `pendingDigest` vazio: não enviar nada.
3. Se tiver itens: montar a mensagem começando pelos achados **excepcionais** (`foda: true`) em destaque com o motivo, depois os "bons" agrupados por MUST / SHOULD / rotação (título, preço, cidade/UF, link). Priorizar excepcionais e MUST no topo.
4. Enviar via `message` tool `action=send` pro `target` configurado pela instância.
5. Limpar `pendingDigest` (`[]`) depois de enviar — os hrefs já enviados continuam em `seenListingUrls`.

## Erros

- Página com recaptcha/bloqueio (checar via extração: `document.title` contém "Just a moment" ou a lista de anúncios vem vazia repetidamente): incrementar `consecutiveErrors`, **não** avançar `rotationIndex` (tenta o mesmo item na próxima execução). Não notificar erro a menos que `consecutiveErrors >= 3` — nesse caso avisar uma vez no canal configurado que o monitor está falhando e parar de tentar até revisão. Vale pra rotação e pra wishlist.
- Resetar `consecutiveErrors` pra 0 em qualquer checagem bem-sucedida (mesmo sem achados novos).

## Notas gerais

- Execução autônoma via cron — não pedir confirmação pra rodar.
- Um item de rotação genérica por execução é intencional (ritmo lento evita recaptcha). A wishlist é checada em lotes rotativos, não inteira de uma vez.
- Pra editar a rotação genérica: editar o array `queries` no state file (a rotação continua do índice atual).
- Pra adicionar/remover item da wishlist ou mudar prioridade: editar `wishlist.md` (coluna Prioridade).
- Ao avaliar "muito abaixo do normal", comparar com os outros anúncios do mesmo item vistos na mesma busca.
- Modelo de notificação (imediato vs digest acumulado), teto de preço, tamanhos de lote e cadência são todos parâmetros de instância — esta skill só define o mecanismo.
