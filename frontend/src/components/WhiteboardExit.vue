<template>
	<button
		v-if="preferences.whiteboard_mode"
		type="button"
		class="lms-whiteboard-exit"
		@click="leave"
	>
		{{ __('Exit whiteboard mode') }}
	</button>
</template>

<script setup>
/**
 * The way out of whiteboard mode.
 *
 * Whiteboard mode hides the sidebar and the mobile navigation, which is
 * the point of it — but the control that turns it off lives on the
 * `/accessibility` page, and the sidebar was the way there. Hiding the
 * chrome without this leaves a teacher on a smart board with no route
 * back except typing a URL, which is not something you do in front of a
 * class.
 *
 * Deliberately carries no `data-whiteboard-hide`: it is the one piece of
 * chrome that must survive the mode.
 */
import { useAccessibility } from '@/stores/accessibility'

const { preferences, update } = useAccessibility()

const leave = () => update({ whiteboard_mode: 0 })
</script>
