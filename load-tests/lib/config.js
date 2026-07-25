// Shared configuration for the k6 load suite.
//
// Everything is env-driven so the same scripts run against dev, staging and
// a pre-prod rehearsal without edits. Never point these at production with
// write scenarios enabled — they create real attempts and submissions.

export const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
export const VOD_BASE_URL = (__ENV.VOD_BASE_URL || BASE_URL).replace(/\/$/, '');

// The seed script (see load-tests/seed/) creates users named
// <prefix><n>@<domain> sharing one password. One user per VU keeps the test
// realistic AND avoids tripping the per-user rate limits.
export const USER_PREFIX = __ENV.USER_PREFIX || 'loadtest';
export const USER_DOMAIN = __ENV.USER_DOMAIN || 'loadtest.invalid';
export const USER_PASSWORD = __ENV.USER_PASSWORD || 'loadtest-password';
export const USER_COUNT = parseInt(__ENV.USER_COUNT || '1000', 10);

// Fixtures created by the seed script.
export const BLUEPRINT = __ENV.BLUEPRINT || '';
export const SPEAKING_PROMPT = __ENV.SPEAKING_PROMPT || '';
export const VOD_PLAYLIST_PATH = __ENV.VOD_PLAYLIST_PATH || '';

// Peak concurrency. §6.2 targets 1000 concurrent students taking an exam.
export const PEAK_VUS = parseInt(__ENV.PEAK_VUS || '1000', 10);

// §6.2 latency targets, in milliseconds.
export const THRESHOLD_CRUD_P95 = parseInt(__ENV.THRESHOLD_CRUD_P95 || '300', 10);
export const THRESHOLD_EXAM_START_P95 = parseInt(__ENV.THRESHOLD_EXAM_START_P95 || '800', 10);

export const API = `${BASE_URL}/api/method`;
export const LANG_API = `${API}/lms.lms.language_platform.api`;

/**
 * Deterministic user for the current VU, so a VU always drives the same
 * account across iterations (its placement attempt is stateful).
 */
export function userForVU(vu) {
	const index = ((vu - 1) % USER_COUNT) + 1;
	return {
		username: `${USER_PREFIX}${index}@${USER_DOMAIN}`,
		password: USER_PASSWORD,
	};
}

export function requireFixture(value, name) {
	if (!value) {
		throw new Error(
			`Missing ${name}. Run the seed script and export it, e.g. ${name}=... k6 run <script>`
		);
	}
	return value;
}
