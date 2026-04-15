"""``/brainstorm`` — generate a diverse set of ideas around a topic."""

from __future__ import annotations

from openvibe.skill.base import CostTier, SkillDefinition, SkillExample
from openvibe.skill.verifier import KeywordValidator, MinLengthValidator


class BrainstormSkill(SkillDefinition):
    name = "brainstorm"
    description = "Generate a wide set of ideas, options, or approaches for any topic or problem."
    aliases = ["ideas", "bs"]
    capabilities = ["ideation", "exploration", "creative_thinking"]
    input_types = ["topic", "problem", "question", "goal"]
    output_types = ["ideas", "list", "options"]
    tags = ["ideas", "brainstorm", "options", "explore", "creative"]
    cost_estimate = CostTier.LOW
    user_invocable = True
    when_to_use = "When you need a broad set of options or are stuck on where to start."
    argument_hint = "[topic or problem]"
    examples = [
        SkillExample("names for a productivity app", "generate product name ideas"),
        SkillExample("ways to reduce meeting time", "brainstorm process improvements"),
    ]
    validators = [MinLengthValidator(100), KeywordValidator(required=["1."])]

    def get_prompt(self, args: str) -> str:
        topic = args.strip() or "the topic raised in the current conversation"
        return (
            f"Brainstorm ideas for: {topic}\n\n"
            "Generate at least 8 distinct ideas. For each idea:\n"
            "- Number it (1. 2. 3. …)\n"
            "- Give it a short name or headline\n"
            "- Add one sentence explaining the core concept\n\n"
            "Prioritise variety — include obvious options alongside unconventional ones.\n"
            "Do not evaluate or rank the ideas; just list them.\n"
            "After the list, briefly note any interesting tensions or trade-offs you see."
        )
