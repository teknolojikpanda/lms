<template>
	<header
		class="sticky top-0 z-10 flex items-center justify-between border-b bg-surface-base px-3 py-2.5 sm:px-5"
	>
		<Breadcrumbs :items="[{ label: __('Speaking Grading'), route: { name: 'SpeakingGrading' } }]" />
		<Button @click="refresh">
			<template #prefix>
				<span class="lucide-refresh-cw size-4" />
			</template>
			{{ __('Refresh') }}
		</Button>
	</header>

	<div class="md:w-8/12 md:mx-auto mx-4 py-10">
		<h1 class="text-2xl font-semibold text-ink-gray-9 mb-1">
			{{ __('Grading Center — Speaking') }}
		</h1>
		<p class="text-sm text-ink-gray-6 mb-6">
			{{ __('Review AI-scored submissions. Your override always wins and is audit-logged.') }}
		</p>

		<div v-if="queue.length" class="space-y-2">
			<div
				v-for="row in queue"
				:key="row.name"
				class="flex items-center justify-between rounded-md border border-outline-gray-2 p-3 text-sm"
			>
				<div class="flex items-center gap-3 min-w-0">
					<Badge :theme="row.is_overridden ? 'gray' : 'orange'">
						{{ row.is_overridden ? __('Reviewed') : __('Pending Review') }}
					</Badge>
					<span class="text-ink-gray-8 truncate">{{ row.member }}</span>
					<span class="text-ink-gray-5">{{ row.prompt }}</span>
				</div>
				<div class="flex items-center gap-3 shrink-0">
					<span class="text-ink-gray-7">
						{{ __('AI') }}: <span class="font-medium">{{ row.ai_total_score }}</span>
					</span>
					<span class="text-ink-gray-9">
						{{ __('Final') }}: <span class="font-semibold">{{ row.final_score }}</span>
					</span>
					<Button variant="ghost" @click="openDetail(row.name)">
						{{ __('Review') }}
					</Button>
				</div>
			</div>
		</div>
		<div
			v-else
			class="text-ink-gray-5 italic text-sm border border-dashed border-outline-gray-2 rounded-md p-8 text-center"
		>
			{{ __('No submissions waiting for review.') }}
		</div>
	</div>

	<!-- DETAIL / OVERRIDE DIALOG -->
	<Dialog v-model:open="showDetail" :title="__('Review Submission')" size="2xl">
		<template #default>
			<div v-if="detail" class="text-base space-y-5">
				<div class="grid grid-cols-2 gap-3 text-sm">
					<div>
						<span class="text-ink-gray-5">{{ __('Student') }}:</span>
						<span class="text-ink-gray-8 ms-1">{{ detail.member }}</span>
					</div>
					<div>
						<span class="text-ink-gray-5">{{ __('Prompt') }}:</span>
						<span class="text-ink-gray-8 ms-1">{{ detail.prompt }}</span>
					</div>
					<div>
						<span class="text-ink-gray-5">{{ __('AI Score') }}:</span>
						<span class="text-ink-gray-8 ms-1 font-medium">{{ detail.ai_total_score }}/100</span>
					</div>
					<div>
						<span class="text-ink-gray-5">{{ __('Final Score') }}:</span>
						<span class="text-ink-gray-8 ms-1 font-semibold">{{ detail.final_score }}/100</span>
					</div>
				</div>

				<div class="grid grid-cols-4 gap-2 text-center text-xs text-ink-gray-6">
					<div class="rounded bg-surface-gray-1 p-2">
						<div class="font-semibold text-ink-gray-8 text-sm">{{ detail.metrics.wpm }}</div>
						{{ __('wpm') }}
					</div>
					<div class="rounded bg-surface-gray-1 p-2">
						<div class="font-semibold text-ink-gray-8 text-sm">{{ detail.metrics.word_count }}</div>
						{{ __('words') }}
					</div>
					<div class="rounded bg-surface-gray-1 p-2">
						<div class="font-semibold text-ink-gray-8 text-sm">
							{{ detail.metrics.lexical_diversity }}
						</div>
						{{ __('diversity') }}
					</div>
					<div class="rounded bg-surface-gray-1 p-2">
						<div class="font-semibold text-ink-gray-8 text-sm">{{ detail.metrics.filler_ratio }}</div>
						{{ __('fillers') }}
					</div>
				</div>

				<div class="space-y-2">
					<div
						v-for="row in detail.rubric_scores"
						:key="row.dimension"
						class="rounded-md border border-outline-gray-2 p-3 text-sm"
					>
						<div class="flex justify-between">
							<span class="font-medium text-ink-gray-9">{{ __(row.dimension) }}</span>
							<span class="font-semibold">{{ row.score }}/100</span>
						</div>
						<p class="text-ink-gray-7 mt-1">{{ row.feedback }}</p>
					</div>
				</div>

				<details v-if="detail.transcript" class="text-sm text-ink-gray-7">
					<summary class="cursor-pointer font-medium text-ink-gray-8">
						{{ __('Transcript') }}
					</summary>
					<p class="mt-2 whitespace-pre-wrap">{{ detail.transcript }}</p>
				</details>

				<div class="border-t pt-4">
					<div class="font-medium text-ink-gray-9 mb-3">{{ __('Override Score') }}</div>
					<div class="grid grid-cols-2 gap-4">
						<FormControl
							:label="__('Final Score (0-100)')"
							v-model="override.score"
							type="number"
						/>
						<FormControl
							:label="__('Reason (required, audit-logged)')"
							v-model="override.reason"
							type="text"
						/>
					</div>
				</div>
			</div>
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button @click="showDetail = false">{{ __('Close') }}</Button>
				<Button variant="solid" :loading="overriding" @click="applyOverride">
					{{ __('Apply Override') }}
				</Button>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import {
	Badge,
	Breadcrumbs,
	Button,
	Dialog,
	FormControl,
	toast,
	usePageMeta,
} from 'frappe-ui'
import { inject, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { sessionStore } from '@/stores/session'
import {
	getGradingQueue,
	getSpeakingResult,
	overrideSpeakingScore,
} from '@/utils/langApi'

const { brand } = sessionStore()
const user = inject('$user')
const router = useRouter()

const queue = ref([])
const detail = ref(null)
const showDetail = ref(false)
const overriding = ref(false)
const override = reactive({ score: null, reason: '' })

onMounted(async () => {
	const canGrade =
		user.data?.is_moderator || user.data?.is_instructor || user.data?.is_evaluator
	if (!canGrade) {
		router.push({ name: 'Courses' })
		return
	}
	await refresh()
})

const refresh = async () => {
	try {
		queue.value = await getGradingQueue()
	} catch (error) {
		toast.error(error.message)
	}
}

const openDetail = async (submissionName) => {
	try {
		detail.value = await getSpeakingResult(submissionName)
		override.score = detail.value.final_score
		override.reason = ''
		showDetail.value = true
	} catch (error) {
		toast.error(error.message)
	}
}

const applyOverride = async () => {
	if (!override.reason.trim()) {
		toast.error(__('An override reason is mandatory.'))
		return
	}
	overriding.value = true
	try {
		detail.value = await overrideSpeakingScore(
			detail.value.name,
			Number(override.score),
			override.reason
		)
		toast.success(__('Score overridden'))
		showDetail.value = false
		await refresh()
	} catch (error) {
		toast.error(error.message)
	} finally {
		overriding.value = false
	}
}

usePageMeta(() => {
	return {
		title: __('Speaking Grading'),
		icon: brand.favicon,
	}
})
</script>
