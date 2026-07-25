<template>
	<header
		class="sticky top-0 z-10 flex items-center justify-between border-b bg-surface-base px-3 py-2.5 sm:px-5"
	>
		<Breadcrumbs :items="[{ label: __('Owner Dashboard'), route: { name: 'OwnerDashboard' } }]" />
	</header>

	<div class="mx-4 lg:w-10/12 lg:mx-auto py-8">
		<div v-if="loading" class="flex justify-center py-20">
			<LoadingIndicator class="size-5 text-ink-gray-6" />
		</div>

		<div v-else-if="dashboard">
			<!-- KPI CARDS -->
			<div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
				<div class="border rounded-md">
					<NumberChart :config="{ title: __('Total Users'), value: dashboard.kpis.total_users }" />
				</div>
				<div class="border rounded-md">
					<NumberChart
						:config="{ title: __('Active Users (30d)'), value: dashboard.kpis.active_users_30d }"
					/>
				</div>
				<div class="border rounded-md">
					<NumberChart
						:config="{
							title: __('Video Minutes (total)'),
							value: dashboard.kpis.watch_minutes_total,
						}"
					/>
				</div>
				<div class="border rounded-md">
					<NumberChart
						:config="{
							title: __('Speaking Minutes (total)'),
							value: dashboard.kpis.speaking_minutes_total,
						}"
					/>
				</div>
			</div>

			<!-- OPS SIGNALS -->
			<div class="grid grid-cols-3 gap-3 mt-4">
				<div
					class="border rounded-md p-4"
					:class="dashboard.ops.speaking_failed ? 'border-outline-red-2' : ''"
				>
					<div class="text-sm text-ink-gray-6">{{ __('Speaking — failed jobs') }}</div>
					<div
						class="text-2xl font-semibold mt-1"
						:class="dashboard.ops.speaking_failed ? 'text-ink-red-3' : 'text-ink-gray-9'"
					>
						{{ dashboard.ops.speaking_failed }}
					</div>
				</div>
				<div class="border rounded-md p-4">
					<div class="text-sm text-ink-gray-6">{{ __('Speaking — in pipeline') }}</div>
					<div class="text-2xl font-semibold text-ink-gray-9 mt-1">
						{{ dashboard.ops.speaking_in_flight }}
					</div>
				</div>
				<div class="border rounded-md p-4">
					<div class="text-sm text-ink-gray-6">{{ __('Error logs (24h)') }}</div>
					<div class="text-2xl font-semibold text-ink-gray-9 mt-1">
						{{ dashboard.ops.pending_error_logs_24h }}
					</div>
				</div>
			</div>

			<!-- TRENDS -->
			<div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
				<div class="border rounded-md min-h-72">
					<AxisChart
						v-if="usageTrend.length"
						:config="{
							data: usageTrend,
							title: __('Usage (30 days)'),
							subtitle: __('Video and speaking minutes per day'),
							xAxis: { key: 'date', type: 'time', timeGrain: 'day', title: __('Date') },
							yAxis: { title: __('Minutes') },
							series: [
								{ name: 'video', type: 'line', showDataPoints: true },
								{ name: 'speaking', type: 'line', showDataPoints: true },
							],
						}"
					/>
				</div>
				<div class="border rounded-md min-h-72">
					<AxisChart
						v-if="signupTrend.length"
						:config="{
							data: signupTrend,
							title: __('New Users (30 days)'),
							subtitle: __('Signups per day'),
							xAxis: { key: 'date', type: 'time', timeGrain: 'day', title: __('Date') },
							yAxis: { title: __('Users') },
							series: [{ name: 'users', type: 'line', showDataPoints: true }],
						}"
					/>
				</div>
			</div>

			<!-- COST ESTIMATE -->
			<div class="border rounded-md mt-4 p-5">
				<div class="flex items-center justify-between">
					<h2 class="font-semibold text-ink-gray-9">
						{{ __('Cost Estimate (application-metered)') }}
					</h2>
					<div class="text-2xl font-semibold text-ink-gray-9">
						${{ dashboard.cost_estimate.total_usd }}
					</div>
				</div>
				<div class="mt-4 space-y-2">
					<div
						v-for="row in dashboard.cost_estimate.line_items"
						:key="row.item"
						class="flex items-center justify-between text-sm border-b border-outline-gray-1 pb-2"
					>
						<span class="text-ink-gray-7">{{ row.item }}</span>
						<span class="font-medium text-ink-gray-9">${{ row.usd }}</span>
					</div>
				</div>
				<p class="text-xs text-ink-gray-5 mt-4">
					{{ dashboard.cost_estimate.disclaimer }}
				</p>
			</div>

			<div class="grid grid-cols-2 gap-3 mt-4">
				<div class="border rounded-md p-4">
					<div class="text-sm text-ink-gray-6">{{ __('Storage Used') }}</div>
					<div class="text-2xl font-semibold text-ink-gray-9 mt-1">
						{{ dashboard.kpis.storage_gb }} GB
					</div>
				</div>
				<div class="border rounded-md p-4">
					<div class="text-sm text-ink-gray-6">{{ __('Published Courses') }}</div>
					<div class="text-2xl font-semibold text-ink-gray-9 mt-1">
						{{ dashboard.kpis.published_courses }}
					</div>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import {
	AxisChart,
	Breadcrumbs,
	LoadingIndicator,
	NumberChart,
	toast,
	usePageMeta,
} from 'frappe-ui'
import { computed, inject, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { sessionStore } from '@/stores/session'
import { getOwnerDashboard } from '@/utils/langApi'

const { brand } = sessionStore()
const user = inject('$user')
const router = useRouter()

const dashboard = ref(null)
const loading = ref(true)

onMounted(async () => {
	if (!user.data?.is_system_manager) {
		router.push({ name: 'Courses' })
		return
	}
	try {
		dashboard.value = await getOwnerDashboard()
	} catch (error) {
		toast.error(error.message)
	} finally {
		loading.value = false
	}
})

const usageTrend = computed(() => {
	if (!dashboard.value) return []
	const speakingByDate = Object.fromEntries(
		dashboard.value.trends.speaking_minutes.map((row) => [row.date, row.value])
	)
	return dashboard.value.trends.watch_minutes.map((row) => ({
		date: new Date(row.date),
		video: row.value,
		speaking: speakingByDate[row.date] || 0,
	}))
})

const signupTrend = computed(() => {
	if (!dashboard.value) return []
	return dashboard.value.trends.new_users.map((row) => ({
		date: new Date(row.date),
		users: row.value,
	}))
})

usePageMeta(() => {
	return {
		title: __('Owner Dashboard'),
		icon: brand.favicon,
	}
})
</script>
