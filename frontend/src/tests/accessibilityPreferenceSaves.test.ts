import { describe, it, expect, vi, beforeEach } from 'vitest'

// Each save is a separate request with no ordering guard, so a slow early
// one could land after a fast later one and overwrite the server record
// with a preference the user had already moved past. Locally everything
// still looked right, which is what made it hard to notice — the stale
// value only appeared on the next reload or on another device.
const mocks = vi.hoisted(() => ({
	save: vi.fn(),
	load: vi.fn(),
}))

vi.mock('@/utils/langApi', () => ({
	saveAccessibilityPreferences: mocks.save,
	getAccessibilityPreferences: mocks.load,
}))

import { updateAccessibility } from '@/stores/accessibility'

/** A save that resolves only when the test says so. */
function deferred() {
	let resolve!: () => void
	const promise = new Promise<void>((r) => {
		resolve = r
	})
	return { promise, resolve }
}

describe('accessibility preference saves', () => {
	beforeEach(() => {
		mocks.save.mockReset()
		mocks.save.mockResolvedValue(undefined)
		localStorage.clear()
	})

	it('never has two saves in flight at once', async () => {
		const first = deferred()
		let concurrent = 0
		let maxConcurrent = 0
		mocks.save.mockImplementation(async () => {
			concurrent += 1
			maxConcurrent = Math.max(maxConcurrent, concurrent)
			await first.promise
			concurrent -= 1
		})

		const a = updateAccessibility({ font_step: 4 })
		const b = updateAccessibility({ font_step: 5 })
		const c = updateAccessibility({ font_step: 6 })

		first.resolve()
		await Promise.all([a, b, c])

		expect(maxConcurrent).toBe(1)
	})

	it('sends the newest state last, so it is the one that sticks', async () => {
		const gate = deferred()
		let firstCall = true
		mocks.save.mockImplementation(async () => {
			if (firstCall) {
				firstCall = false
				await gate.promise
			}
		})

		const a = updateAccessibility({ font_step: 2 })
		const b = updateAccessibility({ font_step: 5 })
		const c = updateAccessibility({ font_step: 6 })

		gate.resolve()
		await Promise.all([a, b, c])

		const sent = mocks.save.mock.calls.map((call) => call[0].font_step)
		expect(sent.at(-1)).toBe(6)
	})

	it('coalesces the middle of a drag rather than sending every step', async () => {
		const gate = deferred()
		let firstCall = true
		mocks.save.mockImplementation(async () => {
			if (firstCall) {
				firstCall = false
				await gate.promise
			}
		})

		const updates = [2, 3, 4, 5, 6].map((step) =>
			updateAccessibility({ font_step: step })
		)

		gate.resolve()
		await Promise.all(updates)

		// The first save plus one carrying the final state. The intermediate
		// steps of a drag are not worth a round trip each.
		expect(mocks.save.mock.calls.length).toBeLessThan(updates.length)
		expect(mocks.save.mock.calls.at(-1)?.[0].font_step).toBe(6)
	})

	it('a failed save does not wedge later ones', async () => {
		mocks.save.mockRejectedValueOnce(new Error('offline'))

		await updateAccessibility({ font_step: 4 })
		await updateAccessibility({ font_step: 5 })

		expect(mocks.save.mock.calls.at(-1)?.[0].font_step).toBe(5)
	})
})
