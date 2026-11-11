# Local evaluation contract

Source: competition description, evaluation table and PA formula supplied by the
user on 2026-11-09. This implements a local diagnostic, not the organizer kit.

`datamate score --predictions PATH --labels PATH` scores an existing file without
invoking inference. References are read separately from submissions: `alias` is
allowed and an unanswerable reference may contain `answer: null`. Inference never
receives these labels. Missing predictions abstain; order is irrelevant to scoring.
Duplicate IDs, unknown IDs and malformed records fail validation rather than being
silently repaired. The prediction writer still preserves question order.

- PA = (correct - wrong) / reference question count. Explicit abstentions score zero.
- Types must match. Reasons do not affect matching of unanswerable answers.
- Numbers compare exactly with Decimal parsing, without float conversion or tolerance.
- Percents round to two decimals and must retain the correct sign. Half-ties use
  Decimal ROUND_HALF_UP as an explicit local convention; the supplied evaluation
  image does not specify the half-tie algorithm. Organizer parity remains unverified.
- Names use NFC, lowercase, replacement of punctuation with spaces, and whitespace
  collapse. Accents are retained; lowercase is deliberately not Unicode casefold.
- Flat aliases are supported for scalar string answers. The released train metadata
  contains nested aliases for 106 lists (one alias group per position) and 19 tables
  (one group per row, with exactly one string cell in every row). The local scorer
  supports these observed shapes. It does not assign row aliases to tables with
  multiple string cells, whose mapping would be ambiguous.
- Lists compare position by position with normalized names and exact numbers.
- Tables align columns by normalized name when the complete unique name sets match;
  otherwise they compare by position. Rows compare as sets, as the evaluation table
  states. Duplicate normalized column names use positional comparison.

The training rulebook mentions floating-point error tolerance without specifying
its magnitude. This implementation does not invent one. Partial column-name
alignment and the inferred nested-alias interpretation require the organizer evaluator for parity.

Use synthetic references for regression tests and train-only held-out references
for development measurements. Never obtain or construct public/private test labels.
