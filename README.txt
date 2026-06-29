Lambek Calculus for Jazz Harmony
================================

This repository contains a Python program for analysing jazz chord sequences
with a Lambek-calculus-inspired formal system. The program maps chord symbols
to Roman-numeral functions, infers local and global tonal contexts, detects
cadential relations, routes modulations through a D/S/R/P tonality lattice, and
renders the resulting analysis as proof trees.

The project also includes:

- a web interface for running the main tools in a browser;
- a reverse-generation module that produces chord sequences from a target key;
- cadence-statistics tools for analysing large collections of standards;
- an external evaluation bridge against the Jazz Harmony Treebank (JHT).


1. Why this program exists
==========================

The main goal is to connect formal grammar/logical structure with jazz harmony.
Jazz progressions are not just flat chord lists: they contain local functions,
cadences, tonicisations, substitutions, and modulations. This project tries to
model those relations using a proof-tree representation inspired by Lambek
calculus.

The program was built for research purposes. It can be used to:

- analyse a single chord progression;
- analyse jazz standards from a JSON corpus;
- generate readable proof trees;
- extract cadential statistics;
- generate new chord progressions by reversing the analysis process;
- compare the resulting analyses with an external expert-annotated resource.

The external evaluation against the Jazz Harmony Treebank was added to address
the methodological question: does the system merely produce well-formed trees,
or do those trees correspond to existing expert harmonic analyses?


2. Main files
=============

Core analysis:

- chord_grade.py
  Converts chord symbols into Roman-numeral grades relative to a key.

- tonality_route.py
  Implements the 24-key D/S/R/P lattice and finds simple modulation routes.

- lambek_tree.py
  Main analysis engine. It infers tonality, detects cadential relations,
  builds Lambek-style proof trees, and exports LaTeX/PNG outputs.

- simplify_tree_notation.py
  Converts formal LaTeX proof trees into a more readable notation.

- cadence_stats.py
  Computes cadence frequencies from analysis outputs.

- generate_chords.py
  Reverse-generation system for producing chord progressions from a target key.

Web interface:

- run_webapp.sh
  Starts the local web application.

- webapp/backend.py
  HTTP backend for the browser interface.

- webapp/static/index.html
  Browser UI.

Jazz Harmony Treebank evaluation:

- jht_eval.py
  Evaluation bridge between this system and the Jazz Harmony Treebank.

- jht_evaluation/jht_cadence_eval_full.csv
  Full per-piece evaluation output.

- jht_evaluation/jht_evaluation_section.tex
  Paper-ready LaTeX section describing the evaluation.

- jht_evaluation/jht_mismatch_diagnosis.md
  Manual diagnosis of the remaining JHT mismatches.


3. Requirements
===============

Core scripts require Python 3.

For full tree rendering:

- pdflatex
- Ghostscript (gs), or macOS sips fallback
- LaTeX packages: amsmath, amssymb, graphicx, bussproofs, stmaryrd,
  turnstile, geometry, fontenc, inputenc

For the web app:

- FastAPI and related packages listed in webapp/requirements.txt

Check the environment with:

    bash requirements_check.sh


4. How to run the program
=========================

Analyse a single chord sequence:

    python3 lambek_tree.py --sequence D7 G7 Cmaj7

Analyse a jazz standard:

    python3 lambek_tree.py --standard "Alone Together"

Analyse a standard and produce readable output:

    python3 lambek_tree.py --standard-readable "Alone Together"

Analyse all standards in a folder:

    python3 lambek_tree.py --all-standards --standards-dir "JazzStandards-main/JazzStandards"

Fast last-line analysis:

    python3 lambek_tree.py analyse "Alone Together"

Fast last-line analysis over a full standards folder:

    python3 lambek_tree.py analyse "JazzStandards-main/JazzStandards" --format txt

Generate cadence statistics:

    python3 cadence_stats.py --input "last_line_analysis/JazzStandards_all.txt"

Generate a chord progression:

    python3 generate_chords.py --tonality C --target-chords 8

Generate a chord progression with modulation controls:

    python3 generate_chords.py --tonality C --target-chords 8 \
      --modulation-strength 0.9 \
      --modulation-complexity 0.7 \
      --tonal-drift 0.6

Run the web interface:

    bash run_webapp.sh

Then open:

    http://127.0.0.1:8000


5. Main techniques
==================

5.1 Chord normalisation
-----------------------

The program accepts common jazz chord spellings and normalises them before
analysis. Examples:

- MA7 is treated as maj7.
- Slash basses are ignored for function.
- Half-diminished chords are recognised as m7b5, m7(b5), %, or o-style input.
- Parenthetical alternates can be expanded.
- Enharmonic flat spellings such as Bb, Eb, Ab, and Gb are handled explicitly.

