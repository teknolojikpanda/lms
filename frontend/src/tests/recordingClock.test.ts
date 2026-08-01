import { describe, expect, it } from 'vitest'
import { recordedSeconds, isOverRecordingLimit } from '@/utils/recordingClock'

const START = 1_000_000

describe('recordedSeconds', () => {
	it('measures elapsed time', () => {
		expect(recordedSeconds(START, START + 30_000, 120)).toBe(30)
	})

	it('is unaffected by how often the timer got to run', () => {
		// The defect: the counter was incremented once per setInterval
		// callback. A backgrounded tab or a locked phone screen throttles
		// setInterval to as little as once a minute while MediaRecorder
		// keeps recording, so four minutes of audio read as a handful of
		// seconds — and that undercount was what the server received for
		// its duration check and daily quota.
		expect(recordedSeconds(START, START + 240_000, 600)).toBe(240)
	})

	it('never reports more than the limit', () => {
		expect(recordedSeconds(START, START + 240_000, 120)).toBe(120)
	})

	it('reports nothing before a recording starts', () => {
		expect(recordedSeconds(null, START + 5_000, 120)).toBe(0)
	})

	it('does not go negative if the clock steps backwards', () => {
		expect(recordedSeconds(START, START - 5_000, 120)).toBe(0)
	})

	it('is uncapped when no limit is configured', () => {
		expect(recordedSeconds(START, START + 300_000, 0)).toBe(300)
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
