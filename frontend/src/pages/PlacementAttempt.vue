<template>
	<header
		class="sticky top-0 z-10 flex items-center justify-between border-b bg-surface-base px-3 py-2.5 sm:px-5"
	>
		<Breadcrumbs :items="breadcrumbs" />
		<Badge v-if="remainingSeconds !== null && !result" size="lg" :theme="remainingSeconds < 60 ? 'red' : 'blue'">
			<span class="lucide-timer size-4 me-1" />
			{{ formatSeconds(remainingSeconds) }}
		</Badge>
	</header>

	<div class="md:w-7/12 md:mx-auto mx-4 py-10">
		<!-- RESULT VIEW -->
		<div v-if="result">
			<h1 class="text-2xl font-semibold text-ink-gray-9 mb-6">
				{{ __('Your Placement Result') }}
			</h1>
			<div class="rounded-md border border-outline-gray-2 p-6 text-center">
				<div class="text-sm text-ink-gray-6">{{ __('Your level') }}</div>
				<div class="text-5xl font-bold text-ink-gray-9 mt-2">
					{{ result.effective_level || '—' }}
				</div>
				<div class="text-sm text-ink-gray-6 mt-3">
					{{ __('Score') }}: {{ result.score }} / {{ result.max_score }} ({{ result.percentage }}%)
				</div>
			</div>

			<div v-if="Object.keys(result.skill_scores || {}).length" class="mt-8">
				<h2 class="font-semibold text-ink-gray-9 mb-4">
					{{ __('Breakdown by Skill') }}
				</h2>
				<div class="space-y-3">
					<div v-for="(bucket, skill) in result.skill_scores" :key="skill">
						<div class="flex justify-between text-sm mb-1">
							<span class="text-ink-gray-8">{{ __(skill) }}</span>
							<span class="text-ink-gray-6">
								{{ bucket.score }} / {{ bucket.max_score }}
							</span>
						</div>
						<div class="h-2 rounded-full bg-surface-gray-2 overflow-hidden">
							<div
								class="h-full rounded-full bg-surface-gray-7"
								:style="{ width: skillPercentage(bucket) + '%' }"
							></div>
						</div>
					</div>
				</div>
			</div>

			<Button class="mt-8" @click="router.push({ name: 'PlacementTests' })">
				{{ __('Back to Placement Tests') }}
			</Button>
		</div>

		<!-- TAKING VIEW -->
		<div v-else-if="attempt">
			<h1 class="text-xl font-semibold text-ink-gray-9">
				{{ attempt.blueprint_title }}
			</h1>
			<div class="text-sm text-ink-gray-6 mt-1 mb-6">
				{{ __('Question {0} of {1}').format(currentIndex + 1, attempt.questions.length) }}
			</div>

			<div v-if="currentQuestion" class="rounded-md border border-outline-gray-2 p-5">
				<div class="prose prose-sm max-w-none" v-html="currentQuestion.question"></div>

				<!-- Choices -->
				<div v-if="currentQuestion.type === 'Choices'" class="mt-4 space-y-2">
					<label
						v-for="option in currentQuestion.options"
						:key="option"
						class="flex items-center gap-2 rounded-md border p-3 cursor-pointer text-sm"
						:class="
							isSelected(option)
								? 'border-outline-gray-4 bg-surface-gray-2'
								: 'border-outline-gray-2'
						"
					>
						<input
							:type="currentQuestion.multiple ? 'checkbox' : 'radio'"
							:name="currentQuestion.name"
							:checked="isSelected(option)"
							@change="toggleOption(option)"
							class="accent-gray-900"
						/>
						<span class="text-ink-gray-8">{{ option }}</span>
					</label>
				</div>

				<!-- User Input -->
				<div v-else class="mt-4">
					<FormControl
						type="text"
						:placeholder="__('Type your answer')"
						:modelValue="answers[currentQuestion.name] || ''"
						@update:modelValue="(value) => setTextAnswer(value)"
					/>
				</div>
			</div>

			<!-- NAVIGATION -->
			<div class="flex items-center justify-between mt-6">
				<Button :disabled="currentIndex === 0" @click="currentIndex--">
					{{ __('Previous') }}
				</Button>
				<div class="flex gap-1 flex-wrap justify-center">
					<button
						v-for="(question, index) in attempt.questions"
						:key="question.name"
						class="size-7 rounded text-xs border"
						:class="[
							index === currentIndex
								? 'border-outline-gray-4 bg-surface-gray-7 text-ink-white'
								: answers[question.name]
									? 'border-outline-gray-3 bg-surface-gray-2 text-ink-gray-8'
									: 'border-outline-gray-2 text-ink-gray-6',
						]"
						@click="currentIndex = index"
					>
						{{ index + 1 }}
					</button>
				</div>
				<Button
					v-if="currentIndex < attempt.questions.length - 1"
					@click="currentIndex++"
				>
					{{ __('Next') }}
				</Button>
				<Button v-else variant="solid" :loading="submitting" @click="submit">
					{{ __('Submit') }}
				</Button>
			</div>
		</div>

		<div v-else-if="loadError" class="text-center py-20">
			<div class="text-ink-gray-7">{{ loadError }}</div>
			<Button class="mt-4" @click="router.push({ name: 'PlacementTests' })">
				{{ __('Back to Placement Tests') }}
			</Button>
		</div>
	</div>