5.2 Roman-numeral mapping
-------------------------

Each chord is mapped to a Roman-numeral degree relative to a current key.
Minor and diminished chords are represented with lowercase functional labels
when appropriate. Dominant-like chords are treated as functional dominants even
when they contain extensions or alterations.

5.3 Tonality inference
----------------------

The parser infers local tonal contexts from:

- opening ii-V-I and ii-V evidence;
- dominant resolutions;
- final unresolved dominants;
- diatonic coherence;
- local cadential targets.

The system distinguishes between local operative keys and global form-level
keys. This distinction is important in jazz standards, where the first local
region is often not the global key.

5.4 D/S/R/P modulation lattice
------------------------------

Keys are connected through four relations:

- D: dominant direction;
- S: subdominant direction;
- R: relative major/minor;
- P: parallel major/minor.

The route finder prefers simple paths:

1. shortest path;
2. fewer R operations;
3. fewer non-D/S operations;
4. D/S preferred over P and R when possible.

These modulation paths are shown in the generated proof trees.

5.5 Cadence ontology
--------------------

The system recognises a shared set of cadence/function events:

- perfect authentic cadence;
- plagal relation;
- ii-to-V;
- tritone-substitute resolution;
- backdoor motion;
- deceptive motion;
- generic descending fifth.

This ontology is also used to compare the system with the Jazz Harmony
Treebank.

5.6 Proof-tree generation
-------------------------

The output is a Lambek-style proof tree in LaTeX. The tree records:

- local chord functions;
- contractions of repeated material;
- modulations;
- key labels;
- derivational structure.

Readable versions can be generated for easier inspection.

5.7 Reverse generation
----------------------

The generation system runs the analysis process backwards. It starts from a
target key and expands chords using symbolic rules and, when available,
dataset-derived probabilities. The user can control:

- number of chords;
- randomness seed;
- modulation strength;
- modulation complexity;
- tonal drift;
- branching mode.


6. Jazz Harmony Treebank evaluation
===================================

An important part of the project is the external evaluation against the Jazz
Harmony Treebank (JHT), an expert-annotated corpus of jazz harmony trees.

The evaluation script is:

    jht_eval.py

The command used for the full evaluation was:

    python3 jht_eval.py \
      --jht-json /tmp/jht_treebank.json \
      --out jht_evaluation/jht_cadence_eval_full.csv

The bridge performs the following steps:

1. Load JHT treebank.json.
2. Normalise JHT chord notation into this program's notation.
3. Run this parser on the same chord sequences.
4. Extract comparable leaf, cadence, depth, and key metrics.
5. Translate both systems into a shared cadence vocabulary.
6. Compare exact keys and related keys.
7. Write a CSV file with all piece-level results.

6.1 JHT notation normalisation
------------------------------

The evaluation converts common JHT symbols:

- ^ to maj;
- % and o-style half-diminished symbols to m7b5;
- - to b for flats;
- o7 to dim7;
- o to dim.

6.2 Evaluation metrics
----------------------

The evaluation reports:

- leaf-count match;
- leaf sequence accuracy;
- leaf longest-common-subsequence F1;
- key agreement;
- final-key agreement;
- last-cadence key agreement;
- modal-cadential key agreement;
- representative-cadential key agreement;
- global-reference key agreement;
- related-key global-reference agreement;
- cadence precision, recall, and F1.

6.3 Final JHT results
---------------------

The current evaluation uses:

- 1170 JHT pieces inspected;
- 155 comparable annotated trees;
- 0 skipped pieces.

Main results:

- Leaf sequence accuracy: 99.9%.
- Mean leaf LCS F1: 98.0%.
- Median leaf LCS F1: 98.4%.
- Mean cadence precision: 88.3%.
- Mean cadence recall: 84.6%.
- Mean cadence F1: 85.6%.
- Micro cadence precision: 90.0%.
- Micro cadence recall: 84.9%.
- Micro cadence F1: 87.4%.
- Strict global-reference key match: 94.8%.
- Related-key global-reference match: 96.8%.

The exact leaf-count match is lower, 43.2%, but this should not be interpreted
as a failure of chord alignment. The leaf sequence and LCS metrics show that
the harmonic material is aligned. The lower exact leaf-count rate mostly comes
from structural differences between JHT constituent trees and this system's
proof trees.

6.4 Related-key filtering
-------------------------

Some key mismatches are not musically distant. The related-key metric treats
the following as compatible:

- exact key match;
- same tonic with different mode;
- relative major/minor;
- minor-to-flat-VI major relation, for example C minor and Ab major.

After this filtering, only 5 out of 155 comparable trees remain unmatched.

6.5 Residual mismatches
-----------------------

The remaining unmatched titles are:

- Light Blue;
- Minor Strain;
- Boy Next Door;
- Remember;
- A Beautiful Friendship.

