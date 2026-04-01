from __future__ import annotations

import litellm

from openvibe.config import load_config

_CHUNK_INPUT_FRACTION = 0.10
_SUMMARY_OUTPUT_FRACTION = 0.25
_SUMMARY_OUTPUT_CEIL = 4096


def _resolve_model() -> str:
    config = load_config()
    if config.model:
        return f"{config.model.provider_id}/{config.model.model_id}"
    return "azure/gpt-4.1"


def _model_limits(model: str) -> tuple[int, int]:
    info = litellm.get_model_info(model)
    max_input: int = info["max_input_tokens"]
    max_output: int = info["max_output_tokens"]
    chunk_budget = int(max_input * _CHUNK_INPUT_FRACTION)
    summary_tokens = int(
        min(_SUMMARY_OUTPUT_CEIL, max_output * _SUMMARY_OUTPUT_FRACTION)
    )
    return chunk_budget, summary_tokens


class Summarizer:
    def __init__(self) -> None:
        self._chunk_tokens: int | None = None
        self._max_summary_tokens: int | None = None

    @property
    def model(self) -> str:
        return _resolve_model()

    def _ensure_limits(self) -> tuple[int, int]:
        if self._chunk_tokens is None or self._max_summary_tokens is None:
            self._chunk_tokens, self._max_summary_tokens = _model_limits(self.model)
        return self._chunk_tokens, self._max_summary_tokens

    def _count_tokens(self, text: str) -> int:
        return litellm.token_counter(
            model=self.model,
            messages=[{"role": "system", "content": text}],
        )

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
        response = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=max_summary_tokens,
        )
        return response.choices[0].message.content or ""

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
