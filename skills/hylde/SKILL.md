---
name: hylde
description: >-
  Vælg LLM pr. opgave via Hylde-MCP (lokal Ollama/llama.cpp, billig Claude/GPT,
  eller en frontier-model). Brug når brugeren vil spare, holde noget privat,
  navngive en model, eller have et andet bud. Brug den ikke til spørgsmål
  Claude selv kan svare på.
---

# Hylde

Claude er værten. Hylde er et værktøj, der sender **ét afgrænset prompt** til en anden model. Den deler ikke hele samtalen, medmindre du kopierer det ind.

## Før du kalder

Kan du svare selv, så gør det. Hylde koster tid og nogle gange penge.

## Værktøjer

| Brugeren siger | Værktøj | Hensigt |
|---|---|---|
| bare svar / udtræk / kort | `ask` | `spar` |
| almindeligt godt svar | `ask` | `godt` (standard) |
| det skal være det bedste, svær kode, vigtig afgørelse | `ask` | `bedst` |
| privat, lokalt, må ikke sendes ud | `ask` | `privat` |
| brug haiku / mini / opus / local | `ask` med `model` | hyldenavn |
| hvad ville du vælge, uden at køre det | `route` | samme som ask |
| andet bud, er du sikker, spørg også GPT | `second_opinion` | kun én gang |
| virker den lokale model | `health` | — |

Hylder: `local`, `haiku`, `sonnet`, `opus`, `nano`, `mini`, `gpt`.

`task` må være `auto`. Sæt den kun hvis brugeren er tydelig (`code`, `review`, `extract`, `reason`, `draft`, `classify`).

## Hårde regler

- Nøgler, tokens og private keys sendes ikke. Hvis teksten ligner en nøgle, så sig det til brugeren i stedet for at kalde cloud.
- `privat` vinder over en navngiven cloud-model.
- `second_opinion` er to betalte frontier-kald. Ikke standard. Ikke til "hej".
- Sig i svaret **hvilken model** Hylde brugte (feltet `model:`).
- Hvis Hylde returnerer "stoppede" eller "kunne ikke vælge", så forklar det næste skridt (nøgle i `~/.hylde/.env`, eller start Ollama). Opfind ikke et svar fra en model der ikke kørte.

## Sådan lyder et godt kald

Kort prompt med selve opgaven. Ikke hele chat-historikken. Ikke "vær grundig" som standard — det tænder lange svar og koster mere.
