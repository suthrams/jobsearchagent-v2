# BUG-NNN: <short title>

- **Severity:** Critical | High | Medium
- **Status:** Open | Fixed
- **Reported:** YYYY-MM-DD
- **Fixed:** YYYY-MM-DD
- **Area:** <subsystem / files>
- **Introduced by:** <commit(s)>, if known

## 1. What happened

Symptom as the user/operator saw it. Paste the traceback or observed behavior.

## 2. Root cause

The actual defect. One layer below the symptom. Why the code was wrong.

## 3. Why it was not caught

The specific test or review gap. Which test layers exist, and the exact reason each
one stepped over this defect. This is the section that earns the RCA -- be precise.

## 4. Prevention

- **The fix:** what changed.
- **Forcing function:** the test/lint/process added so recurrence fails the build.
  Link the file. If none is feasible, say so and explain why.
- **Generalization:** does this guard catch a whole class, or only this instance?
