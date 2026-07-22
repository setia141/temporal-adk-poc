# Sample attachments

Manual test fixtures for the intake attachment feature (`IntakeForm.attachment_ref`).
Point `starter.py`'s "Supporting attachment path" prompt, or `app.py`'s file
uploader, at one of these to exercise each parsing path in
`agents/intake/attachment.py`:

| File | Exercises |
|---|---|
| `sample_architecture_notes.pdf` | `pypdf` text extraction |
| `sample_diagram.png` | image path — passed to the model as a real vision part, not extracted text |
| `sample_notes.md` | plain-text/markdown decode path |

None of these contain real or sensitive data — content is invented for
testing only.
