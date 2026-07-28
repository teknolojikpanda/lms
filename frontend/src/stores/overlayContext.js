import { reactive } from 'vue'

/**
 * Bridges lesson context into VideoBlock instances.
 *
 * VideoBlock is mounted by the EditorJS Upload tool via `createApp`, i.e. in
 * a separate Vue app with no router/pinia context. A plain module-level
 * reactive object is shared through the module graph regardless of which app
 * imports it, so Lesson.vue can publish "which lesson is on screen" and
 * VideoBlock can react to it.
 */
export const overlayContext = reactive({
	lesson: null,
	// set true on routes where overlays must not trigger (e.g. teacher preview)
	suspended: false,
})

export function setOverlayLesson(lesson) {
	overlayContext.lesson = lesson || null
}

export function clearOverlayLesson() {
	overlayContext.lesson = null
}
