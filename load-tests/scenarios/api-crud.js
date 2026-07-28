// §6.2: "API p95 latency: < 300 ms (CRUD)".
//
// The read paths a student hits constantly — course list, dashboard data,
// lesson overlays — under moderate concurrency. This is the baseline that
// should stay green even while the exam scenarios are hammering writes;
// running both together is how you find lock contention.

import { sleep, check } from 'k6';
import { getList, login } from '../lib/api.js';
import { THRESHOLD_CRUD_P95 } from '../lib/config.js';

export const options = {
	scenarios: {
		steady_reads: {
			executor: 'constant-arrival-rate',
			// Arrival-rate (not VUs) so throughput stays fixed even if the
			// server slows down — otherwise a degrading server quietly
			// reduces its own load and the test flatters it.
			rate: parseInt(__ENV.CRUD_RPS || '50', 10),
			timeUnit: '1s',
			duration: __ENV.DURATION || '5m',
			preAllocatedVUs: 50,
			maxVUs: 300,
		},
	},
	thresholds: {
		'http_req_duration{name:get_list:LMS Course}': [`p(95)<${THRESHOLD_CRUD_P95}`],
		'http_req_duration{name:get_list:LMS Enrollment}': [`p(95)<${THRESHOLD_CRUD_P95}`],
		http_req_failed: ['rate<0.01'],
		checks: ['rate>0.99'],
	},
};

export default function () {
	login(__VU);

	const courses = getList(
		'LMS Course',
		{ fields: JSON.stringify(['name', 'title', 'published']), limit_page_length: 20 },
		'get_list:LMS Course'
	);
	check(courses, { 'course list ok': (r) => r.status === 200 });

	const enrollments = getList(
		'LMS Enrollment',
		{ fields: JSON.stringify(['name', 'course', 'progress']), limit_page_length: 20 },
		'get_list:LMS Enrollment'
	);
	check(enrollments, { 'enrollment list ok': (r) => r.status === 200 });

	sleep(1);
}
