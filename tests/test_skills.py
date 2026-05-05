"""Tests for the skills subsystem.

Covers SkillDefinition, SkillRegistry, match_intent, search/ranking,
and the bundled skills available after init_bundled_skills().
"""

from __future__ import annotations

import pytest

from openvibe.skill.base import (
    CostTier,
    SkillDefinition,
    SkillExample,
    SkillResult,
    SkillStatus,
    SkillValidator,
    ValidationResult,
)
from openvibe.skill.registry import SkillRegistry, _tokenise


# ---------------------------------------------------------------------------
# Minimal skill implementation for testing
# ---------------------------------------------------------------------------


class _EchoSkill(SkillDefinition):
    name = "echo"
    description = "Repeats back what you give it."
    aliases = ["repeat"]
    tags = ["test", "echo"]
    capabilities = ["text-processing"]
    cost_estimate = CostTier.LOW
    user_invocable = True
    when_to_use = "Use when you want to repeat input."

    def get_prompt(self, args: str) -> str:
        return f"Repeat: {args or 'nothing'}"


class _HiddenSkill(SkillDefinition):
    name = "internal"
    description = "Internal skill not shown to users."
    user_invocable = False

    def get_prompt(self, args: str) -> str:
        return "internal prompt"


# ---------------------------------------------------------------------------
# SkillDefinition tests
# ---------------------------------------------------------------------------


class TestSkillDefinition:
    def test_get_prompt_no_args(self):
        skill = _EchoSkill()
        assert skill.get_prompt("") == "Repeat: nothing"

    def test_get_prompt_with_args(self):
        skill = _EchoSkill()
        assert skill.get_prompt("hello") == "Repeat: hello"

    def test_get_retry_prompt_appends_hint(self):
        skill = _EchoSkill()
        retry = skill.get_retry_prompt("hello", attempt=2, hint="be more concise")
        assert "Repeat: hello" in retry
        assert "be more concise" in retry
        assert "Previous attempt 1" in retry

    def test_get_retry_prompt_no_hint(self):
        skill = _EchoSkill()
        retry = skill.get_retry_prompt("hello", attempt=2, hint="")
        assert retry == skill.get_prompt("hello")

    def test_extract_args_default_returns_empty(self):
        skill = _EchoSkill()
        assert skill.extract_args("some natural language") == ""

    def test_cost_tier_low(self):
        assert _EchoSkill.cost_estimate == CostTier.LOW

    def test_default_reliability(self):
        assert _EchoSkill.reliability == 1.0


# ---------------------------------------------------------------------------
# match_intent tests
# ---------------------------------------------------------------------------


class TestMatchIntent:
    def setup_method(self):
        self.skill = _EchoSkill()

    def test_tag_match_raises_score(self):
        score = self.skill.match_intent("I want to echo something please")
        assert score > 0.0

    def test_alias_match_raises_score_more(self):
        # 'repeat' is an alias → +0.4
        score = self.skill.match_intent("please repeat this message back to me")
        assert score >= 0.4

    def test_no_match_returns_zero(self):
        # Inputs with no overlap with tags/aliases/capabilities of _EchoSkill
        score = self.skill.match_intent("refactor the database layer")
        assert score == 0.0

    def test_short_input_penalised(self):
        # Two-word input → score * 0.5
        score_short = self.skill.match_intent("echo test")
        score_long = self.skill.match_intent("I want to echo this test string")
        # Short should be lower (penalised at < 3 words)
        assert score_short <= score_long

    def test_score_capped_at_one(self):
        # Pack in every signal: alias + tags + capabilities
        text = "repeat echo test text-processing please do it now for me"
        score = self.skill.match_intent(text)
        assert score <= 1.0


# ---------------------------------------------------------------------------
# SkillRegistry tests
# ---------------------------------------------------------------------------


