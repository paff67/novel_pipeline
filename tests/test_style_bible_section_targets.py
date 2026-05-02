from __future__ import annotations

import unittest

from novel_pipeline_stable.style_bible_section_targets import load_style_bible_section_targets


class StyleBibleSectionTargetsLoaderTest(unittest.TestCase):
    def test_slot_specs_expose_required_semantic_anchor_fields(self) -> None:
        targets = load_style_bible_section_targets()
        routing_target = targets.densify_target_for_path("worldbook_binding.routing_hints")

        self.assertIsNotNone(routing_target)
        assert routing_target is not None
        slot = routing_target.slot_specs[0]
        self.assertTrue(slot.slot_id)
        self.assertTrue(slot.cue)
        self.assertTrue(slot.canonical_description)
        self.assertTrue(slot.downstream_shape)

    def test_prompt_payload_uses_minimal_semantic_anchor_set_only(self) -> None:
        targets = load_style_bible_section_targets()
        routing_target = targets.densify_target_for_path("worldbook_binding.routing_hints")

        self.assertIsNotNone(routing_target)
        assert routing_target is not None
        prompt_payload = routing_target.as_prompt_payload()

        self.assertEqual(
            set(prompt_payload["slot_specs"][0].keys()),
            {
                "slot_id",
                "cue",
                "canonical_description",
                "downstream_shape",
                "fresh_evidence_required",
            },
        )


if __name__ == "__main__":
    unittest.main()
