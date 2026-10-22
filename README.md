# Hylde

MCP-server til **Claude**. Den vælger model **pr. opgave** — Ollama og llama.cpp på maskinen, en billig Claude/GPT, Ollama Cloud, eller en frontier-model — i stedet for at hele projektet kører på den dyreste.

Claude bliver i chatten. Hylde er værktøjet.

## Sådan bruger du den

Sig det i almindeligt sprog. Claude skal have skillen nedenfor, så den rammer rigtigt.

| Du siger | Der sker |
|---|---|
| "gør det billigt" / "bare udtræk det" | ét kald til den billigste hylde der dur |
| "svar godt på det her" | standard: mellemmodel |
| "brug den bedste" | ét dyrt kald, ikke to |
| "hold det på maskinen" | kun Ollama, og llama.cpp hvis den er slået til |
| "hvad ville du vælge?" | ingen model kaldes |
| "få et andet bud" | to cloud-modeller, ét samlet svar |

Hylder du kan navngive: `local` (Ollama), `llama` (llama.cpp), `cloud` (Ollama Cloud), `haiku`, `sonnet`, `opus`, `nano`, `mini`, `gpt`. `cloud` koster hos Ollama og vælges kun, når du siger det.

Id'erne i `src/hylde/data/table.json` er sat 11. oktober 2026 ud fra Anthropic og OpenAI: Haiku 4.5, Sonnet 5, Opus 5, GPT-5.4 nano/mini (de billige), GPT-6 Astra (frontier). Fable 5.1 er dyrere end Opus og er bevidst ikke standard. Ret filen når et id skifter — netværket gør det ikke selv.

## Kom i gang

Krav: Python 3.12, [uv](https://docs.astral.sh/uv/), Claude Desktop eller Claude Code.

```bash
cd hylde
uv sync --group dev
uv run hylde
```

Appen åbner i browseren på `http://127.0.0.1:8765`. Der kan du:

1. Vælge en Ollama-model fra listen, og eventuelt slå llama.cpp til ved siden af. Begge kan være tændt.
2. Indsætte Anthropic- og OpenAI-nøglen. Ollama Cloud har sit eget felt og sin egen nøgle. Tomt felt beholder den gamle. Hylde viser ikke nøglen igen.
3. Trykke **Start**. Lampen bliver grøn.
4. Kopiere Claude-konfigurationen fra appen ind i Claude Desktop og genstarte Claude.

Sig derefter: "Kør health på Hylde." Du skal se om den lokale model er oppe, og om nøglerne er **sat**.

Nøglerne gemmes i `~/.hylde/.env` med rettigheder kun til dig. Start bruger den fil, ikke en nøgle der tilfældigvis ligger i terminalen.

Prøvekørsel i appen kalder ingen modeller. Slå den fra, når du vil have rigtige svar.

Ollama-appen (eller `ollama serve`) skal køre, før listen kan hentes. Hent en model én gang:

```bash
ollama pull qwen2.5-coder:7b
```

llama.cpp kan køre samtidig, typisk på `http://127.0.0.1:8080/v1`. Slå den til i appen og sig `llama` til Claude.

Ollama Cloud er et tredje sted, på ollama.com. Det kræver en Ollama API-nøgle, koster hos Ollama, og teksten forlader maskinen. Sig `cloud`. Den vælges ikke af sig selv.

Vil du ikke have appen kørende, kan Claude selv starte serveren. Se `claude_desktop.stdio.example.json`. Kommandoen er `python -m hylde --mcp`. Den læser den samme fil.

## Skill

Kopiér mappen ind, så Claude ved hvornår den må kalde:

```bash
mkdir -p ~/.claude/skills
cp -R skills/hylde ~/.claude/skills/hylde
```

Uden skillen har Claude værktøjerne, men gætter oftere. Skillen siger: svar selv når du kan, `spar` som default til småjob, `bedst` og `second_opinion` kun når det er værd.

## PyCharm

File → Open → denne mappe. Interpreter: `.venv` (uv laver den ved `uv sync`). Kør konfiguration: modul `hylde`. Det åbner appen. Modul `hylde` med parameter `--mcp` er serveren uden vindue.

## Docker

Modellen bliver **på Macen** (Metal). Containeren er kun MCP-processen.

```bash
docker build -t hylde:local .
docker run -i --rm \
  --env-file ~/.hylde/.env \
  -e HYLDE_LOCAL_BASE_URL=http://host.docker.internal:11434/v1 \
  --add-host=host.docker.internal:host-gateway \
  -v hylde-data:/data \
  hylde:local
```

I Claude er `command` så `docker` og `args` resten af `run ...`. På en Mac er `uv run` nemmere. Docker er til når du vil have processen isoleret.

## Vedligehold

Tre ting, bevidst ikke automatisk:

1. **Hylder** — `src/hylde/data/table.json` når et model-id skifter. `uv run hylde-refresh` henter OpenRouters forbrugstal til `~/.hylde/priors/` og **rører ikke tabellen**.
2. **Lokal vægt** — `HYLDE_LOCAL_MODEL` når du har en bedre GGUF.
3. **Prompts** — `src/hylde/data/prompts.json`. Kort tekst pr. trin. Kør testene efter du retter.

DSPy hører til punkt 3 senere, når du har 20 faste eksempler med et facit. Den kører ikke i chatten.

## Test

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

## Sikkerhed

- Nøgler skrives i appen. De ligger i `~/.hylde/.env`, ikke i chatten.
- Tekst der ligner en API-nøgle bliver ikke sendt til cloud.
- `privat` sender kun til Ollama og llama.cpp på maskinen. Hvis de er nede, stopper Hylde. Den falder ikke tilbage til GPT eller Ollama Cloud.
- Audit i `~/.hylde/audit.ndjson` gemmer ikke prompten på private kald og aldrig nøgler.
- Lokal server bør lytte på `127.0.0.1`, ikke `0.0.0.0`.

Mere om opbygningen: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
