import { reactive, readonly } from 'vue'
import {
	getAccessibilityPreferences,
	saveAccessibilityPreferences,
} from '@/utils/langApi'

/**
 * Accessibility preferences (§6.3).
 *
 * Applied by setting data attributes on <html>; accessibility.css does the
 * rest. Preferences are mirrored to localStorage as well as the server so
 * they apply on the *first paint* after a reload — waiting for an API
 * round-trip means a user who needs 24px text reads 16px text for a
 * second on every navigation, which is exactly the population least able
 * to tolerate it.
 */

const STORAGE_KEY = 'lms-accessibility'

const DEFAULTS = {
	font_step: 3,
	contrast_mode: 'normal',
	whiteboard_mode: false,
	reduce_motion: false,
}

const state = reactive({ ...DEFAULTS, loaded: false, fontSteps: [] })

function readLocal() {
	try {
		const raw = localStorage.getItem(STORAGE_KEY)
		return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : { ...DEFAULTS }
	} catch {
		// A corrupt or unavailable store must never break the app; the
		// defaults are always usable.
		return { ...DEFAULTS }
	}
}

function writeLocal(preferences) {
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences))
	} catch {
		// Private browsing or a full quota — the server copy still holds.
	}
}

/** Push the current state onto <html> so the stylesheet can act on it. */
function apply(preferences) {
	const root = document.documentElement
	root.setAttribute('data-font-step', String(preferences.font_step ?? 3))
	root.setAttribute('data-contrast', preferences.contrast_mode || 'normal')
	root.setAttribute('data-whiteboard', preferences.whiteboard_mode ? 'true' : 'false')
	root.setAttribute('data-reduce-motion', preferences.reduce_motion ? 'true' : 'false')
}

/**
 * Apply the cached preferences immediately, before any network call.
 * Called from main.js so the first paint is already correct.
 */
export function initAccessibility() {
	const cached = readLocal()
	Object.assign(state, cached)
	apply(state)
}

/** Fetch the authoritative copy and reconcile. */
export async function loadAccessibility() {
	try {
		const server = await getAccessibilityPreferences()
		Object.assign(state, {
			font_step: server.font_step,
			contrast_mode: server.contrast_mode,
			whiteboard_mode: !!server.whiteboard_mode,
			reduce_motion: !!server.reduce_motion,
			fontSteps: server.font_steps || [],
		})
		writeLocal({
			font_step: state.font_step,
			contrast_mode: state.contrast_mode,
			whiteboard_mode: state.whiteboard_mode,
			reduce_motion: state.reduce_motion,
		})
		apply(state)
	} catch {
		// Guests and offline sessions keep whatever is cached locally.
	} finally {
		state.loaded = true
	}
}

/**
 * Update one or more preferences.
 *
 * Applies locally first, then persists: the control must respond
 * instantly, and a failed save should not mean the setting appears not to
 * work.
 */
export async function updateAccessibility(changes) {
	Object.assign(state, changes)
	apply(state)
	writeLocal({
		font_step: state.font_step,
		contrast_mode: state.contrast_mode,
		whiteboard_mode: state.whiteboard_mode,
		reduce_motion: state.reduce_motion,
	})

	try {
		await saveAccessibilityPreferences({
			font_step: state.font_step,
			contrast_mode: state.contrast_mode,
			whiteboard_mode: state.whiteboard_mode,
			reduce_motion: state.reduce_motion,
		})
	} catch {
		// Kept locally; the next successful save reconciles.
	}
}

export function useAccessibility() {
	return {
		preferences: readonly(state),
		update: updateAccessibility,
		load: loadAccessibility,
	}
}
