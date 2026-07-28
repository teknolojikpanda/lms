# Load and performance tests

k6 suite covering the mandatory load testing of Technical Agreement v1.3
§6.6 ("Sinav baslatma ve sinav cozum ekranlari icin yuk testi; video player
icin stabilite testleri"), with thresholds taken straight from §6.2.

Every threshold below is enforced as a **pass/fail gate** — k6 exits
non-zero when one is breached, so these can gate a release.

| Scenario | Agreement target | Threshold |
|---|---|---|
| `exam-start.js` | §6.2 exam start p95 < 800 ms, 1000 concurrent students | `exam_start_duration: p(95)<800` |
| `exam-taking.js` | §6.2 CRUD p95 < 300 ms (autosave), exam submit < 800 ms | `autosave_duration: p(95)<300` |
| `api-crud.js` | §6.2 API p95 < 300 ms | `http_req_duration: p(95)<300` |
| `vod-cache.js` | §6.2 CDN hit ratio > 85% | `cdn_cache_hit_rate: rate>0.85` |
| `speaking-pipeline.js` | §6.2 score within 2–5 min | `speaking_time_to_score: p(95)<300000` |

## Before you run anything

**Never run the write scenarios against production.** They create real
placement attempts, speaking submissions and (via the seed script) a
thousand accounts sharing one published password. The seed script refuses
to run on sites whose name contains `prod`/`live`, but that is a guard,
not a guarantee — check `--site` yourself.

### 1. Seed fixtures

```bash
bench --site staging.dilplatformu.example.com console
```

```python
exec(open('load-tests/seed/seed_load_test_data.py').read())
seed(users=1000)
```

It prints the environment variables the k6 scripts need. `cleanup()`
removes everything it created.

### 2. Export the fixture ids

```bash
export BASE_URL=https://staging.dilplatformu.example.com
export BLUEPRINT=PLB-00001
export SPEAKING_PROMPT=SPK-00001
export AUDIO_FILE=/private/files/loadtest-audio.webm
export USER_COUNT=1000
```

## Running

k6 is a single binary; via Docker (no install needed):

```bash
docker run --rm -i -v "${PWD}/load-tests:/tests" -e BASE_URL -e BLUEPRINT -e USER_COUNT grafana/k6 run /tests/scenarios/exam-start.js
```

Natively:

```bash
k6 run load-tests/scenarios/exam-start.js
```

Useful overrides: `PEAK_VUS` (default 1000), `CRUD_RPS`, `VIEWERS`,
`SPEAKERS`, `SUBMISSIONS`, `DURATION`.

Start well below the target and climb — `PEAK_VUS=50` first to confirm the
plumbing works, then 250, then 1000. A cold ECS service with one task will
fail at 1000 no matter how good the code is, so let autoscaling settle
before you record a number as the official result.

## Reading the results

- **`checks` rate** — correctness under load. A green latency threshold
  with failing checks means the server was fast at returning errors.
- **`http_req_failed`** — transport failures. Watch for these appearing
  only at peak: that is usually connection-pool or DB-connection
  exhaustion (§8.10 mentions RDS Proxy for exactly this).
- **`exam_start_duration` vs `http_req_duration`** — the custom trends
  isolate the endpoint under test from login and polling noise. Judge the
  agreement targets on the custom trends.

### The rate-limit trap

The platform rate-limits per user (§9.1): placement start 30/h, speaking
upload 60/h. **Running a load test from a single account will trip these
and produce a wall of 429s** that looks like a server failure but is the
protection working correctly.

That is why every script picks a distinct seeded user per VU, and why
`checkNotRateLimited` fails the run loudly instead of letting 429s blend
into the error rate. If you see it fire, check `USER_COUNT` matches the
number of users actually seeded — VUs wrap around the pool, so
`PEAK_VUS=1000` with `USER_COUNT=100` puts ten VUs on each account.

### Speaking pipeline costs money

With the AWS provider each iteration bills Amazon Transcribe minutes and
Bedrock tokens. Run it against a site set to the **Mock** provider (LMS
Language Settings → Speaking Provider) unless you are deliberately
measuring real end-to-end latency — and then keep `SUBMISSIONS` small.
The seeded audio fixture is a placeholder that only the Mock provider
tolerates; real Transcribe needs real audio in S3.

### VOD signed cookies

`/vod/*` requires CloudFront signed cookies (§8.12). The script does not
mint them; pass them in:

```bash
export VOD_COOKIE="CloudFront-Policy=...; CloudFront-Signature=...; CloudFront-Key-Pair-Id=..."
export VOD_PLAYLIST_PATH=/vod/global/<courseId>/<videoId>/index.m3u8
```

Without them CloudFront returns 403 — a correct result for the access
control test, but it tells you nothing about cache behaviour.

Cache ratio is also a **cost** metric, not just a latency one: Ek-6.2
lists cache hit ratio among the primary cost drivers, because every miss
is billed as both an origin request and origin transfer. A ratio below
85% on a warm cache usually means the cache key includes something it
should not (a varying query string or forwarded header).

## Player stability soak (§6.6)

k6 measures the delivery path; player stability needs a real browser, so
that half of §6.6 lives in Cypress:

```bash
yarn cypress run --browser chrome --spec cypress/e2e/video_player_soak.cy.js
```

**Chrome, not Electron.** The spec reads `performance.memory` and forces
a collection through `--expose-gc`; both flags are added for Chromium in
`cypress.config.js`, and the spec fails fast with a clear message if it
finds itself somewhere without them.

It seeds its own course/chapter/lesson through the API and drives
`timeupdate` directly rather than waiting for real playback — one minute
of wall clock reproduces the tick volume of a long viewing session, which
is what actually stresses the overlay handler hooked into that event.
Between cycles it mixes in seeks, replays and speed changes, then samples
the heap.

| Env | Default | Purpose |
|---|---|---|
| `SOAK_CYCLES` | 20 | playback cycles (CI short run; use 200+ nightly) |
| `SOAK_TICKS_PER_CYCLE` | 240 | timeupdate events per cycle (240 = 1 min at 4 Hz) |
| `SOAK_HEAP_GROWTH_MB` | 25 | heap growth allowed before failing |
| `SOAK_VIDEO_URL` | `/files/sample-lesson.mp4` | fixture video the lesson points at |

```bash
yarn cypress run --browser chrome \
  --spec cypress/e2e/video_player_soak.cy.js \
  --env SOAK_CYCLES=200,SOAK_VIDEO_URL=/files/my-sample.mp4
```

**You must supply a video.** The fixture lesson points at
`SOAK_VIDEO_URL`; if that file does not exist on the site the player has
nothing to load and the spec fails on `readyState`. Any small MP4 already
uploaded to the site works.

Reading the result: the spec asserts both **absolute** growth (under the
MB limit) and that growth is **decelerating**. The second matters more —
caches filling and settling produce growth that plateaus, whereas a leaked
listener or timer produces growth that never does, which is why the two
halves of the run are compared rather than just the endpoints.

### What this does not cover

Driving `timeupdate` synthetically stresses the player's own logic
faithfully, but it bypasses the decoder, the network stack and ABR
quality switching. A genuine multi-hour real-time soak against a live HLS
stream is still worth running before a large rollout — this catches the
leak class, not codec or bandwidth-adaptation problems.

## Also not covered here

- **Exam integrity under concurrency** (deterministic selection, no
  duplicates) is covered by unit tests in `lms/tests/language_platform/`,
  not here.
