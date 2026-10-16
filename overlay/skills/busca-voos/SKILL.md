---
name: busca-voos
description: Busca voos domésticos e internacionais via Google Flights usando a biblioteca fast-flights (Python).
homepage: https://github.com/AWeirdDev/flights
metadata:
  openclaw:
    emoji: "✈"
---

# busca-voos

Busca voos usando a biblioteca Python `fast-flights` diretamente no código. Sem CLI — tudo via Python.

> **Requisito:** `fast-flights` instalado. Instale com `pip install fast-flights`.

## Instalação

```bash
pip install fast-flights
```

## API principal

```python
from fast_flights import FlightQuery, Passengers, create_query, get_flights

result = get_flights(
    create_query(
        flights=[FlightQuery(date="YYYY-MM-DD", from_airport="IATA", to_airport="IATA")],
        trip="one-way",          # ou "round-trip"
        seat="economy",          # economy | business | first | premium-economy
        passengers=Passengers(adults=1),
        currency="BRL",
        max_stops=1,             # opcional: int ou None (sem limite)
    )
)

# result é uma lista de Flights
for flight in result:
    print(flight.price, flight.airlines, flight.flights)
```

## Parâmetros de `FlightQuery`

| Parâmetro      | Tipo          | Descrição                                     |
| -------------- | ------------- | --------------------------------------------- |
| `date`         | `str`         | Data no formato `YYYY-MM-DD`                  |
| `from_airport` | `str`         | Código IATA de origem (ex: `GRU`)             |
| `to_airport`   | `str`         | Código IATA de destino (ex: `SSA`)            |
| `max_stops`    | `int \| None` | Máximo de paradas para este trecho (opcional) |
| `airlines`     | `list[str]`   | Filtrar por códigos IATA de companhias        |

## Parâmetros de `create_query`

| Parâmetro    | Tipo                | Descrição                                                  |
| ------------ | ------------------- | ---------------------------------------------------------- |
| `flights`    | `list[FlightQuery]` | Lista de trechos (1 = só ida, 2 = ida e volta)             |
| `trip`       | `str`               | `"one-way"` ou `"round-trip"`                              |
| `seat`       | `str`               | `"economy"`, `"business"`, `"first"`, `"premium-economy"`  |
| `passengers` | `Passengers`        | Objeto com `adults`, `children`, `infants_in_seat`, etc.   |
| `currency`   | `str`               | Código ISO 4217 (ex: `"BRL"`, `"USD"`)                     |
| `max_stops`  | `int \| None`       | Máximo de paradas global para todos os trechos             |
| `language`   | `str`               | Código BCP-47 (ex: `"pt-BR"`). `""` deixa o Google decidir |

## Estrutura de retorno

`get_flights()` retorna um `ResultList` (lista de `Flights`):

```
Flights(
    type='LA',
    price=946,                     # em centavos da moeda escolhida? não — valor direto
    airlines=['LATAM'],
    flights=[
        SingleFlight(
            from_airport=Airport(name='...', code='GRU'),
            to_airport=Airport(name='...', code='GIG'),
            departure=SimpleDatetime(date=[2026, 7, 1], time=[17, 45]),
            arrival=SimpleDatetime(date=[2026, 7, 1], time=[18, 45]),
            duration=60,           # minutos
            plane_type='Airbus A321'
        )
    ],
    carbon=CarbonEmission(typical_on_route=58000, emission=50000)
)
```

`result.metadata` contém listas de `Airline` e `Alliance` disponíveis na rota.

## Códigos IATA úteis (Brasil)

GRU, GIG, SDU, BSB, SSA, FOR, REC, CWB, POA, CNF, MCZ, NAT, BEL, MAO, CGH

## Preferências padrão do usuário

Ver `USER.md` para valores atualizados. Resumo:

- Partida entre 06h e 19h (filtrar manualmente por `departure.time`)
- Até 1 parada (`max_stops=1`)
- Ordenar por preço (o retorno já vem ordenado do mais barato)

## Estratégia de fallback

Se retornar lista vazia, amplie os filtros nesta ordem:

1. **Perfil padrão** — `max_stops=1`, filtrar horário 06h–19h manualmente
2. **Mais paradas** — `max_stops=2`, horário 05h–22h
3. **Sem filtros** — `max_stops=None`, sem filtro de horário

Só sugira nova data ou rota se todos os estágios retornarem vazios.

## Exemplos

```python
## Busca de voos

# Só ida
result = get_flights(
    create_query(
        flights=[FlightQuery(date="2026-07-05", from_airport="GRU", to_airport="SSA")],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        currency="BRL",
        max_stops=1,
    )
)

# Ida e volta
result = get_flights(
    create_query(
        flights=[
            FlightQuery(date="2026-07-05", from_airport="GRU", to_airport="SSA"),
            FlightQuery(date="2026-07-10", from_airport="SSA", to_airport="GRU"),
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        currency="BRL",
        max_stops=1,
    )
)

# Exibir resultados
for f in result:
    leg = f.flights[0]
    dep = f"{leg.departure.time[0]:02d}:{leg.departure.time[1] or 0:02d}"
    arr = f"{leg.arrival.time[0] or 0:02d}:{leg.arrival.time[1] or 0:02d}"
    print(f"R$ {f.price} | {', '.join(f.airlines)} | {dep}→{arr} | {leg.duration}min | {leg.plane_type}")
```
