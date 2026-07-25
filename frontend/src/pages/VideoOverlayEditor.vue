<template>
	<header
		class="sticky top-0 z-10 flex items-center justify-between border-b bg-surface-base px-3 py-2.5 sm:px-5"
	>
		<Breadcrumbs :items="breadcrumbs" />
		<Button variant="solid" @click="openDialog()">
			<template #prefix>
				<span class="lucide-plus size-4" />
			</template>
			{{ __('Add Overlay') }}
		</Button>
	</header>

	<div class="md:w-8/12 md:mx-auto mx-4 py-10">
		<h1 class="text-2xl font-semibold text-ink-gray-9 mb-1">
			{{ __('Video Overlays') }}
		</h1>
		<p class="text-sm text-ink-gray-6 mb-6">
			{{ __('Timestamped questions and notes shown to students during the video of this lesson.') }}
		</p>

		<div v-if="overlays.length" class="space-y-2">
			<div
				v-for="overlay in overlays"
				:key="overlay.name"
				class="flex items-center justify-between rounded-md border border-outline-gray-2 p-3"
			>
				<div class="flex items-center gap-4 min-w-0">
					<Badge :theme="overlay.type === 'Question' ? 'blue' : 'green'">
						{{ __(overlay.type) }}
					</Badge>
					<span class="text-sm font-medium text-ink-gray-8 shrink-0">
						{{ formatSeconds(Math.round(overlay.timestamp_ms / 1000)) }}
					</span>
					<span class="text-sm text-ink-gray-6 truncate">
						{{ overlaySummary(overlay) }}
					</span>
				</div>
				<div class="flex items-center gap-2 shrink-0">
					<Badge :theme="overlay.published ? 'green' : 'gray'">
						{{ overlay.published ? __('Published') : __('Draft') }}
					</Badge>
					<Badge theme="gray">{{ __(overlay.scope) }}</Badge>
					<Button variant="ghost" @click="openDialog(overlay)">
						<template #icon>
							<span class="lucide-pencil size-4" />
						</template>
					</Button>
					<Button variant="ghost" @click="deleteOverlay(overlay)">
						<template #icon>
							<span class="lucide-trash-2 size-4" />
						</template>
					</Button>
				</div>
			</div>
		</div>
		<div
			v-else
			class="text-ink-gray-5 italic text-sm border border-dashed border-outline-gray-2 rounded-md p-8 text-center"
		>
			{{ __('No overlays yet. Add the first question or note.') }}
		</div>
	</div>

	<!-- CREATE / EDIT DIALOG -->
	<Dialog
		v-model:open="showDialog"
		:title="form.name ? __('Edit Overlay') : __('Add Overlay')"
		size="xl"
	>
		<template #default>
			<div class="space-y-4 text-base">
				<div class="grid grid-cols-2 gap-4">
					<FormControl
						:label="__('Time in Video (mm:ss)')"
						v-model="form.timestamp"
						type="text"
						placeholder="2:15"
					/>
					<FormControl
						:label="__('Type')"
						v-model="form.type"
						type="select"
						:options="[
							{ label: __('Note'), value: 'Note' },
							{ label: __('Question'), value: 'Question' },
						]"
					/>
				</div>

				<div class="grid grid-cols-2 gap-4">
					<FormControl
						:label="__('Scope')"
						v-model="form.scope"
						type="select"
						:options="[
							{ label: __('Course'), value: 'Course' },
							{ label: __('Batch'), value: 'Batch' },
							{ label: __('Global'), value: 'Global' },
						]"
					/>
					<Link
						v-if="form.scope === 'Batch'"
						v-model="form.batch"
						:label="__('Batch')"
						doctype="LMS Batch"
					/>
				</div>

				<template v-if="form.type === 'Question'">
					<div class="grid grid-cols-2 gap-4 items-end">
						<Link
							v-model="form.question"
							:label="__('Question (MCQ / short answer)')"
							doctype="LMS Question"
						/>
						<FormControl :label="__('Marks')" v-model="form.marks" type="number" />
					</div>
				</template>
				<template v-else>
					<FormControl
						:label="__('Note')"
						v-model="form.note_text"
						type="textarea"
						:rows="4"
					/>
				</template>

				<div class="flex items-center gap-6">
					<FormControl
						:label="__('Pause video at timestamp')"
						v-model="form.pause_video"
						type="checkbox"
					/>
					<FormControl :label="__('Published')" v-model="form.published" type="checkbox" />
				</div>
			</div>
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button @click="showDialog = false">{{ __('Cancel') }}</Button>
				<Button variant="solid" :loading="saving" @click="save">
					{{ __('Save') }}
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
	call,
	toast,
	usePageMeta,
} from 'frappe-ui'
import { computed, inject, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import Link from '@/components/Controls/Link.vue'
import { sessionStore } from '@/stores/session'
import { formatSeconds } from '@/utils/format'
import { getLessonOverlays, saveOverlay } from '@/utils/langApi'

const props = defineProps({
	lessonName: {
		type: String,
		required: true,
	},
})

const { brand } = sessionStore()
const user = inject('$user')
const router = useRouter()

const overlays = ref([])
const showDialog = ref(false)
const saving = ref(false)

const emptyForm = () => ({
	name: null,
	version: null,
	timestamp: '',
	type: 'Note',
	scope: 'Course',
	batch: null,
	question: null,
	marks: 1,
	note_text: '',
	pause_video: false,
	published: false,
})

const form = reactive(emptyForm())

const breadcrumbs = computed(() => [
	{ label: __('Video Overlays') },
	{ label: props.lessonName },
])

onMounted(async () => {
	const isStaff = user.data?.is_moderator || user.data?.is_instructor
	if (!isStaff) {
		router.push({ name: 'Courses' })
		return
	}
	await refresh()
})

const refresh = async () => {
	try {
		overlays.value = await getLessonOverlays(props.lessonName)
	} catch (error) {
		toast.error(error.message)
	}
}

const overlaySummary = (overlay) => {
	if (overlay.type === 'Question') return overlay.question?.question?.replace(/<[^>]*>/g, '')
	return (overlay.note_text || '').replace(/<[^>]*>/g, '')
}

const openDialog = (overlay = null) => {
	Object.assign(form, emptyForm())
	if (overlay) {
		Object.assign(form, {
			name: overlay.name,
			version: overlay.version,
			timestamp: formatSeconds(Math.round(overlay.timestamp_ms / 1000)),
			type: overlay.type,
			scope: overlay.scope,
			batch: overlay.batch,
			question: overlay.question?.name || null,
			marks: overlay.marks || 1,
			note_text: overlay.note_text || '',
			pause_video: !!overlay.pause_video,
			published: !!overlay.published,
		})
	}
	showDialog.value = true
}

const parseTimestampMs = () => {
	const raw = String(form.timestamp || '').trim()
	if (!raw) return null
	const parts = raw.split(':').map((part) => parseInt(part, 10))
	if (parts.some(isNaN)) return null
	const seconds =
		parts.length === 1 ? parts[0] : parts.length === 2 ? parts[0] * 60 + parts[1] : null
	return seconds === null ? null : seconds * 1000
}

const save = async () => {
	const timestampMs = parseTimestampMs()
	if (timestampMs === null) {
		toast.error(__('Please enter a valid timestamp (mm:ss).'))
		return
	}
	if (form.type === 'Question' && !form.question) {
		toast.error(__('Please select a question.'))
		return
	}
	if (form.type === 'Note' && !form.note_text.trim()) {
		toast.error(__('Please enter the note text.'))
		return
	}

	saving.value = true
	const payload = {
		name: form.name || undefined,
		lesson: props.lessonName,
		timestamp_ms: timestampMs,
		type: form.type,
		scope: form.scope,
		batch: form.scope === 'Batch' ? form.batch : null,
		question: form.type === 'Question' ? form.question : null,
		marks: form.marks || 1,
		note_text: form.type === 'Note' ? form.note_text : null,
		pause_video: form.pause_video ? 1 : 0,
		published: form.published ? 1 : 0,
	}
	try {
		await saveOverlay(payload, form.name ? form.version : null)
		toast.success(__('Overlay saved'))
		showDialog.value = false
		await refresh()
	} catch (error) {
		// CONFLICT (stale version) → reload the list so the editor sees the
		// concurrent change instead of silently overwriting it (§4.5.2).
		toast.error(error.message)
		if (error.code === 'VALIDATION_ERROR') await refresh()
	} finally {
		saving.value = false
	}
}

const deleteOverlay = async (overlay) => {
	try {
		await call('frappe.client.delete', {
			doctype: 'LMS Video Overlay',
			name: overlay.name,
		})
		toast.success(__('Overlay deleted'))
		await refresh()
	} catch (error) {
		toast.error(error.messages?.[0] || error.message)
	}
}

usePageMeta(() => {
	return {
		title: __('Video Overlays'),
		icon: brand.favicon,
	}
})
</script>
