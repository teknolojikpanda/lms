<template>
	<header
		class="sticky top-0 z-10 flex items-center justify-between border-b bg-surface-base px-3 py-2.5 sm:px-5"
	>
		<Breadcrumbs
			:items="[{ label: __('Accessibility'), route: { name: 'AccessibilitySettings' } }]"
		/>
	</header>

	<div class="md:w-7/12 md:mx-auto mx-4 py-10">
		<AccessibilityPanel />
	</div>
</template>

<script setup>
import { Breadcrumbs, usePageMeta } from 'frappe-ui'
import AccessibilityPanel from '@/components/AccessibilityPanel.vue'
import { sessionStore } from '@/stores/session'

const { brand } = sessionStore()

usePageMeta(() => {
	return {
		title: __('Accessibility'),
		icon: brand.favicon,
	}
})
</script>
