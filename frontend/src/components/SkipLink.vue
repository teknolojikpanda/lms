<template>
	<a
		class="lms-skip-link"
		:href="`#${targetId}`"
		@click.prevent="focusTarget"
	>
		{{ __('Skip to main content') }}
	</a>
</template>

<script setup>
/**
 * The first tab stop on every page (WCAG 2.4.1 Bypass Blocks).
 *
 * Styling alone was not the feature: without an anchor in the document
 * there is no first tab stop, and a keyboard user still traverses the
 * whole sidebar on every navigation.
 *
 * The click is handled rather than left to the browser for two reasons.
 * A bare `href="#id"` changes `location.hash`, which the router treats as
 * a navigation; and browsers do not reliably move *focus* to the target
 * of an in-page link, only the scroll position — and focus is the whole
 * point here.
 */
const props = defineProps({
	targetId: {
		type: String,
		default: 'lms-main-content',
	},
})

const focusTarget = () => {
	const target = document.getElementById(props.targetId)
	if (!target) return
	// `tabindex="-1"` on the target makes it programmatically focusable
	// without adding it to the tab order.
	target.focus()
	target.scrollIntoView({ block: 'start' })
}
</script>
