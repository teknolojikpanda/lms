<template>
	<Dialog
		v-model:open="show"
		:title="overlay?.type === 'Question' ? __('Question') : __('Note')"
		size="xl"
		:disableOutsideClickToClose="overlay?.type === 'Question' && !feedback"
	>
		<template #default>
			<div v-if="overlay" class="text-base">
				<!-- NOTE -->
				<div v-if="overlay.type === 'Note'">
					<div class="prose prose-sm max-w-none" v-html="overlay.note_text"></div>
				</div>

				<!-- QUESTION -->
				<div v-else-if="overlay.question">
					<div class="prose prose-sm max-w-none" v-html="overlay.question.question"></div>

					<div v-if="overlay.question.type === 'Choices'" class="mt-4 space-y-2">
						<label
							v-for="option in overlay.question.options"
							:key="option"
							class="flex items-center gap-2 rounded-md border p-3 text-sm"
							:class="[
								optionClass(option),
								feedback ? 'pointer-events-none' : 'cursor-pointer',
							]"
						>
							<input
								:type="overlay.question.multiple ? 'checkbox' : 'radio'"
								name="overlay-question"
								:checked="isSelected(option)"
								:disabled="!!feedback"
								@change="toggleOption(option)"
								class="accent-gray-900"
							/>
							<span class="text-ink-gray-8">{{ option }}</span>
						</label>
					</div>

					<div v-else class="mt-4">
						<FormControl
							type="text"
							v-model="textAnswer"
							:disabled="!!feedback"
							:placeholder="__('Type your answer')"
						/>
					</div>

					<div
						v-if="feedback"
						class="mt-4 rounded-md p-3 text-sm font-medium"
						:class="
							feedback.is_correct
								? 'bg-surface-green-2 text-ink-green-3'
								: 'bg-surface-red-2 text-ink-red-3'
						"
					>
						{{
							feedback.is_correct
								? __('Correct! You earned {0} mark(s).').format(feedback.marks_obtained)
								: __('Not quite right.')
						}}
					</div>
				</div>
			</div>
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button
					v-if="overlay?.type === 'Question' && !feedback && !overlay.response"
					variant="solid"
					:loading="submitting"
					@click="submitAnswer"
				>
					{{ __('Submit Answer') }}
				</Button>
				<Button v-else variant="solid" @click="close">
					{{ __('Continue') }}
				</Button>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog, FormControl, toast } from 'frappe-ui'
import { ref, watch } from 'vue'
import { submitOverlayAnswer } from '@/utils/langApi'

const show = defineModel()

const props = defineProps({
	overlay: {
		type: Object,
		default: null,
	},
})

const emit = defineEmits(['closed', 'answered'])

const selected = ref([])
const textAnswer = ref('')
const feedback = ref(null)
const submitting = ref(false)

watch(
	() => props.overlay,
	(overlay) => {
		selected.value = []
		textAnswer.value = ''
		submitting.value = false
		// If the student answered this overlay before, show it as feedback.
		feedback.value = overlay?.response
			? {
					is_correct: overlay.response.is_correct,
					marks_obtained: overlay.response.marks_obtained,
				}
			: null
	},
	{ immediate: true }
)

const isSelected = (option) => selected.value.includes(option)

const toggleOption = (option) => {
	if (props.overlay.question.multiple) {
		selected.value = isSelected(option)
			? selected.value.filter((item) => item !== option)
			: [...selected.value, option]
	} else {
		selected.value = [option]
	}
}

const optionClass = (option) => {
	if (!feedback.value) {
		return isSelected(option)
			? 'border-outline-gray-4 bg-surface-gray-2'
			: 'border-outline-gray-2'
	}
	return isSelected(option) ? 'border-outline-gray-4 bg-surface-gray-2' : 'border-outline-gray-2'
}

const submitAnswer = async () => {
	const question = props.overlay.question
	const answer =
		question.type === 'Choices' ? JSON.stringify(selected.value) : textAnswer.value

	if (question.type === 'Choices' && !selected.value.length) {
		toast.error(__('Please select an answer.'))
		return
	}
	if (question.type !== 'Choices' && !textAnswer.value.trim()) {
		toast.error(__('Please type an answer.'))
		return
	}

	submitting.value = true
	try {
		feedback.value = await submitOverlayAnswer(props.overlay.name, answer)
		emit('answered', props.overlay.name, feedback.value)
	} catch (error) {
		toast.error(error.message)
	} finally {
		submitting.value = false
	}
}

const close = () => {
	show.value = false
	emit('closed')
}
</script>
