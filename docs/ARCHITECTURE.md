# Arkitektur

Hylde er én proces. Claude taler stdio. Alt der kan fejle, må fejle uden at tage chatten med.

```
Claude
  │  ask | route | second_opinion | health
  ▼
server.py          instruktion + værktøjer
  ▼
service.py         loft, fallback, audit
  ├─ heuristics.py   auto-opgave
  ├─ router.py       hylde, privat-lås, nøgler
  ├─ table.json      familie × trin. Netværket skriver ikke her
  ├─ prompts.json    systemtekst og max_tokens
  └─ providers.py    Anthropic, OpenAI, lokal /v1
         │
         ├─ 127.0.0.1  Ollama eller llama-server
         ├─ api.anthropic.com
         └─ api.openai.com
```

`hylde-refresh` er et separat job. Det gemmer et OpenRouter-prior. Det promoverer ikke tabellen.

## Kald

1. Tom tekst og for lang tekst stoppes.
2. Nøgle-mønster tvinger `local`.
3. `route` stopper her.
4. `ask` kalder kæden. Timeout og ét retry ved 429/5xx/netværk. Fastlåst hylde eller privat kald falder ikke videre til en anden udbyder.
5. Svaret starter med `model:` så Claude kan sige det til dig.

`second_opinion` er to kald (GPT, derefter Sonnet som samler). Ikke en crew, ikke en løkke: de kaldte modeller får ikke Hylde-værktøjerne.

## Hvad der bevidst ikke er med

- CrewAI og DSPy i runtime. De kræver faste jobs og et testhold. Tabellen og prompt-filerne er kroge til det.
- Live opslag i Arena/OpenRouter på hvert spørgsmål. Prior er en fil.
- Automatisk omskrivning af prompts.
