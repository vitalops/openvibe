"""``/summarize`` — distil any content into a concise summary."""

from __future__ import annotations

from openvibe.skill.base import CostTier, SkillDefinition, SkillExample
from openvibe.skill.verifier import MinLengthValidator, NonEmptyValidator


class SummarizeSkill(SkillDefinition):
    name = "summarize"
    description = "Distil content, a document, or a topic into a clear, concise summary."
    aliases = ["sum", "tldr"]
    capabilities = ["summarization", "condensing", "overview"]
    input_types = ["text", "document", "topic", "url"]
    output_types = ["summary", "bullets", "report"]
    tags = ["summary", "overview", "condense", "brief", "tldr"]
    cost_estimate = CostTier.LOW
    user_invocable = True
    when_to_use = "When you need a quick overview of something lengthy or complex."
    argument_hint = "[topic, text, or URL to summarize]"
    examples = [
        SkillExample("the conversation so far", "recap the current conversation"),
        SkillExample("https://example.com/article", "summarize a web page"),
    ]
    validators = [NonEmptyValidator(), MinLengthValidator(50)]

    def get_prompt(self, args: str) -> str:
        subject = args.strip() or "the content or conversation so far"
        return (
            f"Summarize: {subject}\n\n"
            "Provide a concise summary that:\n"
            "- Captures the key points in plain language\n"
            "- Uses bullet points when listing multiple items\n"
            "- Is no longer than needed — omit filler and repetition\n"
            "- Ends with a one-sentence takeaway if helpful\n\n"
            "Do not add opinions or information not present in the source."
        )
