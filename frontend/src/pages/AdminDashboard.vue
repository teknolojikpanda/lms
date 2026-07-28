<template>
	<header
		class="sticky top-0 z-10 flex items-center justify-between border-b bg-surface-base px-3 py-2.5 sm:px-5"
	>
		<Breadcrumbs :items="[{ label: __('Institution Dashboard'), route: { name: 'AdminDashboard' } }]" />
		<div class="flex gap-2">
			<router-link :to="{ name: 'Batches' }">
				<Button>{{ __('Create Class') }}</Button>
			</router-link>
			<router-link :to="{ name: 'NewDataImport', params: { doctype: 'User' } }">
				<Button>{{ __('Import Students') }}</Button>
			</router-link>
			<router-link :to="{ name: 'Quizzes' }">
				<Button variant="solid">{{ __('Plan Exam') }}</Button>
			</router-link>
		</div>
	</header>

	<div class="mx-4 lg:w-10/12 lg:mx-auto py-8">
		<div v-if="loading" class="flex justify-center py-20">
			<LoadingIndicator class="size-5 text-ink-gray-6" />
		</div>

		<div v-else-if="dashboard">
			<!-- KPI CARDS -->
			<div class="grid grid-cols-2 lg:grid-cols-5 gap-3">
				<div class="border rounded-md">
					<NumberChart
						:config="{ title: __('Active Students'), value: dashboard.kpis.active_students }"
					/>
				</div>
				<div class="border rounded-md">
					<NumberChart
						:config="{ title: __('Active Teachers'), value: dashboard.kpis.active_teachers }"
					/>
				</div>
				<div class="border rounded-md">
					<NumberChart :config="{ title: __('Classes'), value: dashboard.kpis.batches }" />
				</div>
				<div class="border rounded-md">
					<NumberChart
						:config="{ title: __('Published Courses'), value: dashboard.kpis.published_courses }"
					/>
				</div>
				<div class="border rounded-md">
					<NumberChart
						:config="{ title: __('Enrollments'), value: dashboard.kpis.enrollments }"
					/>
				</div>
			</div>

			<!-- PENDING GRADING -->
			<div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
				<router-link :to="{ name: 'SpeakingGrading' }" class="block">
					<div
						class="border rounded-md p-4 flex items-center justify-between hover:bg-surface-gray-1"
					>
						<div>
							<div class="text-sm text-ink-gray-6">{{ __('Speaking — pending review') }}</div>
							<div class="text-2xl font-semibold text-ink-gray-9 mt-1">
								{{ dashboard.pending_grading.speaking }}
							</div>
						</div>
						<span class="lucide-mic size-6 text-ink-gray-5" />
					</div>
				</router-link>
				<router-link :to="{ name: 'AssignmentSubmissionList' }" class="block">
					<div
						class="border rounded-md p-4 flex items-center justify-between hover:bg-surface-gray-1"
					>
						<div>
							<div class="text-sm text-ink-gray-6">{{ __('Assignments — not graded') }}</div>
							<div class="text-2xl font-semibold text-ink-gray-9 mt-1">
								{{ dashboard.pending_grading.assignments }}
							</div>
						</div>
						<span class="lucide-pencil size-6 text-ink-gray-5" />
					</div>
				</router-link>
			</div>

			<!-- CHARTS -->
			<div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
				<div class="border rounded-md min-h-72">
					<AxisChart
						v-if="activityData.length"
						:config="{
							data: activityData,
							title: __('Activity (14 days)'),
							subtitle: __('Lesson progress and quiz submissions per day'),
							xAxis: { key: 'date', type: 'time', timeGrain: 'day', title: __('Date') },
							yAxis: { title: __('Count') },
							series: [
								{ name: 'lessons', type: 'line', showDataPoints: true },
								{ name: 'quizzes', type: 'line', showDataPoints: true },
							],
						}"
					/>
				</div>
				<div class="border rounded-md min-h-72">
					<DonutChart
						v-if="dashboard.placement_distribution.length"
						:config="{
							data: dashboard.placement_distribution,
							title: __('Placement Distribution'),
							subtitle: __('Students by CEFR level'),
							categoryColumn: 'level',
							valueColumn: 'value',
						}"
					/>
					<div v-else class="p-5 text-sm italic text-ink-gray-5">
						{{ __('No completed placement attempts yet.') }}
					</div>
				</div>
			</div>

			<!-- RISKY STUDENTS -->
			<div class="border rounded-md mt-4 p-5">
				<h2 class="font-semibold text-ink-gray-9 mb-1">{{ __('Students at Risk') }}</h2>
				<p class="text-xs text-ink-gray-5 mb-4">
					{{ __('Heuristic: inactive 14+ days or average quiz score below 50%.') }}
				</p>
				<div v-if="dashboard.risky_students.length" class="space-y-2">
					<div
						v-for="student in dashboard.risky_students"
						:key="student.member"
						class="flex items-center justify-between rounded border border-outline-gray-2 p-3 text-sm"
					>
						<div class="min-w-0">
							<span class="font-medium text-ink-gray-8">{{ student.full_name }}</span>
							<span class="text-ink-gray-5 ms-2">{{ student.member }}</span>
						</div>
						<div class="flex items-center gap-2 shrink-0">
							<Badge v-for="reason in student.reasons" :key="reason" theme="red">
								{{ reason }}
							</Badge>
						</div>
					</div>
				</div>
				<div v-else class="text-sm italic text-ink-gray-5">
					{{ __('No students currently flagged.') }}
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import {
	AxisChart,
	Badge,
	Breadcrumbs,
	Button,
	DonutChart,
	LoadingIndicator,
	NumberChart,
	toast,
	usePageMeta,
} from 'frappe-ui'
import { computed, inject, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { sessionStore } from '@/stores/session'
import { getAdminDashboard } from '@/utils/langApi'

const { brand } = sessionStore()
const user = inject('$user')
const router = useRouter()

const dashboard = ref(null)
const loading = ref(true)

onMounted(async () => {
	if (!user.data?.is_moderator) {
		router.push({ name: 'Courses' })
		return
	}
	try {
		dashboard.value = await getAdminDashboard()
	} catch (error) {
		toast.error(error.message)
	} finally {
		loading.value = false
	}
})

const activityData = computed(() => {
	if (!dashboard.value) return []
	const quizByDate = Object.fromEntries(
		dashboard.value.activity.quiz_submissions.map((row) => [row.date, row.value])
	)
	return dashboard.value.activity.lesson_progress.map((row) => ({
		date: new Date(row.date),
		lessons: row.value,
		quizzes: quizByDate[row.date] || 0,
	}))
})

usePageMeta(() => {
	return {
		title: __('Institution Dashboard'),
		icon: brand.favicon,
	}
})
</script>
