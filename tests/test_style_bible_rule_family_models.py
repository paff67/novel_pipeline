from __future__ import annotations

import unittest

from novel_pipeline_stable.models import (
    NarrativeRuleItem,
    NegativeRuleItem,
    RoutingHintItem,
    validate_local_rule_row,
)
from novel_pipeline_stable.style_bible_surface_specs import SURFACE_PATH_SPECS, SurfacePath


def _base_runtime_payload() -> dict[str, object]:
    return {
        "rule_id": "row_01",
        "_reasoning_ref": "reasoning_01",
        "evidence_refs": ["scene:0001_001"],
        "anti_pattern_codes": [],
    }


class StyleBibleRuleFamilyModelsTest(unittest.TestCase):
    def test_narrative_rule_item_requires_trigger_and_constraint(self) -> None:
        parsed = NarrativeRuleItem.model_validate(
            {
                **_base_runtime_payload(),
                "trigger": "when a rule gate becomes visible",
                "constraint": "describe the gate before the action lands",
            }
        )

        self.assertEqual(parsed.trigger, "when a rule gate becomes visible")
        self.assertEqual(parsed.constraint, "describe the gate before the action lands")

    def test_routing_hint_item_requires_matcher_and_action(self) -> None:
        parsed = RoutingHintItem.model_validate(
            {
                **_base_runtime_payload(),
                "query_feature_matcher": "when a notice or approval changes the next move",
                "route_target_action": "route to the institutional workflow entry and return the approval chain",
            }
        )

        self.assertTrue(parsed.query_feature_matcher)
        self.assertTrue(parsed.route_target_action)

    def test_negative_rule_item_requires_forbidden_and_correction_fields(self) -> None:
        parsed = NegativeRuleItem.model_validate(
            {
                **_base_runtime_payload(),
                "forbidden_action": "flatten institutional penalties into generic sadness",
                "correction_guideline": "name the gate and the blocked action explicitly",
            }
        )

        self.assertTrue(parsed.forbidden_action)
        self.assertTrue(parsed.correction_guideline)

    def test_local_rule_row_canonicalizes_alias_when_surface_path_is_known(self) -> None:
        parsed = validate_local_rule_row(
            {
                **_base_runtime_payload(),
                "surface_path": "voice_contract.narrator_voice",
                "text": "deadpan",
            }
        )

        self.assertEqual(parsed.text, "deadpan_procedural")

    def test_surface_path_specs_bind_paths_to_concrete_rule_models(self) -> None:
        narrator_voice = SURFACE_PATH_SPECS[SurfacePath.VOICE_NARRATOR_VOICE]
        routing_hints = SURFACE_PATH_SPECS[SurfacePath.WORLDBOOK_ROUTING_HINTS]
        negative_rules = SURFACE_PATH_SPECS[SurfacePath.NEGATIVE_RULES]

        self.assertEqual(narrator_voice.rule_family, "scalar")
        self.assertEqual(narrator_voice.row_model, "ScalarRuleItem")
        self.assertEqual(narrator_voice.enum_source, "voice_contract.narrator_voice")
        self.assertEqual(routing_hints.rule_family, "routing_hint")
        self.assertEqual(routing_hints.row_model, "RoutingHintItem")
        self.assertEqual(negative_rules.rule_family, "negative")
        self.assertEqual(negative_rules.row_model, "NegativeRuleItem")

    def test_local_rule_row_uses_typed_scalar_factory(self) -> None:
        payload = {
            **_base_runtime_payload(),
            "surface_path": "voice_contract.narrator_voice",
            "text": "deadpan",
        }

        parsed = validate_local_rule_row(payload)

        self.assertEqual(parsed.text, "deadpan_procedural")


if __name__ == "__main__":
    unittest.main()
