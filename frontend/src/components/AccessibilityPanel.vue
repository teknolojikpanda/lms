<template>
	<div class="space-y-6">
		<div>
			<h2 class="text-lg font-semibold text-ink-gray-9">
				{{ __('Accessibility') }}
			</h2>
			<p class="text-sm text-ink-gray-6 mt-1">
				{{ __('These settings follow you to any device you sign in from.') }}
			</p>
		</div>

		<!-- FONT SIZE (§6.3: six steps) -->
		<fieldset>
			<legend class="text-sm font-medium text-ink-gray-8 mb-2">
				{{ __('Text size') }}
			</legend>
			<div class="flex flex-wrap gap-2" role="radiogroup" :aria-label="__('Text size')">
				<button
					v-for="step in fontSteps"
					:key="step.step"
					type="button"
					role="radio"
					:aria-checked="preferences.font_step === step.step"
					:class="[
						'rounded-md border px-4 py-2 transition-colors',
						preferences.font_step === step.step
							? 'border-outline-gray-4 bg-surface-gray-2 font-semibold'
							: 'border-outline-gray-2 hover:bg-surface-gray-1',
					]"
					:style="{ fontSize: step.base_px + 'px' }"
					@click="update({ font_step: step.step })"
				>
					A
					<span class="lms-sr-only">{{ __(step.label) }}</span>
				</button>
			</div>
			<p class="text-xs text-ink-gray-5 mt-2">
				{{ __('Currently: {0}').format(__(currentStepLabel)) }}
			</p>
		</fieldset>

		<!-- CONTRAST -->
		<div class="flex items-start justify-between gap-4">
			<div>
				<label for="a11y-contrast" class="text-sm font-medium text-ink-gray-8">
					{{ __('High contrast') }}
				</label>
				<p class="text-xs text-ink-gray-5 mt-0.5">
					{{ __('Stronger text and border colours throughout the interface.') }}
				</p>
			</div>
			<input
				id="a11y-contrast"
				type="checkbox"
				class="mt-1 size-5 accent-gray-900"
				:checked="preferences.contrast_mode === 'high'"
				@change="update({ contrast_mode: $event.target.checked ? 'high' : 'normal' })"
			/>
		</div>

		<!-- WHITEBOARD MODE (Ek-4.1) -->
		<div class="flex items-start justify-between gap-4">
			<div>
				<label for="a11y-whiteboard" class="text-sm font-medium text-ink-gray-8">
					{{ __('Whiteboard mode') }}
				</label>
				<p class="text-xs text-ink-gray-5 mt-0.5">
					{{
						__(
							'For smart boards: larger buttons, controls always visible, less on-screen clutter.'
						)
					}}
				</p>
			</div>
			<input
				id="a11y-whiteboard"
				type="checkbox"
				class="mt-1 size-5 accent-gray-900"
				:checked="preferences.whiteboard_mode"
				@change="update({ whiteboard_mode: $event.target.checked })"
			/>
		</div>

		<!-- REDUCED MOTION -->
		<div class="flex items-start justify-between gap-4">
			<div>
				<label for="a11y-motion" class="text-sm font-medium text-ink-gray-8">
					{{ __('Reduce motion') }}
				</label>
				<p class="text-xs text-ink-gray-5 mt-0.5">
					{{ __('Removes animations and transitions.') }}
				</p>
			</div>
			<input
				id="a11y-motion"
				type="checkbox"
				class="mt-1 size-5 accent-gray-900"
				:checked="preferences.reduce_motion"
				@change="update({ reduce_motion: $event.target.checked })"
			/>
		</div>

		<div class="border-t pt-4">
			<Button @click="resetDefaults">{{ __('Reset to defaults') }}</Button>
		</div>
	</div>
</template>

<script setup>
import { Button } from 'frappe-ui'
import { computed, onMounted } from 'vue'
import { useAccessibility } from '@/stores/accessibility'

const { preferences, update, load } = useAccessibility()

// Fallback list so the control renders before (or without) a server
// response — an accessibility panel that needs the network to appear is
// not much use on a bad connection.
const FALLBACK_STEPS = [
	{ step: 1, label: 'Extra small', base_px: 13 },
	{ step: 2, label: 'Small', base_px: 14 },
	{ step: 3, label: 'Default', base_px: 16 },
	{ step: 4, label: 'Large', base_px: 18 },
	{ step: 5, label: 'Extra large', base_px: 21 },
	{ step: 6, label: 'Maximum', base_px: 24 },
]

const fontSteps = computed(() =>
	preferences.fontSteps?.length ? preferences.fontSteps : FALLBACK_STEPS
)

const currentStepLabel = computed(
	() =>
		fontSteps.value.find((step) => step.step === preferences.font_step)?.label || 'Default'
)

const resetDefaults = () =>
	update({
		font_step: 3,
		contrast_mode: 'normal',
		whiteboard_mode: false,
		reduce_motion: false,
	})

onMounted(() => {
	if (!preferences.loaded) load()
})
</script>
