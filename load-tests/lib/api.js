// Frappe/LMS request helpers for the k6 suite.

import http from 'k6/http';
import { check, fail } from 'k6';
import { BASE_URL, LANG_API, userForVU } from './config.js';

/**
 * Log in as this VU's dedicated user.
 *
 * k6 keeps a cookie jar per VU, so the `sid` cookie Frappe sets here is
 * reused by every subsequent request from the same VU.
 */
export function login(vu) {
	const user = userForVU(vu);
	const response = http.post(
		`${BASE_URL}/api/method/login`,
		JSON.stringify({ usr: user.username, pwd: user.password }),
		{
			headers: { 'Content-Type': 'application/json' },
			tags: { name: 'login' },
		}
	);

	const ok = check(response, {
		'login succeeded': (r) => r.status === 200,
	});
	if (!ok) {
		fail(
			`login failed for ${user.username} (status ${response.status}). ` +
				'Has the seed script run against this site?'
		);
	}
	return user;
}

/**
 * Call a language-platform endpoint and unwrap the §7.3 envelope.
 *
 * Returns `{ response, data, ok }`. A transport-level failure and a
 * `{ok:false}` envelope are both reported as not-ok, because a 200 carrying
 * an error envelope would otherwise look like a success in the metrics.
 */
export function callLangApi(method, payload, tagName) {
	const response = http.post(`${LANG_API}.${method}`, JSON.stringify(payload || {}), {
		headers: { 'Content-Type': 'application/json' },
		tags: { name: tagName || method },
	});

	let envelope = null;
	try {
		envelope = response.json('message');
	} catch (e) {
		envelope = null;
	}

	return {
		response,
		data: envelope && envelope.ok ? envelope.data : null,
		ok: response.status === 200 && !!(envelope && envelope.ok),
		error: envelope && envelope.error ? envelope.error : null,
	};
}

/** Standard Frappe resource read, for the generic CRUD latency budget. */
export function getList(doctype, params, tagName) {
	const query = Object.assign({ doctype: doctype }, params || {});
	const search = Object.keys(query)
		.map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(query[k])}`)
		.join('&');

	return http.get(`${BASE_URL}/api/method/frappe.client.get_list?${search}`, {
		tags: { name: tagName || `get_list:${doctype}` },
	});
}

/**
 * Fail loudly on rate limiting rather than silently degrading.
 *
 * The platform rate-limits placement starts and speaking uploads per user
 * (§9.1). A 429 during a load test almost always means the test is reusing
 * one account instead of one per VU — which would make the whole run
 * meaningless, so it is worth its own signal.
 */
export function checkNotRateLimited(response, label) {
	return check(response, {
		[`${label}: not rate limited`]: (r) => r.status !== 429,
	});
}
