# Third-party fixtures and integrations

Third-party material retains its own license and attribution. Any project license
applies to original Agent Eval Flow material and does not replace these terms.

The source distribution includes small, pinned test fixtures:

| Fixture | Retained provenance and terms |
| --- | --- |
| Published SWE-agent repair trajectory and format documentation | [Provenance](tests/e2e/fixtures/sweagent_archive/PROVENANCE.json), [license](tests/e2e/fixtures/sweagent_archive/LICENSE) |
| OpenSRE source examples and LogHub HDFS sample | [Sources](tests/e2e/fixtures/opensre/SOURCES.json), [OpenSRE license](tests/e2e/fixtures/opensre/upstream/opensre/LICENSE), [LogHub terms](tests/e2e/fixtures/opensre/upstream/LOGHUB_LICENSE) |
| MarkupSafe workflow source example | [Manifest](tests/e2e/fixtures/workflow/SOURCE_MANIFEST.json), [license](tests/e2e/fixtures/workflow/downloaded/LICENSE.txt) |
| Flaskr source used by the OpenKritt integration tests | [Manifest](tests/e2e/fixtures/openkritt/source_manifest.json), [license](tests/e2e/fixtures/openkritt/review_target/LICENSE.txt) |

The LogHub notice makes the dataset available for research or academic work and
requires retaining its notice and attribution. It is not covered by a blanket
project-code license. Source: [LogHub](https://github.com/logpai/loghub). Citation:
Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu, *Loghub: A Large
Collection of System Log Datasets for AI-driven Log Analytics*, ISSRE 2023.

The wheel contains the library, templates and package metadata; downloaded test
fixtures are kept in the source checkout/distribution. External runtimes and
optional packages remain separate dependencies with their own terms. Integrating
with an agent project does not imply endorsement by its maintainers.

## GPT Researcher pilot

`examples/data/gpt-researcher/capture.json` includes three SimpleQA questions
and reference answers from the GPT Researcher mirror at commit
`6f998577d547b1e54ec662dac63583aa11e3b84b`. SimpleQA originates in
https://github.com/openai/simple-evals (MIT); GPT Researcher is Apache-2.0.
Both license texts and detailed provenance accompany the capture. Linked source
webpage bodies are not redistributed. See the example's README for scope.