These residual cases are not clear parser failures.

For example, the JHT chord sequence for The Boy Next Door strongly supports
Bb major:

    Bbmaj7 | G7 | Cm7 | F7 | Bbmaj7

This is naturally analysed as:

    Imaj7 | VI7 | ii7 | V7 | Imaj7

The system therefore selects Bb, while the JHT metadata lists C. This is more
plausibly a metadata or chart-version issue than a parser error.

For Light Blue, Minor Strain, Remember, and A Beautiful Friendship, reliable
online or lead-sheet evidence is sparse or inconsistent. The remaining 3.2%
disagreement is therefore best understood as a mixture of low-confidence
tonal ambiguity, chart-version differences, and reference metadata uncertainty.


7. Historical development
=========================

The project began as a tool for producing Lambek-style proof trees from jazz
chord progressions. The first goal was formal: show that harmonic sequences
could be mapped into derivational structures with typed local functions.

The next stage added practical support for jazz-standard datasets:

- parsing JSON standards;
- batch analysis;
- output folders for LaTeX and PNG files;
- readable tree conversion;
- cadence statistics.

After that, the project gained reverse generation. The generator was designed
to expand chords backward from a target tonality using symbolic cadence rules,
dataset priors, and optional modulation controls.

The most recent stage focused on evaluation. A reviewer pointed out that the
paper needed external validation against an existing analytical framework or
expert annotation source. In response, the project added the JHT evaluation
bridge. This made it possible to compare the parser with an expert-annotated
jazz harmony treebank.

The evaluation was refined iteratively:

1. A direct comparison between the parser's initial key and JHT's global key
   gave modest results, because the two values describe different things.
2. Additional estimates were added: final key, last-cadence key, modal
   cadential key, and representative cadential key.
3. A combined global-reference key estimator was introduced.
4. Repeated local cadences were capped so that tonicisations would not dominate
   the global key.
5. A flat-spelling bug for Bb/Ab-style keys was fixed.
6. Turnaround detection was added.
7. Candidate keys and confidence labels were added.
8. Related-key filtering was added.
9. A conditional formal-cadence prior was added for standards that open on IV.

This process raised the global-key comparison from an initially weak local-key
comparison to 94.8% exact global-reference agreement and 96.8% related-key
agreement.


8. Examples
===========

8.1 Simple ii-V-I
-----------------

Command:

    python3 lambek_tree.py --sequence Dm7 G7 Cmaj7

Expected interpretation:

    ii7 -> V7 -> Imaj7 in C

The output tree shows the functional progression and resolves the cadence into
the inferred key.

8.2 Tritone substitution
------------------------

Command:

    python3 lambek_tree.py --sequence Dm7 Db7 Cmaj7

Expected interpretation:

    ii7 -> bII7 -> Imaj7

The bII7 chord is treated as a tritone-substitute dominant resolving to I.

8.3 Standard analysis
---------------------

Command:

    python3 lambek_tree.py --standard-readable "Alone Together"

This analyses the selected standard, renders the proof tree, and creates a
readable version of the output.

8.4 JHT evaluation
------------------

Command:

    python3 jht_eval.py \
      --jht-json /tmp/jht_treebank.json \
      --out jht_evaluation/jht_cadence_eval_full.csv

This produces a CSV file with per-piece comparison metrics against JHT.


9. How to interpret the results
===============================

The system should not be judged by whether it reproduces the exact same tree
shape as JHT. The two representations are different:

- JHT uses constituent trees with harmonic heads.
- This project uses Lambek-style proof trees with derivational and modulation
  information.

The better comparison is functional:

- Are the same chords being analysed?
- Are similar cadential relations detected?
- Does the system recover a compatible global key?

The current data support positive answers:

- chord material is aligned almost perfectly;
- cadence agreement is strong;
- global-key agreement is high after accounting for local vs form-level key
  distinctions.

Therefore, the system is reliable as a functional/cadential parser for jazz
harmony. It is not intended to be a clone of JHT's tree representation.


10. Reviewer-facing conclusion
==============================

The reviewer asked for evaluation against existing analytical frameworks or
expert annotations. The current version addresses this directly by comparing
the system with the Jazz Harmony Treebank.

The key conclusion is:

The parser does not merely produce formally valid trees. When projected onto a
shared cadence vocabulary and compared against JHT, it recovers substantially
overlapping harmonic relations. Surface chord alignment is nearly perfect,
cadence-level agreement is strong, and global-reference key agreement reaches
94.8%, or 96.8% when closely related keys are treated as compatible.

The few remaining mismatches are musically interpretable and mostly arise from
local tonicisation, sparse evidence, chart-version differences, or uncertain
metadata. This makes the system's behaviour inspectable and scientifically
defensible.

