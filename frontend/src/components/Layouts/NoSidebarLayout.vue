<template>
	<div class="relative flex h-full flex-col">
		<SkipLink target-id="scrollContainer" />
		<div class="h-full flex-1">
			<div class="flex h-screen text-base bg-surface-base">
				<main class="w-full overflow-auto" id="scrollContainer" tabindex="-1">
					<slot />
				</main>
			</div>
		</div>
		<WhiteboardExit />
	</div>
</template>
<script setup>
import SkipLink from '@/components/SkipLink.vue'
import WhiteboardExit from '@/components/WhiteboardExit.vue'
</script>
