# Reporting Model – jobsearchagent-v2

---

## 1. Purpose

This document defines how **jobsearchagent-v2** transforms workflow outputs into structured, user-facing reports.

The system’s true value is not individual agent outputs.

It is:

> A coherent, evidence-based career intelligence report that the user can understand, trust, and act on.

---

## 2. Core Reporting Principle

The system follows this rule:

> Agent outputs are intermediate. Reports are the final product.

Reports must:

* be structured
* be evidence-based
* map cleanly to agent outputs
* avoid hallucinated content
* be easy to scan and act on

---

## 3. Report Types

The system may generate multiple report types.

---

### 3.1 Full Career Intelligence Report

Primary report.

Includes:

* job fit analysis
* resume critique
* career gaps
* interview prep
* tailoring suggestions
* next steps

---

### 3.2 Job Summary Report (Lightweight)

For quick review.

Includes:

* job summary
* fit score
* key strengths
* top gaps

---

### 3.3 Interview Preparation Report

Focused on interview readiness.

Includes:

* likely questions
* technical areas
* leadership stories
* weak areas

---

### 3.4 Tailoring Report

Focused on resume improvement.

Includes:

* suggested rewrites
* evidence mapping
* fidelity risks

---

## 4. Report Structure (Full Report)

---

## 4.1 Executive Summary

### Purpose

Quick overview of job fit.

### Content

```text
Job Title
Company
Overall Match Score
Fit Category (Strong / Moderate / Weak)
Summary Explanation
```

---

## 4.2 Fit Score Breakdown

### Source

Scoring Agent

### Content

```text
Technical Fit
Architecture Fit
Leadership Fit
Domain Fit
Overall Score
Reasoning Summary
```

---

## 4.3 Strengths

### Source

Scoring Agent + Resume Critic

### Content

* strongest alignment points
* transferable strengths
* differentiators

---

## 4.4 Resume Gaps

### Source

Resume Critic

### Content

* missing signals in resume
* weak positioning areas
* under-expressed experience

---

## 4.5 Career Gaps

### Source

Resume Critic + Career Advisor

### Content

* missing real experience
* missing domain exposure
* missing leadership scope

---

### Critical Rule

```text
Resume gaps ≠ Career gaps
```

Must be clearly separated.

---

## 4.6 Research Context

### Source

Research Agent

### Content

* company summary
* role expectations
* key signals
* risk flags

---

## 4.7 Reflection Summary

### Source

Review Auditor

### Content

* number of review rounds
* final audit score
* improvement summary
* stop reason

---

## 4.8 Career Strategy

### Source

Career Advisor

### Content

* positioning strategy
* role fit assessment
* recommended positioning
* skill development plan
* short-term vs long-term actions

---

## 4.9 Interview Preparation

### Source

Interview Coach

### Content

* likely topics
* technical preparation
* leadership stories
* weak areas
* prep plan

---

## 4.10 Tailoring Suggestions

### Source

Tailoring Agent

### Content

For each suggestion:

```text
Original text
Suggested rewrite
Supporting evidence
Claim type
Fidelity risk
```

---

## 4.11 Fidelity Notes

### Source

Fidelity Reviewer

### Content

* unsupported claims
* fabricated metric risks
* inflated scope flags
* required corrections

---

### Critical Rule

```text
Unsafe tailoring must be visible
```

---

## 4.12 Recommended Next Actions

### Source

Career Advisor

### Content

* apply now
* revise resume first
* gain experience first
* prepare for interviews
* defer job

---

## 5. Report Data Mapping

| Section            | Source            |
| ------------------ | ----------------- |
| Executive Summary  | Scoring Agent     |
| Score Breakdown    | Scoring Agent     |
| Strengths          | Scoring + Critic  |
| Resume Gaps        | Resume Critic     |
| Career Gaps        | Critic + Advisor  |
| Research Context   | Research Agent    |
| Reflection Summary | Review Auditor    |
| Career Strategy    | Career Advisor    |
| Interview Prep     | Interview Coach   |
| Tailoring          | Tailoring Agent   |
| Fidelity Notes     | Fidelity Reviewer |
| Next Actions       | Career Advisor    |

---

## 6. Output Formats

Reports should support:

```text
Markdown (primary)
DOCX (export)
PDF (export)
```

---

### Markdown

* primary internal format
* easy to render in UI
* easy to convert

---

### DOCX

* user-facing deliverable
* formatted for sharing

---

### PDF

* final polished output

---

## 7. Report Generation Flow

```text
Workflow completes
        ↓
Aggregate outputs from state
        ↓
Map outputs to report structure
        ↓
Validate content
        ↓
Generate Markdown
        ↓
Optional: convert to DOCX/PDF
        ↓
Persist report
        ↓
Return to UI
```

---

## 8. Report Storage

### Table

```text
reports
```

### Fields

```text
report_json
report_markdown
report_file_path
```

---

## 9. Content Validation

Before generating report:

* ensure required sections exist
* ensure no unsupported claims
* ensure schema compliance
* ensure no raw hidden reasoning
* ensure no PII leakage

---

## 10. Formatting Rules

Reports must:

* use clear headings
* use bullet points
* avoid large paragraphs
* prioritize readability
* highlight key insights

---

### Example Style

```text
## Strengths
- Strong experience in cloud modernization
- Proven leadership of distributed teams
- Experience with enterprise-scale systems
```

---

## 11. UI Integration

The UI should:

* display report sections clearly
* allow section expansion/collapse
* allow export
* allow navigation between sections

---

## 12. Partial Reports

The system may show partial results:

* after scoring
* after deep review
* before tailoring

This improves responsiveness.

---

## 13. Versioning

Reports should be versioned.

Example:

```text
report_v1
report_v2 (after revision)
```

This allows:

* comparison
* audit trail
* user iteration

---

## 14. Observability Integration

Report generation should log:

```text
workflow_id
report_generated
sections_included
generation_time
errors
```

---

## 15. Anti-Patterns to Avoid

Avoid:

* dumping raw agent outputs into UI
* mixing resume gaps and career gaps
* hiding fidelity risks
* overly verbose reports
* generic advice without evidence
* inconsistent section ordering
* missing sections due to agent failure

---

## 16. Future Enhancements

* comparative reports (multiple jobs)
* trend analysis across runs
* resume evolution tracking
* personalized templates
* industry-specific formatting

---

## 17. Final Principle

The report is the system.

Everything else exists to produce it.

A good report should answer:

```text
Is this job right for me?
What am I missing?
What should I fix?
What should I do next?
```

If the report cannot answer these clearly, the system is incomplete.
