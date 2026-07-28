# Watermarking (§1.1 copying deterrence, §4.4.3 Premium)

Two different mechanisms wear the name "watermark", and the agreement
treats them differently. Conflating them oversells what is shipped.

| | Session watermark | Forensic watermark |
|---|---|---|
| Agreement | §1.1 "kopyalama caydiricilik" — **in scope** | §4.4.3 "Premium: forensic watermarking (opsiyonel)" |
| What it is | per-viewer code drawn **over** the player | imperceptible signal embedded **in** the video essence |
| Survives re-encode | no | yes |
| Survives crop | mark moves, so cropping it also crops the picture | yes |
| Survives camcording | partially (legible if the screen is in frame) | yes |
| Needs a vendor | no | **yes** — licence and detector |
| Status | **shipped, off by default** | plumbing built, behind procurement |

## Session watermark (shipped)

Turn it on in **LMS Language Settings → Session Watermark**. The player
then draws a code like `K7M2-QRPW` over the video, and
`LMS Watermark Session` records which viewer it was issued to.

That registry is the entire point. Without it the overlay is decoration;
with it, a recording that surfaces in a group chat can be traced back to
the account that made it.

### Why a code and not the student's email

Burning identity onto the screen is the obvious design and the wrong one:

- **It exposes PII to bystanders.** A projected lesson or a shared screen
  puts the address in front of the whole class. §6.4 data minimisation
  exists to prevent exactly that kind of incidental disclosure.
- **It is easy to forge or blur convincingly.** A leaker who blurs an
  email leaves something that still looks like a lesson. A missing or
  mangled opaque code is conspicuous.
- **It buys nothing.** Tracing goes through the registry either way.

So the overlay is opaque, and resolving it is a deliberate, logged act
(see below).

### Why the mark moves

A watermark fixed in one corner is removed by cropping that corner. The
position schedule keeps the mark inside the middle band of the frame, so
a crop that hides it also crops away content. The schedule is derived
from the code itself, so the client needs no round-trip and the same
session reproduces the same path — useful when checking a leaked
recording against what the player would have drawn.

### Tracing a leak

Moderator-only:

```
POST /api/method/lms.lms.language_platform.api.trace_watermark
{ "code": "K7M2-QRPW" }
```

Codes are transcribed by humans off a recording, so hyphens, spacing and
case are all tolerated — but anything still invalid is **rejected rather
than guessed at**. Half-matching a code to a student is worse than
returning nothing.

The lookup records a comment against the identified user and a security
log line. Turning an anonymous mark into a person is a privacy-relevant
act, so who asked and when is part of the record.

Sessions store IP and user agent to corroborate a trace, which makes them
personal data — they are purged on their own retention window (default
180 days), separate from exam records because the purpose differs:
tracing a leak is useful for months, not years.

### Limits — state these to institutions

Session watermarking deters the **casual** leak: a student recording
their screen and sharing the file. It does not survive a determined
adversary who re-encodes, crops aggressively and accepts quality loss,
and it is absent from any copy obtained by other means. Sold as more than
deterrence, it will disappoint.

## Forensic watermark (Premium, needs procurement)

MediaConvert implements forensic marking through **Nagra NexGuard File
Marker**, which requires a NexGuard licence. As with DRM, §1.2 places
that purchase outside the delivery scope.

Built: the IAM permissions a NexGuard-enabled MediaConvert job needs, and
the job-settings fragment the worker merges per asset
(`modules/media/watermark.tf`, `forensic_watermark_enabled`).

Not built, because it cannot be:

- **The NexGuard licence** and partner id.
- **The detector.** Extracting an identifier from a leaked file is a
  vendor service; there is no self-hosted equivalent.
- **A/B variant delivery**, if session-level (rather than asset-level)
  forensic marking is required. That needs two encoded variants per
  video plus CDN-level per-session segment selection — roughly doubling
  storage and adding an origin component. Ek-6 does not model it.

### If you procure it

1. Obtain the NexGuard licence, partner id and strength preset.
2. `terraform apply -var forensic_watermark_enabled=true -var nexguard_partner_id=...`
3. Extend the transcode worker to merge `nexguard_job_settings` into
   MediaConvert jobs, substituting the per-asset payload.
4. **Re-transcode the back catalogue.** Existing outputs carry no mark;
   forensic marking is applied at encode time and cannot be added later.
5. Agree the detection workflow with the vendor before you need it. The
   moment you need a detector is the worst moment to discover the
   contract does not include one.

## Recommendation

For a language school platform, the session watermark plus the MVP access
controls (signed cookies, private buckets, no public URLs) covers the
realistic threat: students sharing lesson recordings with each other.
Forensic watermarking targets organised redistribution, which is a
different threat model and carries vendor cost, storage cost and a
re-transcode. Treat it as a per-tenant commercial decision, the same as
DRM.
