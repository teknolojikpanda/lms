<template>
	<div>
		<div v-if="quizzes.length && !showQuiz && readOnly" class="leading-6">
			{{
				__('This video contains {0} {1}:').format(
					quizzes.length,
					quizzes.length == 1 ? 'quiz' : 'quizzes'
				)
			}}

			<div v-for="(quiz, index) in quizzes" class="ps-3 mt-1">
				<span>
					{{ index + 1 }}. <span class="font-semibold"> {{ quiz.quiz }} </span>
				</span>
				{{ __('at {0} minutes').format(formatTimestamp(quiz.time)) }}
			</div>
		</div>
		<div
			v-if="!showQuiz"
			ref="videoContainer"
			class="video-block relative group"
		>
			<video
				@timeupdate="updateTime"
				@ended="videoEnded"
				@click="togglePlay"
				oncontextmenu="return false"
				class="rounded-md border border-outline-gray-1 cursor-pointer"
				ref="videoRef"
				:src="fileURL"
				:type="type"
			></video>
			<!--
				Session watermark (§1.1 copying deterrence): a per-viewer code
				that makes a screen recording traceable. It moves on a schedule
				because a mark fixed in one corner is simply cropped out, and
				it stays inside the picture so a crop that removes it also
				removes content. pointer-events-none so it can never swallow a
				click meant for the player, and aria-hidden because it is a
				deterrent, not information for the viewer.
			-->
			<div
				v-if="watermark.enabled"
				class="watermark-mark"
				:style="watermarkStyle"
				aria-hidden="true"
			>
				{{ watermark.display }}
			</div>
			<div
				v-if="!playing"
				class="absolute inset-0 flex items-center justify-center cursor-pointer"
				@click="playVideo"
			>
				<div
					class="rounded-full p-4 ps-4.5"
					style="
						background: radial-gradient(
							circle,
							rgba(0, 0, 0, 0.3) 0%,
							rgba(0, 0, 0, 0.4) 50%
						);
					"
				>
					<Play />
				</div>
			</div>
			<div
				class="flex items-center gap-x-2 py-2 px-1 text-ink-base bg-gradient-to-b from-transparent to-black/75 absolute bottom-0 start-0 end-0 mx-auto rounded-md"
				:class="{
					'invisible group-hover:visible': playing,
				}"
			>
				<Button variant="ghost" class="hover:bg-transparent">
					<template #icon>
						<Play
							v-if="!playing"
							@click="playVideo"
							class="size-4 text-ink-gray-9"
						/>
						<span
							class="lucide-pause size-5 text-ink-base"
							v-else
							@click="pauseVideo"
						/>
					</template>
				</Button>

				<div class="relative flex items-center w-full flex-1">
					<input
						type="range"
						min="0"
						:max="duration"
						step="0.1"
						v-model="currentTime"
						@input="changeCurrentTime"
						class="duration-slider h-1"
					/>
					<!-- QUIZ MARKERS -->
					<div class="absolute top-0 start-0 w-full h-full pointer-events-none">
						<div
							v-for="(quiz, index) in quizzes"
							:key="index"
							:style="getQuizMarkerStyle(quiz.time)"
							class="absolute top-0 h-full w-2 bg-surface-amber-3"
						></div>
					</div>
					<!-- OVERLAY MARKERS (timestamped notes/questions) -->
					<div class="absolute top-0 start-0 w-full h-full pointer-events-none">
						<div
							v-for="overlay in overlays"
							:key="overlay.name"
							:style="getOverlayMarkerStyle(overlay.timestamp_ms)"
							class="absolute top-0 h-full w-2"
							:class="overlay.type === 'Question' ? 'bg-surface-blue-2' : 'bg-surface-green-3'"
						></div>
					</div>
				</div>
				<!-- Transient card for non-pausing note overlays -->
				<transition name="fade">
					<div
						v-if="floatingNote"
						class="absolute top-3 end-3 max-w-sm rounded-md bg-black/80 text-ink-white p-3 text-sm shadow-lg"
					>
						<div class="prose prose-sm prose-invert max-w-none" v-html="floatingNote.note_text"></div>
					</div>
				</transition>

				<span class="text-sm-medium">
					{{ formatSeconds(currentTime) }} / {{ formatSeconds(duration) }}
				</span>

				<Dropdown :options="dropdownOptions">
					<Button>{{ playbackSpeedLabel }}</Button>
				</Dropdown>

				<Button
					variant="ghost"
					@click="toggleMute"
					class="hover:bg-transparent"
				>
					<template #icon>
						<span class="lucide-volume-2 size-5 text-ink-base" v-if="!muted" />
						<span class="lucide-volume-x size-5 text-ink-base" v-else />
					</template>
				</Button>
				<Button
					variant="ghost"
					@click="toggleFullscreen"
					class="hover:bg-transparent"
				>
					<template #icon>
						<span class="lucide-maximize size-5 text-ink-base" />
					</template>
				</Button>
			</div>
		</div>
		<Quiz
			v-if="showQuiz"
			:quizName="currentQuiz"
			:inVideo="true"
			:backToVideo="resumeVideo"
		/>
		<div v-if="!readOnly" @click="showQuizModal = true">
			<Button>
				{{ __('Add Quiz to Video') }}
			</Button>
		</div>
	</div>
	<QuizInVideo
		v-model="showQuizModal"
		:quizzes="quizzes"
		:saveQuizzes="saveQuizzes"
		:duration="duration"
	/>
	<OverlayPopup
		v-model="showOverlayPopup"
		:overlay="currentOverlay"
		@closed="resumeAfterOverlay"
		@answered="markOverlayAnswered"
	/>
	<Dialog v-model:open="showQuizLoader" size="sm" bare>
		<template #default>
			<div class="flex flex-col space-y-2 p-5 text-base leading-5">
				<span class="font-semibold">
					{{ __('Time for a Quiz') }}
				</span>
				<span>
					{{
						__(
							'Complete the upcoming quiz to continue watching the video. The quiz will open in {0} {1}.'
						).format(quizLoadTimer, quizLoadTimer === 1 ? 'second' : 'seconds')
					}}
				</span>
			</div>
		</template>
	</Dialog>
