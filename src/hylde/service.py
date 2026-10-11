"""ask, route og second_opinion. Ét sted, med loft og fallback."""

from __future__ import annotations

from dataclasses import dataclass

from hylde.audit import write_audit
from hylde.config import Settings
from hylde.heuristics import guess_task
from hylde.providers import Completion, ProviderError, Transport, complete
from hylde.router import Choice, RouteError, choose, dialogue_pair, looks_secret
from hylde.table import Catalog, PromptPack, Shelf

PRICE = {"cheap": "lav", "mid": "middel", "frontier": "høj"}


@dataclass
class Report:
    text: str
    shelf_key: str
    model: str
    provider: str
    task: str
    intent: str
    why: str
    price: str
    private: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    second_model: str | None = None

    def render(self) -> str:
        lines = [
            "Hylde",
            f"model: {self.provider}/{self.model}",
            f"hylde: {self.shelf_key}",
            f"opgave: {self.task}",
            f"hensigt: {self.intent}",
            f"pris: {self.price}",
            f"hvorfor: {self.why}",
        ]
        if self.second_model:
            lines.append(f"andet bud fra: {self.second_model}")
        lines.append("---")
        lines.append(self.text)
        return "\n".join(lines)


class Service:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        prompts: PromptPack,
        transport: Transport,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.prompts = prompts
        self.transport = transport

    async def aclose(self) -> None:
        await self.transport.aclose()

    def route(
        self,
        prompt: str,
        *,
        intent: str | None = None,
        task: str | None = None,
        model: str | None = None,
    ) -> Report:
        choice = self._choice(prompt, intent=intent, task=task, model=model)
        return Report(
            text="Ingen model blev kaldt. Sig til om jeg skal køre det.",
            shelf_key=choice.shelf_key,
            model=choice.shelf.model,
            provider=choice.shelf.provider,
            task=choice.task,
            intent=choice.intent,
            why=choice.why,
            price=PRICE[choice.shelf.tier],
            private=choice.private,
        )

    async def ask(
        self,
        prompt: str,
        *,
        intent: str | None = None,
        task: str | None = None,
        model: str | None = None,
    ) -> Report:
        self._check_length(prompt)
        choice = self._choice(prompt, intent=intent, task=task, model=model)
        completion, choice = await self._call_chain(choice, prompt=prompt.strip())
        report = Report(
            text=completion.text,
            shelf_key=choice.shelf_key,
            model=choice.shelf.model,
            provider=choice.shelf.provider,
            task=choice.task,
            intent=choice.intent,
            why=choice.why,
            price=PRICE[choice.shelf.tier],
            private=choice.private,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
        )
        self._audit(report, prompt)
        return report

    async def second_opinion(self, prompt: str) -> Report:
        self._check_length(prompt)
        if looks_secret(prompt):
            raise RouteError(
                "Teksten ligner en nøgle. Den bliver ikke sendt til to cloud-modeller."
            )
        left, right = dialogue_pair(self.catalog, self.settings)
        try:
            right_done = await self._once(
                right.shelf,
                system=self.prompts.dialogue_right,
                prompt=prompt.strip(),
                kind="dialogue",
            )
            left_prompt = f"{prompt.strip()}\n\nAndet modelsvar:\n{right_done.text}"
            left_done = await self._once(
                left.shelf,
                system=self.prompts.dialogue_left,
                prompt=left_prompt,
                kind="dialogue",
            )
        except ProviderError as exc:
            raise RouteError(str(exc)) from exc
        report = Report(
            text=left_done.text,
            shelf_key=left.shelf_key,
            model=left.shelf.model,
            provider=left.shelf.provider,
            task="reason",
            intent="best",
            why="To frontier-modeller. Det er dyrere end et enkelt kald — brug det kun ved tvivl.",
            price="høj",
            private=False,
            input_tokens=(left_done.input_tokens or 0) + (right_done.input_tokens or 0) or None,
            output_tokens=(left_done.output_tokens or 0) + (right_done.output_tokens or 0) or None,
            second_model=f"{right.shelf.provider}/{right.shelf.model}",
        )
        self._audit(report, prompt)
        return report

    def _choice(
        self,
        prompt: str,
        *,
        intent: str | None,
        task: str | None,
        model: str | None,
    ) -> Choice:
        secret = looks_secret(prompt)
        resolved_task = task if task and task != "auto" else guess_task(prompt)
        return choose(
            self.catalog,
            self.settings,
            task=resolved_task,
            intent=intent,
            model=model,
            force_private=secret,
        )

    async def _call_chain(self, choice: Choice, *, prompt: str) -> tuple[Completion, Choice]:
        last: ProviderError | None = None
        keys = (choice.shelf_key,) if choice.pinned or choice.private else choice.chain
        seen: list[str] = []
        for key in keys:
            if key in seen:
                continue
            seen.append(key)
            shelf = self.catalog.shelves[key]
            if choice.private and shelf.provider != "local":
                continue
            if key == "local" and self.settings.local_model:
                shelf = shelf.model_copy(update={"model": self.settings.local_model})
            elif key == choice.shelf_key:
                shelf = choice.shelf
            try:
                completion = await self._once(
                    shelf,
                    system=self.prompts.system[shelf.tier],
                    prompt=prompt,
                    kind=shelf.tier,
                )
            except ProviderError as exc:
                last = exc
                if choice.pinned or choice.private or not exc.retryable:
                    if choice.pinned or choice.private:
                        raise RouteError(str(exc)) from exc
                    if not exc.retryable:
                        continue
                continue
            if key != choice.shelf_key:
                choice = Choice(
                    shelf_key=key,
                    shelf=shelf,
                    task=choice.task,
                    intent=choice.intent,
                    private=choice.private,
                    pinned=False,
                    why=f"{choice.why} {choice.chain[0]} fejlede, så svaret kommer fra {key}.",
                    chain=choice.chain,
                )
            return completion, choice
        raise RouteError(str(last) if last else "Ingen model svarede.")

    async def _once(self, shelf: Shelf, *, system: str, prompt: str, kind: str) -> Completion:
        if self.settings.dry_run:
            preview = prompt[:80]
            return Completion(
                text=f"[dry-run {shelf.provider}/{shelf.model}] {preview}",
                input_tokens=1,
                output_tokens=1,
            )
        timeout = self.prompts.timeout_seconds.get(kind, self.prompts.timeout_seconds[shelf.tier])
        tokens = self.prompts.max_tokens.get(kind, self.prompts.max_tokens[shelf.tier])
        try:
            return await complete(
                self.transport,
                self.settings,
                shelf,
                system=system,
                prompt=prompt,
                max_tokens=tokens,
                timeout=timeout,
            )
        except ProviderError as exc:
            if not exc.retryable:
                raise
            return await complete(
                self.transport,
                self.settings,
                shelf,
                system=system,
                prompt=prompt,
                max_tokens=tokens,
                timeout=timeout,
            )

    def _check_length(self, prompt: str) -> None:
        if not prompt or not prompt.strip():
            raise RouteError("Skriv hvad modellen skal svare på.")
        if len(prompt) > self.settings.max_prompt_chars:
            raise RouteError(
                f"Teksten er for lang ({len(prompt)} tegn). "
                "Del den, eller sæt loftet HYLDE_MAX_PROMPT_CHARS."
            )

    def _audit(self, report: Report, prompt: str) -> None:
        write_audit(
            self.settings.data_dir / "audit.ndjson",
            {
                "shelf": report.shelf_key,
                "model": report.model,
                "provider": report.provider,
                "task": report.task,
                "intent": report.intent,
                "private": report.private,
                "prompt": prompt if not report.private else None,
                "input_tokens": report.input_tokens,
                "output_tokens": report.output_tokens,
            },
        )
