# Request technical support

[Milvus Doctor — Request Technical Support](https://zilliverse.feishu.cn/share/base/form/shrcnlGM0veXFaAKlBE0AaNsTBf)

The user completes the contact, company/organization and project-stage fields
according to the live form's required markers. An optional preferred-contact
field allows the user's chosen communication channel; do not restrict it to
email, phone or WeChat.
Question 5, **Paste your Milvus Doctor support summary**, receives the complete
reviewed English text. Do not duplicate the form's contact/company fields in it.

## When to offer the form

Keep the link available in reports and follow the Skill's **Finish a troubleshooting
response** rule: a lightweight, linked invitation belongs in the final chat answer,
not only in tool output or saved files. Generate its wording and link label in
the user's explicitly requested reply language, or otherwise the current
conversation language. Express the offer of optional community technical help
and assistance preparing a summary; do not reuse a fixed-language template.
Keep the form URL above unchanged. English source material and technical error
messages are not a request to switch the conversation language.

This optional invitation is distinct from preparing a full support request. It
must not imply an unresolved issue if the user reports recovery, demand contact
details, or generate a synthetic diagnosis solely to fill a summary. Do not
repeat an existing linked handoff or override the user's request to omit prompts.
Keep this invitation outside the standard copy-ready summary.

Proactively explain the full handoff when the user
asks for help, the selected evidence cannot establish a cause, the symptom remains
after user action, or expert judgment is appropriate. Explain what is known and
missing first. A warning, intentional unselected check, or unknown optional field
alone is not a reason to pressure a user to submit. Give all available self-service
advice regardless of contact or commercial interest. High-risk/data-integrity
evidence should use the team's approved private channel, not raw data in a form.

## Prepare useful facts, not a longer questionnaire

Use the report, its `evidence.json`, and facts already provided in the conversation.
For an authorized log export, its `report.json` works here unchanged. Use
`log-manifest.json` to locate relevant redacted excerpts and distinguish current
versus previous-container logs. The summary includes export-window/count facts,
not log bodies or an automatic archive attachment. Log-read permission, retention,
empty output and size/time gaps must remain visible; they are not healthy checks.
Get selected log/config/application excerpts through `read-evidence --file ...`
before bringing them into the AI; do not bypass redaction with cat/head/rg.
Use `show-evidence --report ...` for saved dimensions, state and resource counters.
Aliases are report-local, not stable identities across rechecks.

Before formatting, check at most the one or two missing facts that most affect
this handoff. Do not force the user to fill every field or rewrite the problem:

- **SDK/API:** the application's SDK/Python version, exact redacted error and
  minimal call sequence. Doctor's SDK version is not the application's version.
- **Startup/troubleshooting:** incident time and first error, selected config or
  stack excerpt, recent changes and whether a recheck followed an actual change.
- **Deployment review:** intended data/workload/resources and availability or
  recovery requirements, if the user wants production-readiness advice.
- **Performance:** the actual time window, workload and representative measurements.
  Doctor does not currently derive P99/QPS causes from one metrics snapshot.

Unknown information stays `Not provided`; missing information must not block a
user-requested or urgent handoff. The optional user-added note is not a substitute
for preserving facts the Agent already knows. Separate facts from hypotheses and
actual user operations from proposed next steps. Avoid turning an error-code label
into a confirmed cause or claiming that an unobserved finding proves recovery.
Keep Doctor checks out of `actions_already_performed`: that field is for the
user or platform owner only. In the reproduction/diagnostic sequence, state who
performed each step; a Doctor read-only check is not a user repair or workload replay.

## Generate the copy-ready summary

`diagnose --output-dir ...` saves `report.json`, `report.md`, `evidence.json`,
`support-summary.md` and `support-request.md`. The initial summary may lack context.
The Agent can write a small local context JSON from already-established facts,
then regenerate a complete reviewed summary **without reconnecting**:

Keep both case reports and context outside the Skill installation. The paths
below are relative to the user's project; invoke the installed entrypoint by
absolute path, or resolve all three paths before switching command directories.

```bash
python3 scripts/doctor.py support-summary --report ./case/report.json \
  --context ./support-context.json --output ./case/support-summary-reviewed.md
```

The localized invitation is separate from this standard English submission text.
When preparing context, render the user's narrative in English without changing
its facts or uncertainty; keep quoted error messages, code, identifiers and
version strings unchanged. The script formats and redacts text, not translates
it. If useful for review, explain the summary in the user's language outside the
copy-ready block; do not replace or translate the generated submission template.

Prefer the Skill's `.venv/bin/python` if present. `--output` is optional, creates
a 0600 file, and refuses to overwrite existing files; stdout always contains the
same summary. Use a new filename for another revision. Do not hand-edit the
generated template, falsify report fields, or assume a file named snapshot.json
exists. `evidence.json` is not a replay snapshot.

All context fields below are optional strings, at most 2000 characters per field
and 16 KiB total JSON. Omit missing fields; do not guess values to pass validation.
This is an illustrative example, not default customer data:

```json
{
  "problem_type": "sdk",
  "app_sdk_name": "pymilvus",
  "app_sdk_version": "2.6.17",
  "deployment_method": "docker",
  "deployment_topology": "standalone",
  "incident_time": "12:35 UTC, user-reported",
  "symptom_and_impact": "The client raised a JSON serialization error after insert returned.",
  "expected_behavior": "The application should print the returned receipt without retrying the write.",
  "reproduction_steps": "The user ran an isolated insert and then serialized the returned primary keys.",
  "confirmed_facts": "The selected traceback places the exception after the insert call returned.",
  "hypotheses": "Independent persistence and entity visibility have not been verified.",
  "key_evidence": "TypeError: Object of type RepeatedScalarContainer is not JSON serializable",
  "actions_already_performed": "The user fixed local result conversion and tested serialization only; no insert retry.",
  "recheck_status": "after_user_change",
  "remaining_questions": "What is the safe verification and retry procedure?",
  "assistance_requested": "Please review SDK receipt handling and the evidence needed before any retry."
}
```

Supported keys:

- Core fields: `symptom_and_impact`, `actions_already_performed`,
  `assistance_requested`.
- Reviewed context: `incident_time`, `expected_behavior`, `reproduction_steps`,
  `confirmed_facts`, `hypotheses`, `key_evidence`, `remaining_questions`,
  `additional_information`.
- User-confirmed metadata: `milvus_version`, `app_sdk_version`,
  `app_python_version` accept two/three-part numeric versions (for example
  `3.11`, `2.6.17`, `2.6.0rc1`) or `unknown`. Preserve what the user knows;
  never invent a missing patch version. Partial versions remain user-reported.
  `deployment_method`: native/docker/compose/kubernetes/helm/operator/unknown;
  `deployment_topology`: standalone/cluster/unknown.
  `app_sdk_name`: pymilvus/PyMilvus, Java SDK, Node.js SDK, Go SDK, C# SDK,
  REST API, other, unknown.
- `problem_type`: deployment/sdk/troubleshooting/performance/unknown.
- `recheck_status`: not_performed/evidence_expanded/after_user_change. Expanding
  evidence without making a change is not a post-repair verification.

`not_performed` means the user explicitly confirmed no recheck. Do not derive
it from "no repairs", a missing previous report, or this being Doctor's first
check. Omit unknown recheck status so the generated summary says `Not provided`.

Observed metadata and user-confirmed metadata are labelled separately. Conflicts
are displayed for verification rather than silently overwritten. Endpoint-only
and legacy unverified native labels do not establish the actual installation.

## What the engineer receives

The stable English fields include environment/provenance, incident and collection
time, symptom and expected behavior, reproduction steps, confirmed facts versus
hypotheses, rule IDs with readable meanings, safe technical facts, reviewed key
evidence, source coverage, actual actions, recheck type/results, remaining questions
and requested help. The output does not automatically include raw logs, full
configuration, business data or identifiers. Structured evidence uses aliases and
typed metadata. Only deliberately selected, reviewed text goes in `key_evidence`.

Review context text for internal paths/resource names, contact details and sensitive
business values before formatting. Generic credential redaction, including multiline
handling, is not complete anonymization. Preserve a few relevant error lines and
timestamps when safe; request fuller materials later through an approved channel.

## User submission and follow-up

Present the generated summary as **one copyable block**, with the form link
outside it. The user reviews it, fills in the form, pastes the complete summary
into question 5, and manually chooses **Submit Support Request**.

For a shorter version, condense the context values and regenerate the same
standard template. A short prose explanation is not a substitute for the form
summary: do not rename/remove its fixed fields, version or submission marker, and
do not paraphrase the generated text in the copyable block. If stdout was omitted
or truncated by the host, retrieve the full generated file before copying it.

Invite the user to append any important context not already covered under
**Additional information (optional)**, such as impact, urgency or recent changes.
Keep the original summary format, and remind them to exclude credentials and
sensitive business data.

`additional_information` is also supported in context when the user already supplied
such a note. Do not invent one or require it. Contact information belongs in the
form's own fields. Technical support does not imply marketing or Cloud consent.

Files, links and prepared summaries are **NOT SUBMITTED**. Doctor does not open,
fill or submit the form automatically, send email, read responses or create
tickets. Do not promise team notification, ticket numbers, successful submission
or response deadlines without confirmation.
