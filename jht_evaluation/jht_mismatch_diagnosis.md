# JHT Key-Mismatch Diagnosis

This note reflects the current evaluation after adding turnaround detection,
confidence-aware key ranking, relative-major tie-breaking, a conditional
formal-cadence prior, the flat accidental fix, and a related-key match
criterion.

Current result:

- Comparable annotated trees: 155.
- Strict global-reference key matches: 147.
- Strict global-reference key match rate: 94.8%.
- Related-key global-reference matches: 150.
- Related-key global-reference match rate: 96.8%.
- Remaining mismatch rows after related-key filtering: 5.
- Remaining distinct titles after related-key filtering: 5.

## IV-Opening Correction

Several residual errors were caused by tunes opening on the subdominant
relative to the form-level cadence. The current estimator therefore computes a
`final_form_cadential_key`: the last authentic cadence that resolves onto a
stable, non-dominant chord. If the opening key is IV of this formal cadence,
the opening prior is reduced and the formal cadence receives extra weight.

Recovered examples:

- `Just Friends`: opening C is IV of the form-level G cadence; system now returns G.
- `I Can't Believe...You're In Love...`: opening F is IV of the form-level C cadence; system now returns C.
- `I Fall In Love Too Easily`: system now returns Eb.
- `I Wish I Knew How It Would Feel To Be Free`: system now returns F.

The rule is deliberately conditional. An earlier broader version over-weighted
stable chords inside turnarounds and caused regressions; the final version only
applies the formal-cadence prior when the opening-IV relation is detected.

## Related-Key Criterion

The related-key score treats the following as compatible tonal descriptions:

- exact key match;
- same tonic with different mode;
- relative major/minor;
- minor-to-flat-VI major, e.g. C minor and Ab major.

## Remaining Mismatches After Related-Key Filtering

| # | Title | JHT key | Our key | Confidence | Candidates | Class | Diagnosis |
|---:|---|---:|---:|:---:|---|:---:|---|
| 1 | Light Blue | C | F | low | F:7.95; G:5; C:4.25; Bb:1.75 | B | F, G, and C compete. The formal-cadence rule does not find enough stable C evidence to override F. |
| 2 | Minor Strain | G | d | low | Ab:5.5; d:4; g:1.5; c:0.8 | B/C | Evidence is sparse and unstable. The low-confidence flag is appropriate. |
| 3 | Boy Next Door | C | Bb | medium | Bb:20.45; c:4.9; F:3.4; d:1.75; B:1.4 | B | Bb dominates very strongly in the analysed chart; this is likely a chart/local-centre conflict. |
| 4 | Remember | A- | Db | medium | Db:10.8; B:4.8; eb:4; Bb:3.8; C:3.8 | C | Independent local metadata previously conflicted with JHT. This remains a chart-version/manual-check case. |
| 5 | Beautiful Friendship, A | A- | C | low | C:13.75; G:7; F:5.55; D:5.5; d:2.55 | B/C | The current chart evidence contains little Ab support. This is either highly modulatory or a chart-version mismatch. |

## Interpretation

After removing related-key cases, the residual disagreement is 3.2%. The
remaining failures are concentrated in low-confidence ambiguity, strong local
centre dominance, or likely chart-version disagreement. The parser's functional
analysis remains largely aligned with JHT; the residual issue is the choice of
a single form-level key in charts with competing tonal evidence.

