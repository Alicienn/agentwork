# Fictional Example: Local Note Export

This example is not a requirement for any project. The human says: "Export the
current note as UTF-8 text, without modifying it. File naming is undecided. Cloud
export is deferred. You may choose the internal encoder organization."

Initial design (R1); ROOT is only a display container:

```text
ROOT
+-- A1 [F] AXIOM/HIGH Export current note as UTF-8, without mutation
+-- C1 [?] CHOICE/HIGH File naming
|   +-- C1.A [~] INFERENCE/HIGH Use note title
|   +-- C1.B [~] INFERENCE/HIGH Use constant export.txt
+-- S1 [~] ASSUMPTION/HIGH Titles are safe filenames
+-- I1 [*] CHOICE/LOW Internal encoder organization
|   +-- I1.A [~] INFERENCE/LOW Existing function
|   +-- I1.B [~] INFERENCE/LOW Local helper
+-- Q1 [D] QUESTION/HIGH Cloud export
```

Node records (title is abbreviated in the tree only where necessary):

| id | type | status | title | origin | impact | parent |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | AXIOM | FIXED | Export current note as UTF-8, without mutation | Human statement | HIGH | ROOT |
| C1 | CHOICE | OPEN | File naming | Human: undecided | HIGH | ROOT |
| C1.A | INFERENCE | PROVISIONAL | Use note title | Agent pass 1, A1 | HIGH | C1 |
| C1.B | INFERENCE | PROVISIONAL | Use constant export.txt | Agent pass 1, A1 | HIGH | C1 |
| S1 | ASSUMPTION | PROVISIONAL | Titles are safe filenames | Agent pass 1, C1.A | HIGH | ROOT |
| I1 | CHOICE | FREE | Internal encoder organization | Human delegation | LOW | ROOT |
| I1.A | INFERENCE | PROVISIONAL | Existing function | Agent pass 1, I1 | LOW | I1 |
| I1.B | INFERENCE | PROVISIONAL | Local helper | Agent pass 1, I1 | LOW | I1 |
| Q1 | QUESTION | DEFERRED | Cloud export | Human deferral | HIGH | ROOT |

C1 alternatives: C1.A, C1.B. I1 alternatives: I1.A, I1.B; constraints: preserve
A1 and public interfaces, add no dependency. S1 depends_on: C1.A.
Audit: S1 is unconfirmed HIGH impact; filenames can introduce failure behavior.
Potential hidden assumption: existing file replacement policy. Preserve the
project's accepted save behavior; absent such a policy, raise a separate question.

Human steering: "Re-explore C1; replace its alternatives with asking for a name
or using the existing save dialog. Keep the rest unchanged."

R2 preserves A1, I1, Q1, and S1. C1.A and C1.B become REJECTED (replacement
authorized by that instruction), retaining their records/history. Add:

| id | type | status | title | origin | impact | parent |
| --- | --- | --- | --- | --- | --- | --- |
| C1.C | INFERENCE | PROVISIONAL | Ask for a name | Human re-exploration instruction | HIGH | C1 |
| C1.D | INFERENCE | PROVISIONAL | Existing save dialog | Human re-exploration instruction | HIGH | C1 |

C1 alternatives now reference C1.C and C1.D. S1's dependency is invalidated;
flag S1 as unusable in the contract without rewriting this sibling. If the
existing dialog modifies the note, report UPSTREAM CONFLICT with A1; do not
relax A1 or silently repair unrelated design.

Human: "Fix C1 to C1.D, reject C1.C. Use the existing dialog and its existing
overwrite/cancel behavior." R3 records that approval: C1 and C1.D FIXED,
C1.C REJECTED; other states unchanged. The accepted scope is A1, C1/C1.D, I1.
Q1 remains excluded; S1 remains provisional and is not relied upon.

Implementation may choose I1.A or I1.B within I1's bounds and record the choice
without turning that agent choice into human FIXED authority. Encoding and
no-mutation tests cite A1; dialog behavior tests cite C1.D and accepted prior
behavior. Implementing cloud upload is prohibited by Q1. A passing test that
expects silent renaming does not authorize renaming: report UNAUTHORIZED-BEHAVIOR
and question its test provenance. Independently review and repair named findings.
