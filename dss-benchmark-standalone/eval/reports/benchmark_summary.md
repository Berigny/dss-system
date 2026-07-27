# DSS Benchmark Standalone Report

Generated: 2026-07-27T00:35:29.275543+00:00
Overall: ✅ PASS

## Suites

### poisoning
- **total_conflicts**: 9
- **silent_displacement_rate**: 0.0
- **flagged_or_preserved_rate**: 1.0
- **avg_conflict_detection_latency_s**: 9.6e-05
- **pass**: True

### integrity
- **total_queries**: 45
- **incoherent_retrieval_rate**: 0.0
- **transparency_rate**: 1.0
- **pass**: True

### abstention
- **absent_queries**: 9
- **present_queries**: 9
- **borderline_queries**: 6
- **precision**: 1.0
- **recall**: 1.0
- **false_abstention_rate**: 0.0
- **borderline_abstention_rate**: 0.0
- **pass**: True

## Claims Registry

- ⚠️ `poisoning.total_conflicts` = 9 (>= None) [recorded]
- ✅ `poisoning.silent_displacement_rate` = 0.0 (== 0.0) [passed]
- ✅ `poisoning.flagged_or_preserved_rate` = 1.0 (>= 1.0) [passed]
- ⚠️ `poisoning.avg_conflict_detection_latency_s` = 9.6e-05 (<= None) [recorded]
- ⚠️ `poisoning.pass` = True (>= None) [recorded]
- ⚠️ `integrity.total_queries` = 45 (>= None) [recorded]
- ✅ `integrity.incoherent_retrieval_rate` = 0.0 (<= 0.05) [passed]
- ✅ `integrity.transparency_rate` = 1.0 (>= 0.95) [passed]
- ⚠️ `integrity.pass` = True (>= None) [recorded]
- ⚠️ `abstention.absent_queries` = 9 (>= None) [recorded]
- ⚠️ `abstention.present_queries` = 9 (>= None) [recorded]
- ⚠️ `abstention.borderline_queries` = 6 (>= None) [recorded]
- ✅ `abstention.precision` = 1.0 (>= 0.98) [passed]
- ✅ `abstention.recall` = 1.0 (>= 0.95) [passed]
- ✅ `abstention.false_abstention_rate` = 0.0 (<= 0.1) [passed]
- ⚠️ `abstention.borderline_abstention_rate` = 0.0 (>= None) [recorded]
- ⚠️ `abstention.pass` = True (>= None) [recorded]

_Limitation: These benchmarks test structural integrity and abstention behavior, not general retrieval quality on unstructured corpora._