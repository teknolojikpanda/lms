import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const routeQuery = vi.hoisted(() => ({ value: {} as Record<string, string> }))
const startPlacement = vi.hoisted(() => vi.fn())
const getPlacementResult = vi.hoisted(() => vi.fn())

vi.mock('vue-router', () => ({
	useRoute: () => ({ params: {}, query: routeQuery.value }),
	useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/utils/langApi', () => ({
	startPlacement,
	getPlacementResult,
	savePlacementAnswer: vi.fn(),
	submitPlacement: vi.fn(),
}))

vi.mock('@/stores/session', () => ({
	sessionStore: () => ({ brand: { value: {} } }),
}))

vi.mock('frappe-ui', () => ({
	Badge: { template: '<span><slot /></span>' },
	Breadcrumbs: { template: '<nav />' },
	Button: { template: '<button><slot /></button>' },
	FormControl: { template: '<input />' },
	createResource: () => ({ fetch: vi.fn(), data: null }),
	toast: { error: vi.fn(), success: vi.fn() },
	usePageMeta: vi.fn(),
}))

// `<script setup>` calls `__` as a bare global, while the template resolves
// it through the component proxy — so both have to be provided.
// @ts-expect-error - the app installs this at runtime
globalThis.__ = (text: string) => text

// Templates call `__('...').format(a, b)`; the app extends String to
// provide it, so the tests need the same extension.
// @ts-expect-error - runtime extension, not in the String type
String.prototype.format = function (...args: unknown[]) {
	return this.replace(/\{(\d+)\}/g, (match: string, index: string) =>
		args[Number(index)] === undefined ? match : String(args[Number(index)])
	)
}

import PlacementAttempt from '@/pages/PlacementAttempt.vue'

const mountPage = () =>
	mount(PlacementAttempt, {
		props: { blueprintName: 'PLB-0001' },
		global: {
			provide: { $user: { data: { name: 'student@example.com' } } },
			// The app installs `__` as a global property; templates resolve it
			// from there, so setting it on globalThis is not enough.
			mocks: { __: (text: string) => text },
			stubs: { Badge: true, Breadcrumbs: true, Button: true, FormControl: true },
		},
	})

describe('PlacementAttempt', () => {
	beforeEach(() => {
		routeQuery.value = {}
		startPlacement.mockReset()
		getPlacementResult.mockReset()
		getPlacementResult.mockResolvedValue({ result_level: 'B1', percentage: 72 })
		startPlacement.mockResolvedValue({
			name: 'ATT-0001',
			status: 'In Progress',
			blueprint_title: 'Placement',
			duration: 30,
			remaining_seconds: 1800,
			questions: [],
			answers: {},
		})
	})

	afterEach(() => {
		vi.useRealTimers()
	})

	it('reads an existing result without starting a new attempt', async () => {
		// The bug: the list page labels the button "View Result", then routed
		// here — and this page began a test, spending one of the student's
		// remaining attempts to show a score they already had.
		routeQuery.value = { attempt: 'ATT-0009' }

		mountPage()
		await flushPromises()

		expect(startPlacement).not.toHaveBeenCalled()
		expect(getPlacementResult).toHaveBeenCalledWith('ATT-0009')
	})

	it('starts an attempt when none is named', async () => {
		mountPage()
		await flushPromises()

		expect(startPlacement).toHaveBeenCalledWith('PLB-0001')
	})

	it('counts down from the server remainder, not from started_at', async () => {
		// `started_at` is naive and in the site's timezone. Parsing it here
		// with `new Date()` read it as local time, so a student ahead of the
		// server got a deadline already past and was submitted on load.
		// This payload is the shape that broke it: an hour left, but a
		// started_at that a browser 3 hours east reads as long expired.
		startPlacement.mockResolvedValue({
			name: 'ATT-0002',
			status: 'In Progress',
			blueprint_title: 'Placement',
			duration: 60,
			remaining_seconds: 3600,
			started_at: '2020-01-01 00:00:00',
			questions: [],
			answers: {},
		})

		const wrapper = mountPage()
		await flushPromises()

		expect(wrapper.vm.remainingSeconds).toBeGreaterThan(3000)
	})

	it('does not run a timer when the server sends no remainder', async () => {
		startPlacement.mockResolvedValue({
			name: 'ATT-0003',
			status: 'In Progress',
			blueprint_title: 'Placement',
			duration: 0,
			remaining_seconds: null,
			questions: [],
			answers: {},
		})

		const wrapper = mountPage()
		await flushPromises()

		expect(wrapper.vm.remainingSeconds).toBeNull()
	})
})
