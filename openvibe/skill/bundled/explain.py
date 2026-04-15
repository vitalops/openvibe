"""``/explain`` — explain a concept, decision, or piece of content clearly."""

from __future__ import annotations

from openvibe.skill.base import CostTier, SkillDefinition, SkillExample
from openvibe.skill.verifier import MinLengthValidator, NonEmptyValidator


class ExplainSkill(SkillDefinition):
    name = "explain"
    description = "Explain a concept, decision, document, or anything else in plain language."
    aliases = ["eli5", "howdoes"]
    capabilities = ["explanation", "clarification", "teaching"]
    input_types = ["concept", "text", "question", "document"]
    output_types = ["explanation", "analogy", "walkthrough"]
    tags = ["explain", "understand", "clarify", "teach", "how", "why"]
    cost_estimate = CostTier.LOW
    user_invocable = True
    when_to_use = "When something is unclear or you need it broken down into simpler terms."
    argument_hint = "[concept or question]"
    examples = [
        SkillExample("how does OAuth2 work", "explain a technical protocol simply"),
        SkillExample("this contract clause", "explain a document section in plain language"),
    ]
    validators = [NonEmptyValidator(), MinLengthValidator(80)]

    def get_prompt(self, args: str) -> str:
        subject = args.strip() or "the most recent topic in the conversation"
        return (
            f"Explain: {subject}\n\n"
            "Your explanation should:\n"
            "- Start with a one-sentence plain-language answer\n"
            "- Use a concrete analogy or example where it helps\n"
            "- Break down complex parts step by step\n"
            "- Avoid unnecessary jargon; define any term that must be used\n"
            "- Stay focused — do not over-explain tangential details\n\n"
            "Aim for clarity over completeness."
        )
