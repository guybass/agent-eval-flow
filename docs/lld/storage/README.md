# Storage: manifests, typed wire data and evidence — levels 3 and 4

## Next extension: assessment bundles

**Initial assessment implementation in package 0.5.** See the
[implemented surface and boundaries](../../implementation/unified_assessment.md). Use the
[shared assessment contract](../ASSESSMENT_CONTRACT.md) for record meanings.
The new outer roots are stored through an explicitly separate format; existing
v0.4 readers, four root kinds and encodings remain unchanged.

### Level 3: file interfaces

| File | Proposed interface/responsibility |
| --- | --- |
| `assessment_codec.py` | `encode_assessment_root(root) -> bytes`, `decode_assessment_root(payload, expected_kind)`, and Pydantic-derived schemas for AssessmentPlan/AssessmentResult. Delegate nested behavioral roots to the existing codec. |
| `assessment_manifests.py` | `save_assessment(root, path)`, `load_assessment(path, expected_kind)`: owning-root validation, single atomic manifest publication and typed load. Reuse manifest primitives rather than create another persistence backend. |
| `artifacts.py` | Existing exact-byte cache for snapshot files, scanner reports, logs and redacted review payloads. No automatic execution, parsing of instructions or remote materialization on load. |

The proposed envelope has exactly `format`, `schema_version`, `kind`, `data`:

```json
{
  "format": "agent-eval-flow-assessment",
  "schema_version": "0.1",
  "kind": "assessment_result",
  "data": {}
}
```

The empty data object above illustrates the envelope only; a valid result must
contain all fields required by the shared contract. `assessment_plan` is the
other new root kind. Version 0.1 is implemented by the separate assessment
decoder. A nested `behavior_result` retains its
complete `agent-eval-flow` v0.4 result envelope; the optional plan's behavioral
Study is similarly encoded through the v0.4 Study codec. Both are decoded using
known types, never a Python class name from input.

### Level 4: save and load

1. Validate plan, captures, subjects, requested coverage, source projections,
   snapshot bindings, branch outcomes and namespaced activity references.
   Configuration-only data has no behavioral envelope. A failed behavioral
   branch can have no payload but must retain its BranchOutcome and receipts.
2. Encode immutable typed records through Pydantic. Preserve the existing strict
   MetricValue tags, Decimal strings, UTC timestamps and arbitrary JSON boundary.
   New inferred claim basis does not change v0.4 Observation or Measurement enums.
3. Store the complete logical result under one outer manifest, including the
   nested behavioral envelope. This avoids publishing independent manifests
   that could expose mismatched generations after a crash.
4. Write a unique temporary file in the destination directory, flush/close, and
   atomically replace the manifest. Preserve the previous manifest on failure;
   never recursively remove the bundle or mutate a retained v0.4 input bundle.
5. Load only known format/version/kind combinations, reconstruct immutable
   records, and validate every cross-record join before returning methods.
   Unknown future envelopes fail explicitly. No implicit conversion of a v0.4
   EvaluationResult to a new root takes place during load.

Recompute fingerprints only from retained fields. Snapshot entry hashes can be
checked structurally without opening files, but actual byte integrity requires
explicit artifact verification. A missing source file becomes unavailable
evidence on verification; its recorded hash is not recomputed from an empty
file. If referenced candidate-reference table values were not retained, preserve
their recorded projection fingerprint without claiming independent verification.
Reassessment requiring those values must receive a matching reference binding.

Behavioral projection records must equal their nested source measurement on
load; reject changed values, dropped source evidence or altered activity refs.
Validate source collection activity copies against their canonical outer
records. Retained resource totals, if stored, must equal distinct namespaced
source activities with unknown inventory propagated. Never add nested evaluation
totals to the same activities again. `performed_activity_refs` survives unchanged;
loading counts as no new work.

### Evidence, reuse and compatibility

Local artifact materialization is explicit and hashes exact bytes. Runtime
working directories and temporary scanner paths must be translated to logical
snapshot locators in mappings; raw receipts preserve the original locations.
The optional inspection adapter must retain rule/profile versions, exclusions,
suppression settings, coverage and errors along with JSON/SARIF. Saving only
normalized findings cannot promise future rerunning of source-based checks.

References and private answer artifacts retain their classification; report
exports do not automatically include them merely because the bundle can retain
them. Storage does not fetch external URIs or expand report-provided paths.
The existing portability limits remain: copying a manifest does not relocate
external files, and evidence materialization/rewriting is an explicit operation.