</template>

<script setup>
import { Badge, Breadcrumbs, Button, FormControl, createResource, toast, usePageMeta } from 'frappe-ui'
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { sessionStore } from '@/stores/session'
import { formatSeconds } from '@/utils/format'
import {
	getPlacementResult,
	savePlacementAnswer,
	startPlacement,
	submitPlacement,
} from '@/utils/langApi'

const props = defineProps({
	blueprintName: {
		type: String,
		required: true,
	},
})

const { brand } = sessionStore()
const user = inject('$user')
const router = useRouter()

const attempt = ref(null)
const result = ref(null)
const loadError = ref(null)
const currentIndex = ref(0)
const answers = reactive({})
const submitting = ref(false)
const remainingSeconds = ref(null)
let timerInterval = null

const currentQuestion = computed(() => attempt.value?.questions[currentIndex.value])

const breadcrumbs = computed(() => [
	{ label: __('Placement Tests'), route: { name: 'PlacementTests' } },
	{ label: attempt.value?.blueprint_title || __('Placement Test') },
])

onMounted(async () => {
	if (!user.data) {
		router.push({ name: 'Courses' })
		return
	}
	try {
		const data = await startPlacement(props.blueprintName)
		if (data.status === 'Completed') {
			result.value = await getPlacementResult(data.name)
			return
		}
		attempt.value = data
		Object.assign(answers, parseSavedAnswers(data.answers || {}))
		startTimer(data)
	} catch (error) {
		// Most common cause: attempts exhausted — try to show the last result.
		await showLatestResult(error)
	}
})

onBeforeUnmount(() => {
	if (timerInterval) clearInterval(timerInterval)
})

const parseSavedAnswers = (saved) => {
	const parsed = {}
	Object.entries(saved).forEach(([question, answer]) => {
		try {
			const value = JSON.parse(answer)
			parsed[question] = Array.isArray(value) ? value : answer
		} catch {
			parsed[question] = answer
		}
	})
	return parsed
}

const latestAttempt = createResource({
	url: 'frappe.client.get_list',
	makeParams() {
		return {
			doctype: 'LMS Placement Attempt',
			filters: { blueprint: props.blueprintName, status: 'Completed' },
			fields: ['name'],
			order_by: 'submitted_at desc',
			limit_page_length: 1,
		}
	},
})

const showLatestResult = async (error) => {
	try {
		const rows = await latestAttempt.fetch()
		if (rows?.length) {
			result.value = await getPlacementResult(rows[0].name)
			return
		}
	} catch {
		// fall through to the error message below
	}
	loadError.value = error.message
}

const startTimer = (data) => {
	if (!data.duration) return
	const startedAt = new Date(String(data.started_at).replace(' ', 'T'))
	const deadline = startedAt.getTime() + data.duration * 60 * 1000
	const tick = () => {
		remainingSeconds.value = Math.max(0, Math.round((deadline - Date.now()) / 1000))
		if (remainingSeconds.value <= 0) {
			clearInterval(timerInterval)
			submit()
		}
	}
	tick()
	timerInterval = setInterval(tick, 1000)
}

const isSelected = (option) => {
	const answer = answers[currentQuestion.value.name]
	return Array.isArray(answer) ? answer.includes(option) : answer === option
}

const toggleOption = (option) => {
	const question = currentQuestion.value
	if (question.multiple) {
		const current = Array.isArray(answers[question.name]) ? answers[question.name] : []
		answers[question.name] = current.includes(option)
			? current.filter((item) => item !== option)
			: [...current, option]
	} else {
		answers[question.name] = [option]
	}
	autosave(question.name)
}

let saveTimeout = null
const setTextAnswer = (value) => {
	answers[currentQuestion.value.name] = value
	const questionName = currentQuestion.value.name
	clearTimeout(saveTimeout)
	saveTimeout = setTimeout(() => autosave(questionName), 600)
}

const autosave = async (questionName) => {
	const value = answers[questionName]
	const serialized = Array.isArray(value) ? JSON.stringify(value) : String(value ?? '')
	try {
		await savePlacementAnswer(attempt.value.name, questionName, serialized)
	} catch (error) {
		toast.error(error.message)
	}
}

const submit = async () => {
	if (submitting.value || !attempt.value) return
	submitting.value = true
	if (timerInterval) clearInterval(timerInterval)
	try {
		result.value = await submitPlacement(attempt.value.name, answers)
	} catch (error) {
		toast.error(error.message)
		submitting.value = false
	}
}

const skillPercentage = (bucket) => {
	if (!bucket.max_score) return 0
	return Math.round((bucket.score / bucket.max_score) * 100)
}

usePageMeta(() => {
	return {
		title: attempt.value?.blueprint_title || __('Placement Test'),
		icon: brand.favicon,
	}
})
</script>
