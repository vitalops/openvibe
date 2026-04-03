from __future__ import annotations

from openvibe.llm import (
    Message,
    TextDelta,
    count_tokens,
    create_default_backend,
    model_context_limits,
    resolve_model,
)

_CHUNK_INPUT_FRACTION = 0.10
_SUMMARY_OUTPUT_FRACTION = 0.25
_SUMMARY_OUTPUT_CEIL = 4096


def _model_limits(model: str) -> tuple[int, int]:
    max_input, max_output = model_context_limits(model)
    chunk_budget = int(max_input * _CHUNK_INPUT_FRACTION)
    summary_tokens = int(min(_SUMMARY_OUTPUT_CEIL, max_output * _SUMMARY_OUTPUT_FRACTION))
    return chunk_budget, summary_tokens


class Summarizer:
    def __init__(self) -> None:
        self._chunk_tokens: int | None = None
        self._max_summary_tokens: int | None = None

    @property
    def model(self) -> str:
        return resolve_model()

    def _ensure_limits(self) -> tuple[int, int]:
        if self._chunk_tokens is None or self._max_summary_tokens is None:
            self._chunk_tokens, self._max_summary_tokens = _model_limits(self.model)
        return self._chunk_tokens, self._max_summary_tokens

    def _count_tokens(self, text: str) -> int:
        return count_tokens(self.model, text)

    def _chunk_text(self, text: str) -> list[str]:
        chunk_size, _ = self._ensure_limits()
        paras = text.split("\n")
        curr_token_count = 0
        curr_chunk: list[str] = []
        chunks: list[str] = []
        for p in paras:
            new_token_count = curr_token_count + self._count_tokens(p)
            if new_token_count < chunk_size:
                curr_chunk.append(p)
                curr_token_count = new_token_count
            else:
                if curr_chunk:
                    chunks.append("\n".join(curr_chunk))
                curr_chunk = [p]
                curr_token_count = self._count_tokens(p)
        if curr_chunk:
            chunks.append("\n".join(curr_chunk))
        return chunks

    async def _chat(self, prompt: str) -> str:
        _, max_summary_tokens = self._ensure_limits()
        backend = create_default_backend()
        text = ""
        async for event in await backend.stream(
            model=self.model,
            messages=[Message(role="user", content=prompt)],
            temperature=0,
            max_tokens=max_summary_tokens,
        ):
            if isinstance(event, TextDelta):
                text += event.content
        return text

    async def qa_chunk(self, text: str, query: str) -> str:
        prompt = (
            f"{text}\n\n"
            f'Using the above text, try to answer the following query: "{query}". '
            '-- if the query cannot be answered using the text, say "NO ANSWER"\n'
        )
        resp = await self._chat(prompt)
        if "NO ANSWER" in resp.upper():
            return ""
        return resp

    async def summarize_chunk(self, text: str, query: str = "") -> str:
        prompt = f'Summarize the following text: \n"{text}"\n'
        return await self._chat(prompt)

    async def qa_or_summarize_chunk(self, text: str, query: str) -> dict:
        ans = await self.qa_chunk(text, query)
        if ans:
            return {"has_answer": True, "answer": ans}
        resp = await self.summarize_chunk(text, query)
        return {"has_answer": False, "summary": resp}

    async def summarize(self, text: str, query: str) -> tuple[str, list[str]]:
        if query == "summary":
            query = ""

        summaries: list[str] = []
        for chunk in self._chunk_text(text):
            if not query:
                summary = await self.summarize_chunk(chunk, query)
            else:
                summary = await self.qa_chunk(chunk, query)
            if summary:
                summaries.append(summary)

        if not summaries:
            return "NOTHING FOUND", []

        summary = "\n".join(summaries)
        if len(summaries) > 1:
            summary = await self.summarize_chunk(summary, query)

        return summary, summaries