The first bridge supports `wrap_behavior_result(existing_result, *, plan)` as an
explicit pure composition operation owned by results/pipeline. The caller must
supply a compatible behavior-only AssessmentPlan containing the full Study;
DatasetInfo inside a saved result cannot reconstruct its original task tables.
Validate candidate/execution/dataset fingerprints and the saved evaluation suite
against that plan without invoking a backend or grader. The bridge preserves
behavioral IDs, source artifact references and semantics. Its outer
`performed_activity_refs` is empty because wrapping performs no evaluation;
the nested result retains its original performed-activity provenance.
There is no reverse lossless
conversion of a configuration-only result to v0.4 and no fake RunSet fallback.
No existing schema version, source fixture or test expectation is rewritten.

### Required verification

Implement the shared [UA09 and UA12 scenarios](../ASSESSMENT_CONTRACT.md#acceptance-scenarios-for-the-extension):
configuration-only and combined round trips, explicit format rejection,
unchanged v0.4 load behavior, atomic overwrite failure, and policy changes with
no runtime calls. Also cover corrupt snapshot/artifact hashes, dangling
namespaced activities, mismatched nested projections, absent reference data,
partial scans, and historical/performed accounting. These are specified future
checks; the current v0.4 tests below do not establish extension compatibility.

## Existing v0.4 behavioral specification

**Contract 0.4, final-review proposal.** Persistence stores data, never runtime
clients or callables. It uses Pydantic's schema machinery and ordinary local
files. No database, object-store SDK or experiment server is required.

## File interfaces

| Planned file | Internal interface | Responsibility |
| --- | --- | --- |
| `codec.py` | `encode_root(root: SavedRoot) -> bytes`; `decode_root(payload: bytes, expected_kind: RootKind) -> SavedRoot`; `json_schema(kind: RootKind) -> Record` | Pydantic root adapters, discriminated envelopes, lossless typed values and version validation |
| `manifests.py` | `ManifestStore.save(root: SavedRoot, path: Path) -> None`; `load(path: Path, expected_kind: RootKind) -> SavedRoot` | Atomic manifest publication, filesystem errors and validation after reading |
| `artifacts.py` | `ArtifactCache(root: Path)`; `write_bytes(name, data, media_type) -> ArtifactRef`; `write_stream(name, stream, media_type) -> ArtifactRef`; `open_writer(name, media_type) -> ArtifactWriter`; `verify(ref) -> ValidationReport` | Spool exact native bytes, compute hashes, publish local references and check integrity when explicitly reading evidence |

`SavedRoot = Study | EvalDataset | RunSet | EvaluationResult` and RootKind is
the corresponding four literal values. ArtifactWriter exposes `write(bytes)`,
`commit() -> ArtifactRef`, `abort() -> None` and context-manager cleanup. It is
an internal binary writer, not another public domain object. A writer can spool
large subprocess streams incrementally; its consumer performs blocking writes
through the adapter's thread/I/O strategy. No silent truncation.

Dependencies point to objects only. Record save/load methods delegate here via
local imports; storage does not import execution or evaluation. Pydantic adapters
are assembled from the records after their type references are resolved. They
can be cached as immutable schema machinery, never as cached experiment results.

## Wire schema

Every bundle contains `manifest.json` with exactly `format`, `schema_version`,
`kind`, `data`. Version 0.4 stores the complete root in data, including a nested
RunSet within EvaluationResult. Format is `agent-eval-flow`. RunSet/Result schema
fields agree with the envelope; unknown outer fields and mismatched kinds fail.
No path-based imports, `eval`, pickle or dynamically named Python classes.

Pydantic discriminated envelope models select the root type. Register typed
serialization for MetricValue and immutable containers. MetricValue's wire
union is a discriminator `type` plus `value`: bool/int/float/decimal/text.
Decimal values encode as strings; typed Decimal resource fields also encode as
strings but do not need a redundant union tag. JSON null at an optional typed
value means missing; it is not a tagged metric type. Plain JSON data inside
agent output, NativeConfig.values and event fields is never interpreted as
tagged typed data. This is tested with an output containing a fake decimal tag.

The wire schema comes from registered Pydantic adapters, including these union
serializers/validators; do not maintain an unrelated handwritten JSON schema.
Datetime encoding is aware UTC with `Z`; finite numeric constraints survive
loading. Immutable tuples/maps need ordinary JSON arrays/objects on the wire.
After validation, reconstruct immutable snapshots and typed Decimal values.
Native options are only validated by an available integration when executing;
offline load still preserves their versioned JSON dialect and raw contents.

## Save algorithm

1. Validate the owning root, nested records, identity references, reconstructible
   fingerprints, grading inventory and derived stored totals. Saving must not
   repair bad facts or claim access to source definitions absent from the root.
2. Encode with the correct root-kind envelope; clients/registries have no schema
   field and cannot enter this document.
3. Create the destination directory if needed. Write a uniquely named temporary
   manifest in the same directory using exclusive file creation.
4. Flush and close it; atomically replace `manifest.json` using the operating
   system's replace operation. Existing readers see a complete old or new
   manifest, not a partially written document. On failure, remove only the
   operation's temporary file and preserve the last published manifest.
5. Do not start agents, recalculate metrics or rerun reducers. Do not silently
   fetch external artifacts while saving metadata.

Concurrent writers to the same bundle path are unsupported; callers choose
different paths. The implementation need not create a distributed lock service.
Overwrite replaces only the manifest; it is not recursive directory cleanup.
An abandoned temporary manifest can remain after a process crash and is ignored
by load. A future cleanup utility must not become part of read behavior.

## Load algorithm

1. Read `path/manifest.json` as bytes; wrap missing/unreadable files as StorageError.
2. Parse and validate the envelope, kind and supported version before rebuilding
   records. Do not guess a future version or silently migrate older documents.
3. Decode typed values through Pydantic. Revalidate nested existing-instance
   boundaries and all relational joins, not just JSON field syntax.
4. Recompute fingerprints only for definitions actually present, and recompute
   stored resource aggregates; reject disagreements. Retain original IDs,
   timestamps, activity phase and performed activity IDs. Loading does not count
   as a new evaluation.
5. Return immutable objects with usable methods. No runtime registry is needed.

Malformed schema or capture relations surface as the public validation family
with document-path context; read/write/JSON decoding failures use StorageError.
These cases are deliberate errors, not reasons to return an empty result.

A standalone RunSet contains DatasetInfo, not all original input/reference
tables or the original EvalSuite. Its original dataset/study fingerprints are
therefore retained provenance, not hashes that load can independently recompute.
A rescored EvaluationResult contains its new suite and must not use that suite
to validate the capture's original study fingerprint. Validate available
candidate definitions, result suite fingerprints, typed identities and joins;
compare full dataset fingerprints later when a dataset is supplied for grading.
Structural validity does not prove the truth of an imported provenance claim.

## Artifact storage and integrity

`ArtifactCache` owns only its configured workspace subtree. Resolve generated
paths and prove containment before writing/replacing/cleanup. Names are labels,
not trusted path fragments; generated cache filenames use unique IDs or content
hashes. Use standard pathlib/tempfile/hashlib/os primitives. URI schemes from
ArtifactRef are not shell commands or implicit remote download instructions.

Write through a temporary binary file while incrementally hashing exact bytes;
flush/close, move to its final cache filename, then return absolute URI,
media type and SHA-256. Empty bytes have a valid hash. Repeated stream chunks
do not create extra artifact records. A failed reader/writer leaves an explicit
capture failure, not a reference to a supposedly complete file.

`verify(ref)` checks a local reference's existence and recorded hash and reports
issues. A missing hash means integrity is unestablished. External URI metadata
can round-trip without fetching content; verify reports unsupported local
verification for that URI. Remote materialization belongs to its integration
client, which feeds returned bytes into this same cache.

Saving a RunSet or result does not make external evidence portable. Reports
link the recorded reference and describe unavailable content. Tests that require
native evidence explicitly materialize it in a local cache with matching hashes.
No live integration is allowed to replace raw evidence with an invented receipt.

## Acceptance links

- [Result/storage cases](../../../tests/contracts/test_results_storage.py):
  lossless scalar union and arbitrary JSON round-trip, invalid envelopes and
  dangling activity rejection.
- [Capture cases](../../../tests/contracts/test_capture.py): rich import and
  save/load preservation of raw sources, jobs, grades and unknown inventory.
- [End-to-end consumer](../../../tests/e2e/support.py): saved Study, RunSet and
  EvaluationResult remain usable without callbacks.
- [Optional-library cases](../../../tests/integrations/test_optional_libraries.py):
  genuine source hashes, immutable fixture archives and native evidence aliases.