</template>
<script setup>
import { ref, onMounted, computed, watch, onBeforeUnmount } from 'vue'
import { Button, Dialog, Dropdown } from 'frappe-ui'
import { formatSeconds, formatTimestamp } from '@/utils/format'
import { useSettings } from '@/stores/settings'
import Play from '@/components/Icons/Play.vue'
import QuizInVideo from '@/components/Modals/QuizInVideo.vue'
import OverlayPopup from '@/components/Modals/OverlayPopup.vue'
import { overlayContext } from '@/stores/overlayContext'
import { getLessonOverlays, getWatermark } from '@/utils/langApi'

const videoRef = ref(null)
const videoContainer = ref(null)
let playing = ref(false)
let currentTime = ref(0)
let duration = ref(0)
let muted = ref(false)
const showQuizModal = ref(false)
const showQuiz = ref(false)
const showQuizLoader = ref(false)
const quizLoadTimer = ref(0)
const currentQuiz = ref(null)
const nextQuiz = ref({})
const { settings } = useSettings()

// Session watermark (§1.1). Disabled unless the institution turns it on.
const watermark = ref({ enabled: false })
const watermarkIndex = ref(0)
let watermarkTimer = null

// Timestamped overlays (LMS Video Overlay) for the lesson on screen
const overlays = ref([])
const currentOverlay = ref(null)
const showOverlayPopup = ref(false)
const floatingNote = ref(null)
const shownOverlays = new Set()
let wasPlayingBeforeOverlay = false
let floatingNoteTimeout = null

// Speed control states
const playbackSpeed = ref(1)
const playbackSpeedLabel = ref('1x')
const playbackSpeeds = [
	{ label: '0.5x', value: 0.5 },
	{ label: '1x', value: 1 },
	{ label: '1.5x', value: 1.5 },
	{ label: '2x', value: 2 },
]

const props = defineProps({
	file: {
		type: String,
		required: true,
	},
	type: {
		type: String,
		default: 'video/mp4',
	},
	readOnly: {
		type: Boolean,
		default: true,
	},
	quizzes: {
		type: Array,
		default: () => [],
	},
	saveQuizzes: {
		type: Function,
		default: () => {},
	},
})

onMounted(() => {
	updateCurrentTime()
	updateNextQuiz()
	if (videoRef.value) {
		videoRef.value.playbackRate = 1
	}
})

