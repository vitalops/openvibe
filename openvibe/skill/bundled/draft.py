"""``/draft`` — produce a first draft of any written content."""

from __future__ import annotations

from openvibe.skill.base import CostTier, SkillDefinition, SkillExample
from openvibe.skill.verifier import MinLengthValidator, NonEmptyValidator


class DraftSkill(SkillDefinition):
    name = "draft"
    description = "Write a first draft of any content: email, report, proposal, message, or document."
    aliases = ["write", "compose"]
    capabilities = ["writing", "drafting", "composition"]
    input_types = ["topic", "description", "outline", "instructions"]
    output_types = ["draft", "document", "email", "report"]
    tags = ["write", "draft", "compose", "email", "document", "report"]
    cost_estimate = CostTier.LOW
    user_invocable = True
    when_to_use = "When you need a first version of any written content to work from."
    argument_hint = "[what to write and any context]"
    examples = [
        SkillExample("an email declining a meeting politely", "draft a short professional email"),
        SkillExample("a one-page project proposal for a team dashboard", "draft a proposal"),
    ]
    validators = [NonEmptyValidator(), MinLengthValidator(100)]

    def get_prompt(self, args: str) -> str:
        request = args.strip() or "the content described in the current conversation"
        return (
            f"Write a first draft of: {request}\n\n"
            "Guidelines:\n"
            "- Match the appropriate tone and format for the content type\n"
            "- Be clear and direct — avoid filler phrases\n"
            "- Use structure (headings, bullets, paragraphs) only when it aids readability\n"
            "- Keep the draft appropriately concise; do not pad it\n\n"
            "Produce the draft directly, without preamble.\n"
            "After the draft, add a brief note on any assumptions you made."
        )
