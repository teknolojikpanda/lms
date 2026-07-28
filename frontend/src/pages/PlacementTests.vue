<template>
	<header
		class="sticky top-0 z-10 flex items-center justify-between border-b bg-surface-base px-3 py-2.5 sm:px-5"
	>
		<Breadcrumbs :items="[{ label: __('Placement Tests'), route: { name: 'PlacementTests' } }]" />
	</header>

	<div class="md:w-7/12 md:mx-auto mx-4 py-10">
		<h1 class="text-2xl font-semibold text-ink-gray-9 mb-1">
			{{ __('Placement Tests') }}
		</h1>
		<p class="text-sm text-ink-gray-6 mb-6">
			{{ __('Find your CEFR level. Your institution uses the result to place you in the right course.') }}
		</p>

		<div v-if="blueprints.data?.length" class="space-y-3">
			<div
				v-for="blueprint in blueprints.data"
				:key="blueprint.name"
				class="flex items-center justify-between rounded-md border border-outline-gray-2 p-4"
			>
				<div>
					<div class="font-medium text-ink-gray-9">
						{{ blueprint.title }}
					</div>
					<div class="text-sm text-ink-gray-6 mt-1 space-x-3">
						<span v-if="blueprint.duration">
							{{ __('{0} minutes').format(blueprint.duration) }}
						</span>
						<span v-if="blueprint.max_attempts">
							{{ __('{0} attempt(s) allowed').format(blueprint.max_attempts) }}
						</span>
					</div>
					<div v-if="latestResult(blueprint.name)" class="text-sm mt-1 text-ink-gray-7">
						{{ __('Your level') }}:
						<Badge :label="latestResult(blueprint.name)" theme="green" />
					</div>
				</div>
				<Button variant="solid" @click="openTest(blueprint.name)">
					{{ latestResult(blueprint.name) ? __('View Result') : __('Start Test') }}
				</Button>
			</div>
		</div>
		<div
			v-else-if="blueprints.fetched"
			class="text-ink-gray-5 italic text-sm border border-dashed border-outline-gray-2 rounded-md p-8 text-center"
		>
			{{ __('No placement tests are available yet.') }}
		</div>
	</div>
</template>

<script setup>
import { Badge, Breadcrumbs, Button, createResource, usePageMeta } from 'frappe-ui'
import { inject, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { sessionStore } from '@/stores/session'

const { brand } = sessionStore()
const user = inject('$user')
const router = useRouter()

onMounted(() => {
	if (!user.data) router.push({ name: 'Courses' })
})

const blueprints = createResource({
	url: 'frappe.client.get_list',
	params: {
		doctype: 'LMS Placement Blueprint',
		filters: { enabled: 1 },
		fields: ['name', 'title', 'duration', 'max_attempts'],
		order_by: 'title asc',
	},
	auto: true,
})

const attempts = createResource({
	url: 'frappe.client.get_list',
	params: {
		doctype: 'LMS Placement Attempt',
		filters: { status: 'Completed' },
		fields: ['name', 'blueprint', 'result_level', 'override_level', 'submitted_at'],
		order_by: 'submitted_at desc',
	},
	auto: true,
})

const latestResult = (blueprint) => {
	const attempt = attempts.data?.find((row) => row.blueprint === blueprint)
	return attempt ? attempt.override_level || attempt.result_level : null
}

const openTest = (blueprintName) => {
	router.push({ name: 'PlacementAttempt', params: { blueprintName } })
}

usePageMeta(() => {
	return {
		title: __('Placement Tests'),
		icon: brand.favicon,
	}
})
</script>