onBeforeUnmount(() => {
	// The watermark rotation runs on an interval; leaving it behind is
	// exactly the leak the player soak test looks for.
	stopWatermark()
	clearTimeout(floatingNoteTimeout)
})

watch(
	() => overlayContext.lesson,
	async (lesson) => {
		if (!lesson || !props.readOnly || overlayContext.suspended) {
			overlays.value = []
			stopWatermark()
			return
		}
		try {
			const rows = await getLessonOverlays(lesson)
			overlays.value = rows.sort((a, b) => a.timestamp_ms - b.timestamp_ms)
		} catch {
			// Overlays are an enhancement — never break video playback.
			overlays.value = []
		}
		loadWatermark(lesson)
	},
	{ immediate: true }
)

const loadWatermark = async (lesson) => {
	try {
		const config = await getWatermark(lesson)
		watermark.value = config?.enabled ? config : { enabled: false }
	} catch {
		// A failed watermark must not stop the lesson: the institution
		// loses deterrence for this session, the student loses nothing.
		watermark.value = { enabled: false }
	}

	stopWatermark()
	if (watermark.value.enabled && watermark.value.positions?.length) {
		watermarkIndex.value = 0
		watermarkTimer = setInterval(() => {
			watermarkIndex.value = (watermarkIndex.value + 1) % watermark.value.positions.length
		}, (watermark.value.move_interval_seconds || 20) * 1000)
	}
}

const stopWatermark = () => {
	if (watermarkTimer) {
		clearInterval(watermarkTimer)
		watermarkTimer = null
	}
}

const watermarkStyle = computed(() => {
	const positions = watermark.value.positions || []
	const position = positions[watermarkIndex.value] || { x: 50, y: 50 }
	return {
		left: `${position.x}%`,
		top: `${position.y}%`,
		opacity: watermark.value.opacity ?? 0.35,
	}
})

const checkOverlays = (timeSeconds) => {
	if (showOverlayPopup.value || showQuiz.value) return
	const due = overlays.value.find(
		(overlay) =>
			!shownOverlays.has(overlay.name) &&
			timeSeconds >= overlay.timestamp_ms / 1000 &&
			timeSeconds <= overlay.timestamp_ms / 1000 + 1.5
	)
	if (!due) return

	shownOverlays.add(due.name)

	// Already-answered questions never interrupt playback again.
	if (due.type === 'Question' && due.response) return

	if (due.type === 'Question' || due.pause_video) {
		wasPlayingBeforeOverlay = playing.value
		videoRef.value?.pause()
		playing.value = false
		currentOverlay.value = due
		showOverlayPopup.value = true
	} else {
		floatingNote.value = due
		clearTimeout(floatingNoteTimeout)
		floatingNoteTimeout = setTimeout(() => {
			floatingNote.value = null
		}, 8000)
	}
}

const resumeAfterOverlay = () => {
	currentOverlay.value = null
	if (wasPlayingBeforeOverlay && videoRef.value) {
		videoRef.value.play()
		playing.value = true
	}
}

const markOverlayAnswered = (overlayName, feedback) => {
	const overlay = overlays.value.find((row) => row.name === overlayName)
	if (overlay) overlay.response = feedback
}

const getOverlayMarkerStyle = (timestampMs) => {
	const percentage = (timestampMs / 1000 / Math.ceil(duration.value || 1)) * 100
	return {
		insetInlineStart: `${Math.min(percentage, 99)}%`,
	}
}

const updateCurrentTime = () => {
	setTimeout(() => {
		videoRef.value.onloadedmetadata = () => {
			duration.value = videoRef.value.duration
		}
		videoRef.value.ontimeupdate = () => {
			currentTime.value = videoRef.value?.currentTime || currentTime.value
			checkOverlays(currentTime.value)
			if (currentTime.value >= nextQuiz.value.time) {
				videoRef.value.pause()
				playing.value = false
				videoRef.value.onTimeupdate = null
				currentQuiz.value = nextQuiz.value.quiz
				quizLoadTimer.value = 7
			}
		}
	}, 0)
}

watch(quizLoadTimer, () => {
	if (quizLoadTimer.value > 0) {
		showQuizLoader.value = true
		setTimeout(() => {
			quizLoadTimer.value -= 1
		}, 1000)
	} else {
		showQuizLoader.value = false
		showQuiz.value = true
	}
})

