from __future__ import annotations

import unittest

from novel_pipeline_stable.hybrid_retriever import STYLE_ROUTE_CUES, WORLD_ROUTE_CUES
from novel_pipeline_stable.project_domain_vocabulary import (
    default_project_domain_vocabulary_path,
    load_project_domain_vocabulary,
)
from novel_pipeline_stable.style_bible_router import AXIS_LEXICAL_PRIORS, BUCKET_LEXICAL_PRIORS


class ProjectDomainVocabularyTest(unittest.TestCase):
    def test_loader_reads_expected_sections(self) -> None:
        vocabulary = load_project_domain_vocabulary()

        self.assertEqual(vocabulary.source_path, default_project_domain_vocabulary_path())
        self.assertEqual(vocabulary.version, "project-domain-vocabulary-v1")
        self.assertIn("resource_pressure", vocabulary.axis_vocabulary)
        self.assertIn("institutional_pipeline", vocabulary.bucket_vocabulary)
        self.assertIn("文风", vocabulary.route_terms("style"))
        self.assertIn("规则", vocabulary.signal_terms("institution"))
        self.assertIn("触发条件", vocabulary.safe_cues)
        self.assertIn("routing_hints", vocabulary.mechanism_prototypes)
        self.assertIn("keyword stuffing", vocabulary.anti_stuffing_vocabulary)

    def test_router_and_retriever_share_same_lexical_priors(self) -> None:
        vocabulary = load_project_domain_vocabulary()

        self.assertEqual(tuple(STYLE_ROUTE_CUES), vocabulary.route_terms("style"))
        self.assertEqual(tuple(WORLD_ROUTE_CUES), vocabulary.route_terms("world"))
        self.assertEqual(tuple(AXIS_LEXICAL_PRIORS["dark_humor"]), vocabulary.axis_terms("dark_humor"))
        self.assertEqual(
            tuple(BUCKET_LEXICAL_PRIORS["institutional_pipeline"]),
            vocabulary.bucket_terms("institutional_pipeline"),
        )


if __name__ == "__main__":
    unittest.main()
