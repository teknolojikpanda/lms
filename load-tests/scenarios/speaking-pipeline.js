// §6.2: "Speaking pipeline: 2-5 dk icinde skor (batch) hedefi".
//
// Measures wall-clock time from submission to a Ready score — the number a
// student actually experiences — by polling get_speaking_result. What this
// really exercises is queue depth: the pipeline is asynchronous, so the
// failure mode under load is not a slow request but a backlog where the
// last student in the batch waits far longer than the first.
//
// COST WARNING: with the AWS provider every iteration bills Transcribe
// minutes and Bedrock tokens. Run it against a site configured with the
// Mock provider unless you are deliberately measuring real end-to-end
// latency, and set SUBMISSIONS low when you are.

import { check, sleep, fail } from 'k6';
import { Trend, Rate } from 'k6/metrics';
import { login, callLangApi, checkNotRateLimited } from '../lib/api.js';
import { SPEAKING_PROMPT, requireFixture } from '../lib/config.js';

const timeToScore = new Trend('speaking_time_to_score', true);
const pipelineSuccess = new Rate('speaking_pipeline_success');

const AUDIO_FILE = __ENV.AUDIO_FILE || '';
const DURATION_SECONDS = parseInt(__ENV.AUDIO_DURATION || '60', 10);
const POLL_INTERVAL = parseInt(__ENV.POLL_INTERVAL || '5', 10);
const POLL_TIMEOUT = parseInt(__ENV.POLL_TIMEOUT || '600', 10); // 10 min ceiling

export const options = {
	scenarios: {
		speaking_batch: {
			executor: 'per-vu-iterations',
			vus: parseInt(__ENV.SPEAKERS || '50', 10),
			iterations: parseInt(__ENV.SUBMISSIONS || '2', 10),
			maxDuration: '30m',
		},
	},
	thresholds: {
		// The agreement's upper bound, in milliseconds.
		speaking_time_to_score: ['p(95)<300000'],
		speaking_pipeline_success: ['rate>0.95'],
	},
};

export function setup() {
	requireFixture(SPEAKING_PROMPT, 'SPEAKING_PROMPT');
	requireFixture(AUDIO_FILE, 'AUDIO_FILE');
	return { prompt: SPEAKING_PROMPT, audioFile: AUDIO_FILE };
}

export default function (data) {
	login(__VU);

	const submitted = callLangApi(
		'create_speaking_submission',
		{
			prompt: data.prompt,
			audio_file: data.audioFile,
			duration_seconds: DURATION_SECONDS,
		},
		'create_speaking_submission'
	);
	checkNotRateLimited(submitted.response, 'create_speaking_submission');

	if (!submitted.ok || !submitted.data) {
		// A rejected submission is usually the daily minute quota (§8.13)
		// rather than a fault — surface it instead of counting it as a
		// pipeline failure.
		console.warn(
			`submission rejected: ${submitted.error ? submitted.error.message : 'unknown reason'}`
		);
		pipelineSuccess.add(false);
		return;
	}

	const submission = submitted.data.name;
	const start = Date.now();
	let status = submitted.data.status;

	while (Date.now() - start < POLL_TIMEOUT * 1000) {
		sleep(POLL_INTERVAL);

		const polled = callLangApi(
			'get_speaking_result',
			{ submission: submission },
			'get_speaking_result'
		);
		if (!polled.ok || !polled.data) continue;

		status = polled.data.status;
		if (status === 'Ready' || status === 'Failed') break;
	}

	const elapsed = Date.now() - start;
	const ready = status === 'Ready';

	if (ready) {
		// Only successful runs belong in the latency distribution; a failure
		// that gives up quickly would otherwise look like excellent latency.
		timeToScore.add(elapsed);
	}
	pipelineSuccess.add(ready);

	check(
		{ status: status },
		{
			'submission reached Ready': (s) => s.status === 'Ready',
		}
	);
}
