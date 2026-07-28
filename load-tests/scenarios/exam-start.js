// §6.2: "Es zamanli sinav: 1000 ogrenci (MVP hedef)" and
// "API p95 latency: < 800 ms (sinav baslat)".
//
// The worst case for the platform is not steady exam traffic — it is the
// stampede when a class is told "start now" and a thousand students hit
// start_placement within the same minute. That call is the expensive one:
// it runs the blueprint selection, queries the question pool and writes an
// attempt with all its child rows. This scenario reproduces that spike.

import { sleep } from 'k6';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { login, callLangApi, checkNotRateLimited } from '../lib/api.js';
import { BLUEPRINT, PEAK_VUS, THRESHOLD_EXAM_START_P95, requireFixture } from '../lib/config.js';

const examStartDuration = new Trend('exam_start_duration', true);

export const options = {
	scenarios: {
		exam_stampede: {
			executor: 'ramping-vus',
			startVUs: 0,
			stages: [
				{ duration: '1m', target: Math.round(PEAK_VUS * 0.25) }, // early arrivals
				{ duration: '1m', target: PEAK_VUS }, // "everyone start now"
				{ duration: '3m', target: PEAK_VUS }, // hold at peak
				{ duration: '1m', target: 0 },
			],
			gracefulRampDown: '30s',
		},
	},
	thresholds: {
		// The agreement's number, enforced as a pass/fail gate.
		exam_start_duration: [`p(95)<${THRESHOLD_EXAM_START_P95}`],
		http_req_failed: ['rate<0.01'],
		checks: ['rate>0.99'],
	},
};

export function setup() {
	requireFixture(BLUEPRINT, 'BLUEPRINT');
	return { blueprint: BLUEPRINT };
}

export default function (data) {
	login(__VU);

	const result = callLangApi('start_placement', { blueprint: data.blueprint }, 'start_placement');
	examStartDuration.add(result.response.timings.duration);
	checkNotRateLimited(result.response, 'start_placement');

	check(result, {
		'placement started': (r) => r.ok,
		'attempt has questions': (r) => r.data && Array.isArray(r.data.questions) && r.data.questions.length > 0,
	});

	// Students do not immediately start another exam; pace the VU so the
	// scenario measures the start spike rather than a tight retry loop.
	sleep(5);
}
