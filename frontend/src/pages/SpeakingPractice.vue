<template>
	<header
		class="sticky top-0 z-10 flex items-center justify-between border-b bg-surface-base px-3 py-2.5 sm:px-5"
	>
		<Breadcrumbs :items="[{ label: __('Speaking Practice'), route: { name: 'SpeakingPractice' } }]" />
	</header>

	<div class="md:w-7/12 md:mx-auto mx-4 py-10">
		<!-- PROMPT LIST -->
		<div v-if="!activePrompt">
			<h1 class="text-2xl font-semibold text-ink-gray-9 mb-1">
				{{ __('Speaking Practice') }}
			</h1>
			<p class="text-sm text-ink-gray-6 mb-6">
				{{ __('Pick a scenario, record your answer and get rubric-based feedback.') }}
			</p>

			<div v-if="prompts.data?.length" class="space-y-3">
				<div
					v-for="prompt in prompts.data"
					:key="prompt.name"
					class="flex items-center justify-between rounded-md border border-outline-gray-2 p-4"
				>
					<div class="min-w-0">
						<div class="font-medium text-ink-gray-9">{{ prompt.title }}</div>
						<div class="text-sm text-ink-gray-6 mt-1 flex items-center gap-3">
							<Badge v-if="prompt.language_level" theme="blue">
								{{ prompt.language_level }}
							</Badge>
							<span v-if="prompt.topic">{{ prompt.topic }}</span>
							<span>
								{{ __('max {0}s').format(prompt.max_duration_seconds || 120) }}
							</span>
						</div>
					</div>
					<Button variant="solid" @click="openPrompt(prompt)">
						{{ __('Practice') }}
					</Button>
				</div>
			</div>
			<div
				v-else-if="prompts.fetched"
				class="text-ink-gray-5 italic text-sm border border-dashed border-outline-gray-2 rounded-md p-8 text-center"
			>
				{{ __('No speaking scenarios are available yet.') }}
			</div>

			<!-- Past submissions -->
			<div v-if="submissions.data?.length" class="mt-10">
				<h2 class="font-semibold text-ink-gray-9 mb-3">{{ __('My Recordings') }}</h2>
				<div class="space-y-2">
					<div
						v-for="submission in submissions.data"
						:key="submission.name"
						class="flex items-center justify-between rounded-md border border-outline-gray-2 p-3 text-sm"
					>
						<div class="flex items-center gap-3">
							<Badge :theme="statusTheme(submission.status)">
								{{ __(submission.status) }}
							</Badge>
							<span class="text-ink-gray-7">{{ submission.prompt_title }}</span>
						</div>
						<div class="flex items-center gap-3">
							<span v-if="submission.status === 'Ready'" class="font-medium text-ink-gray-9">
								{{ submission.final_score }}/100
							</span>
							<Button variant="ghost" @click="viewResult(submission.name)">
								{{ __('View') }}
							</Button>
						</div>
					</div>
				</div>
			</div>
		</div>

		<!-- RECORD / RESULT VIEW -->
		<div v-else>
			<Button variant="ghost" class="mb-4" @click="closePrompt">
				<template #prefix>
					<span class="lucide-arrow-left size-4" />
				</template>
				{{ __('Back') }}
			</Button>

			<h1 class="text-xl font-semibold text-ink-gray-9">{{ activePrompt.title }}</h1>
			<div
				class="prose prose-sm max-w-none mt-3 rounded-md border border-outline-gray-2 p-4"
				v-html="activePrompt.scenario"
			></div>

			<!-- RESULT -->
			<div v-if="result && result.status === 'Ready'" class="mt-6">
				<div class="rounded-md border border-outline-gray-2 p-6 text-center">
					<div class="text-sm text-ink-gray-6">{{ __('Your score') }}</div>
					<div class="text-5xl font-bold text-ink-gray-9 mt-2">
						{{ result.final_score }}<span class="text-xl text-ink-gray-5">/100</span>
					</div>
					<div v-if="result.is_overridden" class="text-xs text-ink-gray-6 mt-2">
						{{ __('Reviewed by your teacher (AI score: {0})').format(result.ai_total_score) }}
					</div>
				</div>

				<h2 class="font-semibold text-ink-gray-9 mt-6 mb-3">{{ __('Feedback') }}</h2>
				<div class="space-y-3">
					<div
						v-for="row in result.rubric_scores"
						:key="row.dimension"
						class="rounded-md border border-outline-gray-2 p-4"
					>
						<div class="flex items-center justify-between">
							<span class="font-medium text-ink-gray-9">{{ __(row.dimension) }}</span>
							<span class="text-sm font-semibold text-ink-gray-8">{{ row.score }}/100</span>
						</div>
						<p class="text-sm text-ink-gray-7 mt-1">{{ row.feedback }}</p>
						<p class="text-sm text-ink-gray-6 mt-1 italic">💡 {{ row.improvement_tip }}</p>
					</div>
				</div>

				<div class="mt-6 text-sm text-ink-gray-6 grid grid-cols-2 sm:grid-cols-4 gap-3">
					<div class="rounded-md bg-surface-gray-1 p-3 text-center">
						<div class="font-semibold text-ink-gray-8">{{ result.metrics.wpm }}</div>
						<div class="text-xs">{{ __('words/min') }}</div>
					</div>
					<div class="rounded-md bg-surface-gray-1 p-3 text-center">
						<div class="font-semibold text-ink-gray-8">{{ result.metrics.word_count }}</div>
						<div class="text-xs">{{ __('words') }}</div>
					</div>
					<div class="rounded-md bg-surface-gray-1 p-3 text-center">
						<div class="font-semibold text-ink-gray-8">{{ result.metrics.lexical_diversity }}</div>
						<div class="text-xs">{{ __('lexical diversity') }}</div>
					</div>
					<div class="rounded-md bg-surface-gray-1 p-3 text-center">
						<div class="font-semibold text-ink-gray-8">{{ result.metrics.filler_ratio }}</div>
						<div class="text-xs">{{ __('filler ratio') }}</div>
					</div>
				</div>

				<details v-if="result.transcript" class="mt-6 text-sm text-ink-gray-7">
					<summary class="cursor-pointer font-medium text-ink-gray-8">
						{{ __('Transcript') }}
					</summary>
					<p class="mt-2 whitespace-pre-wrap">{{ result.transcript }}</p>
				</details>

				<Button class="mt-6" @click="resetRecording">
					{{ __('Record Again') }}
				</Button>
			</div>

			<!-- PROCESSING -->
			<div v-else-if="result" class="mt-6 rounded-md border border-outline-gray-2 p-8 text-center">
				<div v-if="result.status === 'Failed'">
					<div class="text-ink-red-3 font-medium">
						{{ __('Processing failed. Please try recording again.') }}
					</div>
					<Button class="mt-4" @click="resetRecording">{{ __('Try Again') }}</Button>
				</div>
				<div v-else>
					<LoadingIndicator class="size-5 mx-auto text-ink-gray-6" />
					<div class="text-ink-gray-7 mt-3">
						{{ __('Evaluating your recording ({0})…').format(__(result.status)) }}
					</div>
					<div class="text-xs text-ink-gray-5 mt-1">
						{{ __('This usually takes a couple of minutes. You can leave this page and come back.') }}
					</div>
				</div>
			</div>

			<!-- RECORDER -->
			<div v-else class="mt-6 rounded-md border border-outline-gray-2 p-8 text-center">
				<div class="text-4xl font-mono text-ink-gray-9">
					{{ formatSeconds(recordSeconds) }}
				</div>
				<div class="text-xs text-ink-gray-5 mt-1">
					{{ __('max {0} seconds').format(maxDuration) }}
				</div>

				<div class="flex items-center justify-center gap-3 mt-5">
					<Button v-if="!recording && !audioBlob" variant="solid" theme="red" @click="startRecording">
						<template #prefix>
							<span class="lucide-mic size-4" />
						</template>
						{{ __('Start Recording') }}
					</Button>
					<Button v-if="recording" variant="solid" @click="stopRecording">
						<template #prefix>
							<span class="lucide-square size-4" />
						</template>
						{{ __('Stop') }}
					</Button>
				</div>

				<div v-if="audioBlob && !recording" class="mt-5">
					<audio :src="audioURL" controls class="mx-auto"></audio>
					<div class="flex items-center justify-center gap-3 mt-4">
						<Button @click="resetRecording">{{ __('Discard') }}</Button>
						<Button variant="solid" :loading="uploading" @click="uploadRecording">
							{{ __('Submit for Evaluation') }}
						</Button>
					</div>
				</div>

				<div v-if="micError" class="text-sm text-ink-red-3 mt-4">{{ micError }}</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import {
	Badge,
	Breadcrumbs,
	Button,
	LoadingIndicator,
	createResource,
	toast,
	usePageMeta,
} from 'frappe-ui'
import { inject, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { sessionStore } from '@/stores/session'
import { formatSeconds } from '@/utils/format'
import { createSpeakingSubmission, getSpeakingResult, uploadBlob } from '@/utils/langApi'

const { brand } = sessionStore()
const user = inject('$user')
const router = useRouter()

const activePrompt = ref(null)
const recording = ref(false)
const recordSeconds = ref(0)
const audioBlob = ref(null)
const audioURL = ref(null)
const micError = ref(null)
const uploading = ref(false)
const result = ref(null)
const maxDuration = ref(120)

let mediaRecorder = null
let mediaStream = null
let chunks = []
let recordInterval = null
let pollInterval = null

onMounted(() => {
	if (!user.data) router.push({ name: 'Courses' })
})

onBeforeUnmount(() => {
	cleanupRecorder()
	if (pollInterval) clearInterval(pollInterval)
})

const prompts = createResource({
	url: 'frappe.client.get_list',
	params: {
		doctype: 'LMS Speaking Prompt',
		filters: { enabled: 1 },
		fields: ['name', 'title', 'language_level', 'topic', 'max_duration_seconds', 'scenario'],
		order_by: 'language_level asc, title asc',
	},
	auto: true,
})

const submissions = createResource({
	url: 'frappe.client.get_list',
	params: {
		doctype: 'LMS Speaking Submission',
		fields: ['name', 'prompt', 'prompt.title as prompt_title', 'status', 'final_score'],
		order_by: 'creation desc',
		limit_page_length: 10,
	},
	auto: true,
})

const statusTheme = (status) => {
	if (status === 'Ready') return 'green'
	if (status === 'Failed') return 'red'
	return 'orange'
}

const openPrompt = (prompt) => {
	activePrompt.value = prompt
	maxDuration.value = prompt.max_duration_seconds || 120
	result.value = null
}

const closePrompt = () => {
	cleanupRecorder()
	if (pollInterval) clearInterval(pollInterval)
	activePrompt.value = null
	result.value = null
	resetRecordingState()
	submissions.reload()
}

const viewResult = async (submissionName) => {
	try {
		const data = await getSpeakingResult(submissionName)
		const prompt = prompts.data?.find((row) => row.name === data.prompt)
		activePrompt.value = prompt || { title: data.prompt, scenario: '' }
		result.value = data
		if (!['Ready', 'Failed'].includes(data.status)) pollResult(submissionName)
	} catch (error) {
		toast.error(error.message)
	}
}

const startRecording = async () => {
	micError.value = null
	try {
		mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
	} catch {
		micError.value = __('Microphone access was denied. Please allow it in your browser settings.')
		return
	}
	chunks = []
	mediaRecorder = new MediaRecorder(mediaStream)
	mediaRecorder.ondataavailable = (event) => chunks.push(event.data)
	mediaRecorder.onstop = () => {
		audioBlob.value = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' })
		audioURL.value = URL.createObjectURL(audioBlob.value)
	}
	mediaRecorder.start()
	recording.value = true
	recordSeconds.value = 0
	recordInterval = setInterval(() => {
		recordSeconds.value += 1
		// §8.13 MUST: hard stop at the configured limit
		if (recordSeconds.value >= maxDuration.value) stopRecording()
	}, 1000)
}

const stopRecording = () => {
	if (recordInterval) clearInterval(recordInterval)
	if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop()
	mediaStream?.getTracks().forEach((track) => track.stop())
	recording.value = false
}

const cleanupRecorder = () => {
	if (recordInterval) clearInterval(recordInterval)
	if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop()
	mediaStream?.getTracks().forEach((track) => track.stop())
}

const resetRecordingState = () => {
	audioBlob.value = null
	if (audioURL.value) URL.revokeObjectURL(audioURL.value)
	audioURL.value = null
	recordSeconds.value = 0
	recording.value = false
}

const resetRecording = () => {
	result.value = null
	resetRecordingState()
}

const uploadRecording = async () => {
	uploading.value = true
	try {
		const extension = (audioBlob.value.type.split('/')[1] || 'webm').split(';')[0]
		const file = await uploadBlob(
			audioBlob.value,
			`speaking-${Date.now()}.${extension}`
		)
		const submission = await createSpeakingSubmission(
			activePrompt.value.name,
			file.file_url,
			recordSeconds.value
		)
		toast.success(__('Recording submitted for evaluation'))
		result.value = { status: submission.status, metrics: {}, rubric_scores: [] }
		pollResult(submission.name)
	} catch (error) {
		toast.error(error.message)
	} finally {
		uploading.value = false
	}
}

const pollResult = (submissionName) => {
	if (pollInterval) clearInterval(pollInterval)
	pollInterval = setInterval(async () => {
		try {
			const data = await getSpeakingResult(submissionName)
			result.value = data
			if (['Ready', 'Failed'].includes(data.status)) {
				clearInterval(pollInterval)
				submissions.reload()
			}
		} catch {
			// keep polling; transient errors are fine
		}
	}, 4000)
}

usePageMeta(() => {
	return {
		title: __('Speaking Practice'),
		icon: brand.favicon,
	}
})
</script>