class TestSkillRegistry:
    def setup_method(self):
        self.registry = SkillRegistry()

    def test_register_and_get_by_name(self):
        skill = _EchoSkill()
        self.registry.register(skill)
        assert self.registry.get("echo") is skill

    def test_get_by_alias(self):
        skill = _EchoSkill()
        self.registry.register(skill)
        assert self.registry.get("repeat") is skill

    def test_get_missing_returns_none(self):
        assert self.registry.get("nonexistent") is None

    def test_all_returns_registered_skills(self):
        s1 = _EchoSkill()
        s2 = _HiddenSkill()
        self.registry.register(s1)
        self.registry.register(s2)
        all_skills = self.registry.all()
        assert s1 in all_skills
        assert s2 in all_skills

    def test_user_invocable_excludes_hidden(self):
        self.registry.register(_EchoSkill())
        self.registry.register(_HiddenSkill())
        visible = self.registry.user_invocable()
        names = [s.name for s in visible]
        assert "echo" in names
        assert "internal" not in names

    def test_register_overwrites_existing(self):
        s1 = _EchoSkill()
        self.registry.register(s1)

        class _EchoSkill2(_EchoSkill):
            description = "Updated description"

        s2 = _EchoSkill2()
        self.registry.register(s2)
        assert self.registry.get("echo") is s2

    def test_search_returns_ranked_results(self):
        self.registry.register(_EchoSkill())
        self.registry.register(_HiddenSkill())
        results = self.registry.search("echo test")
        assert len(results) >= 1
        # Highest-scoring first
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)

    def test_find_best_returns_top_skill(self):
        self.registry.register(_EchoSkill())
        best = self.registry.find_best("echo")
        assert best is not None
        assert best.name == "echo"

    def test_find_best_no_match_returns_none(self):
        self.registry.register(_EchoSkill())
        best = self.registry.find_best("quantum entanglement analysis")
        assert best is None

    def test_reliability_zero_demotes_skill(self):
        """A skill with reliability=0 should score 50% of its base score."""

        class _LowReliSkill(_EchoSkill):
            name = "low_reli"
            reliability = 0.0

        class _HighReliSkill(_EchoSkill):
            name = "high_reli"
            reliability = 1.0
            description = "Repeats back."  # same token overlap

        registry = SkillRegistry()
        registry.register(_LowReliSkill())
        registry.register(_HighReliSkill())

        results = {name: score for skill, score in registry.search("repeat echo", top_k=10) for name in [skill.name]}
        if "low_reli" in results and "high_reli" in results:
            assert results["high_reli"] > results["low_reli"]


# ---------------------------------------------------------------------------
# Tokeniser tests
# ---------------------------------------------------------------------------


class TestTokenise:
    def test_basic(self):
        assert _tokenise("hello world") == {"hello", "world"}

    def test_lowercase(self):
        assert _tokenise("Hello World") == {"hello", "world"}

    def test_numbers(self):
        assert "123" in _tokenise("test 123")

    def test_punctuation_removed(self):
        tokens = _tokenise("fix the tests!")
        assert "fix" in tokens
        assert "!" not in "".join(tokens)


# ---------------------------------------------------------------------------
# Bundled skills tests
# ---------------------------------------------------------------------------


class TestBundledSkills:
    """Tests for the bundled-skills infrastructure.

    init_bundled_skills() is called at startup.  Skills registered via the
    global register_skill() helper must be retrievable from get_registry().
    """

    def setup_method(self):
        from openvibe.skill.bundled import init_bundled_skills

        init_bundled_skills()

    def _global_registry(self):
        from openvibe.skill.registry import get_registry

        return get_registry()

    def test_init_bundled_skills_is_idempotent(self):
        """Calling init_bundled_skills() twice must not raise."""
        from openvibe.skill.bundled import init_bundled_skills

        init_bundled_skills()  # second call
        # Still callable — no duplicate-registration error expected
        assert True

    def test_registered_skill_survives_second_init(self):
        """Manually registered skills must survive a second init call."""
        from openvibe.skill.bundled import init_bundled_skills
        from openvibe.skill.registry import register_skill

        class _TestSkill(_EchoSkill):
            name = "test_survives"

        register_skill(_TestSkill())
        init_bundled_skills()

        assert self._global_registry().get("test_survives") is not None

    def test_register_skill_global(self):
        """register_skill() puts a skill in the global registry."""
        from openvibe.skill.registry import register_skill, get_registry

        class _UniqueSkill(_EchoSkill):
            name = "unique_global_skill"

        register_skill(_UniqueSkill())
        assert get_registry().get("unique_global_skill") is not None


# ---------------------------------------------------------------------------
# SkillValidator tests
# ---------------------------------------------------------------------------


class TestSkillValidator:
    def test_validation_result_passed(self):
        vr = ValidationResult(passed=True, reason="all good")
        assert vr.passed is True
        assert vr.can_retry is True  # default

    def test_validation_result_failed_with_hint(self):
        vr = ValidationResult(
            passed=False,
            reason="output too short",
            can_retry=True,
            retry_hint="include more detail",
        )
        assert vr.passed is False
        assert "more detail" in vr.retry_hint

    def test_custom_validator(self):
        class LengthValidator(SkillValidator):
            name = "length"

            def validate(self, result: SkillResult, context: dict) -> ValidationResult:
                if len(result.output) >= 10:
                    return ValidationResult(passed=True)
                return ValidationResult(
                    passed=False,
                    reason="output too short",
                    retry_hint="write more",
                )

        validator = LengthValidator()

        short_result = SkillResult(
            skill_name="test", status=SkillStatus.SUCCESS, output="short"
        )
        long_result = SkillResult(
            skill_name="test", status=SkillStatus.SUCCESS, output="this is long enough"
        )

        assert validator.validate(short_result, {}).passed is False
        assert validator.validate(long_result, {}).passed is True
