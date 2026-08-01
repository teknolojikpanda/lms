import { describe, expect, it } from 'vitest'
import { findDueOverlay } from '@/utils/videoOverlays'

const overlay = (name: string, seconds: number) => ({
	name,
	timestamp_ms: seconds * 1000,
})

const nothingShown = () => false

describe('findDueOverlay', () => {
	it('fires when playback reaches the timestamp', () => {
		const question = overlay('q1', 30)
		expect(findDueOverlay([question], 29.8, 30.1, nothingShown)).toBe(question)
	})

	it('does not fire before the timestamp', () => {
		expect(findDueOverlay([overlay('q1', 30)], 28, 29.9, nothingShown)).toBeNull()
	})

	it('fires when a seek jumps clean over the timestamp', () => {
		// The defect. The old rule needed the current position to land
		// within 1.5s after the timestamp, so seeking from 10s to 60s
		// skipped a question at 30s permanently — and a student could do
		// that to every question in a lesson.
		const question = overlay('q1', 30)
		expect(findDueOverlay([question], 10, 60, nothingShown)).toBe(question)
	})

	it('fires after a coarse timeupdate gap, not only a seek', () => {
		// timeupdate is throttled under load and at higher playback rates;
		// nothing guarantees a position inside a 1.5s window.
		const note = overlay('n1', 12)
		expect(findDueOverlay([note], 11.9, 20, nothingShown)).toBe(note)
	})

	it('returns the earliest of several crossed by one jump', () => {
		const first = overlay('q1', 20)
		const second = overlay('q2', 40)
		const third = overlay('q3', 55)

		// Deliberately out of order: the caller must not have to sort.
		const due = findDueOverlay([third, second, first], 5, 60, nothingShown)
		expect(due).toBe(first)
	})

	it('walks through the rest of a jump on subsequent checks', () => {
		const first = overlay('q1', 20)
		const second = overlay('q2', 40)
		const shown = new Set<string>()
		const isShown = (o: { name: string }) => shown.has(o.name)

		let previous: number | null = 5
		const firstDue = findDueOverlay([first, second], previous, 60, isShown)
		expect(firstDue).toBe(first)
		shown.add(firstDue!.name)
		// The component advances its clock to the overlay shown, not to the
		// current position, so the rest stay ahead of `previous`.
		previous = firstDue!.timestamp_ms / 1000

		expect(findDueOverlay([first, second], previous, 60, isShown)).toBe(second)
	})

	it('ignores overlays already shown', () => {
		const question = overlay('q1', 30)
		expect(findDueOverlay([question], 10, 60, () => true)).toBeNull()
	})

	it('does not fire everything at once on a lesson resumed partway', () => {
		// No previous position: anything well behind the current one was
		// passed before this player existed. Firing them all would be its
		// own bug.
		const overlays = [overlay('q1', 10), overlay('q2', 20), overlay('q3', 30)]
		expect(findDueOverlay(overlays, null, 300, nothingShown)).toBeNull()
	})

	it('still fires one landed on with no previous position', () => {
		const question = overlay('q1', 30)
		expect(findDueOverlay([question], null, 30.4, nothingShown)).toBe(question)
	})

	it('handles an empty or missing overlay list', () => {
		expect(findDueOverlay([], 0, 10, nothingShown)).toBeNull()
		expect(findDueOverlay(undefined, 0, 10, nothingShown)).toBeNull()
	})
})
