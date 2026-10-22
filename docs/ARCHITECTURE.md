# Arkitektur

Appen (`hylde`) er et lokalt vindue på `127.0.0.1`. Den gemmer valg i `~/.hylde/.env` og starter MCP-processen med Start. Claude taler HTTP til den proces, eller stdio hvis Claude selv starter `hylde --mcp`. Nøgler sendes ikke tilbage til browseren.

```
Browser 127.0.0.1:8765     Start / Stop / formular
        │
        ▼
panel.py  →  ~/.hylde/.env
        │
        ▼
hylde --mcp-http 127.0.0.1:8766
        │
Claude  │  ask | route | second_opinion | health
        ▼
server.py          instruktion + værktøjer
  ▼
service.py         loft, fallback, audit
  ├─ heuristics.py   auto-opgave
  ├─ router.py       hylde, privat-lås, nøgler
  ├─ table.json      familie × trin. Netværket skriver ikke her
  ├─ prompts.json    systemtekst og max_tokens
  └─ providers.py    Anthropic, OpenAI, Ollama, llama.cpp, Ollama Cloud
         │
         ├─ 127.0.0.1:11434   Ollama
         ├─ 127.0.0.1:8080    llama.cpp
         ├─ ollama.com        Ollama Cloud (egen nøgle)
         ├─ api.anthropic.com
         └─ api.openai.com
```

`hylde-refresh` er et separat job. Det gemmer et OpenRouter-prior. Det promoverer ikke tabellen.

## Kald

1. Tom tekst og for lang tekst stoppes.
2. Nøgle-mønster tvinger et kald på maskinen.
3. `route` stopper her.
4. `ask` kalder kæden. Timeout og ét retry ved 429/5xx/netværk. En fastlåst hylde falder ikke videre. Et privat kald kan prøve Ollama og derefter llama.cpp, men aldrig en cloud-API.
5. Svaret starter med `model:` så Claude kan sige det til dig.

`second_opinion` er to kald (GPT, derefter Sonnet som samler). Ikke en crew, ikke en løkke: de kaldte modeller får ikke Hylde-værktøjerne.

## Hvad der bevidst ikke er med

- CrewAI og DSPy i runtime. De kræver faste jobs og et testhold. Tabellen og prompt-filerne er kroge til det.
- Live opslag i Arena/OpenRouter på hvert spørgsmål. Prior er en fil.
- Automatisk omskrivning af prompts.
