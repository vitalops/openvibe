"""``/simulate`` — run an enterprise workflow simulation from any context."""

from __future__ import annotations

from openvibe.skill.base import CostTier, SkillDefinition, SkillExample


class SimulateSkill(SkillDefinition):
    name = "simulate"
    description = (
        "Design and run an enterprise workflow simulation from a plain-text description. "
        "Generates scenarios, simulates conversations, and evaluates agent performance."
    )
    aliases = ["sim", "harness"]
    capabilities = ["simulation", "dataset-generation", "evaluation", "benchmarking"]
    input_types = ["context_description", "workflow_description"]
    output_types = ["evaluation_report", "dataset", "environment_design"]
    tags = ["simulation", "evaluation", "dataset", "enterprise", "benchmarking"]
    cost_estimate = CostTier.HIGH
    user_invocable = True
    when_to_use = (
        "When you need to simulate an enterprise workflow, generate test scenarios, "
        "or evaluate an agent against realistic enterprise situations. "
        "Describe the workflow in plain English — the harness designs everything."
    )
    argument_hint = "[workflow description] — e.g. 'customer support for a SaaS platform'"
    examples = [
        SkillExample(
            "customer support for a SaaS billing platform with refund and subscription workflows",
            "Generate and evaluate customer support scenarios for a SaaS product",
        ),
        SkillExample(
            "HR onboarding workflow at a 500-person tech company",
            "Simulate HR onboarding conversations and evaluate agent accuracy",
        ),
        SkillExample(
            "B2B sales pipeline for an enterprise software vendor",
            "Simulate sales qualification conversations with lead personas",
        ),
        SkillExample(
            "",
            "No context provided — LLM infers a sensible enterprise workflow",
        ),
    ]

    def get_prompt(self, args: str) -> str:
        context = args.strip()

        # Parse optional flags
        n_scenarios = 10
        output_path = None
        mode = "full"
        parts = context.split()
        remaining: list[str] = []
        i = 0
        while i < len(parts):
            p = parts[i]
            if p.startswith("--n="):
                try:
                    n_scenarios = int(p.split("=", 1)[1])
                except ValueError:
                    pass
            elif p == "--n" and i + 1 < len(parts):
                try:
                    n_scenarios = int(parts[i + 1])
                    i += 1
                except ValueError:
                    pass
            elif p.startswith("--output="):
                output_path = p.split("=", 1)[1]
            elif p == "--output" and i + 1 < len(parts):
                output_path = parts[i + 1]
                i += 1
            elif p.startswith("--mode="):
                mode = p.split("=", 1)[1]
            else:
                remaining.append(p)
            i += 1
        context = " ".join(remaining)

        output_instruction = (
            f'\n- Save the dataset and report to: "{output_path}"' if output_path else ""
        )

        return f"""Use the `simulate` tool to run a complete enterprise workflow simulation.

Parameters:
- context: "{context}"
- n_scenarios: {n_scenarios}
- mode: "{mode}"{output_instruction}

Call the tool now, then present results clearly:
1. Show the designed environment (name, personas, tools, criteria)
2. Show the evaluation report:
   - Overall score and pass rate
   - Breakdown by difficulty and criterion
   - Top 3 weakest scenarios with improvement suggestions
3. Offer to:
   - Show the full markdown report
   - Drill into specific failing scenarios
   - Re-run with adjusted parameters or a different context

Be concise. Use tables where helpful."""
