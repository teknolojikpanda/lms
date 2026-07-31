import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const pushMock = vi.hoisted(() => vi.fn())
const attemptRows = vi.hoisted(() => ({ value: [] as Record<string, unknown>[] }))
const blueprintRows = vi.hoisted(() => ({ value: [] as Record<string, unknown>[] }))
const resourceParams = vi.hoisted(() => ({ value: [] as Record<string, any>[] }))

vi.mock('vue-router', () => ({
	useRoute: () => ({ params: {}, query: {} }),
	useRouter: () => ({ push: pushMock }),
}))

vi.mock('@/stores/session', () => ({
	sessionStore: () => ({ brand: { value: {} } }),
}))

vi.mock('frappe-ui', () => ({
	Badge: { props: ['label'], template: '<span>{{ label }}</span>' },
	Breadcrumbs: { template: '<nav />' },
	Button: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
	createResource: (options: { params?: { doctype?: string } }) => {
		resourceParams.value.push(options.params || {})
		return {
			get data() {
				return options.params?.doctype === 'LMS Placement Attempt'
					? attemptRows.value
					: blueprintRows.value
			},
			fetched: true,
			fetch: vi.fn(),
		}
	},
	usePageMeta: vi.fn(),
}))

// @ts-expect-error - the app installs this at runtime
globalThis.__ = (text: string) => text
// @ts-expect-error - runtime extension the app installs
String.prototype.format = function (...args: unknown[]) {
	return this.replace(/\{(\d+)\}/g, (match: string, i: string) =>
		args[Number(i)] === undefined ? match : String(args[Number(i)])
	)
}

import PlacementTests from '@/pages/PlacementTests.vue'

const mountPage = async () => {
	const wrapper = mount(PlacementTests, {
		global: {
			provide: { $user: { data: { name: 'student@example.com' } } },
			mocks: { __: (text: string) => text },
		},
	})
	await flushPromises()
	return wrapper
}

const labels = (wrapper: ReturnType<typeof mount>) =>
	wrapper.findAll('button').map((b) => b.text().trim()).filter(Boolean)

describe('PlacementTests', () => {
	beforeEach(() => {
		pushMock.mockReset()
		resourceParams.value = []
		blueprintRows.value = [
			{ name: 'PLB-1', title: 'Placement', duration: 30, max_attempts: 3 },
		]
		attemptRows.value = []
	})

	it('offers only Start Test before any attempt', async () => {
		const wrapper = await mountPage()
		expect(labels(wrapper)).toEqual(['Start Test'])
	})

	it('keeps a retake reachable after a result exists', async () => {
		// The regression: collapsing both actions into one button meant that
		// once a result existed, every remaining retake became unreachable.
		attemptRows.value = [
			{ name: 'ATT-1', blueprint: 'PLB-1', status: 'Completed', result_level: 'B1' },
		]
		const wrapper = await mountPage()

		expect(labels(wrapper)).toEqual(['View Result', 'Retake'])
	})

	it('hides the retake once the attempts are spent', async () => {
		attemptRows.value = [
			{ name: 'ATT-1', blueprint: 'PLB-1', status: 'Completed', result_level: 'B1' },
			{ name: 'ATT-2', blueprint: 'PLB-1', status: 'Completed', result_level: 'B1' },
			{ name: 'ATT-3', blueprint: 'PLB-1', status: 'Completed', result_level: 'B2' },
		]
		const wrapper = await mountPage()

		expect(labels(wrapper)).toEqual(['View Result'])
	})

	it('offers Resume while an attempt is still open', async () => {
		attemptRows.value = [
			{ name: 'ATT-2', blueprint: 'PLB-1', status: 'In Progress' },
			{ name: 'ATT-1', blueprint: 'PLB-1', status: 'Completed', result_level: 'B1' },
		]
		const wrapper = await mountPage()

		expect(labels(wrapper)).toContain('Resume')
	})

	it('names the attempt when viewing, and never when taking', async () => {
		attemptRows.value = [
			{ name: 'ATT-1', blueprint: 'PLB-1', status: 'Completed', result_level: 'B1' },
		]
		const wrapper = await mountPage()
		const buttons = wrapper.findAll('button')

		await buttons[0].trigger('click') // View Result
		expect(pushMock).toHaveBeenLastCalledWith(
			expect.objectContaining({ query: { attempt: 'ATT-1' } })
		)

		await buttons[1].trigger('click') // Retake
		expect(pushMock.mock.calls.at(-1)?.[0]).not.toHaveProperty('query')
	})

	it('counts only this member\'s attempts', async () => {
		// Staff can read every student's attempts. Unscoped, a moderator
		// opening this page counts the whole cohort against the cap and
		// loses the Start button on a test the server would allow.
		await mountPage()

		const attemptQuery = resourceParams.value.find(
			(params) => params.doctype === 'LMS Placement Attempt'
		)
		expect(attemptQuery?.filters).toEqual({ member: 'student@example.com' })
	})

	it('allows unlimited retakes when the blueprint sets no cap', async () => {
		blueprintRows.value = [
			{ name: 'PLB-1', title: 'Placement', duration: 30, max_attempts: 0 },
		]
		attemptRows.value = [
			{ name: 'ATT-1', blueprint: 'PLB-1', status: 'Completed', result_level: 'B1' },
		]
		const wrapper = await mountPage()

		expect(labels(wrapper)).toContain('Retake')
	})
})