const resumeVideo = (restart = false) => {
	showQuiz.value = false
	currentQuiz.value = null
	updateCurrentTime()
	setTimeout(() => {
		videoRef.value.currentTime = restart ? 0 : currentTime.value
		videoRef.value.play()
		playing.value = true
		updateNextQuiz()
	}, 0)
}

const updateNextQuiz = () => {
	if (!props.quizzes.length) return

	props.quizzes.forEach((quiz) => {
		if (typeof quiz.time == 'string' && quiz.time.includes(':')) {
			let time = quiz.time.split(':')
			let timeInSeconds = parseInt(time[0]) * 60 + parseInt(time[1])
			quiz.time = timeInSeconds
		}
	})

	props.quizzes.sort((a, b) => a.time - b.time)

	const nextQuizIndex = props.quizzes.findIndex(
		(quiz) => quiz.time > currentTime.value
	)
	if (nextQuizIndex !== -1) {
		nextQuiz.value = props.quizzes[nextQuizIndex]
	} else {
		nextQuiz.value = {}
	}
}

const fileURL = computed(() => {
	return props.file
})

const playVideo = () => {
	videoRef.value.play()
	playing.value = true
}

const pauseVideo = () => {
	videoRef.value.pause()
	playing.value = false
}

const togglePlay = () => {
	if (playing.value) {
		pauseVideo()
	} else {
		playVideo()
	}
}

const videoEnded = () => {
	playing.value = false
}

const toggleMute = () => {
	videoRef.value.muted = !videoRef.value.muted
	muted.value = videoRef.value.muted
}

const changeCurrentTime = () => {
	if (
		settings.data?.prevent_skipping_videos &&
		currentTime.value > videoRef.value.currentTime
	)
		return
	videoRef.value.currentTime = currentTime.value
	updateNextQuiz()
}

const toggleFullscreen = () => {
	if (document.fullscreenElement) {
		document.exitFullscreen()
	} else {
		videoContainer.value.requestFullscreen()
	}
}

const getQuizMarkerStyle = (time) => {
	const percentage = ((time - 5) / Math.ceil(duration.value)) * 100
	return {
		insetInlineStart: `${percentage}%`,
	}
}

const setPlaybackSpeed = (speed, label) => {
	playbackSpeed.value = speed
	playbackSpeedLabel.value = label
	if (videoRef.value) {
		videoRef.value.playbackRate = speed
	}
}

const dropdownOptions = computed(() =>
	playbackSpeeds.map((speed) => ({
		label: speed.label,
		active: playbackSpeed.value === speed.value,
		onClick: () => setPlaybackSpeed(speed.value, speed.label),
	}))
)
</script>

<style scoped>
.video-block {
	width: 100%;
	margin: 0 auto;
}

.video-block video {
	width: 100%;
	height: auto;
}

iframe {
	width: 100%;
	min-height: 500px;
}

/*
 * Watermark: legible enough to read back off a recording, quiet enough to
 * watch through. Never interactive, never selectable, and it must survive
 * fullscreen — hence positioning against the player container rather than
 * the page.
 */
.watermark-mark {
	position: absolute;
	z-index: 5;
	pointer-events: none;
	user-select: none;
	font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	font-size: 0.875rem;
	letter-spacing: 0.08em;
	color: #fff;
	/* Outline rather than a solid background: readable over both bright and
	   dark footage without blocking the picture. */
	text-shadow:
		0 0 3px rgba(0, 0, 0, 0.9),
		0 0 6px rgba(0, 0, 0, 0.7);
	transition: left 1.2s ease, top 1.2s ease;
	white-space: nowrap;
}

.fade-enter-active,
.fade-leave-active {
	transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
	opacity: 0;
}

.duration-slider {
	-webkit-appearance: none;
	appearance: none;
	border-radius: 10px;
	background-color: theme('colors.gray.600');
	cursor: pointer;
}

.duration-slider::-webkit-slider-thumb {
	width: 2px;
	border-radius: 50%;
	-webkit-appearance: none;
	background-color: theme('colors.white');
}

@media screen and (-webkit-min-device-pixel-ratio: 0) {
	input[type='range'] {
		overflow: hidden;
		width: 100%;
		-webkit-appearance: none;
	}

	input[type='range']::-webkit-slider-thumb {
		-webkit-appearance: none;
		cursor: pointer;
		box-shadow: -500px 0 0 500px theme('colors.white');
	}
}
</style>
