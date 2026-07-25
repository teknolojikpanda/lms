// §6.6: "Sinav baslatma ve sinav cozum ekranlari icin yuk testi."
//
// Where exam-start measures the spike, this measures the sustained load of
// a thousand students working through questions: every answer autosaves,
// and the run ends with a submit that grades the whole attempt server-side.
//
// Autosave is the highest-volume write in the product, so its latency
// budget is the generic CRUD one (§6.2: p95 < 300 ms).

import { sleep, check, group } from 'k6';
import { Trend } from 'k6/metrics';
import { login, callLangApi, checkNotRateLimited } from '../lib/api.js';
import {
	BLUEPRINT,
	PEAK_VUS,
	THRESHOLD_CRUD_P95,
	THRESHOLD_EXAM_START_P95,
	requireFixture,
} from '../lib/config.js';

const autosaveDuration = new Trend('autosave_duration', true);
const submitDuration = new Trend('exam_submit_duration', true);

export const options = {
	scenarios: {
		exam_session: {
			executor: 'ramping-vus',
			startVUs: 0,
			stages: [
				{ duration: '2m', target: PEAK_VUS },
				{ duration: '5m', target: PEAK_VUS },
				{ duration: '1m', target: 0 },
			],
			gracefulRampDown: '1m',
		},
	},
	thresholds: {
		autosave_duration: [`p(95)<${THRESHOLD_CRUD_P95}`],
		// Submit grades every answer in one request, so it gets the exam-start
		// budget rather than the CRUD one.
		exam_submit_duration: [`p(95)<${THRESHOLD_EXAM_START_P95}`],
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

	let attempt = null;
	let questions = [];

	group('start', function () {
		const result = callLangApi('start_placement', { blueprint: data.blueprint }, 'start_placement');
		checkNotRateLimited(result.response, 'start_placement');
		if (!result.ok || !result.data) {
			// Attempts are capped per blueprint (max_attempts); once a VU's
			// user is exhausted there is nothing more to measure for it.
			return;
		}
		attempt = result.data.name;
		questions = result.data.questions || [];
	});

	if (!attempt) {
		sleep(5);
		return;
	}

	group('answer', function () {
		for (const question of questions) {
			const answer =
				question.type === 'Choices' && question.options && question.options.length
					? JSON.stringify([question.options[0]])
					: 'load test answer';

			const result = callLangApi(
				'save_placement_answer',
				{ attempt: attempt, question: question.name, answer: answer },
				'save_placement_answer'
			);
			autosaveDuration.add(result.response.timings.duration);
			checkNotRateLimited(result.response, 'save_placement_answer');
			check(result, { 'answer autosaved': (r) => r.ok });

			// Think time: a student reads and answers, they do not hammer the
			// endpoint. Without this the test measures a synthetic burst that
			// no real cohort produces.
			sleep(Math.random() * 3 + 2);
		}
	});

	group('submit', function () {
		const result = callLangApi('submit_placement', { attempt: attempt }, 'submit_placement');
		submitDuration.add(result.response.timings.duration);
		check(result, {
			'exam submitted': (r) => r.ok,
			'result has level': (r) => r.data && !!r.data.effective_level,
		});
	});

	sleep(5);
}
