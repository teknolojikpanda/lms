import { describe, expect, it } from 'vitest'
import { recordedSeconds, isOverRecordingLimit } from '@/utils/recordingClock'

const START = 1_000_000

describe('recordedSeconds', () => {
	it('measures elapsed time', () => {
		expect(recordedSeconds(START, START + 30_000)).toBe(30)
	})

	it('is unaffected by how often the timer got to run', () => {
		// The defect: the counter was incremented once per setInterval
		// callback. A backgrounded tab or a locked phone screen throttles
		// setInterval to as little as once a minute while MediaRecorder
		// keeps recording, so four minutes of audio read as a handful of
		// seconds — and that undercount was what the server received for
		// its duration check and daily quota.
		expect(recordedSeconds(START, START + 240_000)).toBe(240)
	})

	it('reports an overrun truthfully rather than clamping it', () => {
		// Clamping looked tidy and was the more dangerous option. When a
		// throttled callback lets a recording overrun, MediaRecorder has
		// really captured the longer audio; declaring exactly the limit
		// would hand the server a duration its own check accepts, so a
		// 240-second blob would be admitted as 120 and charged to the
		// daily quota as if it were within bounds.
		expect(recordedSeconds(START, START + 240_000)).toBe(240)
	})

	it('reports nothing before a recording starts', () => {
		expect(recordedSeconds(null, START + 5_000)).toBe(0)
	})

	it('does not go negative if the clock steps backwards', () => {
		expect(recordedSeconds(START, START - 5_000)).toBe(0)
	})

	it('takes no limit at all — the limit belongs to the stop, not the clock', () => {
		expect(recordedSeconds(START, START + 300_000)).toBe(300)
	})
})

describe('isOverRecordingLimit', () => {
	it('is false before the limit', () => {
		expect(isOverRecordingLimit(START, START + 119_000, 120)).toBe(false)
	})

	it('is true at the limit (§8.13 hard stop)', () => {
		expect(isOverRecordingLimit(START, START + 120_000, 120)).toBe(true)
	})

	it('is true when the tab was throttled clean past it', () => {
		// The stop has to fire on the first callback after the limit, not
		// on the callback that happens to land on it.
		expect(isOverRecordingLimit(START, START + 900_000, 120)).toBe(true)
	})

	it('is false with no recording or no limit', () => {
		expect(isOverRecordingLimit(null, START + 900_000, 120)).toBe(false)
		expect(isOverRecordingLimit(START, START + 900_000, 0)).toBe(false)
	})
})
