// Smoke test: the language-platform pages/components/utilities must at least
// compile through the SFC pipeline and be importable. Mount-level behavior is
// covered manually / by backend tests; this catches syntax and import errors.
import { describe, expect, it, vi } from 'vitest'

const passthrough = { template: '<div><slot /><slot name="default" /><slot name="actions" /></div>' }

vi.mock('frappe-ui', () => ({
	AxisChart: passthrough,
	Badge: passthrough,
	Breadcrumbs: passthrough,
	Button: passthrough,
	Dialog: passthrough,
	DonutChart: passthrough,
	Dropdown: passthrough,
	FormControl: passthrough,
	LoadingIndicator: passthrough,
	NumberChart: passthrough,
	createResource: (opts: object) => ({ ...opts, data: null, fetch: vi.fn(), reload: vi.fn() }),
	call: vi.fn(),
	toast: { success: vi.fn(), error: vi.fn() },
	usePageMeta: vi.fn(),
}))

vi.mock('vue-router', () => ({
	useRouter: () => ({ push: vi.fn() }),
	useRoute: () => ({ params: {}, query: {} }),
}))

vi.mock('@/stores/session', () => ({
	sessionStore: () => ({ brand: { favicon: '' } }),
}))

vi.mock('@/stores/settings', () => ({
	useSettings: () => ({ settings: { data: {} } }),
}))

describe('language platform frontend modules', () => {
	it('imports utils and stores', async () => {
		const langApi = await import('@/utils/langApi')
		expect(typeof langApi.startPlacement).toBe('function')
		expect(typeof langApi.saveOverlay).toBe('function')
		expect(typeof langApi.uploadBlob).toBe('function')

		const context = await import('@/stores/overlayContext')
		expect(context.overlayContext).toBeTruthy()
		context.setOverlayLesson('LESSON-1')
		expect(context.overlayContext.lesson).toBe('LESSON-1')
		context.clearOverlayLesson()
		expect(context.overlayContext.lesson).toBeNull()
	})

	it('compiles the placement pages', async () => {
		expect((await import('@/pages/PlacementTests.vue')).default).toBeTruthy()
		expect((await import('@/pages/PlacementAttempt.vue')).default).toBeTruthy()
	})

	it('compiles the overlay editor, popup and video player integration', async () => {
		expect((await import('@/pages/VideoOverlayEditor.vue')).default).toBeTruthy()
		expect((await import('@/components/Modals/OverlayPopup.vue')).default).toBeTruthy()
		expect((await import('@/components/VideoBlock.vue')).default).toBeTruthy()
	})

	it('compiles the speaking pages', async () => {
		expect((await import('@/pages/SpeakingPractice.vue')).default).toBeTruthy()
		expect((await import('@/pages/SpeakingGrading.vue')).default).toBeTruthy()
	})

	it('compiles the dashboards', async () => {
		expect((await import('@/pages/AdminDashboard.vue')).default).toBeTruthy()
		expect((await import('@/pages/OwnerDashboard.vue')).default).toBeTruthy()
	})
})
