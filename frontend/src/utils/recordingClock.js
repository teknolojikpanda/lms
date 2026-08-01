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
 * Deliberately *not* capped at the limit. Clamping looked tidy and was
 * the more dangerous of the two options: when a throttled callback lets
 * a recording overrun, MediaRecorder has genuinely captured the longer
 * audio, and reporting the limit exactly would hand the server a
 * duration its own check would accept — a 240-second blob declared as
 * 120 seconds, admitted and charged to the daily quota as if it were
 * within bounds. The overrun has to reach the server as what it is, so
 * the check that exists to refuse it can.
 */
export function recordedSeconds(startedAt, now) {
	if (!startedAt) return 0
	const elapsed = Math.floor((now - startedAt) / 1000)
	// A clock adjustment mid-recording must not read as negative.
	return Math.max(elapsed, 0)
}

/** Whether the §8.13 hard stop has been reached. */
export function isOverRecordingLimit(startedAt, now, maxDuration) {
	if (!startedAt || !maxDuration) return false
	return Math.floor((now - startedAt) / 1000) >= maxDuration
}
