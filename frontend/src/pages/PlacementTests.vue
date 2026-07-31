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
				<div class="flex items-center gap-2">
					<Button v-if="completedAttempt(blueprint.name)" @click="viewResult(blueprint.name)">
						{{ __('View Result') }}
					</Button>
					<Button
						v-if="canTake(blueprint)"
						variant="solid"
						@click="takeTest(blueprint.name)"
					>
						{{ startLabel(blueprint.name) }}
					</Button>
				</div>
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

// Every attempt, not only the completed ones: the page needs to count them
// against `max_attempts` and to spot an attempt still in progress.
const attempts = createResource({
	url: 'frappe.client.get_list',
	params: {
		doctype: 'LMS Placement Attempt',
		fields: ['name', 'blueprint', 'status', 'result_level', 'override_level', 'creation'],
		order_by: 'creation desc',
		limit_page_length: 0,
	},
	auto: true,
})

const forBlueprint = (blueprint) =>
	(attempts.data || []).filter((row) => row.blueprint === blueprint)

const completedAttempt = (blueprint) =>
	forBlueprint(blueprint).find((row) => row.status === 'Completed') || null

const inProgressAttempt = (blueprint) =>
	forBlueprint(blueprint).find((row) => row.status === 'In Progress') || null

const latestResult = (blueprint) => {
	const attempt = completedAttempt(blueprint)
	return attempt ? attempt.override_level || attempt.result_level : null
}

// Viewing a result and taking the test are separate actions. Collapsing
// them into one button meant that once a result existed, every remaining
// retake — and any attempt still in progress — became unreachable.
const canTake = (blueprint) => {
	if (inProgressAttempt(blueprint.name)) return true
	if (!blueprint.max_attempts) return true
	return forBlueprint(blueprint.name).length < blueprint.max_attempts
}

const startLabel = (blueprint) => {
	if (inProgressAttempt(blueprint)) return __('Resume')
	return completedAttempt(blueprint) ? __('Retake') : __('Start Test')
}

const viewResult = (blueprintName) => {
	// Naming the attempt tells the page to read it rather than begin a new
	// one, which previously spent one of the student's attempts.
	const attempt = completedAttempt(blueprintName)
	router.push({
		name: 'PlacementAttempt',
		params: { blueprintName },
		query: { attempt: attempt.name },
	})
}

const takeTest = (blueprintName) => {
	router.push({ name: 'PlacementAttempt', params: { blueprintName } })
}

usePageMeta(() => {
	return {
		title: __('Placement Tests'),
		icon: brand.favicon,
	}
})
</script>
