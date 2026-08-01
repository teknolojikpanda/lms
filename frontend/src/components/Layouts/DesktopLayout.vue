<template>
	<div class="flex h-screen w-screen">
		<SkipLink />
		<div class="h-full border-e bg-surface-sidebar" data-whiteboard-hide>
			<AppSidebar />
		</div>
		<main
			id="lms-main-content"
			tabindex="-1"
			class="flex-1 flex flex-col h-full overflow-auto bg-surface-base"
		>
			<slot />
		</main>
		<WhiteboardExit />
	</div>
</template>
<script setup>
import AppSidebar from '@/components/Sidebar/AppSidebar.vue'
import SkipLink from '@/components/SkipLink.vue'
import WhiteboardExit from '@/components/WhiteboardExit.vue'
</script>
