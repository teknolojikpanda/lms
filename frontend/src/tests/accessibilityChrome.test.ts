import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// The layouts pull in the whole sidebar; none of it matters here.
vi.mock('@/components/Sidebar/AppSidebar.vue', () => ({
	default: { template: '<div data-test="sidebar" />' },
}))

vi.mock('@/utils/langApi', () => ({
	saveAccessibilityPreferences: vi.fn().mockResolvedValue(undefined),
	getAccessibilityPreferences: vi.fn().mockResolvedValue({}),
}))

import { updateAccessibility } from '@/stores/accessibility'
import SkipLink from '@/components/SkipLink.vue'
import DesktopLayout from '@/components/Layouts/DesktopLayout.vue'
import NoSidebarLayout from '@/components/Layouts/NoSidebarLayout.vue'

const globals = { mocks: { __: (s: string) => s } }

beforeEach(() => {
	;(globalThis as Record<string, unknown>).__ = (s: string) => s
})

describe('skip link', () => {
	it('renders an anchor, not only a stylesheet rule', () => {
		// The defect: `.lms-skip-link` existed in accessibility.css and
		// nowhere else, so there was no first tab stop and a keyboard user
		// still traversed the entire sidebar on every navigation.
		const wrapper = mount(SkipLink, { global: globals })

		const anchor = wrapper.find('a.lms-skip-link')
		expect(anchor.exists()).toBe(true)
		expect(anchor.attributes('href')).toBe('#lms-main-content')
	})

	it('moves focus to the target rather than only scrolling to it', async () => {
		const target = document.createElement('div')
		target.id = 'lms-main-content'
		target.tabIndex = -1
		target.scrollIntoView = vi.fn()
		document.body.appendChild(target)

		const wrapper = mount(SkipLink, { global: globals, attachTo: document.body })
		await wrapper.find('a').trigger('click')

		expect(document.activeElement).toBe(target)

		wrapper.unmount()
		target.remove()
	})

	it('is the first focusable element in the layout', () => {
		const wrapper = mount(DesktopLayout, { global: globals })
		const focusable = wrapper.element.querySelectorAll('a, button, [tabindex]')
		expect(focusable[0]?.classList.contains('lms-skip-link')).toBe(true)
	})
})

describe('main content target', () => {
	it.each([
		['DesktopLayout', DesktopLayout, 'lms-main-content'],
		['NoSidebarLayout', NoSidebarLayout, 'scrollContainer'],
	])('%s exposes a focusable landmark', (_name, layout, id) => {
		const wrapper = mount(layout, { global: globals })

		const main = wrapper.find(`#${id}`)
		expect(main.exists()).toBe(true)
		expect(main.element.tagName).toBe('MAIN')
		// Focusable on demand without joining the tab order.
		expect(main.attributes('tabindex')).toBe('-1')
	})
})

describe('whiteboard chrome', () => {
	it('marks the sidebar so whiteboard mode can hide it', () => {
		// `[data-whiteboard-hide]` was a selector nothing wore, so enabling
		// whiteboard mode left all the normal chrome on screen and provided
		// none of the full-screen presentation it advertised.
		const wrapper = mount(DesktopLayout, { global: globals })
		expect(wrapper.find('[data-whiteboard-hide]').exists()).toBe(true)
	})

	it('shows no exit button when the mode is off', async () => {
		await updateAccessibility({ whiteboard_mode: 0 })
		const wrapper = mount(DesktopLayout, { global: globals })
		expect(wrapper.find('.lms-whiteboard-exit').exists()).toBe(false)
	})

	it('keeps a way out of the mode visible while it is on', async () => {
		// The toggle lives on /accessibility, which the sidebar was the
		// route to. Hiding the chrome without this strands a teacher at the
		// board with no way back but typing a URL.
		await updateAccessibility({ whiteboard_mode: 1 })
		const wrapper = mount(DesktopLayout, { global: globals })

		const exit = wrapper.find('.lms-whiteboard-exit')
		expect(exit.exists()).toBe(true)
		// It must not hide itself along with the rest of the chrome.
		expect(exit.attributes('data-whiteboard-hide')).toBeUndefined()

		await exit.trigger('click')
		expect(document.documentElement.getAttribute('data-whiteboard')).toBe('false')
	})
})

describe('high contrast palette', () => {
	const css = readFileSync(
		resolve(__dirname, '../styles/accessibility.css'),
		'utf-8'
	)
	const block = css.slice(
		css.indexOf("html[data-contrast='high'] {"),
		css.indexOf("html[data-contrast='high'],")
	)

	it('sets the base surface, not only the text tokens', () => {
		// With `data-theme='dark'` still applied underneath, leaving
		// --surface-base alone put near-black ink on a near-black page —
		// less readable than the default this mode exists to repair.
		expect(block).toContain('--surface-base: #ffffff')
	})

	it('declares light rendering for chrome no variable reaches', () => {
		expect(block).toContain('color-scheme: light')
	})

	it('covers every background surface the app paints with', () => {
		for (const token of [
			'--surface-white',
			'--surface-menu-bar',
			'--surface-sidebar',
			'--surface-elevation-1',
			'--surface-gray-1',
		]) {
			expect(block).toContain(`${token}: #ffffff`)
		}
	})

	it('leaves the inverted surfaces dark', () => {
		// They carry light text; whitening them would destroy contrast in
		// the other direction.
		expect(block).toContain('--surface-gray-10: #111111')
	})
})

describe('whiteboard hover reveals', () => {
	const css = readFileSync(
		resolve(__dirname, '../styles/accessibility.css'),
		'utf-8'
	)

	/**
	 * The rule bodies that a selector appears in.
	 *
	 * Substring checks are too weak here: `.group-hover\:block` also occurs
	 * inside `.hidden.group-hover\:block`, so deleting one of a pair of
	 * selectors left a substring assertion passing. This pairs each
	 * selector with the declaration it actually carries.
	 */
	// Comments sit between rules and would otherwise be captured as part of
	// the following selector list.
	const rules = css.replace(/\/\*[\s\S]*?\*\//g, '')

	function declarationsFor(selector: string) {
		return [...rules.matchAll(/([^{}]+)\{([^}]*)\}/g)]
			.filter(([, selectors]) =>
				selectors
					.split(',')
					.some((one) => one.trim() === selector)
			)
			.map(([, , body]) => body)
	}

	it.each([
		['block', 'block'],
		['inline-block', 'inline-block'],
	])('reveals display-hidden group-hover:%s controls', (variant, display) => {
		// ChapterRow's edit/delete use `hidden group-hover:block`, and
		// ProfileEvaluator's `hidden group-hover:inline-block`. Only
		// visibility and opacity were covered, so on a touch-only board
		// those actions were not merely hard to reach — they were absent.
		const bare = declarationsFor(
			`html[data-whiteboard='true'] .group-hover\\:${variant}`
		)
		const withHidden = declarationsFor(
			`html[data-whiteboard='true'] .hidden.group-hover\\:${variant}`
		)

		// Tailwind's `hidden` is more specific than the bare variant, so
		// both forms have to be named or the commonest markup is missed.
		expect(bare.length, `no rule for the bare group-hover:${variant}`).toBeGreaterThan(0)
		expect(
			withHidden.length,
			`no rule for hidden group-hover:${variant}`
		).toBeGreaterThan(0)
		for (const body of [...bare, ...withHidden]) {
			expect(body).toContain(`display: ${display} !important`)
		}
	})
})
