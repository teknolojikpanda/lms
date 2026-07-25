// §6.2: "Video trafikte CDN hit ratio hedefi: > %85 (static/VOD)".
//
// Cache hit ratio is a cost control as much as a performance one: every
// miss is an origin fetch billed twice (S3 request + CloudFront transfer),
// and Ek-6.2 names cache hit ratio as a primary cost driver. This scenario
// replays a realistic HLS access pattern — one playlist read followed by
// sequential segment fetches — and reports the hit ratio from CloudFront's
// own `x-cache` response header.
//
// VOD paths are protected by signed cookies (§8.12), which this script does
// not mint. Supply them out of band:
//
//   VOD_COOKIE="CloudFront-Policy=...; CloudFront-Signature=...; CloudFront-Key-Pair-Id=..."
//
// Without them CloudFront answers 403 and the run reports zero hits, which
// is a correct result for "unauthenticated users cannot reach segments"
// but tells you nothing about caching.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';
import { VOD_BASE_URL, VOD_PLAYLIST_PATH, requireFixture } from '../lib/config.js';

const cacheHitRate = new Rate('cdn_cache_hit_rate');

const SEGMENT_COUNT = parseInt(__ENV.SEGMENT_COUNT || '10', 10);
const VOD_COOKIE = __ENV.VOD_COOKIE || '';

export const options = {
	scenarios: {
		viewers: {
			executor: 'ramping-vus',
			startVUs: 0,
			stages: [
				{ duration: '1m', target: parseInt(__ENV.VIEWERS || '100', 10) },
				{ duration: '5m', target: parseInt(__ENV.VIEWERS || '100', 10) },
				{ duration: '1m', target: 0 },
			],
		},
	},
	thresholds: {
		cdn_cache_hit_rate: ['rate>0.85'],
		http_req_failed: ['rate<0.01'],
	},
};

function headers() {
	return VOD_COOKIE ? { Cookie: VOD_COOKIE } : {};
}

function recordCacheOutcome(response) {
	// CloudFront reports "Hit from cloudfront" / "Miss from cloudfront" /
	// "RefreshHit from cloudfront". Anything containing "Hit" served from
	// the edge counts.
	const xCache = response.headers['X-Cache'] || response.headers['x-cache'] || '';
	cacheHitRate.add(xCache.indexOf('Hit') !== -1);
}

export function setup() {
	requireFixture(VOD_PLAYLIST_PATH, 'VOD_PLAYLIST_PATH');
	if (!VOD_COOKIE) {
		console.warn(
			'VOD_COOKIE not set: signed-cookie protected paths will return 403. ' +
				'Cache ratio will be meaningless.'
		);
	}
	return { playlist: VOD_PLAYLIST_PATH };
}

export default function (data) {
	const playlistUrl = `${VOD_BASE_URL}${data.playlist}`;
	const playlist = http.get(playlistUrl, { headers: headers(), tags: { name: 'hls_playlist' } });
	recordCacheOutcome(playlist);
	check(playlist, { 'playlist fetched': (r) => r.status === 200 });

	if (playlist.status !== 200) {
		sleep(2);
		return;
	}

	// Pull the first N segment URIs out of the manifest and fetch them the
	// way a player would: sequentially, with roughly segment-duration gaps.
	const segments = playlist.body
		.split('\n')
		.filter((line) => line && line.indexOf('#') !== 0)
		.slice(0, SEGMENT_COUNT);

	const base = playlistUrl.substring(0, playlistUrl.lastIndexOf('/') + 1);
	for (const segment of segments) {
		const url = segment.indexOf('http') === 0 ? segment : `${base}${segment}`;
		const response = http.get(url, { headers: headers(), tags: { name: 'hls_segment' } });
		recordCacheOutcome(response);
		check(response, { 'segment fetched': (r) => r.status === 200 });
		sleep(2);
	}
}
