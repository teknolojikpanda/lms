/**
 * How long a recording has been running.
 *
 * Elapsed wall time, never a count of timer callbacks. Browsers throttle
 * `setInterval` in a backgrounded tab or on a locked phone screen — to as
 * little as once a minute — while `MediaRecorder` keeps going. Counting
 * callbacks therefore let a recording run far past its limit, and sent
 * the same undercount to the server, which trusts it for the duration
 * check and the daily quota (§8.13).
 *
 * Capped at the limit so the number shown and the number submitted can
 * never claim more than the maximum allowed.
 */
export function recordedSeconds(startedAt, now, maxDuration) {
	if (!startedAt) return 0
	const elapsed = Math.floor((now - startedAt) / 1000)
	// A clock adjustment mid-recording must not read as negative.
	const bounded = Math.max(elapsed, 0)
	return maxDuration ? Math.min(bounded, maxDuration) : bounded
}

/** Whether the §8.13 hard stop has been reached. */
export function isOverRecordingLimit(startedAt, now, maxDuration) {
	if (!startedAt || !maxDuration) return false
	return Math.floor((now - startedAt) / 1000) >= maxDuration
}
